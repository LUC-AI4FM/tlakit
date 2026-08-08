"""Issue #28: the public API has to stay documented, not just be documented.

A reference page written once and never checked drifts within a release. These
tests are the check. They are deliberately about *coverage*, not prose: they
assert that a public name has a docstring and that the two things with nowhere
to put one -- environment variables and notebook magics -- appear on
`docs/reference.md`. What the words say is a review question; whether they
exist at all should not be.

None of this needs Java.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import tlakit

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "tlakit"
REFERENCE = REPO / "docs" / "reference.md"


def _documented(obj: object) -> bool:
    doc = inspect.getdoc(obj)
    return bool(doc and doc.strip())


def _public_names() -> list[str]:
    return sorted(tlakit.__all__)


# --------------------------------------------------------------------------
# Every exported name resolves and carries a docstring.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", _public_names())
def test_every_exported_name_is_actually_exported(name: str):
    """`__all__` must not promise a name the package does not have.

    `from tlakit import *` hides this: the import machinery imports a submodule
    named in `__all__` as a side effect, so a missing submodule import passes
    the star-import smoke test and fails only on plain attribute access. That
    is exactly how `tlakit.remote` was broken.
    """
    assert hasattr(tlakit, name), f"tlakit.__all__ names {name!r}, which does not exist"


@pytest.mark.parametrize("name", _public_names())
def test_every_exported_name_has_a_docstring(name: str):
    obj = getattr(tlakit, name)
    assert _documented(obj), f"tlakit.{name} has no docstring"


@pytest.mark.parametrize("name", _public_names())
def test_public_methods_and_properties_are_documented(name: str):
    """Reaching one level in: a documented class whose methods are not is not
    a documented class. Dunders are excluded, except that the ones defining
    a protocol a caller uses directly are not exempt anywhere they exist."""
    obj = getattr(tlakit, name)
    if not inspect.isclass(obj):
        pytest.skip(f"{name} is not a class")
    undocumented = [
        f"{name}.{attr}"
        for attr, value in vars(obj).items()
        if not attr.startswith("_")
        and (inspect.isfunction(value) or isinstance(value, property))
        and not _documented(value)
    ]
    assert not undocumented, f"undocumented: {', '.join(undocumented)}"


# --------------------------------------------------------------------------
# The two things with no docstring to live in must live on the reference page.
# --------------------------------------------------------------------------


def _env_vars_read_in_source() -> set[str]:
    """Every `TLAKIT_*` string literal appearing anywhere under `src/tlakit`.

    Read from the source text rather than by importing, because the names are
    assigned to module constants (`ENV_TOOLS = "TLAKIT_TLA2TOOLS"`) as often as
    they are passed to `os.environ.get` directly.
    """
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        found |= set(re.findall(r"TLAKIT_[A-Z0-9_]+", path.read_text(encoding="utf-8")))
    return found


def test_every_environment_variable_is_on_the_reference_page():
    """Compares whole tokens, not substrings.

    A substring test is worse than useless here, because the names nest:
    `TLAKIT_SERVE_KEY` occurs inside `TLAKIT_SERVE_KEY_FILE`, so documenting
    only the second would silently satisfy a naive `in` check for the first.
    """
    documented = set(
        re.findall(r"TLAKIT_[A-Z0-9_]+", REFERENCE.read_text(encoding="utf-8"))
    )
    missing = sorted(_env_vars_read_in_source() - documented)
    assert not missing, (
        f"{', '.join(missing)} read in src/tlakit but absent from docs/reference.md. "
        "Issue #28's requirement is that the variables are documented in one place; "
        "add them there rather than deleting this assertion."
    )


def _registered_magics() -> set[str]:
    """Magic names from `magics.py`'s decorators, without importing IPython.

    A `@cell_magic`/`@line_magic` method's name is the magic's name, so the
    decorator list is the registry. Parsed with `ast` so this test runs with
    or without the `notebook` extra installed.
    """
    tree = ast.parse((SRC / "magics.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id in {
                "cell_magic",
                "line_magic",
            }:
                names.add(node.name)
    return names


def test_the_magics_are_the_ones_the_reference_page_documents():
    """Both directions. A new magic that nobody documented fails here, and so
    does a documented magic that was renamed or removed -- the second is the
    one a docs-coverage check usually misses."""
    registered = _registered_magics()
    assert registered, "found no magics at all; the ast walk has stopped matching"

    text = REFERENCE.read_text(encoding="utf-8")
    # `%%tla` is a prefix of `%%tla_eval`-style names, so match the heading
    # form (`### %%tla ModuleName`) rather than a bare substring.
    documented = {
        m.group("name")
        for m in re.finditer(r"^###\s+`%{1,2}(?P<name>\w+)", text, re.M)
    }

    assert registered == documented, (
        f"magics registered but undocumented: {sorted(registered - documented)}; "
        f"documented but not registered: {sorted(documented - registered)}"
    )


# --------------------------------------------------------------------------
# A regression test for the specific defect the audit turned up.
# --------------------------------------------------------------------------


def test_no_unreachable_code_after_a_return_in_the_public_api_module():
    """`api.py` carried a duplicate `sweep` method nested inside
    `check_source`, after its `return` -- a bad merge that the interpreter
    accepts silently because a nested `def` is valid anywhere. Nothing in
    `api.py` should have statements following a `return` at the same level.
    """
    tree = ast.parse((SRC / "api.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, statement in enumerate(body[:-1]):
            if isinstance(statement, ast.Return):
                offenders.append(
                    f"line {body[i + 1].lineno}: unreachable after return on "
                    f"line {statement.lineno}"
                )
    assert not offenders, "; ".join(offenders)
