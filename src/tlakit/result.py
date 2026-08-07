"""Normalized results.

This is a naming layer over what TLC already produces, not a new interchange
format. Trace content comes verbatim from `-dumpTrace json`; the only thing
computed here is `Trace.delta`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    OK = "ok"
    INVARIANT_VIOLATION = "invariant_violation"
    DEADLOCK = "deadlock"
    TEMPORAL_VIOLATION = "temporal_violation"
    PARSE_ERROR = "parse_error"
    TIMEOUT = "timeout"
    ERROR = "error"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    module: str | None = None
    line: int | None = None
    column: int | None = None

    def __str__(self) -> str:
        where = self.module or ""
        if self.line is not None:
            where = f"{where}:{self.line}" if where else f"line {self.line}"
            if self.column is not None:
                where = f"{where}:{self.column}"
        return f"{where}: {self.message}" if where else self.message


@dataclass(frozen=True)
class Stats:
    generated: int | None = None
    distinct: int | None = None
    queue_left: int | None = None
    depth: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class Action:
    name: str
    module: str | None = None
    begin_line: int | None = None
    begin_column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


@dataclass(frozen=True)
class Trace:
    states: list[dict[str, Any]]
    actions: list[Action] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.states and len(self.actions) != len(self.states) - 1:
            raise ValueError(
                f"expected {len(self.states) - 1} actions for "
                f"{len(self.states)} states, got {len(self.actions)}"
            )

    def __len__(self) -> int:
        return len(self.states)

    def delta(self, index: int) -> frozenset[str]:
        """Variables whose value differs from the previous state.

        `delta(0)` is empty: the initial state has no predecessor.
        """
        if not 0 <= index < len(self.states):
            raise IndexError(index)
        if index == 0:
            return frozenset()
        before, after = self.states[index - 1], self.states[index]
        keys = set(before) | set(after)
        return frozenset(k for k in keys if before.get(k) != after.get(k))

    def to_dataframe(self):
        """One row per state, one column per variable, plus `step`/`action`."""
        import pandas as pd  # lazy: pandas is not a hard dependency

        rows = []
        for i, state in enumerate(self.states):
            row = {
                "step": i + 1,
                "action": self.actions[i - 1].name if i else "<initial>",
            }
            row.update(state)
            rows.append(row)
        return pd.DataFrame(rows)


@dataclass(frozen=True)
class RawOutput:
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CheckResult:
    outcome: Outcome
    diagnostics: list[Diagnostic]
    trace: Trace | None
    stats: Stats
    raw: RawOutput

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]
