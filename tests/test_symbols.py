"""Issue #35: completion and hover, the two things only a kernel can answer.

The symbol layer is deliberately separate from `tlakit.kernel` (same reason
`routing.py` is): nothing here imports Jupyter, so all of it is testable
without a running kernel, and a browser kernel can reuse it.

Tests that read the standard modules are marked `java` -- not because they run
java, but because they need `tla2tools.jar` present, and that marker is how
this repo says "needs the toolchain".
"""
from __future__ import annotations

import pytest

from tlakit.symbols import (
    KEYWORDS,
    complete,
    definitions,
    describe,
    extends,
    standard_modules,
    symbols_in_scope,
    word_at,
)

COUNTER = """---- MODULE Counter ----
EXTENDS Naturals, Sequences
CONSTANTS Limit, Workers(_)
VARIABLES count, log
vars == <<count, log>>

Init == count = 0 /\\ log = <<>>

Bump(n) == count' = count + n /\\ log' = Append(log, n)

Total[s \\in Seq(Nat)] == Len(s)

Inv == count <= Limit
====
"""


# --------------------------------------------------------------------------
# Reading a module
# --------------------------------------------------------------------------


def test_definitions_finds_operators_functions_and_declarations():
    found = {s.name: s for s in definitions(COUNTER, "Counter")}

    assert found["Init"].kind == "operator"
    assert found["Init"].arity == 0

    assert found["Bump"].kind == "operator"
    assert found["Bump"].arity == 1
    assert found["Bump"].signature == "Bump(n)"

    assert found["Total"].kind == "function"
    assert found["Total"].signature == "Total[s \\in Seq(Nat)]"

    assert found["count"].kind == "variable"
    assert found["log"].kind == "variable"
    assert found["Limit"].kind == "constant"
    assert found["Workers"].kind == "constant"


def test_definitions_ignores_things_that_only_look_like_definitions():
    source = """---- MODULE M ----
\\* Commented == out
(* Blocked == out too *)
LOCAL Hidden == 1
Real == 2
s \\o t == 3
====
"""
    names = {s.name for s in definitions(source)}
    assert names == {"Real"}, names


def test_extends_reads_a_list_that_wraps_across_lines():
    source = "---- MODULE M ----\nEXTENDS Naturals,\n        Sequences,\n  TLC\n===="
    assert extends(source) == ["Naturals", "Sequences", "TLC"]


# --------------------------------------------------------------------------
# The cursor
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,pos,expected",
    [
        ("Len", 3, "Len"),          # at the end of a word
        ("Len(s)", 2, "Len"),       # inside a word: the whole token
        ("Len(s)", 0, "Len"),       # at its very start: still the whole token
        ("a + Len", 7, "Len"),      # after an operator
        ("   ", 2, ""),             # whitespace
        ("Len(s)", 4, "s"),         # a different token
    ],
)
def test_word_at_finds_the_whole_identifier_under_the_cursor(code, pos, expected):
    """Hover semantics: the cursor anywhere on a name means that name."""
    assert word_at(code, pos)[0] == expected


def test_word_at_clamps_a_cursor_past_the_end():
    """A frontend can send a stale cursor_pos after the buffer shrinks."""
    assert word_at("Len", 99) == ("Len", 0, 3)


def test_completing_nothing_offers_nothing():
    """An empty prefix must not dump every name in scope into the frontend."""
    matches, start, end = complete("", 0)
    assert matches == []
    assert start == end == 0


def test_completion_uses_only_the_text_left_of_the_cursor():
    """Hover and completion want different halves of the same token.

    With the cursor at the start of `Bump`, nothing has been typed yet, so
    there is nothing to complete -- even though hovering there describes
    `Bump`. Using the whole token would offer every name in scope and then
    overwrite `Bump` with whichever the frontend picked.
    """
    code = COUNTER + "\nBump"
    at_start = len(code) - len("Bump")
    assert word_at(code, at_start)[0] == "Bump"
    assert complete(code, at_start)[0] == []


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


def test_completion_offers_names_from_the_cell_itself():
    code = COUNTER + "\nBu"
    matches, start, end = complete(code, len(code))
    assert "Bump" in matches
    assert code[start:end] == "Bu"


def test_completion_offers_keywords_but_ranks_definitions_first():
    code = "---- MODULE M ----\nCASCADE == 1\nCAS"
    matches, _, _ = complete(code, len(code))
    assert matches.index("CASCADE") < matches.index("CASE")


def test_completion_reaches_a_module_defined_earlier_in_the_session():
    """The point of a session: a second cell can see the first cell's module."""
    session = {"Counter": COUNTER}
    code = "---- MODULE Uses ----\nEXTENDS Counter\nBu"
    matches, _, _ = complete(code, len(code), session)
    assert "Bump" in matches


def test_a_modules_own_definition_shadows_the_one_it_extends():
    session = {"Counter": COUNTER}
    code = "---- MODULE Uses ----\nEXTENDS Counter\nInv == TRUE\n"
    scope = symbols_in_scope(code, "Uses", session_modules=session)
    assert scope["Inv"].module == "Uses"


def test_mutually_extending_modules_do_not_recurse_forever():
    session = {
        "A": "---- MODULE A ----\nEXTENDS B\nFromA == 1\n====",
        "B": "---- MODULE B ----\nEXTENDS A\nFromB == 2\n====",
    }
    scope = symbols_in_scope(session["A"], "A", session_modules=session)
    assert {"FromA", "FromB"} <= set(scope)


def test_no_jar_degrades_to_no_standard_library_rather_than_raising():
    """Completion is a convenience. Losing the jar must not break a keystroke."""
    matches, _, _ = complete(
        "---- MODULE M ----\nMine == 1\nMi", 32, tools_jar=__file__
    )
    assert "Mine" in matches


# --------------------------------------------------------------------------
# Against the real standard modules in tla2tools.jar
# --------------------------------------------------------------------------


@pytest.fixture
def stdlib():
    modules = standard_modules()
    if not modules:
        pytest.skip("tla2tools.jar not available")
    return modules


@pytest.mark.java
def test_the_standard_modules_come_from_the_jar(stdlib):
    assert {"Sequences", "Naturals", "FiniteSets", "TLC"} <= set(stdlib)


@pytest.mark.java
def test_sequences_exports_exactly_its_named_operators(stdlib):
    """Pinned against the real module. If TLA+ adds an operator this fails,
    which is the point -- the alternative is a hardcoded list nobody notices
    has gone stale."""
    found = {s.name for s in definitions(stdlib["Sequences"], "Sequences")}
    assert found == {"Seq", "Len", "Append", "Head", "Tail", "SubSeq"}


@pytest.mark.java
def test_hover_carries_the_operators_own_documentation(stdlib):
    found = {s.name: s for s in definitions(stdlib["Sequences"], "Sequences")}
    assert found["Len"].doc == "The length of sequence s."
    assert found["Len"].signature == "Len(s)"


@pytest.mark.java
def test_documentation_stops_at_the_next_definition(stdlib):
    """A boxed comment is three separate `(* ... *)` comments, and the
    definition after `Len` is infix (`s \\o t == ...`), which is not matched --
    so the window to the next matched definition runs straight past it. Both
    together used to give `Len` the concatenation operator's documentation."""
    found = {s.name: s for s in definitions(stdlib["Sequences"], "Sequences")}
    assert "concatenat" not in found["Len"].doc
    assert "*****" not in found["Len"].doc


@pytest.mark.java
def test_completion_reaches_through_extends_into_the_jar():
    code = "---- MODULE M ----\nEXTENDS Sequences\nSub"
    matches, _, _ = complete(code, len(code))
    assert "SubSeq" in matches


def test_extends_is_transitive():
    session = {
        "Base": "---- MODULE Base ----\nDeep == 1\n====",
        "Middle": "---- MODULE Middle ----\nEXTENDS Base\nMid == 2\n====",
    }
    code = "---- MODULE Top ----\nEXTENDS Middle\nDe"
    matches, _, _ = complete(code, len(code), session)
    assert "Deep" in matches


@pytest.mark.java
def test_a_local_instance_is_not_treated_as_an_export(stdlib):
    """`Sequences` pulls in `Naturals` with `LOCAL INSTANCE`, whose whole
    point is that it does not re-export. So a module EXTENDing Sequences gets
    `Len` but not `Nat` -- matching TLA+, and worth pinning because the
    tempting "just union everything reachable" shortcut gets it wrong.
    """
    code = "---- MODULE M ----\nEXTENDS Sequences\n"
    scope = symbols_in_scope(code, "M")
    assert "Len" in scope
    assert "Nat" not in scope


@pytest.mark.java
def test_hover_describes_a_standard_operator():
    code = "---- MODULE M ----\nEXTENDS Sequences\nX == Len(<<1>>)"
    text = describe(code, code.index("Len") + 1)
    assert "Len(s)" in text
    assert "Sequences" in text
    assert "length of sequence" in text


def test_hover_on_a_keyword_says_so():
    assert describe("EXTENDS Naturals", 3) == "EXTENDS — TLA+ keyword"


def test_hover_on_an_unknown_name_says_nothing():
    assert describe("---- MODULE M ----\nX == Nonexistent", 30) is None


def test_keywords_are_not_reported_as_definitions():
    """`IF x THEN a ELSE b == c` must not make `ELSE` an operator."""
    names = {s.name for s in definitions("---- MODULE M ----\nIN == 1\n====")}
    assert "IN" not in names
    assert "IN" in KEYWORDS
