import shutil

import pytest

from tlakit.cli import CliRunner
from tlakit.jar import JarNotFound
from tlakit.result import Outcome

pytestmark = pytest.mark.java

SPIKE = """---- MODULE Spike ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = x + 1
Spec == Init /\\ [][Next]_x
Inv == x < 3
====
"""
SPIKE_CFG = "SPECIFICATION Spec\nINVARIANT Inv\n"

OK = """---- MODULE Ok ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == IF x < 2 THEN x' = x + 1 ELSE x' = x
Spec == Init /\\ [][Next]_x
Inv == x <= 2
====
"""
OK_CFG = "SPECIFICATION Spec\nINVARIANT Inv\n"

BROKEN = "---- MODULE Broken ----\nVARIABLE y\nInit == y = \n====\n"

SEMANTIC = """---- MODULE Sem ----
VARIABLE x
Init == x = undefinedOp
Next == x' = x
====
"""


@pytest.fixture(scope="module")
def runner():
    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        return CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))


def test_parse_accepts_a_valid_module(runner):
    result = runner.parse(OK, "Ok")
    assert result.ok
    assert result.diagnostics == []


def test_parse_reports_syntax_error_with_location(runner):
    result = runner.parse(BROKEN, "Broken")
    assert result.outcome is Outcome.PARSE_ERROR
    assert any(d.line == 4 for d in result.errors)


def test_parse_reports_semantic_error_despite_zero_exit(runner):
    result = runner.parse(SEMANTIC, "Sem")
    assert result.outcome is Outcome.PARSE_ERROR
    assert any("Unknown operator" in d.message for d in result.errors)


def test_check_finds_invariant_violation_with_trace(runner):
    result = runner.check(SPIKE, "Spike", SPIKE_CFG)
    assert result.outcome is Outcome.INVARIANT_VIOLATION
    assert result.trace is not None
    assert [s["x"] for s in result.trace.states] == [0, 1, 2, 3]
    assert result.trace.delta(1) == frozenset({"x"})


def test_check_passes_a_sound_spec(runner):
    result = runner.check(OK, "Ok", OK_CFG)
    assert result.ok
    assert result.trace is None
    assert result.stats.distinct == 3


def test_raw_is_always_populated(runner):
    result = runner.check(OK, "Ok", OK_CFG)
    assert result.raw.exit_code == 0
    assert "TLC2 Version" in result.raw.stdout
    assert result.raw.argv[0].endswith("java")


def test_timeout_returns_partial_result_not_an_exception(runner):
    unbounded = """---- MODULE Big ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = (x + 1) % 100000000
Spec == Init /\\ [][Next]_x
Inv == TRUE
====
"""
    result = runner.check(
        unbounded, "Big", "SPECIFICATION Spec\nINVARIANT Inv\n", timeout=3
    )
    assert result.outcome is Outcome.TIMEOUT
    assert result.raw.argv
    assert any("did not finish" in d.message for d in result.errors)


def test_runs_are_isolated_from_each_other(runner):
    """Repeated checks must not trip over leftover *_TTrace_*.tla files."""
    for _ in range(3):
        assert (
            runner.check(SPIKE, "Spike", SPIKE_CFG).outcome
            is Outcome.INVARIANT_VIOLATION
        )
