"""Issue #6: alias fields must not masquerade as state variables."""
import pytest

from tlakit.source import declared_variables
from tlakit.trace import trace_from_json

SINGLE = "---- MODULE M ----\nVARIABLE x\nInit == x = 0\n===="
PLURAL = "---- MODULE M ----\nVARIABLES x, y\nInit == x = 0\n===="
MULTILINE = """---- MODULE M ----
VARIABLES
    door,
    radiation,
    timeRemaining

Init == door = "closed"
===="""
REPEATED = """---- MODULE M ----
VARIABLE x
VARIABLE y
Init == x = 0
===="""
WITH_COMMENT = '---- MODULE M ----\nVARIABLE x  \\* the counter\nInit == x = 0\n===='


def test_single_declaration():
    assert declared_variables(SINGLE) == ["x"]


def test_plural_declaration():
    assert declared_variables(PLURAL) == ["x", "y"]


def test_multiline_declaration():
    assert declared_variables(MULTILINE) == ["door", "radiation", "timeRemaining"]


def test_repeated_declarations_accumulate():
    assert declared_variables(REPEATED) == ["x", "y"]


def test_trailing_comment_does_not_leak_into_the_names():
    assert declared_variables(WITH_COMMENT) == ["x"]


def test_declaration_does_not_swallow_the_next_definition():
    assert declared_variables(PLURAL) == ["x", "y"]
    assert "Init" not in declared_variables(MULTILINE)


def test_no_declarations_yields_empty():
    assert declared_variables("---- MODULE M ----\nInit == TRUE\n====") == []


# --- Trace integration -------------------------------------------------

ALIASED = {
    "vars": ["x", "y", "doubled", "tag"],
    "counterexample": {
        "state": [
            [1, {"x": 0, "y": True, "doubled": 0, "tag": "extra"}],
            [2, {"x": 1, "y": False, "doubled": 2, "tag": "extra"}],
        ],
        "action": [
            [
                [1, {"x": 0}],
                {"name": "Next", "location": {"module": "A"}},
                [2, {"x": 1}],
            ]
        ],
    },
}
ALIAS_SOURCE = "---- MODULE A ----\nVARIABLES x, y\nInit == x = 0\n===="


def test_without_declarations_every_key_is_a_variable():
    t = trace_from_json(ALIASED)
    assert t.variables == ["doubled", "tag", "x", "y"]
    assert t.aliases == []


def test_declared_variables_exclude_alias_fields():
    t = trace_from_json(ALIASED, declared=declared_variables(ALIAS_SOURCE))
    assert t.variables == ["x", "y"]
    assert t.aliases == ["doubled", "tag"]


def test_delta_ignores_alias_fields():
    t = trace_from_json(ALIASED, declared=["x", "y"])
    assert t.delta(1) == frozenset({"x", "y"})
    assert t.delta(1, include_aliases=True) == frozenset({"x", "y", "doubled"})


def test_compare_ignores_alias_fields():
    t = trace_from_json(ALIASED, declared=["x", "y"])
    assert set(t.compare(0, 1)) == {"x", "y"}
    assert "doubled" in t.compare(0, 1, include_aliases=True)


def test_alias_values_stay_reachable():
    t = trace_from_json(ALIASED, declared=["x", "y"])
    assert t.value_at(1, "doubled") == 2


def test_declared_name_absent_from_the_trace_is_not_invented():
    t = trace_from_json(ALIASED, declared=["x", "y", "neverDumped"])
    assert t.variables == ["x", "y"]
