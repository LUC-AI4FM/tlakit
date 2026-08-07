import pytest

import tlakit
from tlakit.api import build_config, module_name_of, tla_value


def test_tla_value_renders_scalars():
    assert tla_value(3) == "3"
    assert tla_value("abc") == '"abc"'
    assert tla_value(True) == "TRUE"
    assert tla_value(False) == "FALSE"


def test_tla_value_renders_collections():
    assert tla_value([1, 2]) == "<<1, 2>>"
    assert tla_value({1}) == "{1}"
    assert tla_value({"a": 1}) == "[a |-> 1]"


def test_tla_value_escapes_strings():
    assert tla_value('he said "hi"') == '"he said \\"hi\\""'


def test_tla_value_rejects_unknown_types():
    with pytest.raises(TypeError):
        tla_value(object())


def test_build_config_emits_constants_and_invariants():
    cfg = build_config(spec="Spec", constants={"N": 3}, invariants=["Inv"])
    assert "SPECIFICATION Spec" in cfg
    assert "CONSTANT" in cfg and "N = 3" in cfg
    assert "INVARIANT Inv" in cfg


def test_build_config_uses_init_next_when_no_spec():
    cfg = build_config(init="Init", next_="Next", invariants=["Inv"])
    assert "INIT Init" in cfg and "NEXT Next" in cfg
    assert "SPECIFICATION" not in cfg


def test_load_reads_module_name_from_disk(tmp_path):
    p = tmp_path / "Widget.tla"
    p.write_text("---- MODULE Widget ----\nVARIABLE x\n====\n")
    spec = tlakit.load(p)
    assert spec.name == "Widget"
    assert "MODULE Widget" in spec.source
    assert spec.path == p


def test_module_name_is_inferred_from_source():
    assert module_name_of("---- MODULE Inferred ----\nVARIABLE x\n====\n") == (
        "Inferred"
    )


def test_module_name_of_raises_when_absent():
    with pytest.raises(ValueError):
        module_name_of("no module header here")
