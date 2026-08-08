"""Check specs over HTTP instead of by spawning a JVM.

This exists so tlakit can run where there is no Java: most importantly a
Pyodide kernel in a browser tab, which is what makes a zero-install TLA+
notebook possible. `RemoteRunner` presents the same `check()` surface as
`CliRunner`, so `Spec`, the magics, and the renderers are unchanged.

Two things about it are deliberate and easy to get wrong later.

**The transport is chosen by platform, not by preference.** Under Pyodide the
socket layer does not exist, so `urllib` cannot reach the network at all. What
does work is `XMLHttpRequest` in synchronous mode, which browsers permit inside
a web worker -- and a JupyterLite kernel *is* a web worker. That is the only
reason a blocking `check()` can be honest here rather than returning a promise
and forcing every caller to become async.

**Unsupported options raise instead of being dropped.** The service accepts a
spec, a config, and a couple of flags; it deliberately takes no TLC options,
because `-metadir` and friends would let a request write files of its choosing.
Silently ignoring `heap="2G"` would make a spec that merely needs memory look
like it deadlocked, so anything the service cannot honour is an error here.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .result import (
    Action,
    CheckResult,
    Diagnostic,
    Outcome,
    RawOutput,
    Severity,
    Stats,
    Trace,
)

#: The public runner. Model checks a spec and returns JSON; no I/O primitives
#: are available to the spec it runs.
DEFAULT_ENDPOINT = "https://tla-runner.ericspencer.us"

#: Longest a client waits on the whole HTTP exchange. The service caps a single
#: check at 30s, so this only needs headroom above that.
DEFAULT_TIMEOUT = 60.0


class RemoteError(RuntimeError):
    """The service could not be reached, or refused the request."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Unsupported(ValueError):
    """This option exists locally but the service does not accept it."""


#: Sent on every request, and not optional. Cloudflare's bot protection answers
#: 403 to urllib's default `Python-urllib/3.x` agent, which surfaces as an
#: opaque failure with nothing to debug -- measured against the live service on
#: 2026-08-08, where the only difference between 403 and 200 was this header.
USER_AGENT = "tlakit/0.1 (+https://github.com/LUC-AI4FM/tlakit)"


def _request_urllib(
    method: str, url: str, body: bytes | None, timeout: float
) -> tuple[int, str]:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    headers = {"user-agent": USER_AGENT}
    if body is not None:
        headers["content-type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed scheme
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as exc:  # a 4xx/5xx still carries a JSON body worth reading
        return exc.code, exc.read().decode("utf-8", "replace")
    except URLError as exc:
        raise RemoteError(f"cannot reach {url}: {exc.reason}") from exc


def _request_xhr(
    method: str, url: str, body: bytes | None, timeout: float
) -> tuple[int, str]:
    """Synchronous XMLHttpRequest, for Pyodide.

    Synchronous XHR is deprecated on the main thread and browsers warn about
    it, but it is fully supported inside a worker -- and it is the only way to
    keep a blocking Python API over a browser network stack. `timeout` cannot
    be set on a synchronous XHR (the browser throws InvalidAccessError), so the
    service's own 30s cap is what actually bounds a call here.

    The user agent is deliberately not set: browsers forbid scripts from
    overriding it, and a real browser's own agent is not what bot protection
    objects to anyway.
    """
    from js import XMLHttpRequest  # type: ignore[import-not-found]

    xhr = XMLHttpRequest.new()
    xhr.open(method, url, False)
    if body is not None:
        xhr.setRequestHeader("content-type", "application/json")
    try:
        xhr.send(body.decode("utf-8") if body is not None else None)
    except Exception as exc:  # noqa: BLE001 - JS errors are not Python exceptions
        raise RemoteError(f"cannot reach {url}: {exc}") from exc
    return int(xhr.status), str(xhr.responseText)


def default_transport() -> Callable[[str, str, bytes | None, float], tuple[int, str]]:
    """Pick a transport for this interpreter.

    Pyodide reports `sys.platform == "emscripten"`. Checking the platform
    rather than trying an import keeps the choice explicit: `js` is importable
    in some non-browser embeddings too, and guessing from that would pick the
    wrong transport there.
    """
    return _request_xhr if sys.platform == "emscripten" else _request_urllib


@dataclass
class RemoteRunner:
    """A `CliRunner`-shaped client for the public checking service."""

    #: The service exposes checking only, so callers that parse merely to give
    #: fast feedback -- `%%tla` above all -- can skip it instead of failing.
    can_parse = False

    endpoint: str = DEFAULT_ENDPOINT
    timeout: float = DEFAULT_TIMEOUT
    #: Injectable so tests never touch the network.
    transport: Callable[[str, str, bytes | None, float], tuple[int, str]] | None = None
    #: Mirrors CliRunner's attributes so code that inspects a runner still works.
    tools_jar: Any = None
    community_jar: Any = None
    _session: dict[str, Any] = field(default_factory=dict, repr=False)

    def _send(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        transport = self.transport or default_transport()
        url = f"{self.endpoint.rstrip('/')}{path}"
        request_body = json.dumps(payload).encode("utf-8") if payload is not None else None
        status, text = transport(
            "GET" if payload is None else "POST", url, request_body, self.timeout
        )
        try:
            body = json.loads(text) if text else {}
        except ValueError as exc:
            raise RemoteError(
                f"{url} returned {status} with a non-JSON body: {text[:200]!r}",
                status=status,
            ) from exc
        if status == 429:
            wait = body.get("retry_after_seconds") or body.get("detail") or "a moment"
            raise RemoteError(
                f"rate limited by {self.endpoint}; retry in {wait}", status=status
            )
        if status >= 400:
            detail = body.get("detail") or body.get("error") or text[:200]
            raise RemoteError(f"{url} returned {status}: {detail}", status=status)
        return body

    def parse(self, source: str, module: str) -> CheckResult:
        """Not available remotely.

        The service exposes checking only. Parsing alone would need a second
        endpoint, and SANY's value is mostly in fast local feedback -- which a
        network round trip does not provide anyway.
        """
        raise Unsupported(
            "the remote runner cannot parse; SANY is not exposed by the "
            "service. Check the spec instead -- TLC parses before it explores, "
            "so syntax errors surface from check()."
        )

    def check(
        self,
        source: str,
        module: str,
        config: str,
        timeout: float | None = None,
        extra_opts: list[str] | None = None,
        extra_modules: dict[str, str] | None = None,
        collect: str | None = None,
        declared: list[str] | None = None,
        heap: str | None = None,
        graph: bool = False,
        max_graph_nodes: int | None = None,
    ) -> CheckResult:
        options = list(extra_opts or [])
        coverage = "-coverage" in options
        if coverage:
            index = options.index("-coverage")
            # Drop the flag and its argument; the service takes a boolean.
            del options[index : index + 2]
        for name, value in (
            ("extra_modules", extra_modules),
            ("collect", collect),
            ("heap", heap),
        ):
            if value:
                raise Unsupported(
                    f"{name}= is not available on the remote runner. The "
                    "service runs one self-contained module with a fixed heap; "
                    "use a local CliRunner for this."
                )
        if options:
            raise Unsupported(
                f"the service accepts no TLC options, but got {options}. It "
                "refuses them on purpose: -metadir and -dump would let a "
                "request write files on the host."
            )
        if max_graph_nodes is not None:
            # The service enforces its own ceiling and reports truncation, so a
            # client-side number here would be a promise it cannot keep.
            raise Unsupported(
                "max_graph_nodes= is set by the service, not the client; it "
                "reports graph.truncated when it applies its own limit."
            )

        payload: dict[str, Any] = {"spec": source, "config": config}
        if timeout is not None:
            payload["timeout"] = timeout
        if coverage:
            payload["coverage"] = True
        if graph:
            payload["graph"] = True
        body = self._send("/check", payload)
        return from_json(body, source=source, declared=declared)

    def health(self) -> dict[str, Any]:
        """What the service will accept, straight from the service."""
        return self._send("/health", None)


def _diagnostic(item: dict[str, Any]) -> Diagnostic:
    return Diagnostic(
        severity=Severity(item.get("severity", "error")),
        message=item.get("message", ""),
        module=item.get("module"),
        line=item.get("line"),
        column=item.get("column"),
    )


def _trace(payload: dict[str, Any], declared: list[str] | None) -> Trace:
    return Trace(
        states=list(payload.get("states") or []),
        actions=[
            Action(
                name=item.get("name", ""),
                module=item.get("module"),
                begin_line=item.get("line"),
                begin_column=item.get("column"),
            )
            for item in payload.get("actions") or []
        ],
        # The service sends the variable names it observed; without them a trace
        # rebuilt from JSON could not tell a declared variable from an alias.
        declared=list(declared or payload.get("variables") or []),
        loop_start=payload.get("loop_start"),
    )


def _graph(payload: dict[str, Any]):
    from .graph import Edge, Node, StateGraph

    return StateGraph(
        nodes=[
            Node(
                id=str(item.get("id")),
                variables=dict(item.get("vars") or {}),
                initial=bool(item.get("initial")),
            )
            for item in payload.get("nodes") or []
        ],
        edges=[
            Edge(
                source=str(item.get("from")),
                target=str(item.get("to")),
                action=item.get("action", ""),
            )
            for item in payload.get("edges") or []
        ],
        truncated=bool(payload.get("truncated")),
    )


def from_json(
    body: dict[str, Any],
    *,
    source: str | None = None,
    declared: list[str] | None = None,
) -> CheckResult:
    """Rebuild a CheckResult from the service's JSON.

    The service omits raw tool output on purpose -- it carries absolute paths
    and the java invocation -- so `raw` here records the exchange rather than
    pretending to hold TLC's stdout. `raw.exit_code` is None because there was
    no local process to exit.
    """
    stats_json = body.get("stats") or {}
    trace_json = body.get("trace")
    diagnostics = [_diagnostic(item) for item in body.get("diagnostics") or []]
    if trace_json and trace_json.get("truncated"):
        diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING,
                message=(
                    "the service truncated this counterexample; the states "
                    "shown are a prefix of the real trace"
                ),
            )
        )
    return CheckResult(
        outcome=Outcome(body.get("outcome", Outcome.ERROR.value)),
        diagnostics=diagnostics,
        trace=_trace(trace_json, declared) if trace_json else None,
        stats=Stats(
            generated=stats_json.get("generated"),
            distinct=stats_json.get("distinct"),
            depth=stats_json.get("depth"),
            duration_ms=stats_json.get("duration_ms"),
        ),
        raw=RawOutput(
            argv=[],
            exit_code=None,
            stdout=(
                "checked by a remote service; it omits raw tool output because "
                "that output carries host filesystem paths"
            ),
            stderr="",
        ),
        source=source,
        graph=_graph(body["graph"]) if body.get("graph") else None,
    )
