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


def test_tla_eval_points_at_m2(ip):
    with pytest.raises(TlaMagicError, match="M2"):
        ip.run_line_magic("tla_eval", "1 + 1")
