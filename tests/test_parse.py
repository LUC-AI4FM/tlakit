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
    outcome, diags, _ = parse_tlc("TLC2 Version 1\nsomething odd\n", 42)
    assert outcome is Outcome.ERROR
    assert any("42" in d.message for d in diags)


def test_documented_exit_codes_map_to_their_own_outcomes():
    """Read from tlc2.output.EC$ExitStatus, not inferred."""
    cases = {
        10: Outcome.ASSUMPTION_VIOLATION,
        14: Outcome.ASSERTION_FAILED,
        75: Outcome.EVALUATION_ERROR,
        76: Outcome.EVALUATION_ERROR,
        77: Outcome.EVALUATION_ERROR,
        150: Outcome.PARSE_ERROR,
        151: Outcome.CONFIG_ERROR,
        152: Outcome.STATE_SPACE_TOO_LARGE,
        153: Outcome.ERROR,
        255: Outcome.ERROR,
    }
    for code, expected in cases.items():
        outcome, _, _ = parse_tlc("TLC2 Version 1\n", code)
        assert outcome is expected, f"exit {code}"


def test_temporal_violation_by_exit_code():
    outcome, _, _ = parse_tlc("TLC2 Version 1\n", 13)
    assert outcome is Outcome.TEMPORAL_VIOLATION


def test_old_tlc_without_dumptrace_gets_an_actionable_diagnostic():
    """v1.7.4 (TLC 2.19) has no -dumpTrace, which tlakit passes on every run.
    Without this the user sees Outcome.ERROR and exit code 1, which is not even
    in TLC's own exit table."""
    stdout = (
        "TLC2 Version 2.19 of 08 August 2024 (rev: 5a47802)\n"
        "Error: Error: unrecognized option: -dumpTrace\n"
        "Usage: java tlc2.TLC [-option] inputfile\n"
    )
    outcome, diags, _ = parse_tlc(stdout, 1)
    assert outcome is Outcome.ERROR
    message = " ".join(d.message for d in diags)
    assert "-dumpTrace" in message
    assert "v1.8.0" in message
    assert "TLC2 Version 2.19" in message


def test_a_missing_extends_produces_a_diagnostic():
    """SANY reports this with no line or column, so it matched none of the
    existing patterns -- a public client got parse_error and no explanation."""
    stdout = (
        "Parsing file /tmp/E.tla\n"
        "Cannot find source file for module IOUtils imported in module E.\n"
        "*** Errors: 1\n"
    )
    diags = parse_sany(stdout)
    assert len(diags) == 1
    assert "IOUtils" in diags[0].message
    assert diags[0].module == "E"

    outcome, diags, _ = parse_tlc(stdout, 150)
    assert outcome is Outcome.PARSE_ERROR
    assert diags, "an outcome of parse_error with no diagnostics tells nobody anything"


def test_a_missing_module_without_an_importer_still_reports():
    diags = parse_sany("Cannot find source file for module Widgets\n")
    assert len(diags) == 1
    assert "Widgets" in diags[0].message
