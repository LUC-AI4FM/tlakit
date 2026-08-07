"""Issue #13: per-action coverage, and the specs that pass for the wrong reason."""
import shutil
from pathlib import Path

import pytest

from tlakit.parse import parse_coverage, parse_tlc
from tlakit.result import Outcome

# Captured from TLC 2026.03.19 with -coverage 1, on a spec whose `Never` action
# has a guard that can never hold.
FIXTURE = Path(__file__).parent / "fixtures" / "tlc_coverage.txt"
COVERAGE_OUTPUT = FIXTURE.read_text()


def test_actions_are_captured_with_their_counts():
    cov = parse_coverage(COVERAGE_OUTPUT)
    assert set(cov) == {"Init", "Bump", "Never"}
    assert (cov["Bump"].distinct, cov["Bump"].total) == (3, 4)
    assert cov["Bump"].module == "C"
    assert cov["Bump"].line == 5


def test_a_variable_entry_is_not_an_action():
    """`<x ...>: 3` carries a single count, not distinct:total."""
    assert "x" not in parse_coverage(COVERAGE_OUTPUT)


def test_an_invariant_entry_is_not_an_action():
    """`<Inv ...>` carries no count at all."""
    assert "Inv" not in parse_coverage(COVERAGE_OUTPUT)


def test_indented_subexpressions_are_not_actions():
    assert len(parse_coverage(COVERAGE_OUTPUT)) == 3


def test_an_action_that_never_fired_is_flagged():
    cov = parse_coverage(COVERAGE_OUTPUT)
    assert cov["Never"].unused is True
    assert cov["Bump"].unused is False


def test_stats_lists_unused_actions():
    _, _, stats = parse_tlc(COVERAGE_OUTPUT, 0)
    assert stats.unused_actions == ["Never"]
    assert stats.distinct == 4


def test_no_coverage_flag_means_no_coverage_not_zero_coverage():
    """An empty mapping must read as 'not measured', never 'nothing ran'."""
    _, _, stats = parse_tlc("TLC2 Version 1\nModel checking completed.\n", 0)
    assert stats.coverage == {}
    assert stats.unused_actions == []


def test_renderer_warns_about_an_action_that_never_fired():
    from tlakit.render import result_html
    from tlakit.result import CheckResult, RawOutput

    _, _, stats = parse_tlc(COVERAGE_OUTPUT, 0)
    raw = RawOutput(argv=[], exit_code=0, stdout="", stderr="")
    html = result_html(CheckResult(Outcome.OK, [], None, stats, raw))
    assert "Never enabled" in html
    assert "wrong reason" in html


# --- end to end -----------------------------------------------------------

SPEC = """---- MODULE C ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Bump == IF x < 3 THEN x' = x + 1 ELSE x' = x
Never == x = 99 /\\ x' = 0
Next == Bump \\/ Never
Spec == Init /\\ [][Next]_x
Inv == x <= 3
====
"""


@pytest.fixture
def ready():
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))


@pytest.mark.java
def test_coverage_is_off_unless_requested(ready):
    import tlakit

    result = tlakit.Spec(source=SPEC, name="C").check(invariants=["Inv"])
    assert result.ok
    assert result.stats.coverage == {}


@pytest.mark.java
def test_coverage_finds_the_action_that_can_never_fire(ready):
    import tlakit

    result = tlakit.Spec(source=SPEC, name="C").check(
        invariants=["Inv"], coverage=True
    )
    assert result.ok, "the spec passes -- that is the point"
    assert result.stats.unused_actions == ["Never"]
    assert result.stats.coverage["Bump"].total > 0
