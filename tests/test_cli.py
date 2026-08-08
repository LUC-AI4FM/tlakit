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
