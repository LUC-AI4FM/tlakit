"""Notebook output: static HTML, plus an interactive TraceView widget.

The static views (`result_html`, `CheckResult._repr_html_`) need nothing but
the standard library and render everywhere -- a GitHub-rendered notebook, an
nbconvert export, a plain `str()`. `TraceView` is richer: it scrubs through a
trace one step at a time instead of dumping every step into one table, which
stops being usable past a handful of states.

That richness is optional. `anywidget` (M2's addition) is declared under the
`widget` extra, not as a hard dependency, so `import tlakit` and every static
render path must keep working with it absent. The import below is guarded;
when anywidget is missing, `TraceView` still exists and still holds the same
per-step data, it just has no JS half and falls back to the static
`_repr_html_` used everywhere else in this module.
"""
from __future__ import annotations

import textwrap
from html import escape
from typing import TYPE_CHECKING, Any

from .result import CheckResult, Outcome, Trace

try:
    import anywidget
    import traitlets

    HAS_ANYWIDGET = True
except ImportError:  # anywidget is the `widget` extra, not a hard dependency
    anywidget = None  # type: ignore[assignment]
    traitlets = None  # type: ignore[assignment]
    HAS_ANYWIDGET = False

if TYPE_CHECKING:  # magics imports render at call time, so only for types
    from .magics import ModuleDefined

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
.tlakit-film { display: flex; gap: 10px; overflow-x: auto; padding: 6px 2px;
               margin-top: 8px; }
.tlakit-frame { flex: 0 0 auto; text-align: center; }
.tlakit-frame svg { border: 1px solid rgba(127,127,127,.32); border-radius: 4px;
                    background: #fff; display: block; max-width: 260px;
                    height: auto; }
.tlakit-frame figcaption { font-size: 11px; opacity: .7; margin-top: 3px; }
.tlakit-note { background: rgba(90,130,220,.16); }
.tlakit-hint { opacity: .75; margin-top: 4px; }
.tlakit-fail td { background: rgba(210,60,60,.16); }
</style>
"""

#: The deadlock hint. TLC cannot tell "finished" from "stuck" -- both are a
#: state with no successor -- so a terminating algorithm trips this check every
#: time. Saying only "Deadlock reached" sends that reader hunting for a bug
#: that is not there, and says nothing to the reader whose bug is real.
#:
#: Held as plain prose so both renderers can say it. The HTML view marks the
#: code spans up on the way out (`_prose_html`); the text view wraps it
#: (`_wrap`). Two copies of this paragraph would drift the moment either the
#: flag or the wording changed.
_DEADLOCK_HINT = (
    "A state with no successor. That is a real deadlock only if this "
    "specification was not meant to terminate — TLC cannot tell the two apart. "
    "If it was, turn the check off: CHECK_DEADLOCK FALSE in the "
    "config, or check_deadlock=False from Python."
)

#: Substrings of the prose above that name code rather than English, and so
#: want <code> in the HTML view. Plain text leaves them as they are.
_CODE_SPANS = ("CHECK_DEADLOCK FALSE", "check_deadlock=False")


def _unused_actions_note(unused: list[str]) -> str:
    """The never-enabled warning, as prose. Shared for the same reason the
    deadlock hint is."""
    return (
        f"Never enabled: {', '.join(unused)}. An action that never fires "
        "means the specification may be passing for the wrong reason."
    )


def _prose_html(text: str) -> str:
    """Escape prose, then re-mark the code spans. Escaping first means the
    prose can never smuggle markup in; only the fixed spans above come back."""
    out = escape(text)
    for span in _CODE_SPANS:
        out = out.replace(escape(span), f"<code>{escape(span)}</code>")
    return out


def _wrap(label: str, text: str, indent: str = "  ", width: int = 79) -> str:
    """One labelled paragraph, wrapped to a terminal and hanging-indented
    under its own label. Long words and hyphens are left alone: these lines
    carry identifiers and paths, and breaking those makes them uncopyable."""
    prefix = f"{indent}{label}"
    return textwrap.fill(
        text,
        width=width,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )

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
    out = ""
    if bits:
        out = f'<div class="tlakit-stats">{escape(", ".join(bits))}</div>'
    unused = result.stats.unused_actions
    if unused:
        note = _prose_html(_unused_actions_note(unused))
        out += f'<div class="tlakit-banner tlakit-bad">{note}</div>'
    return out


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


def _frames_html(result: CheckResult) -> str:
    """The spec's own AnimView output, one SVG per step, as a filmstrip.

    Deliberately static: no JavaScript, so it renders in every frontend and in
    an exported notebook. The interactive scrubber belongs to the anywidget
    TraceView, not here.
    """
    if not result.frames:
        return ""
    figures = []
    for index, svg in enumerate(result.frames, start=1):
        action = ""
        trace = result.trace
        if trace is not None and 1 < index <= len(trace.states):
            action = escape(trace.actions[index - 2].name)
        caption = f"{index}" + (f" &middot; {action}" if action else "")
        # SVG.tla emits a complete <svg> document; embed it as-is.
        figures.append(
            f'<figure class="tlakit-frame">{svg}'
            f"<figcaption>{caption}</figcaption></figure>"
        )
    return '<div class="tlakit-film">' + "".join(figures) + "</div>"


def module_defined_html(module: "ModuleDefined") -> str:
    """Render the `%%tla` acknowledgement. See `magics.ModuleDefined`."""
    variables = ", ".join(module.variables) or "none declared"
    return (
        _CSS
        + '<div class="tlakit">'
        + '<div class="tlakit-banner tlakit-note">Module '
        + f"<b>{escape(module.name)}</b> stored. Variables: "
        + f"{escape(variables)}.</div>"
        + '<div class="tlakit-hint">Not parsed yet — this runner has no SANY. '
        + "Run a config cell to check it; TLC parses before it explores.</div>"
        + "</div>"
    )


def result_html(result: CheckResult, source: str | None = None) -> str:
    """Render a CheckResult as self-contained HTML."""
    source = source if source is not None else result.source
    banner = "tlakit-ok" if result.ok else "tlakit-bad"
    headline = _HEADLINE.get(result.outcome, result.outcome.value.replace("_", " "))
    parts = [
        _CSS,
        '<div class="tlakit">',
        f'<div class="tlakit-banner {banner}">{escape(headline)}</div>',
    ]
    if result.outcome is Outcome.DEADLOCK:
        hint = _prose_html(_DEADLOCK_HINT)
        parts.append(f'<div class="tlakit-hint">{hint}</div>')
    parts.append(_diagnostics_html(result))
    if source is not None:
        parts.append(_source_html(source, result))
    parts.append(_frames_html(result))
    parts.append(_trace_html(result))
    parts.append(_stats_html(result))
    parts.append("</div>")
    return "".join(parts)


def _steps_from_trace(trace: Trace) -> list[dict[str, Any]]:
    """One entry per state: the state itself, what changed, and the action
    that produced it. `trace.states` already came from JSON (`-dumpTrace
    json`), so every value here is JSON-safe -- nothing to convert.
    """
    steps: list[dict[str, Any]] = []
    for index, state in enumerate(trace.states):
        if index == 0:
            action_name = action_module = None
            action_line: int | None = None
        else:
            action = trace.actions[index - 1]
            action_name = action.name
            action_module = action.module
            action_line = action.begin_line
        steps.append(
            {
                "index": index,
                "state": dict(state),
                "changed": sorted(trace.delta(index)),
                "action": action_name,
                "module": action_module,
                "line": action_line,
            }
        )
    return steps


def _static_trace_view_html(steps: list[dict[str, Any]], variables: list[str]) -> str:
    """The non-interactive rendering of a TraceView: every step, changed
    variables highlighted, same as `result_html`'s own trace table. This is
    what `_repr_html_` gives frontends that do not run the widget's JS --
    nbconvert, GitHub's notebook renderer, `str()`.
    """
    header = "".join(f"<th>{escape(v)}</th>" for v in variables)
    rows = [f"<tr><th>step</th><th>action</th>{header}</tr>"]
    for step in steps:
        action = escape(step["action"]) if step["action"] else "&lt;initial&gt;"
        changed = set(step["changed"])
        cells = []
        for name in variables:
            value = escape(repr(step["state"].get(name)))
            css = ' class="tlakit-changed"' if name in changed else ""
            cells.append(f"<td{css}>{value}</td>")
        rows.append(
            f"<tr><td>{step['index'] + 1}</td><td>{action}</td>"
            + "".join(cells)
            + "</tr>"
        )
    return _CSS + "<table>" + "".join(rows) + "</table>"


_TRACE_VIEW_ESM = """
function render({ model, el }) {
  el.classList.add("tlakit-tracewidget");
  el.innerHTML = "";

  const header = document.createElement("div");
  header.className = "tlakit-tw-header";
  const prevBtn = document.createElement("button");
  prevBtn.textContent = "\\u2190";
  const nextBtn = document.createElement("button");
  nextBtn.textContent = "\\u2192";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "0";
  const label = document.createElement("span");
  label.className = "tlakit-tw-label";
  header.appendChild(prevBtn);
  header.appendChild(slider);
  header.appendChild(nextBtn);
  header.appendChild(label);

  const actionLine = document.createElement("div");
  actionLine.className = "tlakit-tw-action";

  const table = document.createElement("table");

  el.appendChild(header);
  el.appendChild(actionLine);
  el.appendChild(table);

  function draw() {
    const steps = model.get("steps");
    const variables = model.get("variables");
    const i = model.get("step");
    slider.max = String(Math.max(steps.length - 1, 0));
    slider.value = String(i);
    label.textContent = `step ${i + 1} / ${steps.length}`;
    prevBtn.disabled = i <= 0;
    nextBtn.disabled = i >= steps.length - 1;

    const step = steps[i];
    if (step.action) {
      const where = step.module
        ? step.module + (step.line ? ":" + step.line : "")
        : "";
      actionLine.textContent = where ? `${step.action} (${where})` : step.action;
    } else {
      actionLine.textContent = "<initial>";
    }

    const changed = new Set(step.changed || []);
    // Build rows as DOM nodes, not an HTML string: variable names and state
    // values are untrusted (LLM-generated specs, or -- via tlakit.serve --
    // arbitrary HTTP callers), so textContent is what keeps them inert.
    // JSON.stringify escapes quotes/backslashes but not `<`/`>`/`&`, so
    // interpolating it into an HTML template and assigning via innerHTML
    // would let a state value like "<img src=x onerror=alert(1)>" execute.
    while (table.firstChild) {
      table.removeChild(table.firstChild);
    }
    for (const name of variables) {
      const row = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = name;
      const td = document.createElement("td");
      if (changed.has(name)) {
        td.className = "tlakit-changed";
      }
      td.textContent = JSON.stringify(step.state[name]);
      row.appendChild(th);
      row.appendChild(td);
      table.appendChild(row);
    }
  }

  slider.addEventListener("input", () => {
    model.set("step", Number(slider.value));
    model.save_changes();
  });
  prevBtn.addEventListener("click", () => {
    model.set("step", Math.max(0, model.get("step") - 1));
    model.save_changes();
  });
  nextBtn.addEventListener("click", () => {
    const steps = model.get("steps");
    model.set("step", Math.min(steps.length - 1, model.get("step") + 1));
    model.save_changes();
  });
  model.on("change:step", draw);
  draw();
}
export default { render };
"""

_TRACE_VIEW_CSS = """
.tlakit-tracewidget { font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
.tlakit-tw-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.tlakit-tw-label { opacity: .7; }
.tlakit-tw-action { margin-bottom: 6px; font-weight: 600; }
.tlakit-tracewidget table { border-collapse: collapse; }
.tlakit-tracewidget td, .tlakit-tracewidget th { border: 1px solid rgba(127,127,127,.32);
                                                  padding: 3px 8px; text-align: left; }
.tlakit-tracewidget .tlakit-changed { background: rgba(255,180,0,.30); font-weight: 600; }
"""


def _trace_view_init(view: Any, trace: Trace) -> tuple[list[dict[str, Any]], list[str]]:
    if trace is None or not trace.states:
        raise ValueError("TraceView requires a non-empty trace")
    variables = trace.variables or sorted({k for state in trace.states for k in state})
    return _steps_from_trace(trace), variables


if HAS_ANYWIDGET:

    class TraceView(anywidget.AnyWidget):  # type: ignore[misc]
        """Scrubs through a counterexample trace one step at a time.

        Works in Jupyter, Lab, Colab, and VSCode notebooks with no separate
        labextension: the JS and CSS are inlined in `_esm`/`_css` and shipped
        with anywidget's own runtime, per anywidget's model. `step`, `steps`,
        `variables`, and `loop_start` are synced traitlets, so the current
        step set from JS (dragging the slider) is visible from Python too.
        """

        _esm = _TRACE_VIEW_ESM
        _css = _TRACE_VIEW_CSS

        step = traitlets.Int(0).tag(sync=True)
        steps = traitlets.List([]).tag(sync=True)
        variables = traitlets.List([]).tag(sync=True)
        loop_start = traitlets.Any(None).tag(sync=True)

        def __init__(self, trace: Trace, **kwargs: Any) -> None:
            steps, variables = _trace_view_init(self, trace)
            super().__init__(
                step=0,
                steps=steps,
                variables=variables,
                loop_start=trace.loop_start,
                **kwargs,
            )

        def __len__(self) -> int:
            return len(self.steps)

        @property
        def current(self) -> dict[str, Any]:
            return self.steps[self.step]

        def set_step(self, index: int) -> None:
            if not 0 <= index < len(self.steps):
                raise IndexError(index)
            self.step = index

        def _repr_html_(self) -> str:
            return _static_trace_view_html(self.steps, self.variables)

else:

    class TraceView:  # type: ignore[no-redef]
        """`TraceView` without anywidget installed: same per-step data and the
        same public API, but nothing to scrub with. `_repr_html_` renders the
        whole trace statically, same as `result_html` does.
        """

        def __init__(self, trace: Trace) -> None:
            self.steps, self.variables = _trace_view_init(self, trace)
            self.step = 0
            self.loop_start = trace.loop_start

        def __len__(self) -> int:
            return len(self.steps)

        @property
        def current(self) -> dict[str, Any]:
            return self.steps[self.step]

        def set_step(self, index: int) -> None:
            if not 0 <= index < len(self.steps):
                raise IndexError(index)
            self.step = index

        def _repr_html_(self) -> str:
            return _static_trace_view_html(self.steps, self.variables)


def trace_view(result: CheckResult) -> "TraceView | None":
    """Build a TraceView from a CheckResult's trace, or None when TLC found
    no counterexample. Mirrors `result_html(result)`'s CheckResult -> HTML
    convenience, one level up: CheckResult -> widget.
    """
    if result.trace is None or not result.trace.states:
        return None
    return TraceView(result.trace)


def stepper_view(
    source: str,
    module: str,
    config: str,
    *,
    limit: int = 100,
    **kwargs: Any,
) -> "TraceView | None":
    """Step a spec under the TLA+ Debugger and scrub the result (issue #24).

    The debugger half of `trace_view`. `trace_view` shows the counterexample a
    finished run produced; this drives `tlakit.dap` to walk the state space as
    TLC explores it, and hands the deepest behaviour it reached to the same
    widget.

    They share a widget because `dap.Step.as_trace()` returns an ordinary
    `Trace` -- the debugger reports states as TLA+ records and names frames the
    way `-dumpTrace json` names actions, so both paths land on the same object
    and nothing downstream has to know which one it came from.

    Returns None when the spec produced no states at all. Needs Java: it runs
    a real TLC.
    """
    from .dap import walk
    from .source import declared_variables

    steps = walk(source, module, config, limit=limit, **kwargs)
    if not steps:
        return None
    deepest = max(steps, key=lambda step: len(step.states))
    trace = deepest.as_trace(declared=declared_variables(source))
    if not trace.states:
        return None
    return TraceView(trace)


def trace_text(trace: Trace, indent: str = "  ") -> str:
    if not trace.states:
        return ""

    variables = sorted({key for state in trace.states for key in state})

    total_states = len(trace.states)
    if total_states <= 20:
        visible_indices = list(range(total_states))
    else:
        visible_indices = list(range(10)) + [None] + list(range(total_states - 10, total_states))

    # Calculate column widths using the visible rows + headers
    col_widths = {
        "step": len("step"),
        "action": len("action"),
    }
    for v in variables:
        col_widths[v] = len(v)

    steps_data: list[dict[str, str] | None] = []
    for index in visible_indices:
        if index is None:
            steps_data.append(None)
            continue

        state = trace.states[index]
        changed = trace.delta(index)

        step_str = str(index + 1)
        action_str = "<initial>" if index == 0 else trace.actions[index - 1].name

        col_widths["step"] = max(col_widths["step"], len(step_str))
        col_widths["action"] = max(col_widths["action"], len(action_str))

        row_data = {
            "step": step_str,
            "action": action_str,
        }

        for v in variables:
            val = state.get(v)
            cell_str = repr(val)
            if index > 0 and v in changed:
                cell_str += " *"
            row_data[v] = cell_str
            col_widths[v] = max(col_widths[v], len(cell_str))

        steps_data.append(row_data)

    # Format header
    header_parts = [
        "step".rjust(col_widths["step"]),
        "action".ljust(col_widths["action"]),
    ]
    for v in variables:
        header_parts.append(v.ljust(col_widths[v]))
    header_line = indent + "  ".join(header_parts)
    lines = [header_line.rstrip()]

    # Format rows
    for row_data in steps_data:
        if row_data is None:
            omitted = total_states - 20
            lines.append(f"{indent}... ({omitted} state{'s' if omitted > 1 else ''} omitted) ...")
            continue

        row_parts = [
            row_data["step"].rjust(col_widths["step"]),
            row_data["action"].ljust(col_widths["action"]),
        ]
        for v in variables:
            row_parts.append(row_data[v].ljust(col_widths[v]))
        line = indent + "  ".join(row_parts)
        lines.append(line.rstrip())

    # Lasso repeating note
    if trace.is_lasso:
        lines.append(f"{indent}Lasso: the behaviour repeats from step {trace.loop_start + 1} onwards.")

    return "\n".join(lines)


def _source_text(source: str, result: CheckResult, indent: str = "  ") -> str:
    hit_lines = {d.line for d in result.diagnostics if d.line is not None}
    if not hit_lines:
        return ""
    rendered = []
    for number, text in enumerate(source.splitlines(), start=1):
        marker = ">" if number in hit_lines else " "
        rendered.append(f"{indent}{marker} {number:>3}  {text}")
    return "\n".join(rendered)


def result_text(result: CheckResult) -> str:
    # 1. Headline / Outcome name
    status_line = result.outcome.name

    # Stats
    s = result.stats
    bits = []
    if s.distinct is not None:
        bits.append(f"{s.distinct} state{'s' if s.distinct != 1 else ''} explored")
    elif s.generated is not None:
        bits.append(f"{s.generated} state{'s' if s.generated != 1 else ''} generated")

    if s.depth is not None:
        bits.append(f"depth {s.depth}")

    if bits:
        status_line += f"  \u2014  {', '.join(bits)}"

    parts = [status_line]

    # 2. Diagnostics. Every one of these is prose and so gets wrapped -- an
    # unwrapped paragraph here is the exact thing __str__ exists to fix.
    diagnostics_lines = []
    for d in result.diagnostics:
        diagnostics_lines.append(_wrap(f"{d.severity.value}: ", str(d)))

    if result.outcome is Outcome.DEADLOCK:
        diagnostics_lines.append(_wrap("Note: ", _DEADLOCK_HINT))

    unused = result.stats.unused_actions
    if unused:
        diagnostics_lines.append(_wrap("warning: ", _unused_actions_note(unused)))

    if diagnostics_lines:
        parts.append("\n".join(diagnostics_lines))

    # 3. Source code snippet (if any)
    if result.source is not None:
        src_text = _source_text(result.source, result)
        if src_text:
            parts.append(src_text)

    # 4. Trace (if any)
    if result.trace is not None and result.trace.states:
        parts.append(trace_text(result.trace))

    return "\n\n".join(p for p in parts if p)


# --- sweeps ---------------------------------------------------------------
#
# A `SweepResult` holds one `CheckResult` per grid point, so its dataclass
# repr nests the line #61 already found too wide, once per configuration --
# tens of kilobytes for a five-point sweep carrying TLC's full stdout. The one
# thing the reader wants is the shape of the grid and where it broke (#81).
#
# Both views render `SweepResult.rows()`, which is also what `to_dataframe`
# wraps. Three views picking their own columns would be three things to keep
# in step.

#: Configurations shown before the printed grid is truncated. The same limit
#: `trace_text` uses for states, for the same reason; two numbers would be two
#: things to remember.
MAX_SWEEP_ROWS = 20

#: Column names that read better in a table than as dict keys.
_SWEEP_LABELS = {"trace_len": "trace"}


def _sweep_columns(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for row in rows:
        for name in row:
            if name not in columns:
                columns.append(name)
    return columns


def _sweep_cell(name: str, value: Any) -> str:
    """One cell. A dash rather than `None` or a bare `0` for "there was none":
    a trace length of 0 means no counterexample, not a counterexample of no
    steps, and TLC leaves stats unset when it never got that far."""
    if value is None or (name == "trace_len" and not value):
        return "-"
    if name == "outcome":
        # `rows()` carries `Outcome.value` because that is what a DataFrame
        # wants; every other rendered view in this module shows `Outcome.name`,
        # and the names are exactly the values upper-cased.
        return str(value).upper()
    return str(value)


def _sweep_headline(sweep: Any) -> str:
    total = len(sweep.runs)
    if not total:
        return "SweepResult — no configurations"
    failed = len(sweep.failures)
    plural = "configuration" if total == 1 else "configurations"
    return f"SweepResult — {total} {plural}, " + (
        f"{failed} failed" if failed else "all passed"
    )


def _sweep_failure_note(sweep: Any) -> str:
    """Where it broke, without inlining the counterexample.

    `first_failure()` is what the README leads with, and one trace per failing
    point is the wall of text this whole view exists to remove. Naming the row
    and pointing at the call is the same information at a hundredth the size.
    """
    first = sweep.first_failure()
    if first is None:
        return ""
    where = first.label or "the only configuration"
    return _wrap(
        "First failure: ",
        f"{where} — sweep.first_failure().result has the trace.",
    )


def sweep_text(sweep: Any) -> str:
    """A `SweepResult` as a grid: one row per configuration, failures marked."""
    rows = sweep.rows()
    headline = _sweep_headline(sweep)
    if not rows:
        return headline

    columns = _sweep_columns(rows)
    header = [_SWEEP_LABELS.get(name, name) for name in columns]
    cells = [[_sweep_cell(name, row.get(name)) for name in columns] for row in rows]
    widths = [
        max(len(header[i]), max(len(row[i]) for row in cells))
        for i in range(len(columns))
    ]

    def line(marker: str, values: list[str]) -> str:
        rendered = [
            value.ljust(width) if name == "outcome" else value.rjust(width)
            for name, value, width in zip(columns, values, widths)
        ]
        return (marker + "  ".join(rendered)).rstrip()

    total = len(rows)
    if total <= MAX_SWEEP_ROWS:
        visible: list[int | None] = list(range(total))
    else:
        half = MAX_SWEEP_ROWS // 2
        visible = list(range(half)) + [None] + list(range(total - half, total))

    lines = [line("  ", header)]
    for index in visible:
        if index is None:
            omitted = total - MAX_SWEEP_ROWS
            lines.append(
                f"  ... ({omitted} configuration{'s' if omitted != 1 else ''} "
                "omitted) ..."
            )
            continue
        failed = not sweep.runs[index].result.ok
        lines.append(line("! " if failed else "  ", cells[index]))

    parts = [headline, "\n".join(lines), _sweep_failure_note(sweep)]
    return "\n\n".join(part for part in parts if part)


def sweep_html(sweep: Any) -> str:
    """The same grid for a notebook, and without pandas.

    Not truncated the way `sweep_text` is: a browser scrolls a table, and the
    reason to cut the printed form -- that it scrolls a terminal past the
    headline -- does not apply.
    """
    rows = sweep.rows()
    banner = "tlakit-ok" if sweep.ok else "tlakit-bad"
    parts = [
        _CSS,
        '<div class="tlakit">',
        f'<div class="tlakit-banner {banner}">'
        f"{escape(_sweep_headline(sweep))}</div>",
    ]
    if rows:
        columns = _sweep_columns(rows)
        head = "".join(
            f"<th>{escape(_SWEEP_LABELS.get(name, name))}</th>" for name in columns
        )
        body = []
        for index, row in enumerate(rows):
            css = "" if sweep.runs[index].result.ok else ' class="tlakit-fail"'
            cells = "".join(
                f"<td>{escape(_sweep_cell(name, row.get(name)))}</td>"
                for name in columns
            )
            body.append(f"<tr{css}>{cells}</tr>")
        parts.append(f"<table><tr>{head}</tr>{''.join(body)}</table>")

    first = sweep.first_failure()
    if first is not None:
        where = escape(first.label or "the only configuration")
        parts.append(
            f'<div class="tlakit-hint">First failure: {where} — '
            "<code>sweep.first_failure().result</code> has the trace.</div>"
        )
    parts.append("</div>")
    return "".join(parts)
