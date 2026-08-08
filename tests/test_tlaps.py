"""Issue #68: TLAPS, the proof half.

Record parsing is pure and tested without tlapm. The rest runs the real thing,
because the format is undocumented enough that a mock would only confirm my
reading of it -- and my reading was wrong once already (the records go to
stderr, not stdout).
"""
from __future__ import annotations

import pytest

from tlakit.tlaps import (
    Obligation,
    ProofResult,
    TlapmNotFound,
    TlapsRunner,
    find_tlapm,
    parse_obligations,
)

GOOD = """---- MODULE Arith ----
EXTENDS Integers
THEOREM Easy == \\A n \\in Nat : n + 0 = n
  OBVIOUS
====
"""
MIXED = GOOD.replace("====", "THEOREM Wrong == \\A n \\in Nat : n + 1 = n\n  OBVIOUS\n====")
NO_THEOREMS = "---- MODULE Empty ----\nEXTENDS Integers\nFoo == 1\n====\n"

RECORDS = """@!!BEGIN
@!!type:obligation
@!!id:1
@!!loc:4:3:4:10
@!!status:to be proved
@!!END

@!!BEGIN
@!!type:obligationsnumber
@!!count:1
@!!END

@!!BEGIN
@!!type:obligation
@!!id:1
@!!loc:4:3:4:10
@!!status:proved
@!!prover:smt
@!!meth:time-limit: 5; time-used: 0.0 (0%)
@!!END
"""


def test_an_obligation_is_folded_to_its_last_status():
    """tlapm reports the same obligation repeatedly as it moves through the
    backends. Counting every record would triple-count the work and make a
    proved obligation look like it was also unproved."""
    obligations = parse_obligations(RECORDS)
    assert len(obligations) == 1
    only = obligations[0]
    assert only.status == "proved" and only.proved
    assert only.prover == "smt"
    assert (only.line, only.column, only.end_line, only.end_column) == (4, 3, 4, 10)


def test_non_obligation_records_are_ignored():
    """`obligationsnumber` is a progress report, not an obligation."""
    assert len(parse_obligations(RECORDS)) == 1


def test_output_with_no_records_yields_nothing_rather_than_raising():
    assert parse_obligations("just some prose\n") == []


def test_a_result_with_no_obligations_is_not_ok():
    """A module with no theorems is not a proved module. Calling it success
    would let a typo that deletes a proof report as a pass."""
    assert ProofResult(module="M").ok is False


def test_ok_requires_every_obligation():
    proved = Obligation(id=1, status="proved")
    failed = Obligation(id=2, status="failed")
    assert ProofResult(module="M", obligations=[proved]).ok is True
    assert ProofResult(module="M", obligations=[proved, failed]).ok is False
    assert ProofResult(module="M", obligations=[proved, failed]).failed == [failed]


def test_an_in_flight_status_is_neither_proved_nor_failed():
    """`being proved` is not a verdict. Treating it as either would report a
    half-finished run as settled."""
    pending = Obligation(id=1, status="being proved")
    assert not pending.proved and not pending.failed
    assert ProofResult(module="M", obligations=[pending]).unproved == [pending]


def test_str_lists_only_what_is_unproved():
    result = ProofResult(
        module="M",
        obligations=[Obligation(id=1, status="proved"),
                     Obligation(id=2, status="failed", reason="false", line=6, column=3)],
    )
    text = str(result)
    assert "1/2 obligations proved" in text
    assert "6:3: failed (false)" in text


def test_a_missing_tlapm_names_the_arm64_asset(monkeypatch):
    """The 1.5.0 macOS installers are i386 and cannot run on Apple Silicon at
    all, so the message has to point at 1.6.0-pre specifically."""
    monkeypatch.delenv("TLAKIT_TLAPM", raising=False)
    monkeypatch.setattr("tlakit.tlaps.shutil.which", lambda _: None)
    with pytest.raises(TlapmNotFound, match="arm64"):
        find_tlapm()


def test_an_explicit_tlapm_path_that_is_not_there_says_so():
    with pytest.raises(TlapmNotFound, match="is not a file"):
        find_tlapm("/nonexistent/tlapm")


@pytest.fixture(scope="module")
def prover():
    try:
        return TlapsRunner()
    except TlapmNotFound as exc:
        pytest.skip(str(exc))


@pytest.mark.tlaps
def test_a_provable_theorem_is_proved(prover):
    result = prover.prove(GOOD, "Arith", timeout=600)
    assert result.ok
    assert len(result) == 1
    assert result.obligations[0].prover


@pytest.mark.tlaps
def test_an_unprovable_theorem_fails_at_its_location(prover):
    """The thing a proof result has instead of a counterexample: which
    obligation, where, and why."""
    result = prover.prove(MIXED, "Arith", timeout=900)
    assert not result.ok
    assert len(result) == 2
    failed = result.failed
    assert len(failed) == 1
    assert failed[0].line == 6
    assert failed[0].reason


@pytest.mark.tlaps
def test_a_module_with_no_theorems_is_not_a_pass(prover):
    result = prover.prove(NO_THEOREMS, "Empty", timeout=300)
    assert len(result) == 0
    assert result.ok is False


@pytest.mark.tlaps
def test_the_records_are_read_from_stderr(prover):
    """Pins where tlapm actually writes them. Reading stdout alone returned
    zero obligations, which reads as 'this module has no theorems' rather than
    as a parsing failure -- the worst possible way to be wrong here."""
    result = prover.prove(GOOD, "Arith", timeout=600)
    assert "@!!BEGIN" in result.raw.stderr
    assert result.obligations
