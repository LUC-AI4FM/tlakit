import pytest

from tlakit.result import (
    Action,
    CheckResult,
    Diagnostic,
    Outcome,
    RawOutput,
    Severity,
    Stats,
    Trace,
)


def make_trace():
    return Trace(
        states=[{"x": 0, "y": "a"}, {"x": 1, "y": "a"}, {"x": 1, "y": "b"}],
        actions=[
            Action(name="Next", module="M", begin_line=5, begin_column=9),
            Action(name="Next", module="M", begin_line=5, begin_column=9),
        ],
    )


def test_trace_length_is_state_count():
    assert len(make_trace()) == 3


def test_delta_reports_only_changed_variables():
    t = make_trace()
    assert t.delta(1) == frozenset({"x"})
    assert t.delta(2) == frozenset({"y"})


def test_delta_of_initial_state_is_empty():
    assert make_trace().delta(0) == frozenset()


def test_delta_rejects_out_of_range():
    with pytest.raises(IndexError):
        make_trace().delta(3)


def test_actions_must_be_one_shorter_than_states():
    with pytest.raises(ValueError):
        Trace(states=[{"x": 0}], actions=[Action("N", "M", 1, 1)])


def test_ok_property_reflects_outcome():
    raw = RawOutput(argv=["java"], exit_code=0, stdout="", stderr="")
    good = CheckResult(Outcome.OK, [], None, Stats(), raw)
    bad = CheckResult(Outcome.DEADLOCK, [], None, Stats(), raw)
    assert good.ok is True
    assert bad.ok is False


def test_diagnostic_str_includes_location():
    d = Diagnostic(Severity.ERROR, "boom", module="M", line=4, column=1)
    assert "M" in str(d) and "4" in str(d)


def test_errors_filters_warnings():
    raw = RawOutput(argv=[], exit_code=0, stdout="", stderr="")
    result = CheckResult(
        Outcome.OK,
        [Diagnostic(Severity.WARNING, "meh"), Diagnostic(Severity.ERROR, "bad")],
        None,
        Stats(),
        raw,
    )
    assert [d.message for d in result.errors] == ["bad"]
