"""Issue #24: driving the TLA+ Debugger over DAP.

The tests that matter here run a real TLC under `-debugger` and speak the real
protocol to it. A mocked debug adapter would only prove that the client agrees
with my idea of TLC, and my idea of TLC is exactly what turned out to be wrong
twice while writing it -- see `test_a_disjunctive_next_needs_breakpoints_on_the
_leaves`.

The pure parsing is tested without java, because it is pure.
"""
from __future__ import annotations

import shutil

import pytest

from tlakit.dap import (
    DapClient,
    DebuggerError,
    DebuggerTimeout,
    DebugSession,
    next_relation,
    relation_lines,
    walk,
)

SPIKE = """---- MODULE Spike ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = (x + 1) % 4
Spec == Init /\\ [][Next]_x
====
"""

MICROWAVE = """---- MODULE Microwave ----
EXTENDS Naturals
VARIABLES door, radiation
vars == <<door, radiation>>
Init == door = "closed" /\\ radiation = "off"
Open  == door' = "open"   /\\ UNCHANGED radiation
Close == door' = "closed" /\\ UNCHANGED radiation
Start == radiation' = "on" /\\ UNCHANGED door
Next == Open \\/ Close \\/ Start
Spec == Init /\\ [][Next]_vars
====
"""

SPEC_CONFIG = "SPECIFICATION Spec\n"


# --------------------------------------------------------------------------
# Finding the next-state relation. No java.
# --------------------------------------------------------------------------


def test_next_directive_names_the_relation_outright():
    assert next_relation(SPIKE, "INIT Init\nNEXT Next\n") == "Next"


def test_a_specification_is_read_for_its_boxed_action():
    """`SPECIFICATION Spec` does not name the relation, so `Spec`'s own
    definition is read for the `[][Next]_vars` inside it."""
    assert next_relation(SPIKE, SPEC_CONFIG) == "Next"
    assert next_relation(MICROWAVE, SPEC_CONFIG) == "Next"


def test_a_boxed_action_belonging_to_a_later_operator_is_not_picked_up():
    source = """---- MODULE M ----
VARIABLE x
Init == x = 0
A == x' = 1
B == x' = 2
Spec == Init /\\ [][A]_x
Other == Init /\\ [][B]_x
====
"""
    assert next_relation(source, "SPECIFICATION Spec\n") == "A"
    assert next_relation(source, "SPECIFICATION Other\n") == "B"


def test_a_config_naming_neither_yields_nothing():
    assert next_relation(SPIKE, "INVARIANT Inv\n") is None


def test_relation_lines_follows_references_to_the_leaves():
    """The rule the debugger actually needs: not just `Next`, but everything
    it reaches, because that is where evaluation happens."""
    assert relation_lines(SPIKE, SPEC_CONFIG) == [5]
    assert relation_lines(MICROWAVE, SPEC_CONFIG) == [6, 7, 8, 9]


def test_relation_lines_skips_declarations():
    """A VARIABLES line is never evaluated, so a breakpoint on it is noise --
    and the identifier scan matches variable names constantly."""
    lines = relation_lines(MICROWAVE, SPEC_CONFIG)
    assert 3 not in lines  # VARIABLES door, radiation


def test_relation_lines_terminates_on_mutual_reference():
    source = """---- MODULE M ----
VARIABLE x
Init == x = 0
A == B
B == A
Spec == Init /\\ [][A]_x
====
"""
    assert relation_lines(source, "SPECIFICATION Spec\n") == [4, 5]


# --------------------------------------------------------------------------
# Against a real TLC under the real protocol.
# --------------------------------------------------------------------------


@pytest.fixture
def ready():
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))


#: How many stops to take before asserting. Deliberately short of exhausting
#: Spike's four states.
#:
#: Assertions here are about a *prefix* of the walk, never about reaching the
#: end. Whether the final state is observed is a race that tlakit cannot win
#: from outside: TLC exits as soon as it finishes, and reading a stop requires
#: a TLC still alive to answer for it. Pinning an exact total made these tests
#: pass on every local run and fail on the macOS and Windows runners, which is
#: the flakiness those assertions were measuring rather than the behaviour.
PREFIX = 3


@pytest.mark.java
def test_a_session_steps_through_the_state_space(ready):
    with DebugSession(SPIKE, "Spike", SPEC_CONFIG) as session:
        seen = [session.step() for _ in range(PREFIX)]
    assert all(step is not None for step in seen), "ran out before the prefix"
    assert [step.states[-1]["x"] for step in seen] == [0, 1, 2]
    assert [a.name for a in seen[-1].actions] == ["Next", "Next"]


@pytest.mark.java
def test_a_disjunctive_next_needs_breakpoints_on_the_leaves(ready):
    """The bug this whole design exists around.

    A breakpoint on `Next == Open \\/ Close \\/ Start` never fires -- the
    debugger breaks on the body being evaluated, not on the name referring to
    it. Breaking only on `Next` made a four-state spec look like an empty
    state space, which is indistinguishable from a spec that does not run.
    """
    steps = walk(MICROWAVE, "Microwave", SPEC_CONFIG, limit=20)
    assert steps, "no stops: breakpoints landed somewhere never evaluated"
    assert steps[0].states == [{"door": "closed", "radiation": "off"}]

    # TLC's own action attribution, not tlakit guessing from the state delta.
    named = {a.name for step in steps for a in step.actions}
    assert {"Open", "Start"} <= named


@pytest.mark.java
def test_only_breaking_on_next_itself_finds_nothing(ready):
    """Pins the finding above in the failing direction too, so a future
    'simplification' back to one breakpoint fails loudly rather than
    silently reporting an empty state space."""
    steps = walk(
        MICROWAVE, "Microwave", SPEC_CONFIG, limit=6, breakpoint_lines=[9]
    )
    assert steps == []


@pytest.mark.java
def test_a_step_becomes_the_same_trace_a_completed_run_produces(ready):
    """The payoff for reusing `parse_tla_value` and the action parser: a
    stepped trace is a `Trace`, so `delta` and the rest work unchanged."""
    with DebugSession(SPIKE, "Spike", SPEC_CONFIG) as session:
        for _ in range(PREFIX):
            step = session.step()
    trace = step.as_trace(declared=["x"])
    assert len(trace) == PREFIX
    assert trace.variables == ["x"]
    assert trace.delta(1) == frozenset({"x"})
    assert trace.actions[0].module == "Spike"
    assert trace.actions[0].begin_line == 5


@pytest.mark.java
def test_distinct_collapses_repeated_evaluations(ready):
    """TLC evaluates every disjunct at every state, so stops repeat the
    behaviour unchanged. Asserted as a property of the same fixed number of
    stops rather than by comparing two whole walks, whose totals depend on
    which side of TLC's exit the last stop lands."""
    with DebugSession(MICROWAVE, "Microwave", SPEC_CONFIG) as session:
        stops = [session.step() for _ in range(6)]
    seen = [step for step in stops if step is not None]
    assert len(seen) >= 2

    collapsed = []
    for step in seen:
        if not collapsed or collapsed[-1].states != step.states:
            collapsed.append(step)
    assert len(collapsed) < len(seen), "no stop repeated its predecessor"


@pytest.mark.java
def test_evaluate_answers_in_the_stopped_frame(ready):
    with DebugSession(MICROWAVE, "Microwave", SPEC_CONFIG) as session:
        session.step()
        assert session.evaluate("door") == "closed"
        assert session.evaluate("1 + 1") == "2"


@pytest.mark.java
def test_step_back_is_the_debuggers_own_feature(ready):
    """TLC advertises `supportsStepBack`, so this is not a replay of a
    recorded trace -- the request goes to the debugger."""
    with DebugSession(SPIKE, "Spike", SPEC_CONFIG) as session:
        session.step()
        session.step()
        assert session.step_back() is not None


@pytest.mark.java
def test_requests_after_exhaustion_return_rather_than_hang(ready):
    """Once TLC finishes it answers nothing, so a `stackTrace` would block
    for the full request timeout. Exhaustion is a normal end, not a stall.
    """
    with DebugSession(SPIKE, "Spike", SPEC_CONFIG) as session:
        for _ in range(30):
            if session.step() is None:
                break
        assert session.exhausted
        assert session.step() is None
        assert session.evaluate("x") is None
        assert session.step_back() is None


@pytest.mark.java
def test_a_config_with_no_relation_says_so_before_starting_a_jvm(ready):
    with pytest.raises(DebuggerError, match="next-state relation"):
        DebugSession(SPIKE, "Spike", "INVARIANT Inv\n")


@pytest.mark.java
def test_closing_a_session_terminates_tlc(ready):
    session = DebugSession(SPIKE, "Spike", SPEC_CONFIG)
    session.step()
    assert session.running
    session.close()
    assert not session.running


@pytest.mark.java
def test_closing_twice_is_harmless(ready):
    session = DebugSession(SPIKE, "Spike", SPEC_CONFIG)
    session.close()
    session.close()


# --------------------------------------------------------------------------
# Connecting, without a debugger. A real TLC always suspends before the first
# ASSUME promptly, so the *absence* of that stop is not something a java test
# can stage -- and it is the case that has to be got right, because getting it
# wrong is silent.
# --------------------------------------------------------------------------


class _SilentClient:
    """A DAP client that answers requests and never announces a stop."""

    stops = 0

    def __init__(self, *_: object, **__: object):
        self.commands: list[str] = []
        self.closed = False
        self._left = self.stops

    def request(self, command: str, timeout: float = 30.0, **arguments: object) -> dict:
        self.commands.append(command)
        return {}

    def send(self, command: str, **arguments: object) -> int:
        self.commands.append(command)
        return 1

    def wait_for_event(self, name: str, timeout: float = 30.0) -> dict | None:
        if self._left <= 0:
            return None
        self._left -= 1
        return {"event": name}

    def close(self) -> None:
        self.closed = True


class _StoppingClient(_SilentClient):
    """The same, but it does deliver the pre-ASSUME stop."""

    stops = 1


class _FakeProcess:
    stdout = None

    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:  # pragma: no cover - terminate always succeeds here
        self.terminated = True


def _unstarted_session(tmp_path) -> DebugSession:
    """A session with everything `_connect` and `close` touch, and no JVM."""
    session = DebugSession.__new__(DebugSession)
    session._module = "Spike"
    session._work = tmp_path
    session._lines = [5]
    session._port = 4712
    session._client = None
    session._terminated = False
    session._exhausted = False
    session._process = _FakeProcess()
    return session


def test_a_missing_pre_assume_stop_is_a_timeout_not_a_silent_desync(monkeypatch, tmp_path):
    """Dropping the result of that wait is worse than useless.

    On a slow runner the pre-ASSUME stop can miss the deadline, and then it is
    still queued: the caller's first `step()` consumes it as though `continue`
    had produced it, returns a `Step`, and every later read is one stop ahead
    of TLC. It surfaced on CI as `evaluate("door")` answering "the identifier
    door is either undefined or not an operator" -- an "undefined identifier" a
    long way from "the debugger never stopped".
    """
    import tlakit.dap as dap

    monkeypatch.setattr(dap, "DapClient", _SilentClient)
    session = _unstarted_session(tmp_path)

    with pytest.raises(DebuggerTimeout, match="ASSUME"):
        session._connect(0.05)

    assert session._process.terminated, "a failed connect must not leak a JVM"


def test_a_delivered_pre_assume_stop_is_consumed_and_connect_succeeds(monkeypatch, tmp_path):
    """The other half: the check must not reject the normal case."""
    import tlakit.dap as dap

    monkeypatch.setattr(dap, "DapClient", _StoppingClient)
    session = _unstarted_session(tmp_path)

    session._connect(0.05)

    assert session._client.commands == ["initialize", "setBreakpoints", "launch"]
    assert not session._process.terminated


# --------------------------------------------------------------------------
# Transport framing, without a debugger.
# --------------------------------------------------------------------------


def test_the_reader_reassembles_a_message_split_across_reads():
    """A socket read lands wherever it lands; a DAP message is framed, not
    aligned to it."""
    client = DapClient.__new__(DapClient)
    client._buffer = b""
    client._events = []
    client._responses = {}
    import threading

    client._lock = threading.Lock()

    body = b'{"type":"event","event":"stopped","body":{"reason":"breakpoint"}}'
    frame = b"Content-Length: %d\r\n\r\n" % len(body) + body

    client._buffer = frame[:20]
    client._drain()
    assert client._events == []

    client._buffer += frame[20:]
    client._drain()
    assert client._events[0]["event"] == "stopped"


def test_the_reader_handles_two_messages_in_one_read():
    client = DapClient.__new__(DapClient)
    client._buffer = b""
    client._events = []
    client._responses = {}
    import threading

    client._lock = threading.Lock()

    def framed(payload: bytes) -> bytes:
        return b"Content-Length: %d\r\n\r\n" % len(payload) + payload

    client._buffer = framed(b'{"type":"event","event":"a"}') + framed(
        b'{"type":"event","event":"b"}'
    )
    client._drain()
    assert [e["event"] for e in client._events] == ["a", "b"]


def test_a_response_is_matched_to_its_request_not_to_arrival_order():
    client = DapClient.__new__(DapClient)
    client._buffer = b""
    client._events = []
    client._responses = {}
    import threading

    client._lock = threading.Lock()

    body = b'{"type":"response","request_seq":7,"success":true,"body":{"ok":1}}'
    client._buffer = b"Content-Length: %d\r\n\r\n" % len(body) + body
    client._drain()
    assert 7 in client._responses
    assert client._events == []


@pytest.mark.java
def test_the_stepper_feeds_the_same_widget_as_a_finished_run(ready):
    """`stepper_view` is `trace_view`'s debugger half. They share a widget
    because a stepped `Step.as_trace()` is an ordinary `Trace`."""
    from tlakit.render import TraceView, stepper_view

    view = stepper_view(SPIKE, "Spike", SPEC_CONFIG)
    assert isinstance(view, TraceView)
    assert len(view) == 4
    assert view.variables == ["x"]
    assert view.steps[2]["state"] == {"x": 2}
    assert view.steps[2]["action"] == "Next"


@pytest.mark.java
def test_a_stop_that_is_not_a_state_is_not_a_behaviour(ready):
    """TLC suspends before the first ASSUME, and a breakpoint can land where
    no behaviour exists yet. Either produces a `Step` with no states, which
    `walk` must not report: a run of empty steps reads as "the spec has an
    empty state space", which is exactly what a wrongly-placed breakpoint
    looks like. This is how `test_only_breaking_on_next_itself_finds_nothing`
    failed on a CI runner while passing locally -- the empty stop was drained
    at connect time here and not there.
    """
    steps = walk(MICROWAVE, "Microwave", SPEC_CONFIG, limit=8, distinct=False)
    assert all(step.states for step in steps)
