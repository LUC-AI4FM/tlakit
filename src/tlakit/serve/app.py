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
import os
import pathlib
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..api import build_config, module_name_of
from ..cli import CliRunner
from . import Limits, RequestTooLarge, as_json, clamp_timeout, startup_checks, validate


#: When set, every request must present this value in X-Tlakit-Key. The edge
#: Worker supplies it, so learning the tunnel hostname is not enough to reach
#: the service directly.
KEY_ENV = "TLAKIT_SERVE_KEY"
#: Preferred over KEY_ENV under launchd: a plist is world-readable, so the
#: secret lives in a mode-640 file that only the service's group can read.
KEY_FILE_ENV = "TLAKIT_SERVE_KEY_FILE"


def expected_key() -> Optional[str]:
    path = os.environ.get(KEY_FILE_ENV)
    if path:
        try:
            value = pathlib.Path(path).read_text().strip()
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


def create_app(runner: Optional[CliRunner] = None, limits: Optional[Limits] = None):
    from . import public_runner

    limits = limits or Limits()
    runner = runner or public_runner()
    startup_checks(runner)
    gate = asyncio.Semaphore(limits.concurrency)

    app = FastAPI(
        title="tla-runner",
        description=(
            "Model-check a TLA+ specification. Specs run without "
            "CommunityModules, so they have no I/O primitives."
        ),
        version="0.1.0",
    )

    @app.get("/health")
    async def health(
        x_tlakit_key: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        require_key(x_tlakit_key)
        return {
            "ok": True,
            "isolated": True,
            "community_modules": False,
            "key_required": bool(expected_key()),
            "limits": {
                "spec_bytes": limits.spec_bytes,
                "max_timeout": limits.max_timeout,
                "concurrency": limits.concurrency,
            },
        }

    @app.post("/check")
    async def check(
        payload: CheckRequest,
        x_tlakit_key: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
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
            )
        return as_json(result, limits)

    return app
