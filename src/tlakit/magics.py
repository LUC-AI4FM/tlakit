"""IPython magics: %%tla, %%tlc, %tla_eval."""
from __future__ import annotations

import shlex

from IPython.core.magic import Magics, cell_magic, line_magic, magics_class

from .api import Spec, module_name_of

#: Module name -> source, for the current kernel session.
MODULES: dict[str, str] = {}


class TlaMagicError(RuntimeError):
    """Raised for usage errors in a magic, for example an unknown module."""


def _positional(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("--")]


@magics_class
class TlaMagics(Magics):
    """Registered by `%load_ext tlakit`."""

    @cell_magic
    def tla(self, line: str, cell: str):
        """Define a TLA+ module.

        Usage: `%%tla ModuleName [--no-parse]`. The module name may be omitted
        when the cell contains a `---- MODULE Name ----` header.
        """
        args = shlex.split(line)
        names = _positional(args)
        name = names[0] if names else module_name_of(cell)
        MODULES[name] = cell
        if "--no-parse" in args:
            return None
        return Spec(source=cell, name=name).parse()

    @cell_magic
    def tlc(self, line: str, cell: str):
        """Model-check a module defined earlier; the cell body is the .cfg.

        Usage: `%%tlc ModuleName [--timeout=SECONDS]`.
        """
        args = shlex.split(line)
        names = _positional(args)
        if not names:
            raise TlaMagicError("Usage: %%tlc ModuleName")
        name = names[0]
        if name not in MODULES:
            known = ", ".join(sorted(MODULES)) or "none"
            raise TlaMagicError(
                f"No module named {name!r} in this session. Define it with "
                f"`%%tla {name}` first. Known modules: {known}."
            )
        timeout = None
        for arg in args:
            if arg.startswith("--timeout="):
                timeout = float(arg.split("=", 1)[1])
        return Spec(source=MODULES[name], name=name).check(
            config=cell, timeout=timeout
        )

    @line_magic
    def tla_eval(self, line: str):
        """Evaluate a constant TLA+ expression. Arrives in M2."""
        raise TlaMagicError(
            "%tla_eval arrives in M2 together with the REPL runner. For now, "
            "express the property as an INVARIANT and use %%tlc."
        )
