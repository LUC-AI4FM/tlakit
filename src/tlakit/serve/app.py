"""FastAPI wiring for the runner.

Deliberately without `from __future__ import annotations`: FastAPI resolves
endpoint annotations at import time, and a stringified annotation naming a model
that lives in a function's local scope cannot be resolved — the parameter then
silently degrades to a query parameter and every request 422s. Models are
module-level here for the same reason.

All the decisions live in `tlakit.serve`; this file only routes.
"""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..api import build_config, module_name_of
from ..cli import CliRunner
from . import Limits, RequestTooLarge, as_json, clamp_timeout, startup_checks, validate


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
    async def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "isolated": True,
            "community_modules": False,
            "limits": {
                "spec_bytes": limits.spec_bytes,
                "max_timeout": limits.max_timeout,
                "concurrency": limits.concurrency,
            },
        }

    @app.post("/check")
    async def check(payload: CheckRequest) -> Dict[str, Any]:
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
