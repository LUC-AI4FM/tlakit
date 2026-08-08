"""The notebooks the browser build ships.

These are the first thing a visitor runs, and a broken one is worse than no
page at all -- it teaches the reader that the tool does not work.

Two layers. The structural checks need nothing but the notebook JSON: that a
config cell names a module the notebook already defined, that `%pip` has a cell
to itself, that no cell asks for an option the service refuses. The `java`-
marked ones below run every module through SANY and every config cell through
TLC, and compare the outcome against what the surrounding prose promises the
reader -- the layer that would have caught all three bugs the published page
shipped with.

Nothing here reaches the public runner. A suite that depended on a live service
would fail for reasons having nothing to do with the commit.
"""
from __future__ import annotations

import json
import re
import shutil
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


def markdown_cells(path: Path) -> list[str]:
    nb = json.loads(path.read_text(encoding="utf-8"))
    return [
        "".join(cell["source"])
        for cell in nb["cells"]
        if cell["cell_type"] == "markdown"
    ]


#: How one notebook is allowed to link to another. JupyterLab's markdown
#: renderer rewrites every *relative* href into `/files/<the whole thing,
#: percent-encoded>` -- so `[x](examples.ipynb)` serves raw JSON as a download,
#: and `[x](../lab/index.html?path=examples.ipynb)` 404s with the `?` encoded
#: away. Measured in both the lab and notebooks apps on 2026-08-08. Absolute
#: paths are left alone, which makes these the only forms that open anything.
APP_URLS = ("/lab/index.html?path=", "/notebooks/index.html?path=")


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_links_between_notebooks_open_an_app(path: Path):
    found = 0
    for source in markdown_cells(path):
        for href in re.findall(r"\]\(([^)\s]+)\)", source):
            if ".ipynb" not in href:
                continue
            found += 1
            assert href.startswith(APP_URLS), (
                f"{path.name} links to {href!r}; a relative notebook link is "
                f"rewritten into a raw download. Use one of {APP_URLS}."
            )
    assert found or path.name == "scratch.ipynb"


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


# --------------------------------------------------------------------------
# Against a real TLC. Everything above is structural; this is the layer that
# would have caught all three bugs the published page actually shipped with.
# --------------------------------------------------------------------------

#: What each config cell in these notebooks is supposed to report, written down
#: rather than inferred, so a spec that quietly changes its answer is a failure
#: instead of a new baseline. The prose around each cell tells the reader the
#: same thing; this is that claim, made executable.
EXPECTED_OUTCOMES = {
    # The bug the tutorial is about. This spec terminates, so it would deadlock
    # too -- but TLC evaluates the invariant on the offending state before it
    # ever tries to expand it, so the violation is what gets reported.
    ("tla-in-your-browser.ipynb", "LostUpdate"): "invariant_violation",
    # The fix, and clean only because the config turns the deadlock check off.
    ("tla-in-your-browser.ipynb", "Atomic"): "ok",
    # Not a termination artifact: this spec loops forever by construction.
    ("examples.ipynb", "LockOrder"): "deadlock",
}


@pytest.fixture(scope="module")
def runner():
    """A local TLC with CommunityModules refused.

    The public runner has none, so a notebook that leans on one has to fail
    here rather than pass locally and break for every visitor.
    """
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        return tlakit.CliRunner(community_jar=False)
    except JarNotFound as exc:
        pytest.skip(str(exc))


def modules_and_configs(path: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Walk a notebook the way a kernel does, pairing configs with modules."""
    modules: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for code in code_cells(path):
        kind, rewritten = classify(code, set(modules))
        if kind == Cell.TLA:
            modules[module_name(code)] = code
        elif kind == Cell.TLC and rewritten:
            head, _, body = rewritten.partition("\n")
            pairs.append((head.removeprefix("%%tlc ").strip(), body))
    return modules, pairs


@pytest.mark.java
@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_every_module_parses(runner, path: Path):
    """SANY on each module -- the check the browser itself cannot run.

    The service exposes no parser, which is exactly why `LostUpdate` shipped
    calling Cardinality without EXTENDS FiniteSets: nothing on the page could
    tell anyone until a reader ran the cell and got a traceback.
    """
    modules, _ = modules_and_configs(path)
    assert modules or path.name == "scratch.ipynb", f"{path.name} defines none"
    for name, source in modules.items():
        result = runner.parse(source, name)
        assert result.ok, f"{path.name}: {name} does not parse: {result.errors}"


@pytest.mark.java
@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_every_config_cell_reports_what_the_notebook_says_it_does(runner, path):
    """The end of the chain: run it, and hold it against the prose."""
    modules, pairs = modules_and_configs(path)
    for name, config in pairs:
        expected = EXPECTED_OUTCOMES.get((path.name, name))
        assert expected is not None, (
            f"{path.name} checks {name}, which has no entry in "
            "EXPECTED_OUTCOMES; add what the notebook claims it reports"
        )
        result = runner.check(modules[name], name, config, timeout=60)
        assert result.outcome.value == expected, (
            f"{path.name}: {name} reported {result.outcome.value}, and the "
            f"notebook tells the reader to expect {expected}"
        )
