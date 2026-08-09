"""A `CliRunner`-shaped client for vscode-tlaplus's MCP server.

Worth having as an *optional* backend because the server does PlusCal
transpilation and the extension's own diagnostic post-processing, and because an
editor or agent is already talking to one. Not worth having as the only backend,
because its results are prose:

    Model check completed with exit code 12.

    Output:
    TLC2 Version 2026.07.31.184830 (rev: 30cc360)
    Running breadth-first search Model-Checking with fp 130 ...

So structure does not come from parsing that. The exit code comes off the first
line, the console output underneath goes to the same `parse_tlc` `CliRunner`
uses, and the trace comes from a real `-dumpTrace json` file, asked for through
the tool's `extraOpts`. Nothing here re-implements a TLC output parser.

**This runner writes specs inside the server's workspace, and has to.** MCP
tools take paths rather than module text, and the server refuses any path
outside the workspace it was started with:

    Access denied: Path /var/folders/.../DieHard.tla is outside the workspace
    (path traversal detected)

So `workspace=` is not a convenience -- it is the directory the server was given,
and a temp directory anywhere else is rejected. A server on another host cannot
work at all, which is a property of the protocol rather than something to work
around: `RemoteRunner` is the client for a service across a network.
"""
from __future__ import annotations

import json
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from ..parse import parse_loop_start, parse_tlc
from ..result import CheckResult, Diagnostic, Outcome, RawOutput, Severity, Stats
from ..source import declared_variables
from ..trace import load_trace, parse_text_trace

DEFAULT_URL = "http://127.0.0.1:8931/mcp"
DEFAULT_TIMEOUT = 600.0

#: The revision of the MCP spec the server speaks.
PROTOCOL_VERSION = "2025-06-18"

TRACE_FILE = "trace.json"
GRAPH_FILE = "graph.dot"

#: `Model check completed with exit code 12.`
_EXIT_CODE = re.compile(r"exit code (-?\d+)")
#: Everything after the `Output:` header is TLC's own console output.
_OUTPUT_SECTION = re.compile(r"^Output:\s*$", re.MULTILINE)
#: `TLC2 Version 2026.07.31.184830 (rev: 30cc360)`
_TLC_VERSION = re.compile(r"^TLC2 Version (\S+)", re.MULTILINE)
#: `Parsing of file /x/Broken.tla failed at line 4 with error: '...'`
_SANY_FAILURE = re.compile(
    r"^Parsing of file (?P<file>.+?) failed at line (?P<line>\d+) with error: "
    r"'(?P<message>.*?)'\s*$",
    re.MULTILINE | re.DOTALL,
)
_SANY_OK = "No errors found"


class McpUnavailable(RuntimeError):
    """No MCP server answered, or it answered with an error."""


class Unsupported(NotImplementedError):
    """The MCP server exposes no tool for this."""


def _http_transport(
    url: str, body: bytes, timeout: float
) -> tuple[int, str, str]:
    """POST one JSON-RPC message. Returns (status, content type, body)."""
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            # Streamable HTTP: the server may answer as JSON or as SSE, and
            # which one it picks is its choice, not ours.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read().decode(
            "utf-8", "replace"
        )


@dataclass
class McpRunner:
    """Drive SANY and TLC through the extension's MCP server."""

    url: str = DEFAULT_URL
    timeout: float = DEFAULT_TIMEOUT
    #: The workspace the server was started with. Specs are written into a fresh
    #: subdirectory of it per call, because the server refuses paths outside it.
    #: Defaults to the current directory, which is what `tlakit.mcp.serve` also
    #: defaults its workspace to.
    workspace: Path | None = None
    #: Injectable so tests never touch a socket.
    transport: Callable[[str, bytes, float], tuple[int, str, str]] | None = None

    #: The server has a SANY, so `%%tla` gets a real parse.
    can_parse = True
    #: Mirrors CliRunner's attributes so code that inspects a runner still
    #: works. The server resolves its own jar -- see `tlakit.mcp.serve`, which
    #: points it at the one tlakit pins.
    tools_jar: Any = None
    community_jar: Any = None

    _next_id: int = field(default=1, repr=False)
    _initialized: bool = field(default=False, repr=False)

    # --- the wire ---------------------------------------------------------

    def _rpc(self, method: str, params: dict[str, Any] | None = None,
             timeout: float | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {
            "jsonrpc": "2.0", "id": self._next_id, "method": method,
        }
        if params is not None:
            message["params"] = params
        request_id = self._next_id
        self._next_id += 1

        transport = self.transport or _http_transport
        body = json.dumps(message).encode("utf-8")
        try:
            status, content_type, text = transport(
                self.url, body, self.timeout if timeout is None else timeout
            )
        except OSError as exc:
            raise McpUnavailable(
                f"No MCP server answered at {self.url}: {exc}. Start one with "
                "`python -m tlakit.mcp.serve`."
            ) from exc

        if status >= 400:
            raise McpUnavailable(f"{self.url} answered {status}: {text.strip()[:400]}")

        payload = _decode(text, content_type, request_id)
        if payload is None:
            raise McpUnavailable(
                f"{self.url} sent no reply to {method} (id {request_id})."
            )
        if "error" in payload:
            error = payload["error"]
            raise McpUnavailable(
                f"{method} failed: {error.get('message', error)}"
            )
        return payload.get("result", {})

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tlakit", "version": _version()},
        })
        self._initialized = True

    def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float | None = None
    ) -> str:
        """Call one MCP tool and return its text content.

        A tool that reports failure raises, rather than returning its complaint
        as if it were output. A violated invariant is *not* one of those: that
        comes back as an ordinary result with a non-zero exit code in the text.
        """
        self._initialize()
        result = self._rpc(
            "tools/call", {"name": name, "arguments": arguments}, timeout=timeout
        )
        text = "\n".join(
            part.get("text", "")
            for part in result.get("content", [])
            if part.get("type", "text") == "text"
        )
        if result.get("isError"):
            raise McpUnavailable(_tool_error(name, text, self.workspace))
        return text

    def tools(self) -> list[str]:
        """The tool names the server offers."""
        self._initialize()
        return [tool["name"] for tool in self._rpc("tools/list", {}).get("tools", [])]

    def health(self) -> dict[str, Any]:
        """Whether a server is there, and what it offers."""
        return {"url": self.url, "tools": self.tools()}

    # --- the runner interface ---------------------------------------------

    def parse(
        self, source: str, module: str, timeout: float | None = None,
        heap: str | None = None,
    ) -> CheckResult:
        """Syntax- and level-check a module with the server's SANY.

        `heap` is accepted and ignored: the parse tool takes no Java options, and
        raising at a caller who passed a harmless default would be worse than
        not applying it.
        """
        with _work(self.workspace) as work:
            spec = work / f"{module}.tla"
            spec.write_text(source, encoding="utf-8")
            text = self.call_tool(
                "tlaplus_mcp_sany_parse", {"fileName": str(spec)}, timeout=timeout
            )
        raw = RawOutput(
            argv=["mcp", self.url, "tlaplus_mcp_sany_parse", f"{module}.tla"],
            exit_code=None, stdout=text, stderr="",
        )
        diagnostics = _sany_diagnostics(text)
        if diagnostics:
            return CheckResult(
                Outcome.PARSE_ERROR, diagnostics, None, Stats(), raw, source=source
            )
        if _SANY_OK in text:
            return CheckResult(Outcome.OK, [], None, Stats(), raw, source=source)
        # Neither shape. Saying so beats reporting OK for something unread.
        return CheckResult(
            Outcome.ERROR,
            [Diagnostic(
                Severity.ERROR,
                "The MCP server's parse result was neither a success nor a "
                "recognised failure; see result.raw.",
            )],
            None, Stats(), raw, source=source,
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
        """Model-check a module through the server's TLC.

        `timeout` bounds the *request*, not the run: there is no tool for
        cancelling a check, so a run that overruns keeps going on the server
        while this returns `Outcome.TIMEOUT`. A hard budget needs `CliRunner`,
        which owns the process it started.

        `graph=True` asks for `-dump dot` rather than tlakit's streaming state
        writer: the server invokes `tlc2.TLC` itself, so there is nowhere to put
        a custom `IStateWriter`. The graph therefore arrives after the run, as it
        used to everywhere.
        """
        frames: list[str] = []
        with _work(self.workspace) as work:
            (work / f"{module}.tla").write_text(source, encoding="utf-8")
            (work / f"{module}.cfg").write_text(config, encoding="utf-8")
            for name, text in (extra_modules or {}).items():
                (work / f"{name}.tla").write_text(text, encoding="utf-8")

            options = [
                "-dumpTrace", "json", str(work / TRACE_FILE),
                *(["-dump", "dot,actionlabels", str(work / GRAPH_FILE)] if graph else []),
                *(extra_opts or []),
            ]
            arguments: dict[str, Any] = {
                "fileName": str(work / f"{module}.tla"),
                "cfgFile": str(work / f"{module}.cfg"),
                "extraOpts": options,
            }
            if heap:
                arguments["extraJavaOpts"] = [f"-Xmx{heap}"]

            timed_out = False
            try:
                text = self.call_tool(
                    "tlaplus_mcp_tlc_check", arguments, timeout=timeout
                )
            except McpUnavailable as exc:
                if timeout is None or not _is_timeout(exc):
                    raise
                timed_out, text = True, ""

            names = declared if declared is not None else declared_variables(source)
            trace = load_trace(work / TRACE_FILE, names)
            stdout = _tlc_output(text)
            if trace is None:
                trace = parse_text_trace(stdout, names)
            state_graph = None
            if graph and (work / GRAPH_FILE).is_file():
                from ..graph import parse_dot

                state_graph = parse_dot(
                    (work / GRAPH_FILE).read_text(encoding="utf-8"), max_graph_nodes
                )
            if collect:
                def step_of(path: Path) -> int:
                    digits = "".join(c for c in path.stem if c.isdigit())
                    return int(digits) if digits else 0

                frames = [
                    p.read_text(encoding="utf-8")
                    for p in sorted(work.glob(collect), key=step_of)
                ]

        exit_code = _exit_code(text)
        raw = RawOutput(
            argv=["mcp", self.url, "tlaplus_mcp_tlc_check", *options],
            exit_code=exit_code, stdout=text, stderr="",
        )
        if trace is not None:
            detected_loop = parse_loop_start(stdout)
            if detected_loop is not None:
                trace = replace(trace, loop_start=detected_loop)

        if timed_out:
            _, diagnostics, stats = parse_tlc(stdout, None)
            return CheckResult(
                Outcome.TIMEOUT,
                [Diagnostic(
                    Severity.ERROR,
                    f"The MCP server did not answer within {timeout}s. Unlike a "
                    "local run this does not stop TLC -- the server has no tool "
                    "for that, so the check is probably still going.",
                )] + diagnostics,
                trace, stats, raw, source=source, frames=frames, graph=state_graph,
            )

        outcome, diagnostics, stats = parse_tlc(stdout, exit_code)
        return CheckResult(
            outcome, diagnostics, trace, stats, raw,
            source=source, frames=frames, graph=state_graph,
        )

    def eval(
        self, expr: str, modules: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Not available: the server exposes no `tlc2.REPL` tool."""
        raise Unsupported(
            "the MCP server has no REPL tool, so it cannot evaluate an "
            "expression. Its nine tools are SANY, TLC and the knowledge base. "
            "Use a local CliRunner for %tla_eval."
        )

    # --- version, for comparing against another runner --------------------

    def tlc_version(self, timeout: float | None = None) -> str | None:
        """The TLC the server actually runs, from its own output.

        There is no version tool, so this is read off a real check -- the only
        place the server prints it. Two runners that disagree about a spec are
        worth investigating only once they are known to be the same checker.
        """
        with _work(self.workspace) as work:
            module = "TlakitVersionProbe"
            (work / f"{module}.tla").write_text(
                f"---- MODULE {module} ----\n"
                "VARIABLE x\n"
                "Init == x = 0\n"
                "Next == x' = x\n"
                "Spec == Init /\\ [][Next]_x\n"
                "====\n",
                encoding="utf-8",
            )
            (work / f"{module}.cfg").write_text(
                "SPECIFICATION Spec\n", encoding="utf-8"
            )
            text = self.call_tool(
                "tlaplus_mcp_tlc_check",
                {
                    "fileName": str(work / f"{module}.tla"),
                    "cfgFile": str(work / f"{module}.cfg"),
                },
                timeout=timeout,
            )
        match = _TLC_VERSION.search(_tlc_output(text))
        return match.group(1) if match else None


# --- helpers ------------------------------------------------------------


def _decode(text: str, content_type: str, request_id: int) -> dict[str, Any] | None:
    """One JSON-RPC reply, out of JSON or out of an SSE stream.

    A streamable-HTTP server may interleave notifications with the reply, so the
    id is what identifies it rather than "the last message".
    """
    if "text/event-stream" not in content_type:
        try:
            return json.loads(text)
        except ValueError:
            return None
    found: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            message = json.loads(line[5:].strip())
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            found = message
    return found


def _tool_error(name: str, text: str, workspace: Path | None) -> str:
    """Explain a tool failure, and the one that is really a misconfiguration.

    "outside the workspace" means the runner and the server disagree about where
    the workspace is, which is worth saying outright -- the server's own message
    reads like a security incident rather than a mismatched argument.
    """
    detail = text.strip() or "(no message)"
    if "outside the workspace" in text or "path traversal" in text:
        where = workspace if workspace is not None else Path.cwd()
        return (
            f"{name} refused the path: the server only reads files inside the "
            f"workspace it was started with, and this runner wrote to {where}. "
            "Construct McpRunner(workspace=...) with the directory the server "
            f"was given.\n  {detail}"
        )
    return f"{name} failed: {detail}"


def _exit_code(text: str) -> int | None:
    match = _EXIT_CODE.search(text)
    return int(match.group(1)) if match else None


def _tlc_output(text: str) -> str:
    """TLC's own console output, without the prose wrapped around it."""
    match = _OUTPUT_SECTION.search(text)
    return text[match.end():].lstrip("\n") if match else text


def _sany_diagnostics(text: str) -> list[Diagnostic]:
    """Parse errors out of the server's prose, each reported once.

    The extension reports one bad module three times -- the syntax error, then
    "Fatal errors while parsing", then "Could not parse module" -- and a reader
    who has been told already does not need it twice (the same problem #74
    fixed in rendering).
    """
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[int | None, str]] = set()
    for match in _SANY_FAILURE.finditer(text):
        message = " ".join(match.group("message").split())
        line = int(match.group("line"))
        module = Path(match.group("file")).stem
        if _is_followup(message):
            continue
        key = (line, message)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(
            Diagnostic(Severity.ERROR, message, module=module, line=line)
        )
    return diagnostics


def _is_followup(message: str) -> bool:
    """Whether a message only restates a failure already reported."""
    lowered = message.lower()
    return lowered.startswith(("fatal errors while parsing", "in module"))


def _is_timeout(exc: Exception) -> bool:
    reason = str(exc).lower()
    return "timed out" in reason or "timeout" in reason


def _version() -> str:
    from .. import __version__

    return __version__


class _work:
    """A fresh directory inside the workspace to write one run's specs into.

    Inside the workspace because the server rejects anything else. Fresh per run
    because TLC leaves `<Module>_TTrace_*.tla` files behind, and leftovers from a
    previous run of a different module make later runs fail with exit 255.
    """

    def __init__(self, workspace: Path | None) -> None:
        self._base = Path(workspace) if workspace is not None else Path.cwd()
        self._tmp: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self._base.mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(
            prefix=".tlakit-mcp-", dir=str(self._base)
        )
        return Path(self._tmp.name)

    def __exit__(self, *_: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None
