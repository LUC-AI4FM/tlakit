import shutil
from pathlib import Path

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
    # java.exe on Windows.
    assert Path(result.raw.argv[0]).stem == "java"


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


def test_java_executable_honours_the_env_override(monkeypatch):
    from tlakit.cli import java_executable

    monkeypatch.setenv("TLAKIT_JAVA", "/opt/custom/bin/java")
    assert java_executable() == "/opt/custom/bin/java"


def test_java_executable_error_names_the_env_var(monkeypatch):
    import tlakit.cli as cli

    monkeypatch.delenv("TLAKIT_JAVA", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    with pytest.raises(cli.JavaNotFound, match="TLAKIT_JAVA"):
        cli.java_executable()


# --- issue #10: interrupting a cell must not orphan the JVM ---------------


def test_terminate_kills_a_running_process():
    import subprocess as sp
    import time

    from tlakit.cli import _terminate

    import sys

    from tlakit.cli import GROUP_KWARGS

    proc = sp.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=sp.PIPE, stderr=sp.PIPE, text=True, **GROUP_KWARGS,
    )
    _terminate(proc)
    deadline = time.time() + 5
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None, "process survived _terminate"


def test_terminate_is_safe_on_an_already_dead_process():
    import subprocess as sp

    from tlakit.cli import _terminate

    import sys

    from tlakit.cli import GROUP_KWARGS

    proc = sp.Popen([sys.executable, "-c", ""], stdout=sp.PIPE, stderr=sp.PIPE,
                    text=True, **GROUP_KWARGS)
    proc.wait()
    _terminate(proc)  # must not raise


def test_keyboard_interrupt_kills_the_child_and_propagates(runner, monkeypatch):
    """The notebook interrupt path: KeyboardInterrupt must re-raise, but only
    after the JVM is gone."""
    import subprocess as sp

    killed = {}
    real_terminate = __import__("tlakit.cli", fromlist=["_terminate"])._terminate

    def spy(proc):
        killed["pid"] = proc.pid
        return real_terminate(proc)

    monkeypatch.setattr("tlakit.cli._terminate", spy)

    original = sp.Popen.communicate

    def interrupt(self, *a, **kw):
        sp.Popen.communicate = original
        raise KeyboardInterrupt

    monkeypatch.setattr(sp.Popen, "communicate", interrupt)

    with pytest.raises(KeyboardInterrupt):
        runner.check(OK, "Ok", OK_CFG)
    assert "pid" in killed, "_terminate was not called on interrupt"


# --- issue #4: text-mode trace fallback -----------------------------------
# These do not need java or a real jar: `_run` is monkeypatched to return a
# canned RawOutput, so what's under test is `check()`'s fallback wiring, not
# TLC itself.


def _fake_jar_runner(tmp_path):
    fake = tmp_path / "tla2tools.jar"
    fake.write_bytes(b"not a real jar")
    return CliRunner(tools_jar=fake, community_jar=False)


def test_check_falls_back_to_the_text_trace_when_the_json_dump_is_missing(
    tmp_path, monkeypatch
):
    from tlakit.result import RawOutput

    runner = _fake_jar_runner(tmp_path)
    stdout = (Path(__file__).parent / "fixtures" / "tlc_invariant_violation.txt").read_text()
    raw = RawOutput(["java"], 12, stdout, "")
    monkeypatch.setattr(runner, "_run", lambda argv, cwd, timeout: (raw, False))

    result = runner.check(SPIKE, "Spike", SPIKE_CFG)

    assert result.outcome is Outcome.INVARIANT_VIOLATION
    assert result.trace is not None
    assert [s["x"] for s in result.trace.states] == [0, 1, 2, 3]


def test_check_prefers_the_json_dump_when_both_are_present(tmp_path, monkeypatch):
    from tlakit.result import RawOutput

    runner = _fake_jar_runner(tmp_path)
    # Text output disagrees with the JSON dump on purpose, so a test failure
    # here would mean the fallback fired when it should not have.
    stdout = (Path(__file__).parent / "fixtures" / "tlc_invariant_violation.txt").read_text()
    raw = RawOutput(["java"], 12, stdout, "")

    real_run = CliRunner._run

    def fake_run(self, argv, cwd, timeout):
        (cwd / "trace.json").write_text(
            '{"vars": ["x"], "counterexample": {'
            '"state": [[1, {"x": 99}], [2, {"x": 100}]], "action": []}}'
        )
        return raw, False

    monkeypatch.setattr(CliRunner, "_run", fake_run)
    try:
        result = runner.check(SPIKE, "Spike", SPIKE_CFG)
    finally:
        monkeypatch.setattr(CliRunner, "_run", real_run)

    assert [s["x"] for s in result.trace.states] == [99, 100]


# --- issue #18: PlusCal translation ----------------------------------------

PLUSCAL_SOURCE = """---- MODULE Euclid ----
EXTENDS Naturals, TLC

(*--algorithm euclid
variables x = 10, y = 4;
begin
  while x /= y do
    if x > y then
      x := x - y;
    else
      y := y - x;
    end if;
  end while;
end algorithm; *)
====
"""

PLUSCAL_CFG = "SPECIFICATION Spec\n"

PLUSCAL_BROKEN = """---- MODULE Broken3 ----
EXTENDS Naturals

(*--algorithm broken3
variables x = 10;
begin
  if x > 0 then
    x := x - 1;
end algorithm; *)
====
"""


def test_is_pluscal_detects_an_algorithm_block():
    from tlakit.cli import _is_pluscal

    assert _is_pluscal(PLUSCAL_SOURCE) is True
    assert _is_pluscal(SPIKE) is False


def test_is_pluscal_detects_fair_algorithm():
    from tlakit.cli import _is_pluscal

    assert _is_pluscal("(*--fair algorithm Foo\nbegin skip; end algorithm; *)") is True


def test_pluscal_diagnostics_names_pluscal_and_carries_location():
    from tlakit.cli import _pluscal_diagnostics

    stdout = (
        'pcal.trans Version 1.12 of 01 July 2024\n\n'
        'Unrecoverable error:\n'
        ' -- Expected "if" but found "algorithm"\n'
        '    line 9, column 5.\n\n'
    )
    diags = _pluscal_diagnostics(stdout, 255)
    assert len(diags) == 1
    assert "PlusCal" in diags[0].message
    assert diags[0].line == 9
    assert diags[0].column == 5


def test_pluscal_diagnostics_falls_back_when_unrecognized(tmp_path):
    from tlakit.cli import _pluscal_diagnostics

    diags = _pluscal_diagnostics("something unexpected", 255)
    assert len(diags) == 1
    assert "PlusCal" in diags[0].message
    assert "255" in diags[0].message


def test_check_translates_pluscal_before_running_tlc(tmp_path, monkeypatch):
    """No jar needed: `_run` is faked, but it stands in for both pcal.trans
    and TLC, so this proves the translated .tla file -- not the original --
    is what TLC's argv ends up pointing at."""
    from tlakit.result import RawOutput

    runner = _fake_jar_runner(tmp_path)
    calls = []

    def fake_run(self, argv, cwd, timeout):
        calls.append(argv)
        if "pcal.trans" in argv:
            # Simulate the translator inserting VARIABLES + Spec, as the real
            # tool does between BEGIN/END TRANSLATION markers.
            tla_path = cwd / "Euclid.tla"
            translated = tla_path.read_text() + (
                "\nVARIABLES pc, x, y\n"
                "Init == x = 10 /\\ y = 4 /\\ pc = \"Lbl_1\"\n"
                "Next == UNCHANGED <<pc, x, y>>\n"
                "Spec == Init /\\ [][Next]_<<pc, x, y>>\n"
            )
            tla_path.write_text(translated)
            return RawOutput(argv, 0, "Translation completed.\n", ""), False
        return RawOutput(argv, 0, "Model checking completed. No error has been found.\n", ""), False

    monkeypatch.setattr(CliRunner, "_run", fake_run)
    result = runner.check(PLUSCAL_SOURCE, "Euclid", PLUSCAL_CFG)

    assert any("pcal.trans" in c for c in calls), "translator was never invoked"
    assert result.ok
    assert "x" in declared_variables_of(result)


def declared_variables_of(result):
    from tlakit.source import declared_variables

    return declared_variables(result.source)


def test_check_surfaces_translator_failure_as_a_diagnostic(tmp_path, monkeypatch):
    from tlakit.result import RawOutput

    runner = _fake_jar_runner(tmp_path)
    error_stdout = (
        'pcal.trans Version 1.12 of 01 July 2024\n\n'
        'Unrecoverable error:\n'
        ' -- Expected "if" but found "algorithm"\n'
        '    line 9, column 5.\n\n'
    )

    def fake_run(self, argv, cwd, timeout):
        assert "pcal.trans" in argv
        return RawOutput(argv, 255, error_stdout, ""), False

    monkeypatch.setattr(CliRunner, "_run", fake_run)
    result = runner.check(PLUSCAL_BROKEN, "Broken3", "SPECIFICATION Spec\n")

    assert result.outcome is Outcome.PARSE_ERROR
    assert any("PlusCal" in d.message for d in result.errors)
    assert any(d.line == 9 for d in result.errors)


def test_parse_translates_pluscal_before_sany(tmp_path, monkeypatch):
    from tlakit.result import RawOutput

    runner = _fake_jar_runner(tmp_path)
    calls = []

    def fake_run(self, argv, cwd, timeout):
        calls.append(argv)
        if "pcal.trans" in argv:
            return RawOutput(argv, 0, "Translation completed.\n", ""), False
        return RawOutput(argv, 0, "", ""), False

    monkeypatch.setattr(CliRunner, "_run", fake_run)
    runner.parse(PLUSCAL_SOURCE, "Euclid")

    assert len(calls) == 2
    assert "pcal.trans" in calls[0]
    assert "tla2sany.SANY" in calls[1]


@pytest.mark.java
def test_pluscal_end_to_end_translates_and_checks(runner):
    result = runner.check(PLUSCAL_SOURCE, "Euclid", PLUSCAL_CFG)
    assert result.ok
    assert result.stats.distinct is not None and result.stats.distinct > 0


@pytest.mark.java
def test_pluscal_end_to_end_reports_a_real_translator_error(runner):
    result = runner.check(PLUSCAL_BROKEN, "Broken3", "SPECIFICATION Spec\n")
    assert result.outcome is Outcome.PARSE_ERROR
    assert any("PlusCal" in d.message for d in result.errors)


def test_unicode_in_a_spec_survives_a_round_trip(runner):
    """TLA+ allows Unicode operators, so every file and pipe must be UTF-8.
    Windows defaults to cp1252 and raised UnicodeDecodeError on the landing
    page for exactly this reason."""
    spec = (
        "---- MODULE Uni ----\n"
        "EXTENDS Naturals\n"
        "VARIABLE x\n"
        "\\* TLA⁺ operators: ∀ ∈ ∧ → ≤\n"
        "Init == x = 0\n"
        "Next == x' = x + 1\n"
        "Spec == Init /\\ [][Next]_x\n"
        "Inv == x < 3\n"
        "====\n"
    )
    result = runner.check(spec, "Uni", "SPECIFICATION Spec\nINVARIANT Inv\n")
    assert result.outcome is Outcome.INVARIANT_VIOLATION
    assert [s["x"] for s in result.trace.states] == [0, 1, 2, 3]
