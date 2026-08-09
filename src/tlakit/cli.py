"""Run SANY and TLC as subprocesses.

Each invocation gets its own working directory. TLC writes
`<Module>_TTrace_<timestamp>.tla` and `.bin` files next to the spec, and
leftovers from a previous run of a different module make later runs fail with
exit 255 (verified 2026-08-07).
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import statewriter
from .graph import GraphBuilder, StateGraph, parse_ndjson
from .jar import find_community_jar, find_tools_jar
from .parse import parse_loop_start, parse_sany, parse_tlc
from .result import CheckResult, Diagnostic, Outcome, RawOutput, Severity, Stats
from .source import declared_variables, strip_comments
from .trace import TlaValueError, load_trace, parse_text_trace, parse_tla_value

TRACE_FILE = "trace.json"
#: Where the `IStateWriter` streams the state graph. The default path.
GRAPH_NDJSON_FILE = "graph.ndjson"
#: Where `-dump dot` writes it instead, on a machine with no JDK to compile
#: the writer with.
GRAPH_FILE = "graph.dot"

# A PlusCal source names its own translator with `--algorithm Name` or
# `--fair algorithm Name` inside a `(* ... *)` comment. A bare substring
# search over the whole file is not enough: a pure TLA+ spec can legitimately
# contain a *string literal* like "run with --algorithm x", which is not a
# PlusCal block and must not be misdetected as one (confirmed 2026-08-07: it
# was, and got a bogus PARSE_ERROR instead of being checked). So string
# literals are blanked out first -- a string can itself contain "(*"/"*)" --
# and only the bodies of actual `(* ... *)` block comments are searched,
# which is the one place +cal's own manual says an algorithm block may live.
_PLUSCAL_ALGORITHM = re.compile(r"--(?:fair\s+)?algorithm\b")
_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')
_BLOCK_COMMENT = re.compile(r"\(\*.*?\*\)", re.DOTALL)

# pcal.trans reports a failure as (verified 2026-08-07, pcal.trans 1.12):
#
#     Unrecoverable error:
#      -- Expected "if" but found "algorithm"
#         line 9, column 5.
#
# The location line is not always present (a missing algorithm block, for
# example, has none).
_PLUSCAL_ERROR = re.compile(
    r"Unrecoverable error:\s*\n\s*--\s*(?P<message>[^\n]+)"
    r"(?:\n\s*line (?P<line>\d+), column (?P<column>\d+)\.)?"
)


def _is_pluscal(source: str) -> bool:
    """Whether `source` embeds a genuine PlusCal algorithm block."""
    without_strings = _STRING_LITERAL.sub(lambda m: " " * len(m.group(0)), source)
    return any(
        _PLUSCAL_ALGORITHM.search(comment)
        for comment in _BLOCK_COMMENT.findall(without_strings)
    )


def _pluscal_diagnostics(stdout: str, exit_code: int | None) -> list[Diagnostic]:
    diags = [
        Diagnostic(
            Severity.ERROR,
            f"PlusCal translation failed: {m.group('message').strip()}",
            line=int(m.group("line")) if m.group("line") else None,
            column=int(m.group("column")) if m.group("column") else None,
        )
        for m in _PLUSCAL_ERROR.finditer(stdout)
    ]
    if diags:
        return diags
    return [
        Diagnostic(
            Severity.ERROR,
            f"PlusCal translation failed: pcal.trans exited with code "
            f"{exit_code}; see result.raw for output.",
        )
    ]


# --- %tla_eval / tlc2.REPL ---------------------------------------------------
#
# `java -cp tla2tools.jar tlc2.REPL "<expr>"` (a single argv, not stdin)
# evaluates one constant-level expression and exits 0 whether or not it
# succeeded (verified 2026-08-07, tlc2.REPL from TLC2 2026.07.31):
#
#     $ java -cp tla2tools.jar tlc2.REPL '1 + 1'
#     2
#     $ java -cp tla2tools.jar tlc2.REPL 'undefinedThing'
#     Error evaluating expression: 'undefinedThing'
#     [line 6, col 14 to line 6, col 27 of module tlarepl
#
#     Unknown operator: `undefinedThing'.]
#
# The REPL's own module-loading (`REPL.setSpecFile`) is a Java API, not a
# command-line option -- there is no argv that puts another module's
# operators in scope. So a prior module is spliced in as a `LET` ahead of
# the expression instead: still the real REPL doing the evaluating, just
# handed a bigger expression to evaluate. A LET cannot hold VARIABLE or
# CONSTANT declarations, which matches the REPL only ever being
# constant-level to begin with.
_REPL_ERROR_PREFIX = "Error evaluating expression:"

_MODULE_HEADER_LINE = re.compile(r"^-{4,}\s*MODULE\s+\w+\s*-{4,}\s*$", re.M)
_MODULE_FOOTER_LINE = re.compile(r"^={4,}\s*$", re.M)

# EXTENDS/VARIABLE/CONSTANT lists commonly wrap across lines:
#
#     EXTENDS Naturals,
#             Sequences
#
# `\s` matches a newline, so `_DECL_ITEM`'s separators reach across the wrap
# and the whole list is removed -- not just its first physical line, which
# used to leave a bare orphaned `Sequences` spliced into the LET body and a
# REPL syntax error on an otherwise-valid module. CONSTANT operators keep
# their arity, e.g. `Foo(_, _)`.
_IDENT = r"[A-Za-z_]\w*"
_DECL_ITEM = rf"{_IDENT}(?:\([^)]*\))?"
_EXTENDS_DECL = re.compile(
    rf"^[ \t]*EXTENDS\b\s*(?:{_DECL_ITEM}\s*,\s*)*{_DECL_ITEM}", re.M
)
_VAR_CONST_DECL = re.compile(
    rf"^[ \t]*(?:VARIABLES?|CONSTANTS?)\b\s*(?:{_DECL_ITEM}\s*,\s*)*{_DECL_ITEM}", re.M
)


def _operator_definitions(source: str) -> str:
    """The operator-definition text of a module, for splicing into a LET.

    Strips the module header/footer and the EXTENDS/VARIABLE/CONSTANT
    declarations a LET cannot contain -- a LET may only hold operator,
    function, and recursive definitions.
    """
    text = strip_comments(source)
    text = _MODULE_HEADER_LINE.sub("", text)
    text = _MODULE_FOOTER_LINE.sub("", text)
    text = _EXTENDS_DECL.sub("", text)
    text = _VAR_CONST_DECL.sub("", text)
    return text.strip()


@dataclass(frozen=True)
class EvalResult:
    """The result of `CliRunner.eval()`."""

    outcome: Outcome
    value: Any | None
    diagnostics: list[Diagnostic]
    raw: RawOutput

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]


def _repl_result(raw: RawOutput) -> EvalResult:
    stdout = raw.stdout.strip()
    if stdout.startswith(_REPL_ERROR_PREFIX):
        message = stdout[len(_REPL_ERROR_PREFIX):].strip()
        return EvalResult(
            Outcome.EVALUATION_ERROR, None, [Diagnostic(Severity.ERROR, message)], raw
        )
    if raw.exit_code not in (None, 0):
        return EvalResult(
            Outcome.ERROR,
            None,
            [
                Diagnostic(
                    Severity.ERROR,
                    f"tlc2.REPL exited with code {raw.exit_code}; see result.raw "
                    "for output.",
                )
            ],
            raw,
        )
    if not stdout:
        # A real evaluation always prints something -- even the empty
        # sequence prints "<<>>". Empty output is the REPL having gone
        # silent for some other reason, not a legitimate empty value; the
        # old behavior called this an ok, empty-string result.
        return EvalResult(
            Outcome.ERROR,
            None,
            [Diagnostic(Severity.ERROR, "tlc2.REPL produced no output; see result.raw.")],
            raw,
        )
    try:
        value = parse_tla_value(stdout)
    except TlaValueError:
        # Still a successful evaluation -- just a value shape this reader
        # does not understand yet. The caller gets the raw text rather than
        # nothing.
        value = stdout
    return EvalResult(Outcome.OK, value, [], raw)


class JavaNotFound(FileNotFoundError):
    """Raised when no `java` executable can be located."""


def java_executable() -> str:
    """Path to the `java` binary the TLA+ tools should run under.

    Honours TLAKIT_JAVA, then PATH. Raises rather than letting the failure
    surface later as a ClassNotFoundException.
    """
    java = os.environ.get("TLAKIT_JAVA") or shutil.which("java")
    if java is None:
        raise JavaNotFound(
            "No `java` executable found. The TLA+ tools need a JRE. Install one "
            "(for example `brew install temurin`) or set TLAKIT_JAVA to its path."
        )
    return java


#: Retained for internal call sites.
_java = java_executable


IS_WINDOWS = sys.platform == "win32"

#: Keyword arguments that put a child in its own killable group.
GROUP_KWARGS: dict[str, object] = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    if IS_WINDOWS
    else {"start_new_session": True}
)


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the child and everything it spawned.

    A plain `proc.kill()` reaches the launcher only. TLC forks nothing on
    POSIX, but on Windows the java launcher and the JVM are separate processes,
    so killing the launcher alone leaves the JVM holding its heap.
    """
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
            return
        except OSError:
            proc.kill()
            return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # Already gone, or not ours to signal.
        proc.kill()


#: Every tool process currently running, so an interrupt in one thread can tear
#: down children started by others. KeyboardInterrupt reaches only the main
#: thread, so a worker will never notice on its own.
_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()


def terminate_all() -> int:
    """Kill every running tool process. Returns how many were signalled."""
    with _LIVE_LOCK:
        procs = list(_LIVE)
    for proc in procs:
        _kill_group(proc)
    return len(procs)


class _GraphTail:
    """Build the state graph from the NDJSON file while TLC is still writing it.

    The writer flushes a record at a time, so the graph can be assembled as it
    is generated rather than after the run -- which is the point: a check killed
    on a budget still leaves every state it reached, and a graph too large to
    want is never held past `max_nodes`.

    Reading a file another process is appending to is the one part of this that
    can fail on its own (a Windows share mode, say). It is not worth failing a
    check over, so a reader that dies is recorded and `graph` falls back to
    reading the finished file -- the same records, just not early.
    """

    #: How long to wait before looking for more records.
    POLL_SECONDS = 0.05

    def __init__(self, path: Path, max_nodes: int | None = None) -> None:
        self._path = path
        self._max_nodes = max_nodes
        self._builder = GraphBuilder(max_nodes)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Why tailing stopped early, if it did.
        self.error: OSError | None = None

    def start(self) -> None:
        # The reader has to be able to open the file before the JVM creates
        # it. An empty file is also what the writer truncates on open, so
        # creating it here costs the reader nothing.
        self._path.touch()
        self._thread = threading.Thread(
            target=self._tail, name="tlakit-graph", daemon=True
        )
        self._thread.start()

    def _tail(self) -> None:
        try:
            with open(self._path, "rb") as handle:
                pending = b""
                while True:
                    chunk = handle.read()
                    if chunk:
                        # Bytes, not text: a read can land in the middle of a
                        # multi-byte character, and decoding per line rather
                        # than per read keeps that character whole.
                        pending += chunk
                        *lines, pending = pending.split(b"\n")
                        for line in lines:
                            self._builder.feed(line.decode("utf-8", "replace"))
                    elif self._stop.is_set():
                        return
                    else:
                        time.sleep(self.POLL_SECONDS)
        except OSError as exc:
            self.error = exc

    def stop(self) -> None:
        """Stop tailing, after one last read of whatever TLC left behind."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def graph(self) -> StateGraph:
        if self.error is None:
            return self._builder.graph()
        try:
            text = self._path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Nothing was readable at any point. An empty graph is the honest
            # answer, and `raw` records the argv that produced it.
            return self._builder.graph()
        return parse_ndjson(text, self._max_nodes)


def _terminate(proc: subprocess.Popen) -> tuple[str, str]:
    """Kill a tool process and everything it spawned, then drain its pipes."""
    _kill_group(proc)
    try:
        return proc.communicate(timeout=10)
    except (ValueError, subprocess.TimeoutExpired):
        # Pipes already closed by an earlier drain, or the drain itself hung.
        return "", ""


class CliRunner:
    """Invoke the TLA+ tools via `java -cp tla2tools.jar`."""

    #: SANY is in the jar, so a local runner can parse without checking.
    can_parse = True

    def __init__(
        self,
        tools_jar: Path | None = None,
        community_jar: Path | None | bool = None,
    ) -> None:
        """`community_jar=False` refuses CommunityModules entirely.

        Use that for untrusted input: it ships `IOUtils!IOExec`, which runs
        shell commands from inside a specification.
        """
        self.tools_jar = find_tools_jar(tools_jar)
        self.community_jar = find_community_jar(community_jar)

    def _classpath(self) -> str:
        parts = [str(self.tools_jar)]
        if self.community_jar is not None:
            parts.append(str(self.community_jar))
        return os.pathsep.join(parts)

    def _state_writer(self) -> Path | None:
        """The compiled IStateWriter, or None to fall back to `-dump dot`.

        A missing JDK is the ordinary reason for None, and not a failure: the
        graph still arrives, from TLC's own dump. Compiling happens once per
        jar and is cached, so this is a directory lookup on every run after the
        first.
        """
        try:
            return statewriter.class_directory(self.tools_jar)
        except statewriter.StateWriterUnavailable:
            return None

    def _run(
        self, argv: list[str], cwd: Path, timeout: float | None
    ) -> tuple[RawOutput, bool]:
        """Return (raw, timed_out). Never raises on tool failure."""
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Windows would otherwise decode with cp1252 and choke on the
            # Unicode operators TLA+ genuinely allows.
            encoding="utf-8",
            errors="replace",
            **GROUP_KWARGS,
        )
        with _LIVE_LOCK:
            _LIVE.add(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return RawOutput(argv, proc.returncode, stdout, stderr), False
        except subprocess.TimeoutExpired:
            stdout, stderr = _terminate(proc)
            return RawOutput(argv, None, stdout, stderr), True
        except BaseException:
            # Interrupting a notebook cell raises here. Without this the JVM
            # survives the cell, invisible and holding gigabytes.
            _terminate(proc)
            raise
        finally:
            with _LIVE_LOCK:
                _LIVE.discard(proc)

    def _translate_pluscal(
        self, work: Path, module: str, timeout: float | None
    ) -> tuple[RawOutput, bool]:
        """Run `pcal.trans` over `<module>.tla`, which it rewrites in place.

        `-nocfg` keeps the translator from writing a .cfg: callers manage
        their own, and `check()` writes one before this ever runs.

        `timeout` bounds the subprocess exactly like TLC's own: a spec merely
        *containing* `--algorithm` plus a pathological algorithm block would
        otherwise hang `pcal.trans` forever underneath `check()`'s untimed
        call, which `check()`'s own `timeout` argument does not reach on its
        own -- verified as a live way to wedge `serve/app.py`'s `/check`
        route, which bounds the TLC step but, before this, nothing before it.
        """
        argv = [
            _java(),
            "-cp",
            self._classpath(),
            "pcal.trans",
            "-nocfg",
            f"{module}.tla",
        ]
        return self._run(argv, work, timeout)

    def _prepare_source(
        self, work: Path, module: str, source: str, timeout: float | None = None
    ) -> tuple[str, CheckResult | None]:
        """Write `source` as `<module>.tla`, translating PlusCal first if present.

        pcal.trans ships inside tla2tools.jar, which every caller already
        requires -- no new dependency. Returns the text SANY/TLC will actually
        see (unchanged when there is no PlusCal block) and, on translator
        failure or timeout, a `CheckResult` diagnosing it that the caller
        should return immediately without ever invoking SANY or TLC.
        """
        (work / f"{module}.tla").write_text(source, encoding="utf-8")
        if not _is_pluscal(source):
            return source, None
        raw, timed_out = self._translate_pluscal(work, module, timeout)
        if timed_out:
            diagnostics = [
                Diagnostic(
                    Severity.ERROR,
                    f"PlusCal translation did not finish within {timeout}s.",
                )
            ]
            failure = CheckResult(
                Outcome.TIMEOUT, diagnostics, None, Stats(), raw, source=source,
            )
            return source, failure
        if raw.exit_code != 0:
            diagnostics = _pluscal_diagnostics(raw.stdout, raw.exit_code)
            failure = CheckResult(
                Outcome.PARSE_ERROR, diagnostics, None, Stats(), raw, source=source,
            )
            return source, failure
        translated = (work / f"{module}.tla").read_text(encoding="utf-8")
        return translated, None

    def parse(
        self,
        source: str,
        module: str,
        timeout: float | None = None,
        heap: str | None = None,
    ) -> CheckResult:
        """Syntax- and level-check a module with SANY.

        A PlusCal algorithm block is translated first -- SANY has no idea
        what one is and reports a syntax error that never mentions PlusCal.
        `timeout` bounds that translation step; SANY itself remains untimed,
        as before.

        `heap` caps the JVM, as it does for `check`. Left unset a JVM takes a
        quarter of physical RAM as its maximum, which is fine for a developer
        parsing their own module and not fine for a public endpoint parsing
        anyone's -- `serve` passes its own limit.
        """
        with tempfile.TemporaryDirectory(prefix="tlakit-") as tmp:
            work = Path(tmp)
            source, failure = self._prepare_source(work, module, source, timeout=timeout)
            if failure is not None:
                return failure
            argv = [
                _java(),
                *([f"-Xmx{heap}"] if heap else []),
                "-cp",
                self._classpath(),
                "tla2sany.SANY",
                f"{module}.tla",
            ]
            raw, _ = self._run(argv, work, timeout=None)

        diagnostics = parse_sany(raw.stdout)
        # SANY exits 0 even when it reports semantic errors, so diagnostics
        # take precedence over the exit code.
        if diagnostics:
            outcome = Outcome.PARSE_ERROR
        elif raw.exit_code == 0:
            outcome = Outcome.OK
        else:
            outcome = Outcome.ERROR
            diagnostics = [
                Diagnostic(
                    Severity.ERROR,
                    f"SANY exited with code {raw.exit_code}; see result.raw.",
                )
            ]
        return CheckResult(outcome, diagnostics, None, Stats(), raw, source=source)

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
        """Model-check a module with TLC.

        `extra_modules` are written alongside, so a generated companion module
        can EXTEND the spec. `collect` is a glob read back out of the working
        directory before it is destroyed -- TLC writes animation frames there.

        `heap` caps the JVM's maximum heap (for example "2G"). It matters most
        for sweeps: several unbounded TLC processes will ask for more memory
        than the machine has.

        `declared` overrides the VARIABLES read from `source`. A companion
        module inherits its variables through EXTENDS and declares none of its
        own, so reading them from its text would find nothing and every alias
        field would be mistaken for state.

        A PlusCal algorithm block in `source` is translated before TLC ever
        sees it; `declared` (when not given explicitly) is then read from the
        *translated* text, which is where PlusCal's own `VARIABLES` clause and
        `pc` bookkeeping variable actually live. `timeout` bounds that
        translation step too, not just the TLC step after it -- the two
        subprocesses share the one wall-clock budget the caller asked for.
        """
        frames: list[str] = []
        with tempfile.TemporaryDirectory(prefix="tlakit-") as tmp:
            work = Path(tmp)
            source, failure = self._prepare_source(work, module, source, timeout=timeout)
            if failure is not None:
                return failure
            (work / f"{module}.cfg").write_text(config, encoding="utf-8")
            for name, text in (extra_modules or {}).items():
                (work / f"{name}.tla").write_text(text, encoding="utf-8")
            # With a JDK present the graph is streamed from tlakit's own
            # IStateWriter, so it is built while TLC runs and survives a kill.
            # Without one, TLC's `-dump dot` produces the same graph after the
            # fact and `parse_dot` reads it.
            writer_dir = self._state_writer() if graph else None
            streaming = writer_dir is not None
            classpath = self._classpath()
            if streaming:
                classpath += os.pathsep + str(writer_dir)
            argv = [
                _java(),
                # JVM options must precede -cp.
                *([f"-Xmx{heap}"] if heap else []),
                *(
                    [f"-D{statewriter.OUT_PROPERTY}={GRAPH_NDJSON_FILE}"]
                    if streaming
                    else []
                ),
                "-cp",
                classpath,
                # The writer's own main: handleParameters, setStateWriter,
                # process -- TLC doing the checking either way.
                statewriter.MAIN_CLASS if streaming else "tlc2.TLC",
                "-config",
                f"{module}.cfg",
                "-dumpTrace",
                "json",
                TRACE_FILE,
                # actionlabels puts the action on each edge, which is the whole
                # point of showing the graph rather than a bag of states.
                *(
                    ["-dump", "dot,actionlabels", GRAPH_FILE]
                    if graph and not streaming
                    else []
                ),
                *(extra_opts or []),
                f"{module}.tla",
            ]
            tail = _GraphTail(work / GRAPH_NDJSON_FILE, max_graph_nodes) if streaming else None
            if tail is not None:
                tail.start()
            try:
                raw, timed_out = self._run(argv, work, timeout)
            finally:
                # Before the working directory goes away, and on the way out of
                # an interrupted cell too: the thread holds the file open.
                if tail is not None:
                    tail.stop()
            names = declared if declared is not None else declared_variables(source)
            trace = load_trace(work / TRACE_FILE, names)
            if trace is None:
                # The dump can be missing without TLC having failed outright
                # (path not writable, or -- per issue #4 -- a foreign log read
                # by a caller other than tlakit itself). TLC's own printed
                # trace, when present, is just as good a source.
                trace = parse_text_trace(raw.stdout, names)
            state_graph = None
            if tail is not None:
                state_graph = tail.graph()
            elif graph:
                dot = work / GRAPH_FILE
                if dot.is_file():
                    from .graph import parse_dot

                    state_graph = parse_dot(dot.read_text(encoding="utf-8"), max_graph_nodes)
            if collect:
                # Sort by the trailing step number, not lexically: frame 10
                # must not sort between 1 and 2.
                def step_of(path: Path) -> int:
                    digits = "".join(c for c in path.stem if c.isdigit())
                    return int(digits) if digits else 0

                frames = [
                    p.read_text(encoding="utf-8") for p in sorted(work.glob(collect), key=step_of)
                ]

        if trace is not None:
            # parse_loop_start only recognizes "Back to state N" (the lasso
            # case). A text-mode trace may already carry its own loop_start
            # from parse_text_trace, set when TLC instead prints "State N:
            # Stuttering" -- overwriting it here with None would silently
            # throw that away. The JSON path never sets loop_start on its
            # own, so this is unconditional for it as before.
            detected_loop = parse_loop_start(raw.stdout)
            if detected_loop is not None:
                trace = replace(trace, loop_start=detected_loop)

        if timed_out:
            _, diagnostics, stats = parse_tlc(raw.stdout, None)
            diagnostics = [
                Diagnostic(
                    Severity.ERROR,
                    f"TLC did not finish within {timeout}s. Statistics below "
                    "are partial.",
                )
            ] + diagnostics
            return CheckResult(
                Outcome.TIMEOUT, diagnostics, trace, stats, raw,
                source=source, frames=frames, graph=state_graph,
            )

        outcome, diagnostics, stats = parse_tlc(raw.stdout, raw.exit_code)
        return CheckResult(
            outcome, diagnostics, trace, stats, raw, source=source,
            frames=frames, graph=state_graph,
        )

    def eval(
        self,
        expr: str,
        modules: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> EvalResult:
        """Evaluate a constant TLA+ expression with `tlc2.REPL`.

        `modules` are prior module sources (name -> text, as a notebook
        session tracks them) `expr` may reference. Their operator definitions
        are spliced ahead of it in a `LET` -- the REPL still does the actual
        evaluating; this only assembles the expression handed to it. An
        evaluation error comes back as a diagnostic on the result rather than
        raising: `1 \\div 0` is a legitimate thing to ask the REPL and get a
        clear "no" from, not a reason to blow up the caller.
        """
        prelude = "\n".join(
            defs
            for defs in (_operator_definitions(text) for text in (modules or {}).values())
            if defs
        )
        full_expr = f"LET {prelude} IN\n{expr}" if prelude else expr
        argv = [_java(), "-cp", self._classpath(), "tlc2.REPL", full_expr]
        with tempfile.TemporaryDirectory(prefix="tlakit-") as tmp:
            raw, timed_out = self._run(argv, Path(tmp), timeout)
        if timed_out:
            return EvalResult(
                Outcome.TIMEOUT,
                None,
                [Diagnostic(Severity.ERROR, f"tlc2.REPL did not finish within {timeout}s.")],
                raw,
            )
        return _repl_result(raw)
