"""Decide what a notebook cell means.

Kept out of the kernel package, not merely out of the kernel class. Two clients
need this decision -- the kernel, and the input transformer that gives a browser
kernel the same behaviour -- and importing it through `tlakit.kernel` would drag
in ipykernel, which does not exist under Pyodide. Nothing here imports Jupyter,
which is also what makes routing testable without a running one.
"""
from __future__ import annotations

import re

#: `---- MODULE Name ----`
_MODULE_HEADER = re.compile(r"^\s*-{4,}\s*MODULE\s+(\w+)", re.MULTILINE)

#: `%tlc:ModuleName` on the first line — the config-cell form used by
#: kelvich/tlaplus_jupyter. Accepted so its notebooks keep working.
_LEGACY_CONFIG = re.compile(r"^\s*%tlc:\s*(\w+)\s*$", re.MULTILINE)

#: A TLC configuration: keyword lines, no TLA+ definitions.
_CONFIG_KEYWORD = re.compile(
    r"^\s*(SPECIFICATION|INIT|NEXT|INVARIANT|PROPERTY|CONSTANT[S]?|SYMMETRY"
    r"|VIEW|ALIAS|CHECK_DEADLOCK|POSTCONDITION|CONSTRAINT|ACTION_CONSTRAINT)\b",
    re.MULTILINE,
)

#: Explicit escapes. `%%python` runs the cell as ordinary Python.
_PYTHON_ESCAPE = re.compile(r"^\s*%%(python|py)\s*$", re.MULTILINE)


class Cell:
    """What a cell should be run as."""

    TLA = "tla"
    TLC = "tlc"
    PYTHON = "python"


def module_name(code: str) -> str | None:
    match = _MODULE_HEADER.search(code)
    return match.group(1) if match else None


def classify(code: str, known_modules: set[str] | None = None) -> tuple[str, str]:
    """Return `(kind, rewritten_code)` for a cell.

    A cell is TLA+ when it declares a module, and a config when it names a
    module to check. Anything else is Python — which is the point of building
    on IPython rather than replacing it: a TLA+ notebook can still hold the
    Python that generates specs or reads their traces.
    """
    if _PYTHON_ESCAPE.search(code):
        return Cell.PYTHON, _PYTHON_ESCAPE.sub("", code, count=1).lstrip("\n")

    stripped = code.strip()
    if not stripped:
        return Cell.PYTHON, code

    # Already an explicit magic or shell escape: leave it alone.
    if stripped.startswith(("%", "!", "?")) and not _LEGACY_CONFIG.match(stripped):
        return Cell.PYTHON, code

    name = module_name(code)
    if name is not None:
        return Cell.TLA, f"%%tla {name}\n{code}"

    legacy = _LEGACY_CONFIG.match(stripped)
    if legacy is not None:
        body = _LEGACY_CONFIG.sub("", code, count=1).lstrip("\n")
        return Cell.TLC, f"%%tlc {legacy.group(1)}\n{body}"

    if _CONFIG_KEYWORD.search(code):
        # A bare config cell only makes sense against a module already defined.
        # With exactly one, the intent is unambiguous; otherwise say so rather
        # than guessing which.
        modules = sorted(known_modules or ())
        if len(modules) == 1:
            return Cell.TLC, f"%%tlc {modules[0]}\n{code}"
        return Cell.TLC, ""

    return Cell.PYTHON, code


def ambiguous_config_message(known_modules: set[str] | None) -> str:
    modules = sorted(known_modules or ())
    if not modules:
        return (
            "This looks like a TLC configuration, but no module has been "
            "defined in this session yet. Run a cell containing "
            "`---- MODULE Name ----` first."
        )
    listed = ", ".join(modules)
    return (
        "This looks like a TLC configuration, but several modules are defined "
        f"({listed}). Name the one you mean on the first line, for example "
        f"`%tlc:{modules[0]}`."
    )
