import json
from pathlib import Path

from tlakit.trace import load_trace, trace_from_json

FIX = Path(__file__).parent / "fixtures"


def fixture() -> dict:
    return json.loads((FIX / "trace_invariant.json").read_text())


def test_states_are_ordered_and_complete():
    t = trace_from_json(fixture())
    assert [s["x"] for s in t.states] == [0, 1, 2, 3]


def test_boolean_values_survive_as_python_bools():
    t = trace_from_json(fixture())
    assert t.states[0]["flag"] is False


def test_actions_carry_name_and_location():
    t = trace_from_json(fixture())
    assert len(t.actions) == 3
    a = t.actions[0]
    assert a.name == "Bump"
    assert a.module == "Spike"
    assert a.begin_line is not None and a.begin_column is not None


def test_delta_works_on_a_loaded_trace():
    t = trace_from_json(fixture())
    assert t.delta(1) == frozenset({"x"})


def test_load_trace_returns_none_when_absent(tmp_path):
    assert load_trace(tmp_path / "nothing.json") is None


def test_empty_counterexample_yields_none(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"vars": ["x"], "counterexample": {}}))
    assert load_trace(p) is None


def test_missing_location_does_not_crash():
    data = {
        "vars": ["x"],
        "counterexample": {
            "state": [[1, {"x": 0}], [2, {"x": 1}]],
            "action": [[[1, {"x": 0}], {"name": "N"}, [2, {"x": 1}]]],
        },
    }
    t = trace_from_json(data)
    assert t.actions[0].name == "N"
    assert t.actions[0].module is None


# --- issue #4: text-mode fallback for traces with no JSON dump -------------

from tlakit.trace import TlaValueError, parse_text_trace, parse_tla_value  # noqa: E402

REAL_TLC_TEXT = (FIX / "tlc_invariant_violation.txt").read_text()

# Same content as fixtures/trace_invariant.json, in TLC's *printed* form
# rather than its JSON dump -- used to assert the two readers agree.
TEXT_TWO_VARS = """State 1: <Initial predicate>
/\\ flag = FALSE
/\\ x = 0

State 2: <Bump line 5, col 9 to line 5, col 34 of module Spike>
/\\ flag = FALSE
/\\ x = 1

State 3: <Bump line 5, col 9 to line 5, col 34 of module Spike>
/\\ flag = FALSE
/\\ x = 2

State 4: <Bump line 5, col 9 to line 5, col 34 of module Spike>
/\\ flag = FALSE
/\\ x = 3

4 states generated, 4 distinct states found, 0 states left on queue.
"""


def test_parse_text_trace_reads_a_real_tlc_log():
    t = parse_text_trace(REAL_TLC_TEXT)
    assert [s["x"] for s in t.states] == [0, 1, 2, 3]
    assert t.actions[0].name == "Next"
    assert t.actions[0].module == "Spike"
    assert t.actions[0].begin_line == 5


def test_parse_text_trace_returns_none_without_a_state_block():
    assert parse_text_trace("Model checking completed. No error has been found.\n") is None


def test_text_and_json_traces_agree_on_the_same_content():
    text_trace = parse_text_trace(TEXT_TWO_VARS)
    json_trace = trace_from_json(fixture())
    assert text_trace.states == json_trace.states
    assert [a.name for a in text_trace.actions] == [a.name for a in json_trace.actions]
    assert [a.module for a in text_trace.actions] == [a.module for a in json_trace.actions]
    assert [a.begin_line for a in text_trace.actions] == [
        a.begin_line for a in json_trace.actions
    ]


def test_parse_text_trace_carries_declared_through():
    t = parse_text_trace(REAL_TLC_TEXT, declared=["x"])
    assert t.declared == ["x"]


def test_parse_tla_value_handles_scalars():
    assert parse_tla_value("3") == 3
    assert parse_tla_value("-3") == -3
    assert parse_tla_value("TRUE") is True
    assert parse_tla_value("FALSE") is False
    assert parse_tla_value('"hi"') == "hi"
    assert parse_tla_value("Model1") == "Model1"


def test_parse_tla_value_handles_records_and_tuples():
    assert parse_tla_value("[a |-> 1, b |-> 2]") == {"a": 1, "b": 2}
    assert parse_tla_value("<<1, 2, 3>>") == [1, 2, 3]
    assert parse_tla_value("{1, 2}") == [1, 2]
    assert parse_tla_value("<<>>") == []


def test_parse_tla_value_nests():
    assert parse_tla_value("[a |-> <<1, 2>>, b |-> [c |-> TRUE]]") == {
        "a": [1, 2],
        "b": {"c": True},
    }


def test_parse_tla_value_rejects_garbage():
    import pytest

    with pytest.raises(TlaValueError):
        parse_tla_value("not a value {{{")


def test_multiline_record_value_is_reassembled():
    body = """State 1: <Initial predicate>
r = [a |-> 1,
     b |-> 2]

"""
    t = parse_text_trace(body)
    assert t.states[0]["r"] == {"a": 1, "b": 2}


# --- follow-up: DO_NOT_MERGE finding 2, both counterexample terminators ----
#
# TLC's own -dumpTrace json represents both of these identically: only the
# genuinely distinct states, with a self-loop action closing the cycle
# rather than a literal extra state (verified 2026-08-07 for both a
# stuttering and a "Back to state" run of the same spec). The two fixtures
# below are full, real `tlc2.TLC` stdout, not hand-written.

REAL_STUTTERING_TEXT = (FIX / "tlc_stuttering.txt").read_text()
REAL_LASSO_TEXT = (FIX / "tlc_lasso.txt").read_text()

# A stutter with nothing before it but the initial state -- the case that
# previously "worked" only by accident, because the blank line after `x = 0`
# happened to end the body before the unrecognized "State 2: Stuttering"
# line ever got glued on as a bogus continuation.
STUTTER_SINGLE_STATE_TEXT = """Error: The following behavior constitutes a counter-example:

State 1: <Initial predicate>
x = 0

State 2: Stuttering
Warning: The stuttering counterexample above may be caused by ...
"""


def test_stuttering_marker_is_recognized_as_a_boundary():
    t = parse_text_trace(STUTTER_SINGLE_STATE_TEXT)
    assert [s["x"] for s in t.states] == [0]


def test_stuttering_self_loop_marks_loop_start_on_the_last_state():
    t = parse_text_trace(REAL_STUTTERING_TEXT)
    assert [s["x"] for s in t.states] == [0, 1, 2]
    assert t.loop_start == 2
    assert t.is_lasso
    assert t.loop == [{"x": 2}]


def test_stuttering_does_not_corrupt_the_preceding_state():
    """Regression: without an explicit boundary for "State N: Stuttering",
    a state with more transitions before it than the single-state case had
    its body run past the marker and swallow the trailing warning/stats
    text as bogus continuation lines of the last real variable."""
    t = parse_text_trace(REAL_STUTTERING_TEXT)
    assert t.states[-1] == {"x": 2}


def test_lasso_marker_still_agrees_with_json_after_the_stutter_fix():
    t = parse_text_trace(REAL_LASSO_TEXT)
    assert [s["x"] for s in t.states] == [0, 1, 2]
    # parse_text_trace itself leaves loop_start alone for "Back to state" --
    # tlakit.parse.parse_loop_start supplies it; CliRunner.check applies both.
    assert t.loop_start is None
    assert len(t.actions) == 2


def test_a_trace_with_neither_terminator_has_no_loop_start():
    t = parse_text_trace(REAL_TLC_TEXT)
    assert t.loop_start is None
    assert not t.is_lasso
