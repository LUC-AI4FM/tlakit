"""Load TLC's `-dumpTrace json` output.

The shape is TLC's, not ours:

    {"vars": [...],
     "counterexample": {
        "state":  [[id, {vars}], ...],
        "action": [[[id, {vars}], {"name":..., "location":{...}}, [id2, {vars}]], ...]}}

Action edges are keyed by *destination state id*, not by position, so they are
resolved by id here rather than zipped. This module renames TLC's parts; it does
not reinterpret them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .result import Action, Trace

UNKNOWN_ACTION = "<unknown>"


def _action_from(entry: dict[str, Any]) -> Action:
    loc = entry.get("location") or {}
    return Action(
        name=entry.get("name", UNKNOWN_ACTION),
        module=loc.get("module"),
        begin_line=loc.get("beginLine"),
        begin_column=loc.get("beginColumn"),
        end_line=loc.get("endLine"),
        end_column=loc.get("endColumn"),
    )


def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
    """TLC wraps the trace under `counterexample`; tooling often unwraps it
    before saving. Accept either rather than silently returning nothing."""
    inner = data.get("counterexample")
    if isinstance(inner, dict):
        return inner
    return data if ("state" in data or "action" in data) else {}


def trace_from_json(
    data: dict[str, Any], declared: list[str] | None = None
) -> Trace:
    """Build a Trace from TLC's JSON, wrapped or not.

    `declared` is the module's VARIABLES; pass it so alias fields can be told
    apart from state. TLC's own `vars` key cannot do this — under an ALIAS it
    holds the alias-expanded record.
    """
    counterexample = _unwrap(data)

    action_by_destination: dict[Any, Action] = {}
    for edge in counterexample.get("action", []):
        if not isinstance(edge, list) or len(edge) < 3:
            continue
        entry, destination = edge[1], edge[2]
        if not isinstance(entry, dict):
            continue
        if not isinstance(destination, list) or not destination:
            continue
        action_by_destination[destination[0]] = _action_from(entry)

    state_ids: list[Any] = []
    states: list[dict[str, Any]] = []
    for entry in counterexample.get("state", []):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        state_ids.append(entry[0])
        states.append(dict(entry[1] or {}))

    actions = [
        action_by_destination.get(state_id, Action(name=UNKNOWN_ACTION))
        for state_id in state_ids[1:]
    ]
    return Trace(
        states=states,
        actions=actions,
        state_ids=state_ids,
        declared=list(declared or []),
    )


def load_trace(path: Path, declared: list[str] | None = None) -> Trace | None:
    """Return the trace, or None when TLC wrote no counterexample."""
    path = Path(path)
    if not path.is_file():
        return None
    trace = trace_from_json(json.loads(path.read_text(encoding="utf-8")), declared=declared)
    return trace if trace.states else None


# --- text-mode fallback -----------------------------------------------------
#
# `-dumpTrace json` is what tlakit itself always passes (see cli.py), so this
# exists for reading a TLC log produced elsewhere -- a foreign run with no
# dump alongside it, which is Specula's situation. It parses TLC's own
# printed counterexample:
#
#     State 1: <Initial predicate>
#     x = 0
#
#     State 2: <Next line 5, col 9 to line 5, col 18 of module Spike>
#     x = 1
#
# A state with more than one variable is printed with a leading "/\ " on
# each line; a state with exactly one is not. Values are parsed by a small
# recursive-descent reader for TLC's own value syntax (records, tuples,
# sets, strings, model values) -- still just naming TLC's output, not
# reinterpreting TLA+.

_STATE_BOUNDARY = re.compile(
    r"^(?:State (?P<num>\d+): <(?P<header>.*?)>|Back to state \d+:.*)[ \t]*$",
    re.M,
)

_VAR_LINE = re.compile(r"^(?:/\\\s+)?([A-Za-z_]\w*) = (.*)$")

_ACTION_LOC = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)"
    r"(?:\s+line (?P<bline>\d+), col (?P<bcol>\d+) to "
    r"line (?P<eline>\d+), col (?P<ecol>\d+) of module (?P<module>\w+))?$"
)

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<lbrace>\{)
      | (?P<rbrace>\})
      | (?P<lseq><<)
      | (?P<rseq>>>)
      | (?P<lrec>\[)
      | (?P<rrec>\])
      | (?P<arrow>\|->)
      | (?P<comma>,)
      | (?P<string>"(?:\\.|[^"\\])*")
      | (?P<bool>TRUE|FALSE)
      | (?P<number>-?\d+)
      | (?P<ident>[A-Za-z_]\w*)
    )
    """,
    re.VERBOSE,
)


class TlaValueError(ValueError):
    """Raised when a printed TLA+ value cannot be parsed."""


def _action_from_header(header: str) -> Action:
    m = _ACTION_LOC.match(header.strip())
    if not m:
        return Action(name=header.strip() or UNKNOWN_ACTION)
    g = m.groupdict()
    return Action(
        name=g["name"],
        module=g.get("module"),
        begin_line=int(g["bline"]) if g.get("bline") else None,
        begin_column=int(g["bcol"]) if g.get("bcol") else None,
        end_line=int(g["eline"]) if g.get("eline") else None,
        end_column=int(g["ecol"]) if g.get("ecol") else None,
    )


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    length = len(text)
    while pos < length:
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            raise TlaValueError(f"cannot tokenize {text[pos:pos + 20]!r}")
        pos = m.end()
        tokens.append((m.lastgroup, m.group(m.lastgroup)))
    return tokens


def _parse_seq(tokens: list[tuple[str, str]], i: int, close: str) -> tuple[list[Any], int]:
    items: list[Any] = []
    if i < len(tokens) and tokens[i][0] == close:
        return items, i + 1
    while True:
        value, i = _parse_token(tokens, i)
        items.append(value)
        if i < len(tokens) and tokens[i][0] == "comma":
            i += 1
            continue
        break
    if i >= len(tokens) or tokens[i][0] != close:
        raise TlaValueError(f"expected closing {close!r}")
    return items, i + 1


def _parse_record(tokens: list[tuple[str, str]], i: int) -> tuple[dict[str, Any], int]:
    fields: dict[str, Any] = {}
    if i < len(tokens) and tokens[i][0] == "rrec":
        return fields, i + 1
    while True:
        if i >= len(tokens) or tokens[i][0] != "ident":
            raise TlaValueError("expected a record field name")
        name = tokens[i][1]
        i += 1
        if i >= len(tokens) or tokens[i][0] != "arrow":
            raise TlaValueError("expected |->")
        i += 1
        value, i = _parse_token(tokens, i)
        fields[name] = value
        if i < len(tokens) and tokens[i][0] == "comma":
            i += 1
            continue
        break
    if i >= len(tokens) or tokens[i][0] != "rrec":
        raise TlaValueError("expected ]")
    return fields, i + 1


def _parse_token(tokens: list[tuple[str, str]], i: int) -> tuple[Any, int]:
    if i >= len(tokens):
        raise TlaValueError("unexpected end of value")
    kind, text = tokens[i]
    if kind == "number":
        return int(text), i + 1
    if kind == "bool":
        return text == "TRUE", i + 1
    if kind == "string":
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\"), i + 1
    if kind == "ident":
        return text, i + 1
    if kind == "lseq":
        return _parse_seq(tokens, i + 1, "rseq")
    if kind == "lbrace":
        return _parse_seq(tokens, i + 1, "rbrace")
    if kind == "lrec":
        return _parse_record(tokens, i + 1)
    raise TlaValueError(f"unexpected token {kind!r}")


def parse_tla_value(text: str) -> Any:
    """Parse one printed TLA+ value: an int, bool, string, model value,
    record, or tuple/set (both come back as a list, matching how
    `-dumpTrace json` already represents them).

    Raises `TlaValueError` if `text` is not a single, complete value.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise TlaValueError("empty value")
    value, i = _parse_token(tokens, 0)
    if i != len(tokens):
        raise TlaValueError(f"trailing input after value: {text!r}")
    return value


def _parse_value_lenient(text: str) -> Any:
    """As `parse_tla_value`, but falls back to the raw text on failure.

    A foreign log is not guaranteed to only contain value syntax this reader
    understands; failing the whole trace over one unparsed field would throw
    away everything else in it.
    """
    try:
        return parse_tla_value(text)
    except TlaValueError:
        return text.strip()


def _parse_state_body(body: str) -> dict[str, Any]:
    raw: dict[str, str] = {}
    order: list[str] = []
    for line in body.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        m = _VAR_LINE.match(line)
        if m:
            name, value_text = m.group(1), m.group(2)
            order.append(name)
            raw[name] = value_text
        elif order:
            # A continuation line of a multi-line record/tuple value.
            raw[order[-1]] += " " + line.strip()
    return {name: _parse_value_lenient(text) for name, text in raw.items()}


def parse_text_trace(stdout: str, declared: list[str] | None = None) -> Trace | None:
    """Read TLC's *printed* counterexample out of `stdout`.

    Returns None when `stdout` carries no `State N:` block at all -- a
    successful run, or one that failed before model checking began.
    """
    boundaries = list(_STATE_BOUNDARY.finditer(stdout))
    states: list[dict[str, Any]] = []
    state_ids: list[Any] = []
    headers: list[str] = []
    for i, b in enumerate(boundaries):
        if b.group("num") is None:
            continue
        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(stdout)
        body = stdout[b.end():end]
        # A blank line ends the state block even when it is the last one,
        # where `end` otherwise runs to the end of TLC's whole log --
        # statistics and "Trace exploration spec path" would else be read as
        # more of the last variable's value.
        blank = body.find("\n\n")
        if blank != -1:
            body = body[:blank]
        states.append(_parse_state_body(body))
        state_ids.append(int(b.group("num")))
        headers.append(b.group("header") or "")

    if not states:
        return None

    actions = [_action_from_header(h) for h in headers[1:]]
    return Trace(
        states=states,
        actions=actions,
        state_ids=state_ids,
        declared=list(declared or []),
    )
