"""Issue #25: the kernel's only judgement is what a cell means."""
import pytest

from tlakit.kernel.routing import Cell, ambiguous_config_message, classify, module_name

MODULE = "---- MODULE Widget ----\nVARIABLE x\nInit == x = 0\n===="
CONFIG = "SPECIFICATION Spec\nINVARIANT Inv"


def test_module_cell_is_routed_to_the_tla_magic():
    kind, code = classify(MODULE)
    assert kind == Cell.TLA
    assert code.startswith("%%tla Widget\n")
    assert MODULE in code


def test_module_name_is_read_from_the_header():
    assert module_name(MODULE) == "Widget"
    assert module_name("no header") is None


def test_legacy_tlc_header_still_works():
    """kelvich/tlaplus_jupyter notebooks use `%tlc:Name` for config cells."""
    kind, code = classify(f"%tlc:Widget\n{CONFIG}")
    assert kind == Cell.TLC
    assert code.startswith("%%tlc Widget\n")
    assert "SPECIFICATION Spec" in code
    assert "%tlc:" not in code


def test_bare_config_uses_the_only_module_defined():
    kind, code = classify(CONFIG, {"Widget"})
    assert kind == Cell.TLC
    assert code.startswith("%%tlc Widget\n")


def test_bare_config_with_several_modules_refuses_to_guess():
    kind, code = classify(CONFIG, {"Alpha", "Beta"})
    assert kind == Cell.TLC
    assert code == ""
    message = ambiguous_config_message({"Alpha", "Beta"})
    assert "Alpha" in message and "Beta" in message
    assert "%tlc:Alpha" in message


def test_bare_config_with_no_modules_says_so():
    kind, code = classify(CONFIG, set())
    assert kind == Cell.TLC and code == ""
    assert "no module has been defined" in ambiguous_config_message(set())


def test_ordinary_python_passes_through_untouched():
    src = "import tlakit\nprint(tlakit.__version__)"
    assert classify(src) == (Cell.PYTHON, src)


def test_python_is_the_point_of_building_on_ipython():
    """A TLA+ notebook must still hold the Python that generates specs."""
    src = "df = result.trace.to_dataframe()"
    kind, code = classify(src, {"Widget"})
    assert kind == Cell.PYTHON
    assert code == src


def test_explicit_magics_are_left_alone():
    for src in ("%load_ext tlakit", "!ls", "%%tlc Widget\nSPECIFICATION Spec"):
        kind, code = classify(src, {"Widget"})
        assert kind == Cell.PYTHON
        assert code == src


def test_python_escape_strips_its_own_marker():
    kind, code = classify("%%python\nSPECIFICATION = 1")
    assert kind == Cell.PYTHON
    assert code == "SPECIFICATION = 1"


def test_empty_cell_is_harmless():
    assert classify("   ") == (Cell.PYTHON, "   ")


@pytest.mark.parametrize(
    "keyword", ["SPECIFICATION", "INIT", "INVARIANT", "PROPERTY", "CONSTANT", "ALIAS"]
)
def test_config_keywords_are_recognised(keyword):
    kind, _ = classify(f"{keyword} Foo", {"Widget"})
    assert kind == Cell.TLC
