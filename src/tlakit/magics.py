"""IPython magics: %%tla, %%tlc, %tla_eval."""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from IPython.core.magic import Magics, cell_magic, line_magic, magics_class

from .api import Spec, default_runner, module_name_of
from .source import declared_variables

#: Module name -> source, for the current kernel session.
MODULES: dict[str, str] = {}


class TlaMagicError(RuntimeError):
    """Raised for usage errors in a magic, for example an unknown module."""


@dataclass(frozen=True)
class ModuleDefined:
    """What `%%tla` shows when it could not parse the module.

    Against the remote runner there is no SANY to call, so the alternative is a
    cell that produces nothing at all -- and in a tutorial where the reader
    cannot see a filesystem, silence is indistinguishable from failure. This
    says what was stored and what to do next; it makes no claim about validity,
    because nothing has read the spec yet.
    """

    name: str
    variables: list[str]

    def __repr__(self) -> str:
        return f"<module {self.name} stored; not yet parsed>"

    def _repr_html_(self) -> str:
        from .render import module_defined_html  # lazy: render imports result

        return module_defined_html(self)


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

        Parsing here is a convenience, not the point of the cell. A runner that
        cannot parse at all skips it; so does one that could not be reached.

        That second case is why this is not simply `return spec.parse()`. Since
        #67 the remote runner *can* parse, which means the browser's module
        cell now depends on a network round trip -- and a service that is down,
        busy, or rate-limiting would otherwise turn every `%%tla` into a
        traceback. Storing the module is the part that must not fail: `%%tlc`
        reads it from `MODULES`, and TLC parses before it explores, so a
        syntax error still surfaces from the check either way.

        A parse *error* is not caught here. That is an answer about the spec,
        and it is the answer the reader asked for.
        """
        args = shlex.split(line)
        names = _positional(args)
        name = names[0] if names else module_name_of(cell)
        MODULES[name] = cell
        spec = Spec(source=cell, name=name)
        defined = ModuleDefined(name=name, variables=declared_variables(cell))
        if "--no-parse" in args or not spec.can_parse:
            return defined
        try:
            return spec.parse()
        except Exception:
            # Deliberately broad: every runner reaches SANY its own way, and
            # the failure modes range from RemoteError to a missing JVM. None
            # of them is a fact about the module, and none is worth losing the
            # cell over.
            return defined

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
        """Evaluate a constant TLA+ expression with `tlc2.REPL`.

        Usage: `%tla_eval <expression>`. The expression may reference an
        operator from any module defined earlier in the session with `%%tla`.
        """
        expr = line.strip()
        if not expr:
            raise TlaMagicError("Usage: %tla_eval <expression>")
        result = default_runner().eval(expr, modules=MODULES)
        if not result.ok:
            detail = "; ".join(str(d) for d in result.diagnostics) or "evaluation failed"
            raise TlaMagicError(f"%tla_eval failed: {detail}")
        return result.value
