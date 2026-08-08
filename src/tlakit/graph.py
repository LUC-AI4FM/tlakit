"""Parse TLC's `-dump dot` state graph.

TLC writes the reachable state graph as Graphviz DOT. Node ids are state
fingerprints (signed 64-bit, so often negative), labels hold the variable
assignments in TLA+ conjunction form, and with `actionlabels` each edge carries
the action that produced it.

Nothing here renders anything. Layout is the client's job; this module only
turns TLC's text into a graph.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: ``123 [label="/\\ x = 0\n/\\ y = 1",style = filled]``
_NODE = re.compile(
    r'^(-?\d+)\s+\[label="((?:[^"\\]|\\.)*)"(?P<rest>[^\]]*)\]', re.MULTILINE
)
#: ``123 -> 456 [label="Next(\"a\")",color="black"...];``
_EDGE = re.compile(
    r'^(-?\d+)\s*->\s*(-?\d+)\s*\[label="((?:[^"\\]|\\.)*)"', re.MULTILINE
)
#: A line of a state label. TLC writes a conjunction list when a spec has
#: several variables (``/\ counter = 0``) but drops the ``/\`` entirely when
#: there is only one (``x = 0``) -- measured 2026-08-07.
_ASSIGNMENT = re.compile(r"^(?:/\\\s*)?(\w+)\s*=\s*(.*)$")


def _unescape(text: str) -> str:
    """DOT escapes; TLC writes \\n for line breaks and \\" for quotes."""
    return (
        text.replace("\\n", "\n")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def parse_state_label(label: str) -> dict[str, str]:
    """Turn a node label into `{variable: value}` with values left as text.

    Values stay as TLA+ source rather than being coerced. A record renders as
    `[a |-> 0]`, and re-parsing that into Python would be inventing a second,
    worse version of what `-dumpTrace json` already does properly.
    """
    variables: dict[str, str] = {}
    current: str | None = None
    for line in _unescape(label).splitlines():
        match = _ASSIGNMENT.match(line.strip())
        if match:
            current = match.group(1)
            variables[current] = match.group(2).strip()
        elif current is not None and line.strip():
            # A value wrapped across lines.
            variables[current] += " " + line.strip()
    return variables


@dataclass(frozen=True)
class Node:
    id: str
    variables: dict[str, str]
    initial: bool = False


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    action: str


@dataclass(frozen=True)
class StateGraph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    #: True when the graph was cut short by a node limit.
    truncated: bool = False

    def __len__(self) -> int:
        return len(self.nodes)

    @property
    def variables(self) -> list[str]:
        seen: list[str] = []
        for node in self.nodes:
            for name in node.variables:
                if name not in seen:
                    seen.append(name)
        return seen

    def to_json(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"id": n.id, "vars": n.variables, "initial": n.initial}
                for n in self.nodes
            ],
            "edges": [
                {"from": e.source, "to": e.target, "action": e.action}
                for e in self.edges
            ],
            "variables": self.variables,
            "truncated": self.truncated,
        }


def parse_dot(text: str, max_nodes: int | None = None) -> StateGraph:
    """Build a StateGraph from TLC's DOT output.

    `max_nodes` truncates rather than refusing: a partial graph of a large state
    space is still worth looking at, and the flag says it is partial.
    """
    nodes: dict[str, Node] = {}
    truncated = False

    for match in _NODE.finditer(text):
        node_id = match.group(1)
        if node_id in nodes:
            continue
        if max_nodes is not None and len(nodes) >= max_nodes:
            truncated = True
            break
        nodes[node_id] = Node(
            id=node_id,
            variables=parse_state_label(match.group(2)),
            # TLC fills the initial states and leaves them without a tooltip.
            initial="style = filled" in (match.group("rest") or ""),
        )

    edges = [
        Edge(source=src, target=dst, action=_unescape(action))
        for src, dst, action in _EDGE.findall(text)
        # Drop edges to states that were cut, so the client never has a
        # dangling reference.
        if src in nodes and dst in nodes
    ]
    return StateGraph(nodes=list(nodes.values()), edges=edges, truncated=truncated)


def path_through(graph: StateGraph, trace_states: list[dict[str, Any]]) -> list[str]:
    """Node ids matching a counterexample, so a client can highlight it.

    The trace and the graph come from different TLC outputs and share no
    identifiers, so states are matched on their variable values. Graph labels
    are TLA+ source and trace values are JSON, so comparison is on a normalized
    rendering of both.
    """
    if not graph.nodes or not trace_states:
        return []
    index: dict[tuple[tuple[str, str], ...], str] = {}
    for node in graph.nodes:
        index.setdefault(_signature(node.variables), node.id)

    found: list[str] = []
    for state in trace_states:
        key = _signature({k: _render(v) for k, v in state.items()})
        node_id = index.get(key)
        if node_id is None:
            return []  # a partial highlight would mislead more than none
        found.append(node_id)
    return found


def _signature(variables: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, _normalize(v)) for k, v in variables.items()))


def _normalize(value: str) -> str:
    """Collapse whitespace so TLA+ source and rendered JSON can be compared."""
    return re.sub(r"\s+", "", str(value))


def _render(value: Any) -> str:
    """Render a JSON trace value the way TLC prints it in a DOT label."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, dict):
        body = ", ".join(f"{k} |-> {_render(v)}" for k, v in value.items())
        return f"[{body}]"
    if isinstance(value, list):
        return "<<" + ", ".join(_render(v) for v in value) + ">>"
    return str(value)
