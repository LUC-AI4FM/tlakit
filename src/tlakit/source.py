"""Read facts out of TLA+ module text.

Only what tlakit genuinely cannot get from the tools. Anything the toolchain
already answers should be asked of the toolchain instead — see SANY's
`tlaplus_mcp_sany_symbol` for the general version of this.
"""
from __future__ import annotations

import re

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# `VARIABLE x`, `VARIABLES x, y`, or a declaration broken across lines. The body
# stops at the first token that is not an identifier or a separating comma, so a
# following definition is never swallowed.
_DECLARATION = re.compile(
    rf"^[ \t]*VARIABLES?\b\s*((?:{_IDENT}\s*,\s*)*{_IDENT})",
    re.MULTILINE,
)

# TLA+ line comments run to end of line; block comments are (* ... *).
_LINE_COMMENT = re.compile(r"\\\*.*$", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"\(\*.*?\*\)", re.DOTALL)


def strip_comments(source: str) -> str:
    """Blank out comments while preserving line structure."""
    without_block = _BLOCK_COMMENT.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), source
    )
    return _LINE_COMMENT.sub("", without_block)


def declared_variables(source: str) -> list[str]:
    """Names declared by the module's `VARIABLE` / `VARIABLES` clauses.

    TLC's `-dumpTrace json` reports a `vars` key, but when the config declares
    an `ALIAS` that key holds the alias-expanded record rather than the
    declared state, so it cannot be used to tell real variables from alias
    fields. The module text can.

    Order of first appearance is preserved; repeated clauses accumulate.
    """
    names: list[str] = []
    for match in _DECLARATION.finditer(strip_comments(source)):
        for name in match.group(1).split(","):
            name = name.strip()
            if name and name not in names:
                names.append(name)
    return names
