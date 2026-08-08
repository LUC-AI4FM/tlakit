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


@pytest.mark.java
def test_a_session_steps_through_the_state_space(ready):
    steps = walk(SPIKE, "Spike", SPEC_CONFIG, limit=12)
    assert [s.states[-1]["x"] for s in steps] == [0, 1, 2, 3]
    assert [a.name for a in steps[-1].actions] == ["Next", "Next", "Next"]


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
    steps = walk(SPIKE, "Spike", SPEC_CONFIG, limit=12)
    trace = steps[-1].as_trace(declared=["x"])
    assert len(trace) == 4
    assert trace.variables == ["x"]
    assert trace.delta(1) == frozenset({"x"})
    assert trace.actions[0].module == "Spike"
    assert trace.actions[0].begin_line == 5


@pytest.mark.java
def test_distinct_collapses_repeated_evaluations(ready):
    """TLC evaluates every disjunct at every state, so most stops repeat the
    behaviour unchanged. Both views are available."""
    every = walk(MICROWAVE, "Microwave", SPEC_CONFIG, limit=20, distinct=False)
    collapsed = walk(MICROWAVE, "Microwave", SPEC_CONFIG, limit=20, distinct=True)
    assert len(every) > len(collapsed)


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
