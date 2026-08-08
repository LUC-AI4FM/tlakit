"""The `tlakit` console script (#66).

The exit code is the part that matters most here, because it is the part a
Makefile or a CI job depends on and the part that cannot be changed later
without breaking them silently. Everything else the CLI does is a thin wrapper
over `api.Spec`, and is tested as such.

Most of these need no Java: argument handling, config discovery and the exit
code mapping are decided before a runner is ever asked to do anything, and a
fake runner is enough to pin them. The `java`-marked ones at the bottom run a
real TLC, because the mapping is only worth anything if the outcomes it maps
are the ones TLC actually produces.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tlakit.cli_main import CANNOT_RUN, FOUND_A_PROBLEM, OK, main
from tlakit.result import CheckResult, Outcome, RawOutput, Severity, Diagnostic, Stats

COUNTER = """---- MODULE Counter ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = x + 1
Spec == Init /\\ [][Next]_x
Inv == x < 3
====
"""

TERMINATING = """---- MODULE Terminating ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x < 2 /\\ x' = x + 1
Spec == Init /\\ [][Next]_x
Done == x = 2
Inv == x < 5
====
"""

RAW = RawOutput(argv=["java"], exit_code=0, stdout="", stderr="")


@pytest.fixture
def counter(tmp_path: Path) -> Path:
    path = tmp_path / "Counter.tla"
    path.write_text(COUNTER, encoding="utf-8")
    return path


class FakeRunner:
    """Records what it was asked, answers with whatever outcome is set."""

    can_parse = True
    tools_jar = None
    community_jar = None

    def __init__(self, outcome: Outcome = Outcome.OK):
        self.outcome = outcome
        self.calls: list[dict] = []

    def _result(self, source: str) -> CheckResult:
        diagnostics = []
        if self.outcome is not Outcome.OK:
            diagnostics = [Diagnostic(Severity.ERROR, f"{self.outcome.value} here")]
        return CheckResult(
            self.outcome, diagnostics, None, Stats(distinct=3, depth=2), RAW,
            source=source,
        )

    def check(self, source, module, config, **kwargs):
        self.calls.append({"kind": "check", "config": config, **kwargs})
        return self._result(source)

    def parse(self, source, module, **kwargs):
        self.calls.append({"kind": "parse", **kwargs})
        return self._result(source)


def run(argv, runner=None, capsys=None):
    code = main(argv, runner=runner)
    return code, (capsys.readouterr().out if capsys else "")


# --- the exit code contract ----------------------------------------------


def test_a_clean_check_exits_zero(counter, capsys):
    code, out = run(["check", str(counter), "--invariant", "Inv"],
                    FakeRunner(Outcome.OK), capsys)
    assert code == OK == 0
    assert "OK" in out


@pytest.mark.parametrize(
    "outcome",
    [
        Outcome.INVARIANT_VIOLATION,
        Outcome.DEADLOCK,
        Outcome.TEMPORAL_VIOLATION,
        Outcome.ASSERTION_FAILED,
        Outcome.PARSE_ERROR,
    ],
)
def test_a_spec_with_something_wrong_exits_one(counter, capsys, outcome):
    """`tlakit check spec.tla && deploy` must not deploy on a violation.

    This is the decision #66 asked to make deliberately: a found bug is a
    successful *run*, but it is not a passing *spec*, and the shell only has
    one word for the difference.
    """
    code, out = run(["check", str(counter)], FakeRunner(outcome), capsys)
    assert code == FOUND_A_PROBLEM == 1
    assert outcome.name in out


def test_a_missing_file_exits_two_not_one(capsys):
    """Separated from the above on purpose: exit 1 means "I checked it and
    something is wrong with your spec". A typo in a filename is not that, and a
    CI job that cannot tell them apart will report the wrong thing."""
    code, out = run(["check", "/nonexistent/Nope.tla"], FakeRunner(), capsys)
    assert code == CANNOT_RUN == 2
    assert "Nope.tla" in out


def test_a_runner_that_cannot_start_exits_two(counter, capsys):
    class Broken(FakeRunner):
        def check(self, *a, **k):
            raise RuntimeError("no java on PATH")

    code, out = run(["check", str(counter)], Broken(), capsys)
    assert code == CANNOT_RUN
    assert "no java" in out


def test_the_three_codes_are_distinct():
    assert len({OK, FOUND_A_PROBLEM, CANNOT_RUN}) == 3


# --- finding the config ---------------------------------------------------


def test_a_cfg_beside_the_spec_is_used(counter):
    counter.with_suffix(".cfg").write_text("SPECIFICATION Spec\nINVARIANT Inv\n")
    runner = FakeRunner()
    assert main(["check", str(counter)], runner=runner) == OK
    assert "INVARIANT Inv" in runner.calls[0]["config"]


def test_an_explicit_config_wins_over_the_one_beside_it(counter, tmp_path):
    counter.with_suffix(".cfg").write_text("INVARIANT Ignored\n")
    chosen = tmp_path / "other.cfg"
    chosen.write_text("SPECIFICATION Spec\nINVARIANT Chosen\n")
    runner = FakeRunner()

    main(["check", str(counter), "--config", str(chosen)], runner=runner)

    assert "Chosen" in runner.calls[0]["config"]
    assert "Ignored" not in runner.calls[0]["config"]


def test_flags_build_a_config_when_there_is_no_cfg(counter):
    runner = FakeRunner()
    main(
        ["check", str(counter), "--invariant", "Inv", "--constant", "N=3"],
        runner=runner,
    )
    config = runner.calls[0]["config"]
    assert "INVARIANT Inv" in config
    assert "N = 3" in config


def test_flags_and_a_neighbouring_cfg_together_are_refused(counter, capsys):
    """Silently preferring one would make the other's flags look ignored."""
    counter.with_suffix(".cfg").write_text("INVARIANT Inv\n")
    code, out = run(["check", str(counter), "--invariant", "Other"],
                    FakeRunner(), capsys)
    assert code == CANNOT_RUN
    assert "--config" in out


def test_no_deadlock_check_against_a_raw_config_is_refused(counter, capsys):
    """`api.Spec.check` raises for this; the CLI has to say it better.

    A raw .cfg states its own CHECK_DEADLOCK, so the flag would either be
    ignored or silently override the file.
    """
    cfg = counter.with_suffix(".cfg")
    cfg.write_text("SPECIFICATION Spec\n")
    code, out = run(
        ["check", str(counter), "--config", str(cfg), "--no-deadlock-check"],
        FakeRunner(), capsys,
    )
    assert code == CANNOT_RUN
    assert "CHECK_DEADLOCK" in out


def test_no_deadlock_check_reaches_the_config(counter):
    runner = FakeRunner()
    main(["check", str(counter), "--no-deadlock-check"], runner=runner)
    assert "CHECK_DEADLOCK FALSE" in runner.calls[0]["config"]


# --- constants ------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("N=3", "N = 3"),
        ("Debug=TRUE", "Debug = TRUE"),
        ('Name="ada"', 'Name = "ada"'),
        ("Procs={a, b}", "Procs = {a, b}"),
    ],
)
def test_constants_are_rendered_as_tla(counter, text, expected):
    """A bare `{a, b}` is a set of model values, which is the ordinary way to
    write a CONSTANT and is not JSON. It has to survive unquoted."""
    runner = FakeRunner()
    main(["check", str(counter), "--constant", text], runner=runner)
    assert expected in runner.calls[0]["config"]


def test_a_constant_without_an_equals_is_refused(counter, capsys):
    code, out = run(["check", str(counter), "--constant", "N"], FakeRunner(), capsys)
    assert code == CANNOT_RUN
    assert "NAME=VALUE" in out


# --- parse ----------------------------------------------------------------


def test_parse_reports_ok(counter, capsys):
    code, out = run(["parse", str(counter)], FakeRunner(Outcome.OK), capsys)
    assert code == OK
    assert runner_saw_parse(out) or "OK" in out


def runner_saw_parse(out: str) -> bool:
    return "OK" in out


def test_a_parse_error_exits_one(counter, capsys):
    code, _ = run(["parse", str(counter)], FakeRunner(Outcome.PARSE_ERROR), capsys)
    assert code == FOUND_A_PROBLEM


def test_parse_takes_no_config_flags(counter):
    """There is no search, so a config would have nothing to act on."""
    with pytest.raises(SystemExit):
        main(["parse", str(counter), "--invariant", "Inv"])


# --- output ---------------------------------------------------------------


def test_output_is_the_readable_renderer_not_a_dataclass_repr(counter, capsys):
    """#61 and #66 want the same thing; this is the seam between them."""
    code, out = run(["check", str(counter)], FakeRunner(Outcome.DEADLOCK), capsys)
    assert "CheckResult(" not in out
    assert "outcome=<Outcome." not in out
    assert "DEADLOCK" in out
    for line in out.splitlines():
        assert len(line) <= 100, line


def test_no_arguments_prints_usage_and_exits_two(capsys):
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == CANNOT_RUN


# --- against a real TLC ---------------------------------------------------


@pytest.fixture
def java_or_skip():
    from tlakit.jar import JarNotFound, find_tools_jar

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        find_tools_jar()
    except JarNotFound as exc:
        pytest.skip(str(exc))


@pytest.mark.java
def test_a_real_violation_exits_one(tmp_path, java_or_skip, capsys):
    path = tmp_path / "Counter.tla"
    path.write_text(COUNTER, encoding="utf-8")
    code = main(["check", str(path), "--invariant", "Inv"])
    out = capsys.readouterr().out
    assert code == FOUND_A_PROBLEM
    assert "INVARIANT_VIOLATION" in out


@pytest.mark.java
def test_a_terminating_spec_passes_once_the_deadlock_check_is_off(
    tmp_path, java_or_skip, capsys
):
    """The result #66 calls the most confusing one a newcomer gets: without the
    flag this correct spec reports DEADLOCK."""
    path = tmp_path / "Terminating.tla"
    path.write_text(TERMINATING, encoding="utf-8")

    assert main(["check", str(path), "--invariant", "Inv"]) == FOUND_A_PROBLEM
    assert "DEADLOCK" in capsys.readouterr().out

    code = main(
        ["check", str(path), "--invariant", "Inv", "--no-deadlock-check"]
    )
    assert code == OK
    assert "OK" in capsys.readouterr().out


@pytest.mark.java
def test_a_real_parse_error_exits_one(tmp_path, java_or_skip, capsys):
    path = tmp_path / "Broken.tla"
    path.write_text("---- MODULE Broken ----\nEXTENDS Naturals\nInit == \n====\n")
    code = main(["parse", str(path)])
    out = capsys.readouterr().out
    assert code == FOUND_A_PROBLEM
    assert "PARSE_ERROR" in out
    assert "error:" in out
