from pathlib import Path

from tlakit.parse import parse_sany, parse_tlc
from tlakit.result import Outcome, Severity

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text()


def test_sany_parse_error_has_line_and_column():
    diags = parse_sany(read("sany_parse_error.txt"))
    assert any(d.line == 4 and d.column == 1 for d in diags)
    assert all(d.severity is Severity.ERROR for d in diags)
    assert any("Encountered" in d.message for d in diags)


def test_sany_semantic_error_has_module_and_location():
    diags = parse_sany(read("sany_semantic_error.txt"))
    assert len(diags) == 1
    d = diags[0]
    assert (d.module, d.line, d.column) == ("Sem", 3, 13)
    assert "Unknown operator" in d.message


def test_sany_clean_output_yields_no_diagnostics():
    assert parse_sany("SANY finished.\n") == []


def test_tlc_output_is_not_mistaken_for_sany_diagnostics():
    """TLC's coverage statistics contain `line N, col M` text too."""
    assert parse_sany(read("tlc_ok.txt")) == []
    assert parse_sany(read("tlc_invariant_violation.txt")) == []


def test_tlc_invariant_violation():
    outcome, diags, stats = parse_tlc(read("tlc_invariant_violation.txt"), 12)
    assert outcome is Outcome.INVARIANT_VIOLATION
    assert any("Inv" in d.message for d in diags)
    assert (stats.generated, stats.distinct, stats.depth) == (4, 4, 4)


def test_tlc_deadlock():
    outcome, _, stats = parse_tlc(read("tlc_deadlock.txt"), 11)
    assert outcome is Outcome.DEADLOCK
    assert stats.distinct == 3


def test_tlc_ok():
    outcome, diags, stats = parse_tlc(read("tlc_ok.txt"), 0)
    assert outcome is Outcome.OK
    assert diags == []
    assert (stats.generated, stats.distinct, stats.depth) == (4, 3, 3)


def test_text_beats_exit_code():
    """A violation in the text wins even if the exit code is unfamiliar."""
    outcome, _, _ = parse_tlc(read("tlc_invariant_violation.txt"), 99)
    assert outcome is Outcome.INVARIANT_VIOLATION


def test_unknown_nonzero_exit_is_error():
    outcome, diags, _ = parse_tlc("TLC2 Version 1\nsomething odd\n", 77)
    assert outcome is Outcome.ERROR
    assert any("77" in d.message for d in diags)


def test_temporal_violation_by_exit_code():
    outcome, _, _ = parse_tlc("TLC2 Version 1\n", 13)
    assert outcome is Outcome.TEMPORAL_VIOLATION
