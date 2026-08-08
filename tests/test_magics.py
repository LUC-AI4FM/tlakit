import pytest

pytest.importorskip("IPython")

from IPython.testing.globalipapp import start_ipython  # noqa: E402

from tlakit.magics import MODULES, TlaMagicError  # noqa: E402

WIDGET = "---- MODULE Widget ----\nVARIABLE x\n====\n"


@pytest.fixture(scope="module")
def ip():
    shell = start_ipython()
    shell.run_line_magic("load_ext", "tlakit")
    return shell


@pytest.fixture(autouse=True)
def clean_modules():
    MODULES.clear()
    yield
    MODULES.clear()


def test_tla_cell_stores_the_module(ip):
    ip.run_cell_magic("tla", "Widget --no-parse", WIDGET)
    assert "Widget" in MODULES
    assert "MODULE Widget" in MODULES["Widget"]


def test_tla_cell_infers_the_module_name(ip):
    ip.run_cell_magic("tla", "--no-parse", WIDGET)
    assert "Widget" in MODULES


def test_tlc_cell_without_a_defined_module_raises(ip):
    with pytest.raises(TlaMagicError, match="No module named"):
        ip.run_cell_magic("tlc", "NeverDefined", "SPECIFICATION Spec\n")


def test_tlc_cell_without_a_name_raises(ip):
    with pytest.raises(TlaMagicError, match="Usage"):
        ip.run_cell_magic("tlc", "", "SPECIFICATION Spec\n")


def test_tla_eval_requires_an_expression(ip):
    with pytest.raises(TlaMagicError, match="Usage"):
        ip.run_line_magic("tla_eval", "")


# --- issue #17: %tla_eval via tlc2.REPL -------------------------------------
# No jar needed: CliRunner.eval() is faked at the point magics.py calls it,
# so what's under test is the magic's wiring (usage errors, value passthrough,
# diagnostics-to-exception, and that MODULES is handed through), not the REPL
# subprocess itself -- that is covered by CliRunner's own (java-marked) tests.


class _FakeRunner:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def eval(self, expr, modules=None, timeout=None):
        self.calls.append((expr, modules))
        return self._result


def test_tla_eval_returns_the_evaluated_value(ip, monkeypatch):
    from tlakit.cli import EvalResult
    from tlakit.result import Outcome, RawOutput

    fake = _FakeRunner(EvalResult(Outcome.OK, 42, [], RawOutput([], 0, "42", "")))
    monkeypatch.setattr("tlakit.magics.default_runner", lambda: fake)

    assert ip.run_line_magic("tla_eval", "40 + 2") == 42
    assert fake.calls == [("40 + 2", MODULES)]


def test_tla_eval_can_see_a_module_defined_earlier(ip, monkeypatch):
    ip.run_cell_magic("tla", "Widget --no-parse", WIDGET)
    from tlakit.cli import EvalResult
    from tlakit.result import Outcome, RawOutput

    fake = _FakeRunner(EvalResult(Outcome.OK, 1, [], RawOutput([], 0, "1", "")))
    monkeypatch.setattr("tlakit.magics.default_runner", lambda: fake)

    ip.run_line_magic("tla_eval", "1")

    assert fake.calls[0][1] == {"Widget": WIDGET}


def test_tla_eval_raises_with_diagnostic_detail_on_failure(ip, monkeypatch):
    from tlakit.cli import EvalResult
    from tlakit.result import Diagnostic, Outcome, RawOutput, Severity

    fake = _FakeRunner(
        EvalResult(
            Outcome.EVALUATION_ERROR,
            None,
            [Diagnostic(Severity.ERROR, "Unknown operator: `nope'.")],
            RawOutput([], 0, "", ""),
        )
    )
    monkeypatch.setattr("tlakit.magics.default_runner", lambda: fake)

    with pytest.raises(TlaMagicError, match="Unknown operator"):
        ip.run_line_magic("tla_eval", "nope")
