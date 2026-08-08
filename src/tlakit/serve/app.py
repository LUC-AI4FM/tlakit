"""FastAPI wiring for the runner.

Deliberately without `from __future__ import annotations`: FastAPI resolves
endpoint annotations at import time, and a stringified annotation naming a model
that lives in a function's local scope cannot be resolved — the parameter then
silently degrades to a query parameter and every request 422s. Models are
module-level here for the same reason.

All the decisions live in `tlakit.serve`; this file only routes.
"""

import asyncio
import hmac
import logging
import os
import pathlib
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException


async def _part_text(part: Any, cap: int) -> Optional[str]:
    """A form part as text, whether it arrived as a file or a plain field.

    `curl -F spec=@Counter.tla` sends a file part; `-F spec='...'` sends a
    field. Both are reasonable ways to ask the same question, so both work.

    The read is bounded at `cap + 1`. Starlette's own `max_part_size` does not
    cover this: it counts bytes held in memory, and a *file* part is spooled to
    disk instead, so an upload sails past it (measured -- a 69 KB file part
    reached `validate()` untouched with `max_part_size` set to 64 KB). One byte
    over the cap is all that is needed to know it is over.
    """
    if part is None:
        return None
    if isinstance(part, UploadFile):
        return (await part.read(cap + 1)).decode("utf-8", errors="replace")
    return str(part)


def _refuse_oversized_body(request: Request, cap: int) -> None:
    """Reject on the declared length, before a byte of body is read.

    This is the half of the cap that is actually a defence. Everything after it
    -- the bounded read, `validate()` -- runs on data that has already arrived,
    and a limit measured after the fact only tells you how much you accepted.
    A sender that omits Content-Length (chunked) falls through to the bounded
    read, which is why that one exists too.
    """
    declared = request.headers.get("content-length")
    if declared is None:
        return
    try:
        length = int(declared)
    except ValueError:
        raise HTTPException(422, detail="malformed content-length") from None
    # Multipart wraps each part in a boundary and headers; allow a little room
    # so a spec at exactly the cap is not refused for its own envelope.
    if length > cap + 4096:
        raise HTTPException(413, detail=f"body exceeds {cap} bytes")

from ..api import build_config, module_name_of
from ..result import Outcome
from ..cli import CliRunner
from . import Limits, RequestTooLarge, as_json, clamp_timeout, startup_checks, validate
from .limiter import (
    CHEAP_RULES,
    CHECK_RULES,
    PARSE_RULES,
    RateLimiter,
    client_key,
)


#: When set, every request must present this value in X-Tlakit-Key. The edge
#: Worker supplies it, so learning the tunnel hostname is not enough to reach
#: the service directly.
log = logging.getLogger("tlakit.serve")

KEY_ENV = "TLAKIT_SERVE_KEY"
#: Preferred over KEY_ENV under launchd: a plist is world-readable, so the
#: secret lives in a mode-640 file that only the service's group can read.
KEY_FILE_ENV = "TLAKIT_SERVE_KEY_FILE"


def expected_key() -> Optional[str]:
    path = os.environ.get(KEY_FILE_ENV)
    if path:
        try:
            value = pathlib.Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            # Refuse every request rather than silently serving unauthenticated
            # because the key file went missing.
            return "\x00unreadable"
        return value or None
    return os.environ.get(KEY_ENV) or None


def require_key(supplied: Optional[str]) -> None:
    expected = expected_key()
    if not expected:
        return  # unset means local development; bind to localhost.
    # Constant-time: a timing oracle on a shared secret is worth avoiding even
    # behind a rate limiter.
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="missing or invalid key")


class CheckRequest(BaseModel):
    """The entire input surface of this service."""

    # Unknown fields are rejected rather than ignored: a client sending
    # extra_opts should be told it does not exist, not silently obeyed-adjacent.
    model_config = {"extra": "forbid"}

    spec: str = Field(description="A complete TLA+ module.")
    config: Optional[str] = Field(default=None, description="Raw .cfg text.")
    invariants: Optional[List[str]] = None
    properties: Optional[List[str]] = None
    constants: Optional[Dict[str, Any]] = None
    specification: str = "Spec"
    timeout: Optional[float] = None
    coverage: bool = False
    graph: bool = False


class ParseRequest(BaseModel):
    """A spec and nothing else.

    No config, no invariants, no timeout: SANY reads one module and reports
    what is wrong with it. Every field `CheckRequest` carries describes a
    search, and there is no search here -- so `extra: forbid` refuses them
    rather than accepting a request whose options could not have had an effect.
    """

    model_config = {"extra": "forbid"}

    spec: str = Field(description="A complete TLA+ module.")


def create_app(runner: Optional[CliRunner] = None, limits: Optional[Limits] = None):
    from . import public_runner

    limits = limits or Limits()
    runner = runner or public_runner()
    startup_checks(runner)
    gate = asyncio.Semaphore(limits.concurrency)
    # Its own gate, not a share of `gate`: see MAX_PARSE_CONCURRENCY.
    parse_gate = asyncio.Semaphore(limits.parse_concurrency)
    check_limiter = RateLimiter(rules=CHECK_RULES)
    parse_limiter = RateLimiter(rules=PARSE_RULES)
    cheap_limiter = RateLimiter(rules=CHEAP_RULES)

    def key_of(request: Request) -> str:
        return client_key(
            request.headers.get("cf-connecting-ip"),
            request.client.host if request.client else None,
        )

    def enforce(limiter: RateLimiter, request: Request) -> None:
        key = key_of(request)
        wait = limiter.check(key)
        if wait is not None:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded; retry in {wait:g}s",
                headers={"retry-after": str(int(wait) + 1)},
            )

    app = FastAPI(
        title="tla-runner",
        description=(
            "Model-check a TLA+ specification. Specs run without "
            "CommunityModules, so they have no I/O primitives."
        ),
        version="0.1.0",
    )

    # Any origin may call this, because a browser is now a first-class client:
    # tlakit's Pyodide kernel runs in the visitor's own tab and reaches this
    # service by fetch, and a JSON content-type makes that a preflighted
    # request. A wildcard is the right answer rather than a lax one -- the
    # service has no cookies and no session, so there is nothing an attacker
    # could ride. `allow_credentials` stays False for exactly that reason: the
    # moment it is True, a wildcard becomes illegal and the browser will
    # (correctly) refuse the response.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
        max_age=86400,
    )

    # A single static string, read once at startup. Not a file server: there is
    # no path parameter anywhere, so no route can be talked into reading
    # something else.
    landing = (pathlib.Path(__file__).parent / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("referrer-policy", "no-referrer")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault(
            "cross-origin-opener-policy", "same-origin"
        )
        # This service is served over Cloudflare, so HSTS is safe to assert.
        response.headers.setdefault(
            "strict-transport-security", "max-age=31536000; includeSubDomains"
        )
        # Nothing here is cacheable and a shared cache holding a counterexample
        # keyed by URL would be surprising.
        response.headers.setdefault("cache-control", "no-store")
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        enforce(cheap_limiter, request)
        return HTMLResponse(
            landing,
            headers={
                # The page loads fonts from Google and talks only to itself.
                "content-security-policy": (
                    "default-src 'none'; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "font-src https://fonts.gstatic.com; "
                    "script-src 'unsafe-inline'; "
                    "connect-src 'self'; "
                    "base-uri 'none'; form-action 'none'"
                ),
                "referrer-policy": "no-referrer",
                "x-content-type-options": "nosniff",
            },
        )

    @app.get("/health")
    async def health(
        request: Request,
        x_tlakit_key: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        enforce(cheap_limiter, request)
        require_key(x_tlakit_key)
        return {
            "ok": True,
            "isolated": True,
            "community_modules": False,
            "key_required": bool(expected_key()),
            "limits": {
                "checks_per_minute": CHECK_RULES[0].limit,
                "checks_per_hour": CHECK_RULES[1].limit,
                "parses_per_minute": PARSE_RULES[0].limit,
                "parses_per_hour": PARSE_RULES[1].limit,
                "spec_bytes": limits.spec_bytes,
                "max_timeout": limits.max_timeout,
                "concurrency": limits.concurrency,
            },
        }

    @app.post("/parse")
    async def parse(
        payload: ParseRequest,
        request: Request,
        x_tlakit_key: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Syntax- and level-check one module. No search, so no config.

        This exists because a browser has no local SANY (#67): without it
        `%%tla` can only report that a module was defined, and a syntax error
        waits until the reader writes a config and runs a check -- a worse
        moment to find out, with a message about the wrong thing.
        """
        enforce(parse_limiter, request)
        require_key(x_tlakit_key)
        try:
            # Same ceiling as a check: the cost being bounded here is the
            # module SANY has to read, which is the same text either way.
            validate(payload.spec, "", limits)
            module = module_name_of(payload.spec)
        except (RequestTooLarge, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if parse_gate.locked():
            parse_limiter.refund(key_of(request))
            raise HTTPException(status_code=503, detail="busy; retry shortly")

        async with parse_gate:
            result = await asyncio.to_thread(
                runner.parse,
                payload.spec,
                module,
                timeout=clamp_timeout(None, limits),
                heap=limits.parse_heap,
            )
        if result.outcome is Outcome.ERROR:
            # Same reasoning as /check: responses omit raw, so an unexpected
            # SANY failure is otherwise invisible from both sides.
            log.error(
                "unexpected SANY failure: exit=%s argv=%s stderr=%s stdout=%s",
                result.raw.exit_code,
                result.raw.argv,
                result.raw.stderr[:2000],
                result.raw.stdout[:2000],
            )
        return as_json(result, limits)

    async def _payload_from(request: Request) -> CheckRequest:
        """Read the body as JSON or as an uploaded file, by content type.

        The JSON form is the original and is untouched. `multipart/form-data`
        exists because a TLA+ module is full of the three characters JSON
        escaping is worst at -- newlines, backslashes and quotes -- so `curl -F
        spec=@Counter.tla` is the difference between usable and not (#64).

        The size cap is applied by `max_part_size` while the parts are being
        read, not after: a cap checked on a buffer is not a cap, it is a
        measurement of what already arrived.
        """
        _refuse_oversized_body(request, limits.spec_bytes)
        kind = (request.headers.get("content-type") or "").split(";")[0].strip()
        if kind != "multipart/form-data":
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(422, detail=f"body must be JSON: {exc}") from exc
            try:
                return CheckRequest.model_validate(body)
            except PydanticValidationError as exc:
                raise HTTPException(422, detail=exc.errors()) from exc

        try:
            async with request.form(
                max_files=2, max_fields=8, max_part_size=limits.spec_bytes
            ) as form:
                spec = await _part_text(form.get("spec"), limits.spec_bytes)
                config = await _part_text(form.get("cfg"), limits.config_bytes)
        except MultiPartException as exc:
            # What `max_part_size` raises when a part runs over. Say which cap.
            raise HTTPException(
                422, detail=f"upload exceeds {limits.spec_bytes} bytes: {exc}"
            ) from exc
        if spec is None:
            raise HTTPException(
                422,
                detail="multipart requires a `spec` part, e.g. -F spec=@Counter.tla",
            )
        # Note what is *not* read here: the uploaded filename. It is
        # attacker-controlled and the module name comes from the module header
        # below, exactly as it does for the JSON form.
        return CheckRequest(spec=spec, config=config)

    @app.post("/check")
    async def check(
        request: Request,
        x_tlakit_key: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        # Rate limit before doing any work, including before reading the body,
        # so a flood of malformed requests is just as cheap to refuse.
        enforce(check_limiter, request)
        require_key(x_tlakit_key)
        payload = await _payload_from(request)
        config = payload.config or ""
        try:
            validate(payload.spec, config, limits)
            module = module_name_of(payload.spec)
        except (RequestTooLarge, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not config:
            config = build_config(
                spec=payload.specification,
                constants=payload.constants,
                invariants=payload.invariants,
                properties=payload.properties,
            )

        if gate.locked():
            # Refund the slot: this caller is being turned away without a check
            # having run, and they already paid for one above.
            check_limiter.refund(key_of(request))
            raise HTTPException(status_code=503, detail="busy; retry shortly")

        async with gate:
            result = await asyncio.to_thread(
                runner.check,
                payload.spec,
                module,
                config,
                timeout=clamp_timeout(payload.timeout, limits),
                extra_opts=["-coverage", "1"] if payload.coverage else [],
                heap=limits.heap,
                graph=payload.graph,
                max_graph_nodes=limits.graph_nodes,
            )
        if result.outcome is Outcome.ERROR:
            # Responses omit raw on purpose, so without this an unexpected
            # failure is invisible from both sides. This is how
            # "Could not find or load main class tlc2.TLC" -- an unreadable jar
            # -- surfaced as nothing but "exited with code 1".
            log.error(
                "unexpected TLC failure: exit=%s argv=%s stderr=%s stdout=%s",
                result.raw.exit_code,
                result.raw.argv,
                result.raw.stderr[:2000],
                result.raw.stdout[:2000],
            )
        return as_json(result, limits)

    return app
