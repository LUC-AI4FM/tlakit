import importlib
import sys

import pytest

from tlakit.render import result_html, trace_view, TraceView
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


def _three_step_trace() -> Trace:
    return Trace(
        states=[{"x": 0, "y": 7}, {"x": 1, "y": 7}, {"x": 1, "y": 9}],
        actions=[Action("Bump", "M", 5, 9), Action("Shift", "M", 11, 3)],
    )


def test_trace_view_computes_per_step_state_and_deltas():
    view = TraceView(_three_step_trace())
    assert len(view) == 3
    assert view.variables == ["x", "y"]
    # initial state has no predecessor, so nothing "changed"
    assert view.steps[0]["changed"] == []
    assert view.steps[0]["action"] is None
    assert view.steps[1]["changed"] == ["x"]
    assert view.steps[1]["action"] == "Bump"
    assert view.steps[1]["module"] == "M"
    assert view.steps[1]["line"] == 5
    assert view.steps[2]["changed"] == ["y"]
    assert view.steps[2]["state"] == {"x": 1, "y": 9}


def test_trace_view_scrubs_by_step():
    view = TraceView(_three_step_trace())
    assert view.step == 0
    view.set_step(2)
    assert view.step == 2
    assert view.current["index"] == 2
    with pytest.raises(IndexError):
        view.set_step(99)


def test_trace_view_rejects_empty_trace():
    with pytest.raises(ValueError):
        TraceView(Trace(states=[]))


def test_trace_view_from_result_returns_none_without_a_trace():
    r = CheckResult(Outcome.OK, [], None, Stats(), RAW)
    assert trace_view(r) is None


def test_trace_view_from_result_wraps_the_trace():
    r = CheckResult(
        Outcome.INVARIANT_VIOLATION, [], _three_step_trace(), Stats(), RAW
    )
    view = trace_view(r)
    assert isinstance(view, TraceView)
    assert len(view) == 3


def test_trace_view_falls_back_to_static_html():
    """Even the widget's own repr degrades to static HTML: real widget
    rendering in Jupyter goes through the anywidget model/JS protocol, not
    `_repr_html_`, so this is what non-widget frontends (nbconvert, GitHub's
    notebook renderer, plain `str()`-based tooling) actually see."""
    view = TraceView(_three_step_trace())
    html = view._repr_html_()
    assert "Bump" in html
    assert "Shift" in html
    assert "tlakit-changed" in html


@pytest.mark.skipif(
    not importlib.util.find_spec("anywidget"), reason="anywidget not installed"
)
def test_trace_view_is_a_real_anywidget_when_available():
    import anywidget

    view = TraceView(_three_step_trace())
    assert isinstance(view, anywidget.AnyWidget)
    assert view._esm
    view.set_step(1)
    assert view.step == 1  # a synced traitlet, not just a plain attribute


def test_trace_view_degrades_gracefully_without_anywidget(monkeypatch):
    """Simulate anywidget being absent (the default install: it's an optional
    extra) and prove `tlakit.render` still imports and `TraceView` still
    works, minus the interactive JS."""
    monkeypatch.setitem(sys.modules, "anywidget", None)
    monkeypatch.delitem(sys.modules, "tlakit.render", raising=False)
    try:
        render = importlib.import_module("tlakit.render")
        assert render.HAS_ANYWIDGET is False
        view = render.TraceView(_three_step_trace())
        html = view._repr_html_()
        assert "Bump" in html
    finally:
        monkeypatch.delitem(sys.modules, "tlakit.render", raising=False)
        importlib.import_module("tlakit.render")


def test_trace_view_widget_js_renders_values_as_text_not_markup():
    """Regression test for a reachable XSS.

    State values and variable names are untrusted -- tlakit's README frames
    LLM-generated specs as a use case, and `tlakit.serve` accepts spec text
    from arbitrary HTTP callers -- so a value like the payload below is
    plausible input, not a contrived one. The widget's `_esm` used to build
    table rows by interpolating raw values into an HTML template string and
    assign it wholesale via `table.innerHTML`. `JSON.stringify` escapes
    quotes/backslashes but not `<`, `>`, or `&`, so that payload would
    execute as markup in the Jupyter/Colab/VSCode frontend.

    The fix builds each row with `document.createElement` and sets cell
    content through `.textContent`, which cannot execute markup regardless
    of the string it holds. There's no Node/browser harness in this repo to
    run the widget's JS directly (pytest only), so this test pins the fix at
    two levels: the vulnerable sink (`table.innerHTML` fed by a template
    string) is gone from the source, the safe sink (`.textContent`) is
    present, and the Python side hands the payload to the widget model
    unescaped -- escaping is the JS layer's job here, not Python's, since
    pre-escaping in Python would double-escape when the JS does its own
    `.textContent` assignment.
    """
    import tlakit.render as render_module

    payload = "<img src=x onerror=alert(1)>"
    trace = Trace(
        states=[{payload: 0}, {payload: 1}], actions=[Action("Next", "M", 1, 1)]
    )

    view = TraceView(trace)
    assert view.steps[1]["state"][payload] == 1
    assert payload in view.variables

    esm = render_module._TRACE_VIEW_ESM
    assert "table.innerHTML" not in esm
    assert "td.textContent = JSON.stringify(step.state[name])" in esm
    assert "th.textContent = name" in esm
