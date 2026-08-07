"""Run SANY and TLC as subprocesses.

Each invocation gets its own working directory. TLC writes
`<Module>_TTrace_<timestamp>.tla` and `.bin` files next to the spec, and
leftovers from a previous run of a different module make later runs fail with
exit 255 (verified 2026-08-07).
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from .jar import find_community_jar, find_tools_jar
from .parse import parse_loop_start, parse_sany, parse_tlc
from .result import CheckResult, Diagnostic, Outcome, RawOutput, Severity, Stats
from .source import declared_variables
from .trace import load_trace

TRACE_FILE = "trace.json"


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

    def parse(self, source: str, module: str) -> CheckResult:
        """Syntax- and level-check a module with SANY."""
        with tempfile.TemporaryDirectory(prefix="tlakit-") as tmp:
            work = Path(tmp)
            (work / f"{module}.tla").write_text(source)
            argv = [
                _java(),
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
        """
        frames: list[str] = []
        with tempfile.TemporaryDirectory(prefix="tlakit-") as tmp:
            work = Path(tmp)
            (work / f"{module}.tla").write_text(source)
            (work / f"{module}.cfg").write_text(config)
            for name, text in (extra_modules or {}).items():
                (work / f"{name}.tla").write_text(text)
            argv = [
                _java(),
                # JVM options must precede -cp.
                *([f"-Xmx{heap}"] if heap else []),
                "-cp",
                self._classpath(),
                "tlc2.TLC",
                "-config",
                f"{module}.cfg",
                "-dumpTrace",
                "json",
                TRACE_FILE,
                *(extra_opts or []),
                f"{module}.tla",
            ]
            raw, timed_out = self._run(argv, work, timeout)
            names = declared if declared is not None else declared_variables(source)
            trace = load_trace(work / TRACE_FILE, names)
            if collect:
                # Sort by the trailing step number, not lexically: frame 10
                # must not sort between 1 and 2.
                def step_of(path: Path) -> int:
                    digits = "".join(c for c in path.stem if c.isdigit())
                    return int(digits) if digits else 0

                frames = [
                    p.read_text() for p in sorted(work.glob(collect), key=step_of)
                ]

        if trace is not None:
            trace = replace(trace, loop_start=parse_loop_start(raw.stdout))

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
                source=source, frames=frames,
            )

        outcome, diagnostics, stats = parse_tlc(raw.stdout, raw.exit_code)
        return CheckResult(
            outcome, diagnostics, trace, stats, raw, source=source, frames=frames
        )
