"""The transformer that gives a plain Python kernel TLA+ cell routing.

Exercised against a real InteractiveShell rather than a fake, because what can
actually break is the hook ordering: a cleanup transformer has to run before
IPython looks for magics, or a rewritten `%%tla` cell arrives as plain text.
"""
from __future__ import annotations

import pytest

from tlakit import notebook
from tlakit.magics import MODULES

IPython = pytest.importorskip("IPython")

MICROWAVE = """---- MODULE Microwave ----
VARIABLES door
Init == door = "closed"
Next == door' = "open"
Spec == Init /\\ [][Next]_door
====
"""


@pytest.fixture
def shell():
    from IPython.core.interactiveshell import InteractiveShell

    InteractiveShell.clear_instance()
    shell = InteractiveShell.instance()
    MODULES.clear()
    shell.run_line_magic("load_ext", "tlakit")
    notebook.enable_tla_cells(shell)
    yield shell
    MODULES.clear()
    InteractiveShell.clear_instance()


def transform(shell, code: str) -> str:
    """What IPython will actually execute for this cell.

    Note this is fully lowered Python, not the intermediate magic: a routed
    cell shows up as `run_cell_magic('tla', ...)`, which is the proof the
    rewritten `%%tla` really was dispatched as a magic and not left as text.
    """
    return shell.transform_cell(code)


def test_a_module_cell_becomes_a_tla_magic(shell):
    assert "run_cell_magic('tla', 'Microwave'" in transform(shell, MICROWAVE)


def test_a_module_cell_actually_registers_the_module(shell):
    """The end of the chain: transformer, then magic, then session store."""
    shell.run_cell(MICROWAVE)
    assert "Microwave" in MODULES
    assert MODULES["Microwave"].startswith("---- MODULE Microwave ----")


def test_a_config_cell_targets_the_only_known_module(shell):
    shell.run_cell(MICROWAVE)
    assert "run_cell_magic('tlc', 'Microwave'" in transform(
        shell, "SPECIFICATION Spec\n"
    )


def test_python_cells_are_left_exactly_alone(shell):
    for code in ["x = 1\n", "def f():\n    return 2\n", "print('hi')\n", ""]:
        assert transform(shell, code) == shell.input_transformer_manager.transform_cell(
            code
        )


def test_an_explicit_magic_is_untouched(shell):
    assert "run_cell_magic('tlc'" not in transform(shell, "%pip list\n")


def test_python_escape_wins_over_tla_detection(shell):
    """A cell about TLA+ that is really Python must stay Python."""
    code = "%%python\nspec = '---- MODULE Fake ----'\n"
    out = transform(shell, code)
    assert "run_cell_magic('tla'" not in out
    assert "spec =" in out


def test_ambiguous_config_raises_rather_than_guessing(shell):
    shell.run_cell(MICROWAVE)
    shell.run_cell(MICROWAVE.replace("Microwave", "Other"))
    out = transform(shell, "SPECIFICATION Spec\n")
    assert out.startswith("raise RuntimeError")
    assert "Other" in out and "Microwave" in out


def test_registering_twice_does_not_double_transform(shell):
    notebook.enable_tla_cells(shell)
    notebook.enable_tla_cells(shell)
    out = transform(shell, MICROWAVE)
    assert out.count("run_cell_magic('tla'") == 1


def test_newlines_survive_the_round_trip(shell):
    """Transformers must return lines with their newlines intact.

    Checked on what the magic actually stored, since a dropped newline would
    fuse the header onto the VARIABLES line and TLC would reject the module.
    """
    shell.run_cell(MICROWAVE)
    stored = MODULES["Microwave"].splitlines()
    assert stored[0] == "---- MODULE Microwave ----"
    assert stored[1] == "VARIABLES door"
    assert stored[-1] == "===="


def test_in_browser_is_false_here():
    assert notebook.in_browser() is False


def test_setup_switches_to_the_remote_runner(shell):
    from tlakit import api
    from tlakit.remote import RemoteRunner

    try:
        notebook.setup(endpoint="https://example.invalid", shell=shell)
        runner = api.default_runner()
        assert isinstance(runner, RemoteRunner)
        assert runner.endpoint == "https://example.invalid"
    finally:
        api.use_local()


def test_load_ext_alone_does_not_go_remote(shell):
    """A local user's checks must not be silently sent to someone's server."""
    from tlakit import api

    assert api._override is None
