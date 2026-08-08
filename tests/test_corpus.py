"""Issue #29: a conformance corpus of real specs.

Specs defined inline in test files exercise tlakit's parsing but nothing
about whether it still agrees with real TLC output. Each directory under
`tests/corpus/` holds a `spec.tla`, a `model.cfg`, and a `golden.json`
captured from an actual TLC run -- tlakit does not reimplement TLC, so the
only trustworthy oracle for "did this regress" is TLC itself.

Adding a spec to the corpus is exactly: add a new directory here with those
three files. No new test code required -- `_entries()` discovers it and
`test_corpus_entry_matches_its_golden` parametrizes over it automatically.

A golden is a claim about tlakit's output, not only about TLC's, so a change
in what tlakit can *recover* from a run legitimately moves one. Two entries
already show this: `broken_assertion_failed` and `broken_evaluation_error`
both record a trace that was `null` when they were captured. TLC writes no
`-dumpTrace json` file for either -- it dies mid-evaluation rather than
finishing with a counterexample object -- but it does print "The behavior up
to this point is:" followed by the states, and #4's text-mode fallback now
reads them. Both traces were checked against real `tlc2.TLC` stdout before
being pinned. When a golden moves, that is the bar: confirm the new value
against the tool, then re-pin it. Re-pinning to whatever tlakit currently
says is how this file stops being an oracle and becomes a mirror.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import tlakit
from tlakit.jar import JarNotFound
from tlakit.result import CheckResult

CORPUS_ROOT = Path(__file__).parent / "corpus"


def _entries() -> list[Path]:
    """Every corpus entry: a directory with spec.tla + model.cfg + golden.json."""
    return sorted(
        p.parent
        for p in CORPUS_ROOT.glob("*/spec.tla")
        if (p.parent / "model.cfg").is_file() and (p.parent / "golden.json").is_file()
    )


def golden_of(result: CheckResult) -> dict[str, Any]:
    """The comparable slice of a CheckResult.

    Deliberately excludes `raw` (absolute temp paths and the java argv, both
    machine-specific) and `stats.duration_ms` (wall-clock, not a property of
    the spec). Everything else here is determined by the spec and config
    alone, so it is safe to pin as a golden value.
    """
    return {
        "outcome": result.outcome.value,
        "diagnostics": [
            {
                "severity": d.severity.value,
                "message": d.message,
                "module": d.module,
                "line": d.line,
                "column": d.column,
            }
            for d in result.diagnostics
        ],
        "stats": {
            "generated": result.stats.generated,
            "distinct": result.stats.distinct,
            "queue_left": result.stats.queue_left,
            "depth": result.stats.depth,
        },
        "trace": (
            None
            if result.trace is None
            else {
                "variables": result.trace.variables,
                "states": result.trace.states,
                "actions": [a.name for a in result.trace.actions],
                "loop_start": result.trace.loop_start,
            }
        ),
    }


def test_the_corpus_is_not_empty():
    """A canary on the discovery glob itself; needs no java."""
    assert len(_entries()) >= 10


def test_every_entry_has_a_readable_golden():
    """golden.json must at least be well-formed JSON with the fields
    golden_of() produces -- catches a corrupted fixture before java ever
    gets involved."""
    for entry in _entries():
        golden = json.loads((entry / "golden.json").read_text())
        assert "outcome" in golden
        assert "diagnostics" in golden
        assert "stats" in golden
        assert "trace" in golden


@pytest.fixture
def ready():
    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))


@pytest.mark.java
@pytest.mark.parametrize("entry", _entries(), ids=lambda p: p.name)
def test_corpus_entry_matches_its_golden(ready, entry: Path):
    golden = json.loads((entry / "golden.json").read_text())
    spec = tlakit.load(entry / "spec.tla")
    config = (entry / "model.cfg").read_text()
    result = spec.check(config=config, timeout=60)
    assert golden_of(result) == golden
