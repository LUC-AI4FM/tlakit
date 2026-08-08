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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..api import build_config, module_name_of
from ..result import Outcome
from ..cli import CliRunner
from . import Limits, RequestTooLarge, as_json, clamp_timeout, startup_checks, validate
from .limiter import CHEAP_RULES, CHECK_RULES, RateLimiter, client_key


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


def create_app(runner: Optional[CliRunner] = None, limits: Optional[Limits] = None):
    from . import public_runner

    limits = limits or Limits()
    runner = runner or public_runner()
    startup_checks(runner)
    gate = asyncio.Semaphore(limits.concurrency)
    check_limiter = RateLimiter(rules=CHECK_RULES)
    cheap_limiter = RateLimiter(rules=CHEAP_RULES)

    def enforce(limiter: RateLimiter, request: Request) -> None:
        key = client_key(
            request.headers.get("cf-connecting-ip"),
            request.client.host if request.client else None,
        )
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
                "spec_bytes": limits.spec_bytes,
                "max_timeout": limits.max_timeout,
                "concurrency": limits.concurrency,
            },
        }

    @app.post("/check")
    async def check(
        payload: CheckRequest,
        request: Request,
        x_tlakit_key: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        # Rate limit before doing any work, including before validation, so a
        # flood of malformed requests is just as cheap to refuse.
        enforce(check_limiter, request)
        require_key(x_tlakit_key)
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
