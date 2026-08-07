"""Issue #5: liveness counterexamples are lassos, not flat sequences."""
import shutil

import pytest

from tlakit.parse import parse_loop_start
from tlakit.result import Action, Trace

LASSO_OUTPUT = """Error: Temporal properties were violated.
Error: The following behavior constitutes a counter-example:
State 1: <Initial predicate>
x = 0

State 2: <Next line 5, col 9 to line 5, col 24 of module L>
x = 1

Back to state 1: <Next line 5, col 9 to line 5, col 24 of module L>
"""


def test_loop_start_is_zero_based():
    assert parse_loop_start(LASSO_OUTPUT) == 0


def test_loop_start_reads_a_later_state():
    assert parse_loop_start("Back to state 3: <Next of module L>\n") == 2


def test_no_marker_means_a_finite_trace():
    assert parse_loop_start("Error: Invariant Inv is violated.\n") is None


def make(loop_start):
    return Trace(
        states=[{"x": 0}, {"x": 1}, {"x": 2}],
        actions=[Action("Next"), Action("Next")],
        loop_start=loop_start,
    )


def test_finite_trace_reports_no_lasso():
    t = make(None)
    assert t.is_lasso is False
    assert t.loop == []
    assert t.prefix == t.states


def test_lasso_splits_into_prefix_and_cycle():
    t = make(1)
    assert t.is_lasso is True
    assert t.prefix == [{"x": 0}]
    assert t.loop == [{"x": 1}, {"x": 2}]


def test_whole_trace_can_be_the_cycle():
    t = make(0)
    assert t.prefix == []
    assert len(t.loop) == 3


# --- end to end -----------------------------------------------------------

LIVE = """---- MODULE L ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = (x + 1) % 3
Spec == Init /\\ [][Next]_x /\\ WF_x(Next)
Live == <>[](x = 1)
====
"""


@pytest.mark.java
def test_temporal_violation_yields_a_lasso():
    import tlakit
    from tlakit.jar import JarNotFound
    from tlakit.result import Outcome

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    result = tlakit.Spec(source=LIVE, name="L").check(
        config="SPECIFICATION Spec\nPROPERTY Live\n"
    )
    assert result.outcome is Outcome.TEMPORAL_VIOLATION
    assert result.trace is not None
    assert result.trace.is_lasso, "temporal counterexample should be a lasso"
    assert result.trace.loop, "cycle should be non-empty"


def test_renderer_marks_the_cycle():
    from tlakit.render import result_html
    from tlakit.result import CheckResult, Outcome, RawOutput, Stats

    raw = RawOutput(argv=[], exit_code=13, stdout="", stderr="")
    html = result_html(
        CheckResult(Outcome.TEMPORAL_VIOLATION, [], make(1), Stats(), raw)
    )
    assert "tlakit-loop" in html
    assert "repeats from step 2" in html


def test_renderer_says_nothing_about_loops_for_a_finite_trace():
    from tlakit.render import result_html
    from tlakit.result import CheckResult, Outcome, RawOutput, Stats

    raw = RawOutput(argv=[], exit_code=12, stdout="", stderr="")
    html = result_html(
        CheckResult(Outcome.INVARIANT_VIOLATION, [], make(None), Stats(), raw)
    )
    assert "Lasso" not in html
