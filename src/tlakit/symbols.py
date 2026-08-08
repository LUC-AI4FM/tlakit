"""What names exist in a TLA+ module, and what each one is.

This backs completion and hover in the kernel (issue #35). Those are the two
things a kernel can offer that magics in a Python kernel genuinely cannot: the
frontend asks the kernel what a name means, and in a Python kernel the honest
answer is always "a Python name".

Nothing here evaluates TLA+ or resolves it the way SANY does. It reads
declarations out of module text, and -- for the standard modules -- out of the
`.tla` files that ship inside `tla2tools.jar`, which are the real ones. That
matters more than it sounds: the alternative is a hardcoded list of operator
names, which is a second, worse copy of the standard library that goes stale
the first time TLA+ adds an operator. Reading the jar means `Len`, `SubSeq`,
and everything else are described by the same source TLC uses.

The limits are real and worth stating rather than papering over:

- Only definitions the module makes *itself*, plus whatever it EXTENDS. An
  INSTANCE is not followed -- resolving one requires substitution, which is
  SANY's job.
- Infix operator definitions (`s \\o t == ...`) are skipped. They are not
  completable by name, and a caller typing one is not typing an identifier.
- `LOCAL` definitions are skipped: they exist but are not exported, so
  offering them would suggest something that fails to resolve.
"""
from __future__ import annotations

import functools
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .source import strip_comments

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

#: Where the standard modules live inside tla2tools.jar.
_STDLIB_PREFIX = "tla2sany/StandardModules/"

#: `Name == ...` and `Name(a, b) == ...`, at the start of a line. The negative
#: lookbehind on LOCAL keeps unexported definitions out.
_OPERATOR = re.compile(
    rf"^(?!\s*LOCAL\b)[ \t]*(?P<name>{_IDENT})"
    rf"[ \t]*(?:\((?P<params>[^)]*)\))?[ \t]*==",
    re.MULTILINE,
)

#: `Name[x \in S] == ...`, TLA+'s function-definition form.
_FUNCTION = re.compile(
    rf"^(?!\s*LOCAL\b)[ \t]*(?P<name>{_IDENT})[ \t]*\[(?P<params>[^\]]*)\][ \t]*==",
    re.MULTILINE,
)

_DECLARATION = re.compile(
    rf"^[ \t]*(?P<keyword>VARIABLES?|CONSTANTS?)\b"
    rf"(?P<body>(?:\s*{_IDENT}(?:\([^)]*\))?\s*,)*\s*{_IDENT}(?:\([^)]*\))?)",
    re.MULTILINE,
)

_EXTENDS = re.compile(
    rf"^[ \t]*EXTENDS\b(?P<body>(?:\s*{_IDENT}\s*,)*\s*{_IDENT})", re.MULTILINE
)

#: A boxed comment following a definition, which is how the standard modules
#: document every operator they export.
_DOC_BLOCK = re.compile(r"\(\*(?P<body>.*?)\*\)", re.DOTALL)

#: TLA+ keywords. Not derivable from a module -- they are the language, not a
#: library -- so this is the one list here that is written down.
KEYWORDS = (
    "ASSUME", "ASSUMPTION", "AXIOM", "BOOLEAN", "BY", "CASE", "CHOOSE",
    "CONSTANT", "CONSTANTS", "DEF", "DEFINE", "DEFS", "DOMAIN", "ELSE",
    "ENABLED", "EXCEPT", "EXTENDS", "FALSE", "HAVE", "HIDE", "IF", "IN",
    "INSTANCE", "LAMBDA", "LET", "LOCAL", "MODULE", "NEW", "OBVIOUS", "OMITTED",
    "ONLY", "OTHER", "PICK", "PROOF", "PROPOSITION", "PROVE", "QED",
    "RECURSIVE", "SF_", "STATE", "SUBSET", "SUFFICES", "TAKE", "THEN", "THEOREM",
    "TRUE", "UNCHANGED", "UNION", "USE", "VARIABLE", "VARIABLES", "WF_", "WITH",
    "WITNESS",
)


@dataclass(frozen=True)
class Symbol:
    """One name a cell could refer to, and where it came from."""

    #: The identifier itself, which is what completion inserts.
    name: str
    #: `"operator"`, `"function"`, `"variable"`, `"constant"`, or `"keyword"`.
    kind: str
    #: The module defining it. None for keywords.
    module: str | None = None
    #: Parameter count. 0 for a constant-arity operator or a declaration.
    arity: int = 0
    #: How to write it at a call site, e.g. `SubSeq(s, m, n)`.
    signature: str = ""
    #: 1-based line of the definition, when it came from module text.
    line: int | None = None
    #: The documentation comment attached to the definition, if any.
    doc: str = ""

    def render(self) -> str:
        """The hover text: signature, origin, then documentation."""
        head = self.signature or self.name
        where = f" — {self.kind}" + (f" in {self.module}" if self.module else "")
        body = f"{head}{where}"
        if self.doc:
            body += f"\n\n{self.doc}"
        return body


def _params_of(raw: str | None) -> tuple[int, list[str]]:
    if not raw or not raw.strip():
        return 0, []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return len(parts), parts


def _doc_after(source: str, end: int, next_start: int) -> str:
    """The boxed comment between a definition and whatever follows it.

    The standard modules put an operator's documentation *after* it, wrapped in
    a box of asterisks:

        Len(s) == CHOOSE n \\in Nat : DOMAIN s = 1..n
          (*********************************************)
          (* The length of sequence s.                 *)
          (*********************************************)

    Every one of those three lines is a complete `(* ... *)` comment on its
    own, so matching only the first finds the top border and reports a row of
    asterisks as the documentation. All three are collected, and the ones that
    are pure box rule drop out for having no text.

    Collection stops at the first non-whitespace between two blocks. Without
    that, one operator absorbs the next one's documentation whenever the
    definition in between is an infix one (`s \\o t == ...`), which is not a
    definition this module matches -- so `next_start` runs straight past it.
    """
    window = source[end:next_start]
    lines: list[str] = []
    previous_end: int | None = None
    for match in _DOC_BLOCK.finditer(window):
        if previous_end is not None and window[previous_end : match.start()].strip():
            break
        previous_end = match.end()
        for line in match.group("body").splitlines():
            line = line.strip().strip("*").strip()
            if line:
                lines.append(line)
    return " ".join(lines).strip()


def definitions(source: str, module: str | None = None) -> list[Symbol]:
    """Every operator, function, variable, and constant the module defines.

    Definitions are located in a comment-blanked copy so that a `==` inside a
    comment is not mistaken for one, but documentation is read from the
    original text, where the comments still are.
    """
    blanked = strip_comments(source)
    found: dict[str, Symbol] = {}

    matches: list[tuple[re.Match[str], str]] = [
        *((m, "function") for m in _FUNCTION.finditer(blanked)),
        *((m, "operator") for m in _OPERATOR.finditer(blanked)),
    ]
    matches.sort(key=lambda pair: pair[0].start())
    starts = [m.start() for m, _ in matches]

    for index, (match, kind) in enumerate(matches):
        name = match.group("name")
        # A function definition and the operator regex can both match the same
        # line; the function form is more specific, so it wins.
        if name in found and found[name].kind == "function":
            continue
        if name in KEYWORDS:
            continue
        arity, params = _params_of(match.groupdict().get("params"))
        signature = f"{name}({', '.join(params)})" if params and kind == "operator" else name
        if kind == "function" and params:
            signature = f"{name}[{', '.join(params)}]"
        next_start = starts[index + 1] if index + 1 < len(starts) else len(blanked)
        found[name] = Symbol(
            name=name,
            kind=kind,
            module=module,
            arity=arity,
            signature=signature,
            line=blanked[: match.start()].count("\n") + 1,
            doc=_doc_after(source, match.end(), next_start),
        )

    for match in _DECLARATION.finditer(blanked):
        kind = "variable" if match.group("keyword").startswith("VARIABLE") else "constant"
        for item in match.group("body").split(","):
            item = item.strip()
            if not item:
                continue
            name = item.split("(")[0].strip()
            arity, _ = _params_of(item[len(name):].strip("()") or None)
            found.setdefault(
                name,
                Symbol(
                    name=name,
                    kind=kind,
                    module=module,
                    arity=arity,
                    signature=item,
                    line=blanked[: match.start()].count("\n") + 1,
                ),
            )

    return sorted(found.values(), key=lambda s: s.name)


def extends(source: str) -> list[str]:
    """Module names in the module's `EXTENDS` clause, in order."""
    names: list[str] = []
    for match in _EXTENDS.finditer(strip_comments(source)):
        for name in match.group("body").split(","):
            name = name.strip()
            if name and name not in names:
                names.append(name)
    return names


@functools.lru_cache(maxsize=None)
def _stdlib_sources(jar: Path) -> dict[str, str]:
    """Every standard module's text, read straight out of tla2tools.jar."""
    with zipfile.ZipFile(jar) as archive:
        return {
            Path(entry).stem: archive.read(entry).decode("utf-8", "replace")
            for entry in archive.namelist()
            if entry.startswith(_STDLIB_PREFIX) and entry.endswith(".tla")
        }


def standard_modules(tools_jar: Path | None = None) -> dict[str, str]:
    """The standard modules' sources, or `{}` when no jar can be found.

    Completion is a convenience, so a missing jar degrades to "no standard
    library symbols" rather than raising. Everything defined in the notebook's
    own modules still completes.
    """
    if tools_jar is None:
        try:
            from .jar import find_tools_jar

            tools_jar = find_tools_jar()
        except Exception:
            return {}
    try:
        return _stdlib_sources(Path(tools_jar))
    except (OSError, zipfile.BadZipFile):
        return {}


def symbols_in_scope(
    source: str,
    module: str | None = None,
    *,
    tools_jar: Path | None = None,
    session_modules: dict[str, str] | None = None,
    _seen: frozenset[str] = frozenset(),
) -> dict[str, Symbol]:
    """Every name `source` could refer to, keyed by name.

    That is: what it defines itself, plus everything exported by the modules it
    EXTENDS, resolved against the notebook session's own modules first and the
    standard library second. `_seen` breaks the cycle a mutually-EXTENDing pair
    would otherwise create.
    """
    session_modules = session_modules or {}
    scope: dict[str, Symbol] = {}

    stdlib = standard_modules(tools_jar)
    for name in extends(source):
        if name in _seen:
            continue
        text = session_modules.get(name) or stdlib.get(name)
        if text is None:
            continue
        scope.update(
            symbols_in_scope(
                text,
                name,
                tools_jar=tools_jar,
                session_modules=session_modules,
                _seen=_seen | {name},
            )
        )

    # The module's own definitions shadow anything it inherited, which is what
    # TLA+ itself does.
    for symbol in definitions(source, module):
        scope[symbol.name] = symbol
    return scope


def session_scope(
    modules: dict[str, str], *, tools_jar: Path | None = None
) -> dict[str, Symbol]:
    """Everything in scope across every module defined in a kernel session."""
    scope: dict[str, Symbol] = {}
    for name, text in modules.items():
        scope.update(
            symbols_in_scope(
                text, name, tools_jar=tools_jar, session_modules=modules
            )
        )
    return scope


#: `\Z`, not `$`: `$` also matches just before a trailing newline, so on a
#: cursor sitting at the start of a line the search anchors one character early
#: and the "word" comes back with a leading newline attached.
_WORD_BEFORE = re.compile(rf"(?:{_IDENT})?\Z")
_WORD_AT = re.compile(_IDENT)


def word_at(code: str, cursor_pos: int) -> tuple[str, int, int]:
    """The *whole* identifier the cursor sits in or beside, and its span.

    Hover semantics: putting the cursor anywhere on `Len` -- including at its
    very start -- should describe `Len`. Completion wants something different
    and narrower (only the text left of the cursor is what the user has
    actually typed), so `complete` slices this span rather than using it whole.
    Conflating the two makes an empty prefix at the start of a word offer every
    name in scope.

    Returns `("", cursor_pos, cursor_pos)` when the cursor is not on a word.
    """
    cursor_pos = max(0, min(cursor_pos, len(code)))
    start = _WORD_BEFORE.search(code, 0, cursor_pos).start()
    end = cursor_pos
    tail = _WORD_AT.match(code, cursor_pos)
    if tail is not None:
        end = tail.end()
    return code[start:end], start, end


def complete(
    code: str,
    cursor_pos: int,
    modules: dict[str, str] | None = None,
    *,
    tools_jar: Path | None = None,
) -> tuple[list[str], int, int]:
    """Completion candidates for the cursor, plus the span they replace.

    Candidates are the session's own definitions, everything reachable through
    EXTENDS, and TLA+'s keywords. Definitions sort ahead of keywords: in a
    module cell the name you are reaching for is far more often one you wrote
    than one of the forty reserved words.
    """
    _, start, _ = word_at(code, cursor_pos)
    # Only what is left of the cursor has been typed. Completing on the whole
    # token would make a cursor at the start of `Len` offer everything in
    # scope and then replace `Len` with whichever the frontend picked first.
    prefix, end = code[start:cursor_pos], cursor_pos
    if not prefix:
        return [], cursor_pos, cursor_pos

    session = dict(modules or {})
    scope = session_scope(session, tools_jar=tools_jar)
    scope.update(
        symbols_in_scope(code, tools_jar=tools_jar, session_modules=session)
    )

    names = sorted(name for name in scope if name.startswith(prefix))
    keywords = sorted(k for k in KEYWORDS if k.startswith(prefix) and k not in scope)
    return names + keywords, start, end


def describe(
    code: str,
    cursor_pos: int,
    modules: dict[str, str] | None = None,
    *,
    tools_jar: Path | None = None,
) -> str | None:
    """Hover text for the name under the cursor, or None if there is none."""
    name, _, _ = word_at(code, cursor_pos)
    if not name:
        return None

    session = dict(modules or {})
    scope = session_scope(session, tools_jar=tools_jar)
    scope.update(
        symbols_in_scope(code, tools_jar=tools_jar, session_modules=session)
    )

    symbol = scope.get(name)
    if symbol is not None:
        return symbol.render()
    if name in KEYWORDS:
        return f"{name} — TLA+ keyword"
    return None
