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
    """How a run ended.

    Names follow `tlc2.output.EC$ExitStatus`, read from tla2tools.jar rather
    than guessed. See `tlakit.parse.EXIT_OUTCOME`.
    """

    OK = "ok"
    INVARIANT_VIOLATION = "invariant_violation"      # VIOLATION_SAFETY
    DEADLOCK = "deadlock"                            # VIOLATION_DEADLOCK
    TEMPORAL_VIOLATION = "temporal_violation"        # VIOLATION_LIVENESS
    ASSUMPTION_VIOLATION = "assumption_violation"    # VIOLATION_ASSUMPTION
    ASSERTION_FAILED = "assertion_failed"            # VIOLATION_ASSERT
    EVALUATION_ERROR = "evaluation_error"            # FAILURE_*_EVAL
    PARSE_ERROR = "parse_error"                      # ERROR_SPEC_PARSE
    CONFIG_ERROR = "config_error"                    # ERROR_CONFIG_PARSE
    STATE_SPACE_TOO_LARGE = "state_space_too_large"  # ERROR_STATESPACE_TOO_LARGE
    TIMEOUT = "timeout"                              # tlakit, not TLC
    ERROR = "error"


class Severity(str, Enum):
    """Whether a `Diagnostic` stopped the run or merely reported on it."""

    #: The run could not produce an answer, or produced a failing one.
    ERROR = "error"
    #: TLC finished, but said something worth surfacing on the way.
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    """One message from SANY or TLC, with its source location when it has one.

    `str(diagnostic)` renders it as `module:line:column: message`, dropping
    whatever parts are unknown. That form is what `check_source` callers feed
    back to a model, so it stays stable.
    """

    #: Whether this stopped the run. See `CheckResult.errors`.
    severity: Severity
    #: The text, with TLC's own boilerplate framing removed.
    message: str
    #: The module the message points into, when TLC named one. Not always the
    #: module being checked -- an error can surface inside an EXTENDed one.
    module: str | None = None
    #: 1-based line, matching TLC's own numbering and every editor's.
    line: int | None = None
    #: 1-based column. None whenever `line` is None.
    column: int | None = None

    def __str__(self) -> str:
        where = self.module or ""
        if self.line is not None:
            where = f"{where}:{self.line}" if where else f"line {self.line}"
            if self.column is not None:
                where = f"{where}:{self.column}"
        return f"{where}: {self.message}" if where else self.message


@dataclass(frozen=True)
class Coverage:
    """How often TLC evaluated one action.

    `distinct` counts states the action generated that were new; `total` counts
    every generation. `total == 0` means the action never fired -- usually a
    guard that can never hold, which is how a specification passes for the
    wrong reason.
    """

    name: str
    distinct: int
    total: int
    module: str | None = None
    line: int | None = None

    @property
    def unused(self) -> bool:
        """True when TLC never evaluated this action to true."""
        return self.total == 0


@dataclass(frozen=True)
class Stats:
    """What TLC reported about the search itself.

    Every count is None when TLC did not report it -- a run that failed to
    parse never searched, so `generated` is genuinely unknown rather than 0.
    """

    #: States TLC generated in total, including ones it had already seen.
    generated: int | None = None
    #: Distinct states found: the size of the reachable state space explored.
    distinct: int | None = None
    #: States still on the queue when TLC stopped. Non-zero after a violation
    #: (TLC halts on the first one) or a timeout; 0 after an exhaustive run.
    queue_left: int | None = None
    #: Depth of the deepest behaviour explored, in states.
    depth: int | None = None
    #: Wall-clock time for the run, measured by tlakit rather than by TLC.
    duration_ms: int | None = None
    #: Per-action coverage, keyed by action name. Populated only when the run
    #: asked for it -- TLC gathers none by default, so empty means "not
    #: measured", never "nothing ran".
    coverage: dict[str, "Coverage"] = field(default_factory=dict)

    @property
    def unused_actions(self) -> list[str]:
        """Actions TLC never fired. Empty when coverage was not requested."""
        return sorted(name for name, c in self.coverage.items() if c.unused)


@dataclass(frozen=True)
class Action:
    """The disjunct of `Next` that produced one step of a trace.

    The location spans the action's whole definition, which is what makes
    "highlight the transition that got us here" possible in a notebook.
    """

    #: The action's name, e.g. `Next` or the disjunct TLC attributed the step
    #: to. `tlakit.trace.UNKNOWN_ACTION` when TLC named none.
    name: str
    #: The module the action is defined in, when known.
    module: str | None = None
    #: 1-based line where the definition starts.
    begin_line: int | None = None
    #: 1-based column where the definition starts.
    begin_column: int | None = None
    #: 1-based line where the definition ends.
    end_line: int | None = None
    #: 1-based column where the definition ends.
    end_column: int | None = None


@dataclass(frozen=True)
class Trace:
    """A counterexample: the states TLC walked to reach a violation.

    Indexable and iterable over states (`trace[0]`, `len(trace)`,
    `for state in trace`). `actions` is one shorter than `states` -- the
    initial state was not produced by an action -- and `__post_init__`
    enforces that rather than letting the two drift apart.
    """

    #: One dict per state: variable name -> value, already decoded from TLC's
    #: JSON. TLA+ records become dicts; sequences and sets both become lists.
    states: list[dict[str, Any]]
    #: The action producing each state after the first, so `actions[i - 1]`
    #: is what led to `states[i]`.
    actions: list[Action] = field(default_factory=list)
    #: TLC's own state identifiers, parallel to `states`. Empty when the trace
    #: was built by hand rather than loaded from TLC.
    state_ids: list[Any] = field(default_factory=list)
    #: The module's declared VARIABLES, when known. Everything else in a state
    #: came from an ALIAS. Empty means "treat every key as a variable".
    declared: list[str] = field(default_factory=list)
    #: Index of the state the behaviour loops back to, for a lasso
    #: counterexample. None for a finite trace.
    loop_start: int | None = None

    def __post_init__(self) -> None:
        if self.states and len(self.actions) != len(self.states) - 1:
            raise ValueError(
                f"expected {len(self.states) - 1} actions for "
                f"{len(self.states)} states, got {len(self.actions)}"
            )

    def __len__(self) -> int:
        return len(self.states)

    def __getitem__(self, index: int) -> dict[str, Any]:
        """Zero-based state access. Negative indices count from the end."""
        return self.states[index]

    def __iter__(self):
        return iter(self.states)

    @property
    def is_lasso(self) -> bool:
        """True when the behaviour ends by cycling rather than stopping."""
        return self.loop_start is not None

    @property
    def loop(self) -> list[dict[str, Any]]:
        """The repeating suffix. Empty for a finite trace."""
        if self.loop_start is None:
            return []
        return self.states[self.loop_start:]

    @property
    def prefix(self) -> list[dict[str, Any]]:
        """The states before the cycle begins. The whole trace if finite."""
        if self.loop_start is None:
            return list(self.states)
        return self.states[: self.loop_start]

    def _keys(self) -> set[str]:
        return {key for state in self.states for key in state}

    @property
    def variables(self) -> list[str]:
        """State variable names, sorted.

        When the module's declarations are known, alias fields are excluded;
        otherwise every key in the trace counts as a variable.
        """
        present = self._keys()
        if not self.declared:
            return sorted(present)
        return sorted(present & set(self.declared))

    @property
    def aliases(self) -> list[str]:
        """Keys contributed by an ALIAS rather than by the state, sorted."""
        if not self.declared:
            return []
        return sorted(self._keys() - set(self.declared))

    def value_at(self, index: int, path: str) -> Any:
        """Look up a dotted path into a state, e.g. `"progress.s1"`.

        TLA+ records nest arbitrarily, so a consumer inspecting one field of
        one server should not have to walk the dictionaries itself.
        """
        value = self.states[index]
        walked: list[str] = []
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                where = ".".join(walked) or "<state>"
                raise KeyError(f"{path!r}: {part!r} not found under {where}")
            walked.append(part)
            value = value[part]
        return value

    def changes(self, name: str) -> list[int]:
        """Indices of the states at which `name` took a new value."""
        return [i for i in range(1, len(self.states)) if name in self.delta(i)]

    def compare(
        self, left: int, right: int, *, include_aliases: bool = False
    ) -> dict[str, tuple[Any, Any]]:
        """Variables differing between two states, as `name -> (before, after)`."""
        a, b = self.states[left], self.states[right]
        keys = set(a) | set(b)
        if not include_aliases and self.declared:
            keys &= set(self.declared)
        return {
            key: (a.get(key), b.get(key))
            for key in sorted(keys)
            if a.get(key) != b.get(key)
        }

    def delta(self, index: int, *, include_aliases: bool = False) -> frozenset[str]:
        """Variables whose value differs from the previous state.

        `delta(0)` is empty: the initial state has no predecessor. Alias fields
        are excluded unless `include_aliases` is set — an alias is a view of
        the state, so reporting it as a change double-counts.
        """
        if not 0 <= index < len(self.states):
            raise IndexError(index)
        if index == 0:
            return frozenset()
        before, after = self.states[index - 1], self.states[index]
        keys = set(before) | set(after)
        if not include_aliases and self.declared:
            keys &= set(self.declared)
        return frozenset(k for k in keys if before.get(k) != after.get(k))

    def to_dataframe(self, flatten: bool = False):
        """One row per state, one column per variable, plus `step`/`action`.

        With `flatten`, nested TLA+ records expand into dotted columns
        (`progress.s1.term`). Unflattened columns hold Python dicts, which
        pandas cannot group, filter, or plot -- and nested records are the
        common case in real specs, not the exotic one.
        """
        import pandas as pd  # lazy: pandas is not a hard dependency

        rows = []
        for i, state in enumerate(self.states):
            row = {
                "step": i + 1,
                "action": self.actions[i - 1].name if i else "<initial>",
            }
            row.update(flatten_state(state) if flatten else state)
            rows.append(row)
        return pd.DataFrame(rows)


def flatten_state(state: dict[str, Any], separator: str = ".") -> dict[str, Any]:
    """Expand nested records into dotted keys.

    Sequences and sets are left whole: a tuple is a value, not a namespace, and
    exploding it would make the column set depend on the state.
    """
    flat: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict) and value:
            for key, inner in value.items():
                walk(f"{prefix}{separator}{key}" if prefix else str(key), inner)
        else:
            flat[prefix] = value

    for key, value in state.items():
        walk(str(key), value)
    return flat


@dataclass(frozen=True)
class RawOutput:
    """Exactly what the subprocess was asked and exactly what it said.

    The escape hatch for anything tlakit has not normalized. It is also the
    one field carrying machine-specific detail -- absolute temp paths, the
    java argv -- so it is what `tlakit.serve.redact` scrubs before a response
    leaves the process, and what a golden file should never pin.
    """

    #: The full command line, as a list. Never a shell string.
    argv: list[str]
    #: The process exit code, or None if it was killed on timeout.
    exit_code: int | None
    #: Standard output, verbatim. TLC reports essentially everything here.
    stdout: str
    #: Standard error, verbatim. Usually JVM noise rather than TLC's own.
    stderr: str


@dataclass(frozen=True)
class CheckResult:
    """What one `parse` or `check` produced.

    A failing check is a normal result, not an exception: inspect `outcome`,
    or `ok` for the boolean. Only things that stop a run from happening at all
    -- no Java, no jar -- raise.
    """

    #: How the run ended. The single field worth branching on.
    outcome: Outcome
    #: Every message from SANY or TLC, warnings included. `errors` filters.
    diagnostics: list[Diagnostic]
    #: The counterexample, when the outcome has one. None for `OK`, and also
    #: for failures that produce no behaviour (a parse error, say).
    trace: Trace | None
    #: Search statistics. Present but empty-valued when nothing was searched.
    stats: Stats
    #: The underlying subprocess invocation and its unparsed output.
    raw: RawOutput
    #: The module source, when the runner knows it. Lets the notebook view
    #: point at the offending line.
    source: str | None = None
    #: One standalone SVG per trace step, when the run was asked to animate.
    #: Produced by the spec's own AnimView through SVG.tla, not by tlakit.
    frames: list[str] = field(default_factory=list)
    #: The reachable state graph, when the run asked for it. TLC does not
    #: produce one otherwise.
    graph: Any = None

    @property
    def ok(self) -> bool:
        """True only for `Outcome.OK`. Every other outcome, including
        `TIMEOUT`, is a run that did not clear the spec."""
        return self.outcome is Outcome.OK

    @property
    def errors(self) -> list[Diagnostic]:
        """The error-severity diagnostics, dropping warnings."""
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    def _repr_html_(self) -> str:
        from .render import result_html  # lazy: render imports result

        return result_html(self)
