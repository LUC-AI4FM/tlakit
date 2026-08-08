"""Make an ordinary Python kernel behave like the TLA+ kernel.

The `tlakit` kernel subclasses `IPythonKernel`, which needs a real Python
process. JupyterLite has no such thing -- its kernels are WASM -- so a
zero-install notebook cannot use that kernel at all.

What it *can* use is the same routing decision. `classify` is deliberately free
of any Jupyter import, so installing it as an IPython input transformer gives a
plain Pyodide kernel the behaviour that matters: a cell starting with
`---- MODULE Foo ----` is TLA+, a cell of config keywords checks the module in
scope, and everything else is still Python.

This is not a lesser fallback bolted on afterwards. The kernel and this
transformer share one tested implementation of the only judgement either makes;
they differ solely in where they hook into IPython.
"""
from __future__ import annotations

import sys
from typing import Any

from .routing import Cell, ambiguous_config_message, classify


def _known_modules() -> set[str]:
    from .magics import MODULES

    return set(MODULES)


def tla_transformer(lines: list[str]) -> list[str]:
    """Rewrite a TLA+ or config cell into the magic that runs it.

    IPython hands transformers a list of lines with their newlines intact and
    expects the same back, so the round trip through a single string has to
    preserve them -- `splitlines(keepends=True)` rather than a plain split.
    """
    code = "".join(lines)
    kind, rewritten = classify(code, _known_modules())
    if kind == Cell.PYTHON:
        return lines
    if kind == Cell.TLC and not rewritten:
        # Refusing is the honest outcome: guessing which module a bare config
        # belongs to would silently check the wrong spec.
        message = ambiguous_config_message(_known_modules()).replace('"', "'")
        return [f'raise RuntimeError("{message}")\n']
    return rewritten.splitlines(keepends=True)


def enable_tla_cells(shell: Any = None) -> None:
    """Route TLA+ cells without needing the tlakit kernel.

    Registered as a cleanup transformer, which runs before IPython looks for
    magics -- that ordering is what lets a bare module cell turn into `%%tla`
    and still be dispatched as a magic in the same execution.
    """
    shell = shell or _get_shell()
    if shell is None:
        raise RuntimeError("no IPython shell; call this from inside a notebook")
    existing = getattr(shell, "input_transformers_cleanup", None)
    if existing is None:
        raise RuntimeError(
            "this IPython is too old for input_transformers_cleanup; use the "
            "tlakit kernel instead"
        )
    # Idempotent: %load_ext can be run twice, and two copies would classify the
    # already-rewritten cell a second time.
    if not any(getattr(fn, "__name__", "") == "tla_transformer" for fn in existing):
        existing.append(tla_transformer)


def _get_shell() -> Any:
    try:
        from IPython import get_ipython
    except ImportError:  # pragma: no cover - IPython is a notebook-only dep
        return None
    return get_ipython()


def in_browser() -> bool:
    """True under Pyodide, where there is no JVM and no subprocess."""
    return sys.platform == "emscripten"


def setup(endpoint: str | None = None, shell: Any = None) -> None:
    """Configure a notebook to check specs remotely, with TLA+ cell routing.

    Called automatically by `%load_ext tlakit` when running in a browser, since
    there a remote runner is the only one that can work -- Pyodide has neither
    Java nor `subprocess`. Off the browser it stays opt-in: silently redirecting
    a local user's checks to someone else's server would be surprising, and
    their own TLC is faster and unmetered.
    """
    from .api import use_remote

    use_remote(endpoint)
    enable_tla_cells(shell)
