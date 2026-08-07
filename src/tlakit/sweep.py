"""Check one spec across a grid of constants.

Each run is an independent JVM in its own working directory, so a sweep is
embarrassingly parallel — and also the fastest way to exhaust a laptop. Five
concurrent TLC processes at default heap will ask for more memory than most
machines have, so `workers` defaults to 1 and `heap` is passed to every child.
"""
from __future__ import annotations

import itertools
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .result import CheckResult, Outcome


@dataclass(frozen=True)
class Run:
    """One point in the grid and what came of it."""

    constants: dict[str, Any]
    result: CheckResult

    @property
    def label(self) -> str:
        return ", ".join(f"{k}={v!r}" for k, v in sorted(self.constants.items()))


@dataclass(frozen=True)
class SweepResult:
    runs: list[Run] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.runs)

    def __iter__(self):
        return iter(self.runs)

    def __getitem__(self, index: int) -> Run:
        return self.runs[index]

    @property
    def ok(self) -> bool:
        """True when every point in the grid passed."""
        return all(run.result.ok for run in self.runs)

    @property
    def failures(self) -> list[Run]:
        return [run for run in self.runs if not run.result.ok]

    def first_failure(self) -> Run | None:
        """The smallest failing configuration, in grid order.

        Grid order is the useful order here: sweeping upward, the first failure
        is the smallest one that breaks, which is the one worth reading.
        """
        return self.failures[0] if self.failures else None

    def to_dataframe(self):
        """One row per configuration."""
        import pandas as pd  # lazy: pandas is not a hard dependency

        rows = []
        for run in self.runs:
            stats = run.result.stats
            row: dict[str, Any] = dict(run.constants)
            row["outcome"] = run.result.outcome.value
            row["states"] = stats.generated
            row["distinct"] = stats.distinct
            row["depth"] = stats.depth
            row["ms"] = stats.duration_ms
            row["trace_len"] = len(run.result.trace) if run.result.trace else 0
            rows.append(row)
        return pd.DataFrame(rows)


def grid_points(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """The cartesian product of the grid, in stable order.

    Keys keep the caller's order rather than being sorted: a sweep written as
    `{"Servers": ..., "Clients": ...}` should vary Clients fastest, matching
    how it reads.
    """
    if not grid:
        return [{}]
    names = list(grid)
    return [
        dict(zip(names, combination))
        for combination in itertools.product(*(grid[name] for name in names))
    ]


def run_sweep(
    check,
    grid: dict[str, list[Any]],
    workers: int = 1,
    **check_kwargs: Any,
) -> SweepResult:
    """Call `check(constants=..., **kwargs)` over every point in `grid`.

    `check` is injected rather than imported so this module stays testable
    without Java.
    """
    if workers < 1:
        raise ValueError(f"workers must be at least 1, got {workers}")

    points = grid_points(grid)

    def one(constants: dict[str, Any]) -> Run:
        return Run(constants=constants, result=check(constants=constants, **check_kwargs))

    if workers == 1 or len(points) == 1:
        return SweepResult(runs=[one(point) for point in points])

    from .cli import terminate_all

    with ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            # map keeps grid order in the results regardless of finish order.
            return SweepResult(runs=list(pool.map(one, points)))
        except BaseException:
            # A worker thread never sees KeyboardInterrupt, so nothing else
            # would reap the JVMs it started.
            terminate_all()
            raise


def summarize(sweep: SweepResult) -> str:
    """One line per configuration, for a plain-text report."""
    lines = []
    for run in sweep.runs:
        stats = run.result.stats
        detail = f"{stats.distinct} distinct" if stats.distinct is not None else ""
        lines.append(
            f"{run.label or '<no constants>'}: {run.result.outcome.value}"
            + (f" ({detail})" if detail else "")
        )
    return "\n".join(lines)


OUTCOME_ORDER = [
    Outcome.OK,
    Outcome.INVARIANT_VIOLATION,
    Outcome.DEADLOCK,
    Outcome.TEMPORAL_VIOLATION,
    Outcome.TIMEOUT,
]
