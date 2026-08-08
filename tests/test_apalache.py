"""Issue #68: Apalache as a second checker.

The ITF decoding is pure and tested without Apalache. Everything else runs the
real binary, because the whole question this backend has to answer correctly is
"what did Apalache actually say", and a mock only ever says what I expect.
"""
from __future__ import annotations

import shutil

import pytest

from tlakit.apalache import (
    ApalacheNotFound,
    ApalacheRunner,
    find_apalache,
    itf_value,
    trace_from_itf,
)
from tlakit.result import Outcome, Severity

VIOLATES = """---- MODULE Counter ----
EXTENDS Integers
VARIABLE
  \\* @type: Int;
  x
Init == x = 0
Next == x' = (x + 1) % 4
Inv == x < 3
====
"""
HOLDS = VIOLATES.replace("Inv == x < 3", "Inv == x < 9")
UNTYPED = VIOLATES.replace("  \\* @type: Int;\n", "")


# --------------------------------------------------------------------------
# ITF decoding. No Apalache.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tagged,expected",
    [
        ({"#bigint": "42"}, 42),
        ({"#bigint": "-7"}, -7),
        ({"#set": [{"#bigint": "1"}, {"#bigint": "2"}]}, [1, 2]),
        ({"#tup": [{"#bigint": "1"}, "a"]}, [1, "a"]),
        (True, True),
        ("plain", "plain"),
        ({"a": {"#bigint": "1"}}, {"a": 1}),
    ],
)
def test_itf_values_decode_to_the_shapes_tlc_produces(tagged, expected):
    """Sets and tuples both become lists, matching `tlakit.trace`. That is what
    lets one `Trace` serve counterexamples from either checker."""
    assert itf_value(tagged) == expected


def test_a_bigint_is_not_truncated_to_a_float():
    """ITF tags integers as strings precisely because they can exceed what
    JSON numbers carry. Decoding through float would silently lose digits."""
    huge = 2**70 + 1
    assert itf_value({"#bigint": str(huge)}) == huge


def test_a_map_with_unhashable_keys_stays_a_list_of_pairs():
    """A TLA+ function may be keyed by a tuple, which decodes to a list and
    cannot be a dict key. Dropping those entries would lose state; keeping the
    pairs is lossless."""
    decoded = itf_value({"#map": [[{"#tup": [{"#bigint": "1"}]}, {"#bigint": "9"}]]})
    assert decoded == [([1], 9)]


def test_trace_from_itf_builds_an_ordinary_trace():
    doc = {
        "vars": ["x", "y"],
        "states": [
            {"#meta": {"index": 0}, "x": {"#bigint": "0"}, "y": "a"},
            {"#meta": {"index": 1}, "x": {"#bigint": "1"}, "y": "a"},
        ],
    }
    trace = trace_from_itf(doc)
    assert len(trace) == 2
    assert trace.variables == ["x", "y"]
    assert trace.delta(1) == frozenset({"x"})
    assert trace.states[0] == {"x": 0, "y": "a"}
    assert "#meta" not in trace.states[0]


def test_an_empty_itf_document_is_no_trace():
    assert trace_from_itf({"vars": ["x"], "states": []}) is None


def test_an_explicit_path_that_is_not_there_says_so_plainly():
    """An explicit path is a claim the caller made; echo it back rather than
    telling them to go and download something."""
    with pytest.raises(ApalacheNotFound, match="is not a file"):
        find_apalache("/nonexistent/apalache-mc")


def test_no_apalache_anywhere_says_where_to_get_it(monkeypatch):
    """The other case: nothing configured and nothing on PATH. Here the useful
    message is the download link and the env var, not a path."""
    monkeypatch.delenv("TLAKIT_APALACHE", raising=False)
    monkeypatch.setattr("tlakit.apalache.shutil.which", lambda _: None)
    with pytest.raises(ApalacheNotFound, match="releases"):
        find_apalache()


# --------------------------------------------------------------------------
# Against the real Apalache.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def runner():
    try:
        return ApalacheRunner()
    except ApalacheNotFound as exc:
        pytest.skip(str(exc))


@pytest.mark.apalache
def test_a_violation_comes_back_with_a_usable_trace(runner):
    result = runner.check(VIOLATES, "Counter", invariants=["Inv"], length=6)
    assert result.outcome is Outcome.INVARIANT_VIOLATION
    assert [s["x"] for s in result.trace.states] == [0, 1, 2, 3]
    assert result.trace.variables == ["x"]
    # The payoff for reusing Trace: everything built on it works unchanged.
    assert result.trace.delta(1) == frozenset({"x"})


@pytest.mark.apalache
def test_success_is_bounded_and_says_so(runner):
    """The design decision this backend exists around.

    Apalache found no counterexample *up to the length it was given*. That is
    strictly weaker than TLC's exhaustive `OK`, so it is a different outcome
    and `ok` is False. Collapsing the two would put a falsehood in the one
    field callers branch on.
    """
    result = runner.check(HOLDS, "Counter", invariants=["Inv"], length=6)
    assert result.outcome is Outcome.BOUNDED_OK
    assert result.ok is False
    assert result.trace is None


@pytest.mark.apalache
def test_a_missing_type_annotation_explains_itself(runner):
    """A spec that checks fine under TLC may not run here at all. The
    diagnostic has to say why, or it reads as a tlakit bug."""
    result = runner.check(UNTYPED, "Counter", invariants=["Inv"], length=4)
    assert result.outcome is Outcome.PARSE_ERROR
    assert result.errors
    assert "@type" in result.errors[0].message


@pytest.mark.apalache
def test_a_tlc_config_is_refused_out_loud_not_dropped(runner):
    """Apalache takes --init/--next/--inv, not a .cfg. Silently ignoring one
    would check something other than what the caller asked for."""
    result = runner.check(
        VIOLATES, "Counter", config="SPECIFICATION Spec\n", invariants=["Inv"], length=6
    )
    warnings = [d for d in result.diagnostics if d.severity is Severity.WARNING]
    assert warnings and "does not read a TLC .cfg" in warnings[0].message


@pytest.mark.apalache
def test_a_timeout_is_reported_rather_than_raised(runner):
    result = runner.check(VIOLATES, "Counter", invariants=["Inv"], length=6, timeout=0.5)
    assert result.outcome is Outcome.TIMEOUT
    assert result.trace is None


@pytest.mark.apalache
def test_the_raw_invocation_is_kept(runner):
    """`raw.argv` is the escape hatch for anything not normalized, and the
    only way to reproduce a run by hand."""
    result = runner.check(HOLDS, "Counter", invariants=["Inv"], length=3)
    assert "check" in result.raw.argv
    assert "--length=3" in result.raw.argv
    assert "--inv=Inv" in result.raw.argv


@pytest.mark.apalache
def test_apalache_and_tlc_agree_on_the_counterexample():
    """The claim worth testing: two different checkers, one result shape.

    Skips rather than fails without a JVM, since that is a missing tool and
    not a disagreement.
    """
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlc = tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    try:
        apalache = ApalacheRunner()
    except ApalacheNotFound as exc:
        pytest.skip(str(exc))

    by_tlc = tlc.check(
        VIOLATES, "Counter", "INIT Init\nNEXT Next\nINVARIANT Inv\n", timeout=60
    )
    by_apalache = apalache.check(VIOLATES, "Counter", invariants=["Inv"], length=6)

    assert by_tlc.outcome is by_apalache.outcome is Outcome.INVARIANT_VIOLATION
    # Both reach x = 3, the first state violating `x < 3`.
    assert by_tlc.trace.states[-1]["x"] == by_apalache.trace.states[-1]["x"] == 3
    assert by_tlc.trace.variables == by_apalache.trace.variables == ["x"]
