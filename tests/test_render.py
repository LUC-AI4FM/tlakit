from tlakit.render import result_html
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

RAW = RawOutput(argv=["java"], exit_code=12, stdout="TLC2 Version", stderr="")


def test_ok_result_reports_success_and_stats():
    r = CheckResult(
        Outcome.OK, [], None, Stats(generated=4, distinct=3, depth=3), RAW
    )
    html = result_html(r)
    assert "No error has been found." in html
    assert "3 distinct" in html


def test_violation_renders_each_trace_step():
    trace = Trace(
        states=[{"x": 0}, {"x": 1}], actions=[Action("Next", "M", 5, 9)]
    )
    r = CheckResult(
        Outcome.INVARIANT_VIOLATION,
        [Diagnostic(Severity.ERROR, "Invariant Inv is violated.")],
        trace,
        Stats(),
        RAW,
    )
    html = result_html(r)
    assert "Invariant Inv is violated." in html
    assert html.count("<tr") >= 3  # header + two states
    assert "Next" in html


def test_changed_variables_are_marked():
    trace = Trace(
        states=[{"x": 0, "y": 7}, {"x": 1, "y": 7}],
        actions=[Action("Next", "M", 5, 9)],
    )
    r = CheckResult(Outcome.INVARIANT_VIOLATION, [], trace, Stats(), RAW)
    html = result_html(r)
    assert "tlakit-changed" in html
    # y never changes, so exactly one cell is marked
    assert html.count("tlakit-changed") == 2  # once in CSS, once in the cell


def test_diagnostic_line_is_shown_against_source():
    r = CheckResult(
        Outcome.PARSE_ERROR,
        [Diagnostic(Severity.ERROR, "boom", module="M", line=2)],
        None,
        Stats(),
        RAW,
    )
    html = result_html(r, source="---- MODULE M ----\nVARIABLE x\n====\n")
    assert "VARIABLE x" in html
    assert "boom" in html
    assert "tlakit-hit" in html


def test_source_is_taken_from_the_result_when_not_passed():
    r = CheckResult(
        Outcome.PARSE_ERROR,
        [Diagnostic(Severity.ERROR, "boom", line=2)],
        None,
        Stats(),
        RAW,
        source="---- MODULE M ----\nVARIABLE x\n====\n",
    )
    assert "VARIABLE x" in r._repr_html_()


def test_html_escapes_untrusted_text():
    r = CheckResult(
        Outcome.ERROR,
        [Diagnostic(Severity.ERROR, "<script>alert(1)</script>")],
        None,
        Stats(),
        RAW,
    )
    html = result_html(r)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
