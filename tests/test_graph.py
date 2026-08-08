"""Parsing TLC's -dump dot state graph."""
import shutil

import pytest

from tlakit.graph import Edge, StateGraph, parse_dot, parse_state_label, path_through

# A real capture, trimmed: two initial-state edges from the LostUpdate spec.
DOT = r'''strict digraph DiskGraph {
node [shape=box,style=rounded]
nodesep=0.35;
subgraph cluster_graph {
color="white";
2078058696731167495 [label="/\\ counter = 0\n/\\ pc = [a |-> \"read\", b |-> \"read\"]",style = filled]
2078058696731167495 -> 5759866548064758273 [label="Read(\"a\")",color="black",fontcolor="black"];
5759866548064758273 [label="/\\ counter = 0\n/\\ pc = [a |-> \"write\", b |-> \"read\"]",tooltip="x"];
2078058696731167495 -> -5803053462393794555 [label="Read(\"b\")",color="black",fontcolor="black"];
-5803053462393794555 [label="/\\ counter = 0\n/\\ pc = [a |-> \"read\", b |-> \"write\"]",tooltip="x"];
5759866548064758273 -> 1436861393514583738 [label="Write(\"a\")",color="black",fontcolor="black"];
1436861393514583738 [label="/\\ counter = 1\n/\\ pc = [a |-> \"done\", b |-> \"read\"]",tooltip="x"];
}
}
'''


def test_nodes_and_edges_are_parsed():
    g = parse_dot(DOT)
    assert len(g.nodes) == 4
    assert len(g.edges) == 3


def test_negative_fingerprints_are_handled():
    """TLC node ids are signed 64-bit, so many are negative."""
    g = parse_dot(DOT)
    assert any(n.id.startswith("-") for n in g.nodes)
    assert any(e.target.startswith("-") for e in g.edges)


def test_the_initial_state_is_flagged():
    g = parse_dot(DOT)
    initial = [n for n in g.nodes if n.initial]
    assert len(initial) == 1
    assert initial[0].variables["counter"] == "0"


def test_actions_are_unescaped():
    g = parse_dot(DOT)
    assert {e.action for e in g.edges} == {'Read("a")', 'Read("b")', 'Write("a")'}


def test_a_single_variable_label_has_no_conjunction_prefix():
    """TLC drops the `/\\` when a spec has exactly one variable."""
    assert parse_state_label("x = 0") == {"x": "0"}
    assert parse_state_label(r"x = [a |-> \"read\"]")["x"] == '[a |-> "read"]'


def test_state_labels_become_variables():
    vars = parse_state_label(r'/\\ counter = 0\n/\\ pc = [a |-> \"read\"]')
    assert vars["counter"] == "0"
    assert vars["pc"] == '[a |-> "read"]'


def test_variable_order_is_stable():
    assert parse_dot(DOT).variables == ["counter", "pc"]


def test_truncation_drops_dangling_edges():
    """A client must never receive an edge pointing at a node it did not get."""
    g = parse_dot(DOT, max_nodes=2)
    assert g.truncated is True
    assert len(g.nodes) == 2
    ids = {n.id for n in g.nodes}
    assert all(e.source in ids and e.target in ids for e in g.edges)


def test_an_empty_dump_is_an_empty_graph():
    g = parse_dot("strict digraph DiskGraph {\n}\n")
    assert len(g) == 0 and g.edges == [] and g.truncated is False


def test_to_json_shape():
    payload = parse_dot(DOT).to_json()
    assert set(payload) == {"nodes", "edges", "variables", "truncated"}
    assert set(payload["edges"][0]) == {"from", "to", "action"}


# --- matching a counterexample onto the graph ----------------------------


def test_a_trace_is_located_in_the_graph():
    """The trace and the graph share no ids, so matching is on values."""
    g = parse_dot(DOT)
    trace = [
        {"counter": 0, "pc": {"a": "read", "b": "read"}},
        {"counter": 0, "pc": {"a": "write", "b": "read"}},
        {"counter": 1, "pc": {"a": "done", "b": "read"}},
    ]
    path = path_through(g, trace)
    assert path == [
        "2078058696731167495",
        "5759866548064758273",
        "1436861393514583738",
    ]


def test_an_unmatchable_trace_yields_no_path():
    """A partial highlight would mislead more than none at all."""
    g = parse_dot(DOT)
    assert path_through(g, [{"counter": 99, "pc": {}}]) == []


def test_no_trace_or_no_graph_is_empty():
    assert path_through(parse_dot(DOT), []) == []
    assert path_through(StateGraph(), [{"counter": 0}]) == []


# --- end to end ----------------------------------------------------------

SPEC = """---- MODULE G ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == IF x < 3 THEN x' = x + 1 ELSE x' = x
Spec == Init /\\ [][Next]_x
Inv == x < 3
====
"""


@pytest.mark.java
def test_a_real_run_produces_a_graph_matching_its_trace():
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    result = tlakit.Spec(source=SPEC, name="G").check(invariants=["Inv"], graph=True)
    assert result.graph is not None
    assert len(result.graph) >= 4                 # x = 0,1,2,3
    assert result.graph.variables == ["x"]
    assert any(n.initial for n in result.graph.nodes)

    path = path_through(result.graph, result.trace.states)
    assert len(path) == len(result.trace.states), "the trace should lie in the graph"


@pytest.mark.java
def test_no_graph_unless_asked():
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    assert tlakit.Spec(source=SPEC, name="G").check(invariants=["Inv"]).graph is None
