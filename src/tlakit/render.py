"""Static HTML for notebook output. No JavaScript, no widget toolchain.

M1 renders only static views, so anywidget is not a dependency yet. Interactive
widgets (TraceView, AnimPlayer) arrive in M2.
"""
from __future__ import annotations

from html import escape

from .result import CheckResult, Outcome

_CSS = """
<style>
.tlakit { font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
.tlakit-banner { padding: 6px 10px; border-radius: 5px; margin-bottom: 8px; }
.tlakit-ok { background: rgba(40,160,80,.16); }
.tlakit-bad { background: rgba(210,60,60,.16); }
.tlakit ul { margin: 6px 0; padding-left: 20px; }
.tlakit table { border-collapse: collapse; margin-top: 6px; }
.tlakit td, .tlakit th { border: 1px solid rgba(127,127,127,.32);
                         padding: 3px 8px; text-align: left; }
.tlakit-changed { background: rgba(255,180,0,.30); font-weight: 600; }
.tlakit-src { background: rgba(127,127,127,.10); padding: 6px 10px;
              border-radius: 5px; white-space: pre; overflow-x: auto;
              margin-top: 6px; }
.tlakit-hit { background: rgba(210,60,60,.22); display: block; }
.tlakit-stats { opacity: .7; margin-top: 6px; }
.tlakit-loop td, .tlakit-loop th { border-top: 2px solid rgba(120,90,220,.85); }
.tlakit-loopnote { opacity: .8; margin-top: 4px; font-style: italic; }
</style>
"""

_HEADLINE = {
    Outcome.OK: "No error has been found.",
    Outcome.TIMEOUT: "TLC did not finish in time; results are partial.",
    Outcome.INVARIANT_VIOLATION: "Invariant violated.",
    Outcome.DEADLOCK: "Deadlock reached.",
    Outcome.TEMPORAL_VIOLATION: "Temporal property violated.",
    Outcome.PARSE_ERROR: "The specification did not parse.",
    Outcome.ASSUMPTION_VIOLATION: "An ASSUME was violated.",
    Outcome.ASSERTION_FAILED: "An assertion failed.",
    Outcome.EVALUATION_ERROR: "TLC could not evaluate the specification.",
    Outcome.CONFIG_ERROR: "The configuration did not parse.",
    Outcome.STATE_SPACE_TOO_LARGE: "The state space is too large for TLC.",
    Outcome.ERROR: "The tool reported an error.",
}


def _stats_html(result: CheckResult) -> str:
    s = result.stats
    bits = []
    if s.generated is not None:
        bits.append(f"{s.generated} states generated")
    if s.distinct is not None:
        bits.append(f"{s.distinct} distinct")
    if s.depth is not None:
        bits.append(f"depth {s.depth}")
    if s.duration_ms is not None:
        bits.append(f"{s.duration_ms} ms")
    if not bits:
        return ""
    return f'<div class="tlakit-stats">{escape(", ".join(bits))}</div>'


def _diagnostics_html(result: CheckResult) -> str:
    if not result.diagnostics:
        return ""
    items = "".join(f"<li>{escape(str(d))}</li>" for d in result.diagnostics)
    return f"<ul>{items}</ul>"


def _source_html(source: str, result: CheckResult) -> str:
    hit_lines = {d.line for d in result.diagnostics if d.line is not None}
    if not hit_lines:
        return ""
    rendered = []
    for number, text in enumerate(source.splitlines(), start=1):
        line = escape(f"{number:>3}  {text}")
        if number in hit_lines:
            rendered.append(f'<span class="tlakit-hit">{line}</span>')
        else:
            rendered.append(line)
    return '<div class="tlakit-src">' + "\n".join(rendered) + "</div>"


def _trace_html(result: CheckResult) -> str:
    trace = result.trace
    if trace is None or not trace.states:
        return ""
    variables = sorted({key for state in trace.states for key in state})
    header = "".join(f"<th>{escape(v)}</th>" for v in variables)
    rows = [f"<tr><th>step</th><th>action</th>{header}</tr>"]
    for index, state in enumerate(trace.states):
        changed = trace.delta(index)
        action = (
            "&lt;initial&gt;" if index == 0 else escape(trace.actions[index - 1].name)
        )
        cells = []
        for name in variables:
            value = escape(repr(state.get(name)))
            css = ' class="tlakit-changed"' if name in changed else ""
            cells.append(f"<td{css}>{value}</td>")
        css = ' class="tlakit-loop"' if index == trace.loop_start else ""
        rows.append(
            f"<tr{css}><td>{index + 1}</td><td>{action}</td>"
            + "".join(cells)
            + "</tr>"
        )
    table = "<table>" + "".join(rows) + "</table>"
    if trace.is_lasso:
        table += (
            '<div class="tlakit-loopnote">Lasso: the behaviour repeats from '
            f"step {trace.loop_start + 1} onwards.</div>"
        )
    return table


def result_html(result: CheckResult, source: str | None = None) -> str:
    """Render a CheckResult as self-contained HTML."""
    source = source if source is not None else result.source
    banner = "tlakit-ok" if result.ok else "tlakit-bad"
    headline = _HEADLINE.get(result.outcome, result.outcome.value.replace("_", " "))
    parts = [
        _CSS,
        '<div class="tlakit">',
        f'<div class="tlakit-banner {banner}">{escape(headline)}</div>',
        _diagnostics_html(result),
    ]
    if source is not None:
        parts.append(_source_html(source, result))
    parts.append(_trace_html(result))
    parts.append(_stats_html(result))
    parts.append("</div>")
    return "".join(parts)
