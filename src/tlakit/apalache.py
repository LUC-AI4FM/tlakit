"""Check a spec with Apalache, the symbolic model checker.

Issue #68. Apalache matters because it is good at what TLC is worst at:
constant domains large enough that explicit enumeration never finishes. It
discharges the same question to an SMT solver instead of walking states.

## Why this is a runner and not `Spec.check(engine="apalache")`

The issue asked for that decision to be settled before either backend was
written, and this is the answer: **a separate runner, because the two tools do
not answer the same question.**

TLC's `Outcome.OK` means the reachable state space was exhausted. Apalache's
success means no counterexample exists *up to the length it was given* -- one
may sit at `length + 1`. An `engine=` switch invites exactly one mistake, and
it is the worst one available: the same call, the same `CheckResult`, the same
`result.ok`, silently now meaning something weaker. So Apalache's success is
`Outcome.BOUNDED_OK`, and `result.ok` is False for it. A caller who wants to
treat a bounded result as good enough must say so.

Everything else *is* shared on purpose. `ApalacheRunner.check` returns the same
`CheckResult`, and its counterexample is a plain `Trace`, so `delta`,
`to_dataframe`, `TraceView`, and the notebook rendering all work unchanged.

## Reading the counterexample

Apalache writes several forms; this reads `violation1.itf.json`. ITF is the
one with a specification behind it (ADR-015), so it is the one that will still
parse in a year. Values arrive tagged -- `{"#bigint": "3"}`, `{"#set": [...]}`,
`{"#tup": [...]}`, `{"#map": [[k, v], ...]}` -- and are decoded to the same
Python shapes `-dumpTrace json` produces for TLC, which is what lets one
`Trace` serve both.

ITF records no action names, so every `Action` here is `UNKNOWN_ACTION`. That
is a real difference from TLC and is left visible rather than papered over with
a guess from the state delta.

## Types

Apalache requires `\\* @type:` annotations. A spec that checks under TLC may
simply not run here, and that is not a tlakit bug -- so the diagnostic for it
says so in those words rather than surfacing a raw SMT complaint.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

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
from .trace import UNKNOWN_ACTION

#: Overrides the search for an `apalache-mc` launcher.
ENV_APALACHE = "TLAKIT_APALACHE"

#: How many steps to explore when the caller names no bound. Apalache's own
#: default is 10; it is repeated rather than relied upon so that the number in
#: `BoundedResult.length` is always one tlakit chose and can report.
DEFAULT_LENGTH = 10

#: Apalache's exit codes (`EXITCODE:` on the last line of its output).
_EXIT_OK = 0
_EXIT_ERROR = 12

_TYPE_ERROR = re.compile(r"\b(type input error|TypingInputError|Typing input error)\b", re.I)
_PARSE_ERROR = re.compile(r"\b(parsing error|Syntax error|SanyException|parser error)\b", re.I)
_DEADLOCK = re.compile(r"\bdeadlock\b", re.I)
#: `Found 3 error(s)` / `The outcome is: Error`
_OUTCOME_LINE = re.compile(r"The outcome is:\s*(?P<outcome>\w+)")
#: Apalache prefixes its log lines with a level and timestamp; strip that so a
#: diagnostic is the message rather than the formatting.
_LOG_NOISE = re.compile(r"\s+[IEW]@\d{2}:\d{2}:\d{2}\.\d+\s*$")


class ApalacheNotFound(FileNotFoundError):
    """No `apalache-mc` launcher could be located."""


def find_apalache(explicit: str | Path | None = None) -> Path:
    """Locate the `apalache-mc` launcher.

    Explicit argument, then `TLAKIT_APALACHE`, then `PATH`. Unlike the TLA+
    jars there is no download step: Apalache ships as a 180 MB tarball with a
    launcher script, so `tlakit.install`'s pinned-jar machinery does not fit it
    and pretending otherwise would hide a large download inside an import.
    """
    for candidate in (explicit, os.environ.get(ENV_APALACHE)):
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_file():
                return path
            raise ApalacheNotFound(f"{candidate} is not a file")
    found = shutil.which("apalache-mc")
    if found:
        return Path(found)
    raise ApalacheNotFound(
        "apalache-mc is not on PATH. Download it from "
        "https://github.com/apalache-mc/apalache/releases and either add its "
        f"bin/ to PATH or set {ENV_APALACHE} to the launcher."
    )


# --- ITF ---------------------------------------------------------------------


def itf_value(value: Any) -> Any:
    """Decode one ITF-tagged value into the shape TLC's JSON dump would give.

    ADR-015 tags anything that plain JSON cannot carry faithfully. Sets and
    tuples both become lists, matching `tlakit.trace`, so a counterexample from
    either checker indexes the same way.
    """
    if isinstance(value, dict):
        if "#bigint" in value:
            return int(value["#bigint"])
        if "#set" in value:
            return [itf_value(v) for v in value["#set"]]
        if "#tup" in value:
            return [itf_value(v) for v in value["#tup"]]
        if "#map" in value:
            # A function. Keys can be arbitrary values, so this is a list of
            # pairs in ITF; it becomes a dict only when every key is hashable
            # after decoding, and stays a list of pairs otherwise rather than
            # losing entries.
            pairs = [(itf_value(k), itf_value(v)) for k, v in value["#map"]]
            try:
                return dict(pairs)
            except TypeError:
                return pairs
        if "#unserializable" in value:
            return str(value["#unserializable"])
        return {k: itf_value(v) for k, v in value.items() if k != "#meta"}
    if isinstance(value, list):
        return [itf_value(v) for v in value]
    return value


def trace_from_itf(document: dict[str, Any]) -> Trace | None:
    """Build a `Trace` from a parsed `*.itf.json` counterexample."""
    raw_states = document.get("states") or []
    if not raw_states:
        return None
    declared = list(document.get("vars") or [])
    states = [
        {k: itf_value(v) for k, v in state.items() if k != "#meta"}
        for state in raw_states
    ]
    actions = [Action(name=UNKNOWN_ACTION) for _ in states[1:]]
    return Trace(states=states, actions=actions, declared=declared)


def _newest_itf(out_dir: Path) -> Path | None:
    """The counterexample Apalache just wrote.

    It nests output under `<out-dir>/<module>.tla/<timestamp>/`, so the file is
    found by search rather than by a path this module predicts -- a layout
    guess would break silently on the next Apalache release.
    """
    candidates = sorted(
        out_dir.rglob("violation*.itf.json"), key=lambda p: p.stat().st_mtime
    )
    return candidates[-1] if candidates else None


# --- diagnostics -------------------------------------------------------------


def _clean(line: str) -> str:
    return _LOG_NOISE.sub("", line).strip()


def _diagnostics(stdout: str) -> list[Diagnostic]:
    """The lines worth surfacing, out of Apalache's very chatty log."""
    messages: list[Diagnostic] = []
    for line in stdout.splitlines():
        text = _clean(line)
        if not text:
            continue
        if _TYPE_ERROR.search(text):
            messages.append(
                Diagnostic(
                    Severity.ERROR,
                    f"{text}  (Apalache requires `\\* @type:` annotations; a spec "
                    "that checks under TLC may need them added before it runs here)",
                )
            )
        elif _PARSE_ERROR.search(text) or text.startswith("Error by TLA+ parser"):
            messages.append(Diagnostic(Severity.ERROR, text))
        elif text.startswith(("Input error", "Configuration error", "Unexpected error")):
            messages.append(Diagnostic(Severity.ERROR, text))
    return messages


def _outcome(
    exit_code: int | None, stdout: str, trace: Trace | None
) -> tuple[Outcome, list[Diagnostic]]:
    diagnostics = _diagnostics(stdout)
    if any(_TYPE_ERROR.search(d.message) or _PARSE_ERROR.search(d.message) for d in diagnostics):
        return Outcome.PARSE_ERROR, diagnostics

    if exit_code == _EXIT_OK:
        return Outcome.BOUNDED_OK, diagnostics

    if exit_code == _EXIT_ERROR:
        if trace is not None:
            return Outcome.INVARIANT_VIOLATION, diagnostics or [
                Diagnostic(Severity.ERROR, "Invariant violated.")
            ]
        if _DEADLOCK.search(stdout):
            return Outcome.DEADLOCK, diagnostics or [
                Diagnostic(Severity.ERROR, "Deadlock reached.")
            ]
        return Outcome.ERROR, diagnostics or [
            Diagnostic(Severity.ERROR, "Apalache reported an error; see result.raw.")
        ]

    match = _OUTCOME_LINE.search(stdout)
    detail = match.group("outcome") if match else f"exit code {exit_code}"
    return Outcome.ERROR, diagnostics or [
        Diagnostic(Severity.ERROR, f"Apalache failed: {detail}; see result.raw.")
    ]


# --- the runner --------------------------------------------------------------


class ApalacheRunner:
    """Model-check with Apalache, presenting `CliRunner`'s result shape.

    Deliberately *not* interchangeable with `CliRunner` for `parse` or `eval`:
    Apalache has no SANY-equivalent to expose and no REPL, and a stub that
    quietly did something else would be worse than its absence.
    """

    def __init__(self, apalache: str | Path | None = None):
        self.apalache = find_apalache(apalache)

    def check(
        self,
        source: str,
        module: str,
        config: str | None = None,
        *,
        init: str = "Init",
        next_: str = "Next",
        invariants: list[str] | None = None,
        length: int = DEFAULT_LENGTH,
        timeout: float | None = None,
        extra_opts: list[str] | None = None,
    ) -> CheckResult:
        """Check `source` up to `length` steps.

        `config` is accepted and ignored with a warning rather than silently:
        Apalache does not read a TLC `.cfg`, it takes `--init`/`--next`/`--inv`
        on the command line. Dropping a config the caller passed would check
        something other than what they asked for.
        """
        warnings: list[Diagnostic] = []
        if config:
            warnings.append(
                Diagnostic(
                    Severity.WARNING,
                    "Apalache does not read a TLC .cfg; this config was ignored. "
                    "Pass init=/next_=/invariants= instead.",
                )
            )

        with tempfile.TemporaryDirectory(prefix="tlakit-apalache-") as tmp:
            work = Path(tmp)
            (work / f"{module}.tla").write_text(source, encoding="utf-8")
            out_dir = work / "out"

            argv = [
                str(self.apalache),
                "check",
                f"--init={init}",
                f"--next={next_}",
                f"--length={length}",
                f"--out-dir={out_dir}",
                *(f"--inv={name}" for name in (invariants or [])),
                *(extra_opts or []),
                f"{module}.tla",
            ]

            try:
                completed = subprocess.run(
                    argv,
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
                timed_out = False
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                code, timed_out = None, True

            raw = RawOutput(argv=argv, exit_code=code, stdout=stdout, stderr=stderr)

            if timed_out:
                return CheckResult(
                    Outcome.TIMEOUT,
                    [*warnings, Diagnostic(Severity.ERROR, f"Apalache did not finish within {timeout}s.")],
                    None,
                    Stats(depth=length),
                    raw,
                    source=source,
                )

            trace = None
            found = _newest_itf(out_dir) if out_dir.exists() else None
            if found is not None:
                trace = trace_from_itf(json.loads(found.read_text(encoding="utf-8")))

            outcome, diagnostics = _outcome(code, stdout, trace)
            stats = Stats(depth=len(trace) - 1 if trace else length)
            return CheckResult(
                outcome,
                [*warnings, *diagnostics],
                trace,
                stats,
                raw,
                source=source,
            )
