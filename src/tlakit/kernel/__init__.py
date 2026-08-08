"""A Jupyter kernel whose cell language is TLA+.

The kernel implements no protocol of its own. It subclasses `IPythonKernel`,
preloads tlakit's magics, declares TLA+ as the language, and rewrites each cell
into the magic that already handles it.

That is deliberate. kelvich/tlaplus_jupyter was a from-scratch kernel and died
of exactly this: its `do_execute` took a parameter named `payload`, ipykernel
began dispatching by keyword as `code`, and every cell hung until it timed out.
A kernel that owns no protocol cannot fail that way, and if this file is ever
deleted the magics keep working.
"""
from __future__ import annotations

from ipykernel.ipkernel import IPythonKernel

from .. import __version__
from ..routing import Cell, ambiguous_config_message, classify

__all__ = ["TlaKernel", "Cell", "classify"]


class TlaKernel(IPythonKernel):
    implementation = "tlakit"
    implementation_version = __version__
    banner = (
        "TLA⁺ via tlakit — module cells define, config cells check, "
        "everything else is Python."
    )

    language_info = {
        "name": "tlaplus",
        "mimetype": "text/x-tlaplus",
        "file_extension": ".tla",
        "codemirror_mode": "tlaplus",
        "pygments_lexer": "tlaplus",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.shell.run_line_magic("load_ext", "tlakit")

    @property
    def _known_modules(self) -> set[str]:
        from ..magics import MODULES

        return set(MODULES)

    def _error(self, message: str) -> dict:
        self.send_response(
            self.iopub_socket,
            "stream",
            {"name": "stderr", "text": message + "\n"},
        )
        return {
            "status": "error",
            "execution_count": self.execution_count,
            "ename": "TlaCellError",
            "evalue": message,
            "traceback": [message],
        }

    def do_execute(
        self,
        code,
        silent,
        store_history=True,
        user_expressions=None,
        allow_stdin=False,
        *,
        cell_id=None,
        **kwargs,
    ):
        """Route the cell, then let IPython run the result.

        The first parameter must be named `code`: ipykernel dispatches by
        keyword. `cell_id` and `**kwargs` absorb whatever later protocol
        versions add.
        """
        kind, rewritten = classify(code, self._known_modules)
        if kind == Cell.TLC and not rewritten:
            return self._error(ambiguous_config_message(self._known_modules))
        return super().do_execute(
            rewritten,
            silent,
            store_history=store_history,
            user_expressions=user_expressions,
            allow_stdin=allow_stdin,
            cell_id=cell_id,
            **kwargs,
        )
