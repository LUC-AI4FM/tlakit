"""A constrained HTTP service for checking untrusted TLA+ specifications.

The threat model is the point of this module. A visitor may submit any spec, so
the service must expose exactly one capability — model check this text — and
nothing else.

What makes that possible: with `tla2tools.jar` alone in its directory, TLA+ has
no I/O primitives. The standard modules are pure and `TLC!PrintT` only writes
stdout. CommunityModules would break that, because `IOUtils!IOExec` runs shell
commands from inside a spec, and TLC loads jars *adjacent* to tla2tools.jar even
when they are off the classpath. `startup_checks` refuses to serve until the jar
is isolated.

Deliberately absent:

- no endpoint that reads or writes a path
- no way for a client to pass TLC options (`-dump`, `-metadir` and friends
  would let a request write files of its choosing)
- no raw tool output in responses, which would leak filesystem paths
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..cli import CliRunner
from ..jar import assert_isolated, find_tools_jar
from ..result import CheckResult

#: A spec longer than this is refused before Java is started.
MAX_SPEC_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 8 * 1024
#: Hard ceiling on how long one request may occupy a JVM.
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 15.0
#: Per-request JVM heap. Several concurrent unbounded TLCs would take the box.
HEAP = "512M"
#: How many checks may run at once, regardless of how many requests arrive.
MAX_CONCURRENCY = 2
#: Longest counterexample returned. A trace can otherwise be enormous.
MAX_TRACE_STATES = 200


@dataclass(frozen=True)
class Limits:
    spec_bytes: int = MAX_SPEC_BYTES
    config_bytes: int = MAX_CONFIG_BYTES
    max_timeout: float = MAX_TIMEOUT_SECONDS
    default_timeout: float = DEFAULT_TIMEOUT_SECONDS
    heap: str = HEAP
    concurrency: int = MAX_CONCURRENCY
    trace_states: int = MAX_TRACE_STATES


def isolated_jar_dir(destination: Path | None = None) -> Path:
    """Copy tla2tools.jar somewhere it is the only jar, and return that path.

    tlakit's own cache already separates the jars into version directories, so
    this is usually a no-op check rather than a copy — but a hand-configured
    TLAKIT_TLA2TOOLS may well point at a directory holding both.
    """
    source = find_tools_jar()
    try:
        assert_isolated(source)
        return source
    except Exception:
        pass
    destination = destination or (source.parent / "isolated")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    if not target.exists():
        shutil.copy(source, target)
    assert_isolated(target)
    return target


def public_runner() -> CliRunner:
    """A runner that cannot reach CommunityModules."""
    return CliRunner(tools_jar=isolated_jar_dir(), community_jar=False)


def startup_checks(runner: CliRunner) -> None:
    """Refuse to serve unless the isolation property holds."""
    assert_isolated(runner.tools_jar)
    if runner.community_jar is not None:
        raise RuntimeError(
            "CommunityModules is on the classpath. It provides IOUtils!IOExec, "
            "which runs shell commands from inside a specification. Refusing to "
            "serve untrusted specs."
        )


def as_json(result: CheckResult, limits: Limits) -> dict[str, Any]:
    """Serialize a result for a public client.

    `raw` is omitted on purpose: it carries absolute temp paths, the java
    invocation, and the jar location.
    """
    trace: dict[str, Any] | None = None
    if result.trace is not None:
        states = result.trace.states[: limits.trace_states]
        trace = {
            "states": states,
            "truncated": len(result.trace.states) > len(states),
            "actions": [
                {
                    "name": a.name,
                    "module": a.module,
                    "line": a.begin_line,
                    "column": a.begin_column,
                }
                for a in result.trace.actions[: max(len(states) - 1, 0)]
            ],
            "loop_start": result.trace.loop_start,
            "variables": result.trace.variables,
        }
    return {
        "outcome": result.outcome.value,
        "ok": result.ok,
        "diagnostics": [
            {
                "severity": d.severity.value,
                "message": d.message,
                "module": d.module,
                "line": d.line,
                "column": d.column,
            }
            for d in result.diagnostics
        ],
        "trace": trace,
        "stats": {
            "generated": result.stats.generated,
            "distinct": result.stats.distinct,
            "depth": result.stats.depth,
            "duration_ms": result.stats.duration_ms,
        },
    }


class RequestTooLarge(ValueError):
    """The submitted spec or config exceeds the configured limit."""


def validate(spec: str, config: str, limits: Limits) -> None:
    if not spec.strip():
        raise RequestTooLarge("spec is empty")
    if len(spec.encode()) > limits.spec_bytes:
        raise RequestTooLarge(
            f"spec exceeds {limits.spec_bytes} bytes"
        )
    if len(config.encode()) > limits.config_bytes:
        raise RequestTooLarge(f"config exceeds {limits.config_bytes} bytes")


def clamp_timeout(requested: float | None, limits: Limits) -> float:
    if requested is None:
        return limits.default_timeout
    return max(0.5, min(float(requested), limits.max_timeout))


def create_app(runner=None, limits: Limits | None = None):
    """Build the FastAPI application.

    Defined in `tlakit.serve.app`, which deliberately avoids
    `from __future__ import annotations` -- FastAPI resolves endpoint
    annotations at import time and cannot see a stringified name that refers to
    a function-local model. The symptom is a silent degradation to query
    parameters and a 422 on every request.
    """
    from .app import create_app as _create_app

    return _create_app(runner=runner, limits=limits)
