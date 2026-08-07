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
    trace = trace_from_json(json.loads(path.read_text()), declared=declared)
    return trace if trace.states else None
