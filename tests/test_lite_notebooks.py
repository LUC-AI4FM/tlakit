"""The notebooks the browser build ships.

These are the first thing a visitor runs, and a broken one is worse than no
page at all -- it teaches the reader that the tool does not work. Executing
them here would mean a JVM and a network, so this checks the parts that went
wrong in practice and are checkable offline: that a config cell names a module
the notebook already defined, and that no cell asks for something the remote
runner refuses.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tlakit.routing import Cell, classify, module_name

FILES = Path(__file__).resolve().parent.parent / "lite" / "files"
NOTEBOOKS = sorted(FILES.glob("*.ipynb"))


def code_cells(path: Path) -> list[str]:
    nb = json.loads(path.read_text(encoding="utf-8"))
    return [
        "".join(cell["source"])
        for cell in nb["cells"]
        if cell["cell_type"] == "code"
    ]


def test_the_build_ships_notebooks():
    """A glob that silently matches nothing would make every test below pass."""
    assert {p.name for p in NOTEBOOKS} >= {
        "tla-in-your-browser.ipynb",
        "examples.ipynb",
        "scratch.ipynb",
    }


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_every_config_cell_names_a_module_defined_above_it(path: Path):
    """The failure this prevents is a cell that checks the wrong spec.

    A bare config cell is routed to the only module in scope, so a notebook
    that defines two and then writes a bare config is ambiguous -- the reader
    gets a RuntimeError where the page promised a result.
    """
    defined: set[str] = set()
    for index, code in enumerate(code_cells(path)):
        kind, rewritten = classify(code, defined)
        if kind == Cell.TLA:
            defined.add(module_name(code))
        elif kind == Cell.TLC:
            assert rewritten, (
                f"{path.name} cell {index}: a config cell with "
                f"{len(defined)} modules in scope ({sorted(defined)}) cannot "
                "be routed; name one with `%tlc:Module`"
            )


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_install_stands_alone_in_its_own_cell(path: Path):
    """`%pip` and the code that needs the package must not share a cell.

    JupyterLite rewrites `%pip install X` into `await piplite.install(...)`, so
    the ordering within one cell does hold today -- but that is a detail of
    someone else's input transformer, and the cell it would break is the first
    one a visitor runs. Two cells cost nothing and depend on nothing.
    """
    for index, code in enumerate(code_cells(path)):
        if "%pip" not in code:
            continue
        assert code.strip().splitlines() == [code.strip()], (
            f"{path.name} cell {index} runs %pip alongside other code; give "
            "the install a cell of its own"
        )


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_no_cell_asks_the_service_for_what_it_refuses(path: Path):
    """Options the public runner rejects, listed in `RemoteRunner.check`."""
    refused = ("heap=", "extra_opts=", "max_graph_nodes=", "animate=True")
    for index, code in enumerate(code_cells(path)):
        for option in refused:
            assert option not in code, (
                f"{path.name} cell {index} passes {option}, which the remote "
                "runner raises Unsupported for"
            )


#: An intentional exemption from the rule below, spelled out in the config
#: itself. A terminating spec whose invariant fails first reports the violation
#: rather than the dead end, and that is worth a sentence in the notebook -- but
#: it has to be written down, not inferred, because nothing offline can tell
#: which invariant actually fails.
DEADLOCK_WAIVER = r"\* deadlock:"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_a_terminating_spec_says_what_it_wants_from_the_deadlock_check(path: Path):
    """Otherwise TLC reports the reader's correct spec as deadlocked.

    Only specs that are *meant* to stop are at risk, and the marker for those
    in these notebooks is a `Done` operator. `LockOrder` is the deliberate
    counter-example -- it loops forever, so its dead end is a real one.

    Modules accumulate as the notebook goes, exactly as they do in a running
    kernel. Collecting them all up front instead would make an early bare
    config cell look ambiguous, and this check would skip it in silence.
    """
    modules: dict[str, str] = {}
    checked = 0
    for index, code in enumerate(code_cells(path)):
        kind, rewritten = classify(code, set(modules))
        if kind == Cell.TLA:
            modules[module_name(code)] = code
            continue
        if kind != Cell.TLC or not rewritten:
            continue
        target = rewritten.split("\n", 1)[0].removeprefix("%%tlc ").strip()
        if "\nDone ==" not in modules.get(target, ""):
            continue
        checked += 1
        assert "CHECK_DEADLOCK FALSE" in code or DEADLOCK_WAIVER in code, (
            f"{path.name} cell {index} checks {target}, which terminates, "
            "without CHECK_DEADLOCK FALSE and without a "
            f"`{DEADLOCK_WAIVER}` comment saying why not"
        )
    if path.name == "tla-in-your-browser.ipynb":
        # The tutorial is what this rule exists for, and a refactor that stops
        # matching its config cells would otherwise pass by checking nothing.
        assert checked == 2, f"expected 2 terminating checks, matched {checked}"
