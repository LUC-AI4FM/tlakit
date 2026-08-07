"""Trace loading tolerance and the query surface a consumer needs.

The unwrapped shape here matches what real tooling stores on disk: TLC's
`-dumpTrace json` writes `{"vars": ..., "counterexample": {...}}`, but tools
that unwrap it before saving leave the bare `{"action": ..., "state": ...}`.
Both must load.
"""
import json

import pytest

from tlakit.trace import load_trace, trace_from_json

NESTED = {
    "action": [
        [
            [1, {"progress": {"s1": "Probe", "s2": "Probe"}, "round": 0}],
            {
                "name": "Advance",
                "location": {
                    "beginLine": 12,
                    "beginColumn": 3,
                    "endLine": 12,
                    "endColumn": 30,
                    "module": "Consensus",
                },
            },
            [2, {"progress": {"s1": "Commit", "s2": "Probe"}, "round": 0}],
        ],
        [
            [2, {"progress": {"s1": "Commit", "s2": "Probe"}, "round": 0}],
            {"name": "Tick", "location": {"module": "Consensus"}},
            [3, {"progress": {"s1": "Commit", "s2": "Probe"}, "round": 1}],
        ],
    ],
    "state": [
        [1, {"progress": {"s1": "Probe", "s2": "Probe"}, "round": 0}],
        [2, {"progress": {"s1": "Commit", "s2": "Probe"}, "round": 0}],
        [3, {"progress": {"s1": "Commit", "s2": "Probe"}, "round": 1}],
    ],
}

WRAPPED = {"vars": ["progress", "round"], "counterexample": NESTED}


def test_unwrapped_counterexample_loads():
    t = trace_from_json(NESTED)
    assert len(t) == 3
    assert [a.name for a in t.actions] == ["Advance", "Tick"]


def test_wrapped_counterexample_loads_identically():
    assert trace_from_json(WRAPPED).states == trace_from_json(NESTED).states


def test_load_trace_accepts_unwrapped_file(tmp_path):
    p = tmp_path / "trace.json"
    p.write_text(json.dumps(NESTED))
    assert len(load_trace(p)) == 3


def test_indexing_is_zero_based_and_supports_negatives():
    t = trace_from_json(NESTED)
    assert t[0]["round"] == 0
    assert t[-1]["round"] == 1


def test_variables_lists_every_name():
    assert trace_from_json(NESTED).variables == ["progress", "round"]


def test_value_at_walks_a_nested_path():
    t = trace_from_json(NESTED)
    assert t.value_at(0, "progress.s1") == "Probe"
    assert t.value_at(1, "progress.s1") == "Commit"
    assert t.value_at(0, "round") == 0


def test_value_at_rejects_an_unknown_path():
    with pytest.raises(KeyError):
        trace_from_json(NESTED).value_at(0, "progress.nope")


def test_changes_lists_the_steps_where_a_variable_moved():
    t = trace_from_json(NESTED)
    assert t.changes("progress") == [1]
    assert t.changes("round") == [2]


def test_changes_of_a_constant_variable_is_empty():
    t = trace_from_json(
        {"state": [[1, {"x": 1}], [2, {"x": 1}]],
         "action": [[[1, {"x": 1}], {"name": "N"}, [2, {"x": 1}]]]}
    )
    assert t.changes("x") == []


def test_compare_reports_before_and_after_for_differing_variables():
    t = trace_from_json(NESTED)
    assert t.compare(1, 2) == {"round": (0, 1)}


def test_compare_of_identical_states_is_empty():
    t = trace_from_json(NESTED)
    assert t.compare(0, 0) == {}
