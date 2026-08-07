import json
from pathlib import Path

from tlakit.trace import load_trace, trace_from_json

FIX = Path(__file__).parent / "fixtures"


def fixture() -> dict:
    return json.loads((FIX / "trace_invariant.json").read_text())


def test_states_are_ordered_and_complete():
    t = trace_from_json(fixture())
    assert [s["x"] for s in t.states] == [0, 1, 2, 3]


def test_boolean_values_survive_as_python_bools():
    t = trace_from_json(fixture())
    assert t.states[0]["flag"] is False


def test_actions_carry_name_and_location():
    t = trace_from_json(fixture())
    assert len(t.actions) == 3
    a = t.actions[0]
    assert a.name == "Bump"
    assert a.module == "Spike"
    assert a.begin_line is not None and a.begin_column is not None


def test_delta_works_on_a_loaded_trace():
    t = trace_from_json(fixture())
    assert t.delta(1) == frozenset({"x"})


def test_load_trace_returns_none_when_absent(tmp_path):
    assert load_trace(tmp_path / "nothing.json") is None


def test_empty_counterexample_yields_none(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"vars": ["x"], "counterexample": {}}))
    assert load_trace(p) is None


def test_missing_location_does_not_crash():
    data = {
        "vars": ["x"],
        "counterexample": {
            "state": [[1, {"x": 0}], [2, {"x": 1}]],
            "action": [[[1, {"x": 0}], {"name": "N"}, [2, {"x": 1}]]],
        },
    }
    t = trace_from_json(data)
    assert t.actions[0].name == "N"
    assert t.actions[0].module is None
