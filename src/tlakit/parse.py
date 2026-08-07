"""Parse the *text* TLC and SANY print.

Trace structure never comes from here — that is `-dumpTrace json`, handled in
`tlakit.trace`. This module extracts only diagnostics, statistics, and the
overall outcome.

Formats below are matched against captures in `tests/fixtures/`, taken from
TLC 2026.03.19 / SANY 2.2 on 2026-08-07.
"""
from __future__ import annotations

import re

from .result import Diagnostic, Outcome, Severity, Stats

# ``Encountered "====" at line 4, column 1 and token "="``
_SANY_PARSE = re.compile(r"^(Encountered .*? at line (\d+), column (\d+).*)$", re.M)

# SANY prints the location, a blank line, then the message:
#
#     line 3, col 13 to line 3, col 23 of module Sem
#
#     Unknown operator: `undefinedOp'.
_SANY_SEMANTIC = re.compile(
    r"^line (\d+), col (\d+) to line \d+, col \d+ of module (\w+)\s*\n\s*\n(.+)$",
    re.M,
)

# ``4 states generated, 3 distinct states found, 0 states left on queue.``
_TLC_STATS = re.compile(
    r"(\d+) states generated, (\d+) distinct states found, "
    r"(\d+) states left on queue"
)
_TLC_DEPTH = re.compile(r"The depth of the complete state graph search is (\d+)")
_TLC_MS = re.compile(r"Finished in (\d+)ms")
_TLC_SECONDS = re.compile(r"Finished in (\d+)s ")
_TLC_INVARIANT = re.compile(r"^Error: (Invariant .* is violated\.)$", re.M)
_TLC_DEADLOCK = re.compile(r"^Error: (Deadlock reached\.)$", re.M)
_TLC_TEMPORAL = re.compile(r"^Error: (Temporal properties were violated\.)$", re.M)
# ``Back to state 1: <Next line 5, col 9 to line 5, col 24 of module L>``
# TLC reports the cycle only in its text output; the JSON dump has no marker.
_TLC_BACK_TO_STATE = re.compile(r"^Back to state (\d+)\b", re.M)

# Verified 2026-08-07. Note that SANY exits 0 even when it reports semantic
# errors, so exit codes are never the sole signal — see `parse_tlc` and
# `CliRunner.parse`.
_EXIT_OUTCOME = {
    0: Outcome.OK,
    11: Outcome.DEADLOCK,
    12: Outcome.INVARIANT_VIOLATION,
    13: Outcome.TEMPORAL_VIOLATION,
}


def parse_sany(stdout: str) -> list[Diagnostic]:
    """Extract SANY syntax and semantic errors."""
    diags: list[Diagnostic] = []
    for message, line, column in _SANY_PARSE.findall(stdout):
        diags.append(
            Diagnostic(
                Severity.ERROR, message.strip(), line=int(line), column=int(column)
            )
        )
    for line, column, module, message in _SANY_SEMANTIC.findall(stdout):
        diags.append(
            Diagnostic(
                Severity.ERROR,
                message.strip(),
                module=module,
                line=int(line),
                column=int(column),
            )
        )
    return diags


def _parse_stats(stdout: str) -> Stats:
    generated = distinct = queue_left = depth = duration = None
    if m := _TLC_STATS.search(stdout):
        generated, distinct, queue_left = (int(g) for g in m.groups())
    if m := _TLC_DEPTH.search(stdout):
        depth = int(m.group(1))
    if m := _TLC_MS.search(stdout):
        duration = int(m.group(1))
    elif m := _TLC_SECONDS.search(stdout):
        duration = int(m.group(1)) * 1000
    return Stats(
        generated=generated,
        distinct=distinct,
        queue_left=queue_left,
        depth=depth,
        duration_ms=duration,
    )


def parse_tlc(
    stdout: str, exit_code: int | None
) -> tuple[Outcome, list[Diagnostic], Stats]:
    """Return the outcome, diagnostics, and statistics of a TLC run."""
    diags: list[Diagnostic] = []
    outcome: Outcome | None = None

    for pattern, kind in (
        (_TLC_INVARIANT, Outcome.INVARIANT_VIOLATION),
        (_TLC_DEADLOCK, Outcome.DEADLOCK),
        (_TLC_TEMPORAL, Outcome.TEMPORAL_VIOLATION),
    ):
        if m := pattern.search(stdout):
            outcome = outcome or kind
            diags.append(Diagnostic(Severity.ERROR, m.group(1)))

    # SANY runs inside TLC; surface its diagnostics too.
    sany_diags = parse_sany(stdout)
    if sany_diags:
        diags = sany_diags + diags
        outcome = outcome or Outcome.PARSE_ERROR

    if outcome is None:
        outcome = _EXIT_OUTCOME.get(exit_code, Outcome.ERROR)
        if outcome is Outcome.ERROR and exit_code not in (None, 0):
            diags.append(
                Diagnostic(
                    Severity.ERROR,
                    f"TLC exited with code {exit_code}; see result.raw for output.",
                )
            )

    return outcome, diags, _parse_stats(stdout)


def parse_loop_start(stdout: str) -> int | None:
    """Zero-based index of the state a lasso counterexample returns to.

    TLC prints `Back to state N` (one-based) in its text output only -- the
    `-dumpTrace json` file carries no loop marker, verified 2026-08-07.
    """
    match = _TLC_BACK_TO_STATE.search(stdout)
    if match is None:
        return None
    return max(int(match.group(1)) - 1, 0)
