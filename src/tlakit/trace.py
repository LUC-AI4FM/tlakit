"""Load TLC's `-dumpTrace json` output.

The shape is TLC's, not ours:

    {"vars": [...],
     "counterexample": {
        "state":  [[n, {vars}], ...],
        "action": [[[n, {vars}], {"name":..., "location":{...}}, [n2, {vars}]], ...]}}

This module renames its parts; it does not reinterpret them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .result import Action, Trace


def _action_from(entry: dict[str, Any]) -> Action:
    loc = entry.get("location") or {}
    return Action(
        name=entry.get("name", "<unknown>"),
        module=loc.get("module"),
        begin_line=loc.get("beginLine"),
        begin_column=loc.get("beginColumn"),
        end_line=loc.get("endLine"),
        end_column=loc.get("endColumn"),
    )


def trace_from_json(data: dict[str, Any]) -> Trace:
    counterexample = data.get("counterexample") or {}
    states = [state for _, state in counterexample.get("state", [])]
    actions = [_action_from(edge[1]) for edge in counterexample.get("action", [])]
    return Trace(states=states, actions=actions)


def load_trace(path: Path) -> Trace | None:
    """Return the trace, or None when TLC wrote no counterexample."""
    path = Path(path)
    if not path.is_file():
        return None
    trace = trace_from_json(json.loads(path.read_text()))
    return trace if trace.states else None
