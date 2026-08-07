"""Issues #14 and #15: animation frames from the spec's own AnimView."""
import shutil

import pytest

from tlakit.api import ANIM_ALIAS, FRAME_PREFIX, animation_module
from tlakit.source import defines_animview

CROSS = """---- MODULE Cross ----
EXTENDS Naturals, Sequences, SVG
VARIABLES boat, goat
vars == <<boat, goat>>
Flip(s) == IF s = "left" THEN "right" ELSE "left"
Init == boat = "left" /\\ goat = "left"
Move  == boat' = Flip(boat) /\\ UNCHANGED goat
Carry == boat = goat /\\ boat' = Flip(boat) /\\ goat' = Flip(goat)
Next == Move \\/ Carry
Spec == Init /\\ [][Next]_vars
Safety == goat = "left"
AnimView ==
  Svg(<<
    Rect(0, 0, 200, 80, [fill |-> "#eef"]),
    Text(10, 20, "boat: " \\o boat, [fill |-> "black"]),
    Circle(IF goat = "left" THEN 40 ELSE 160, 55, 12, [fill |-> "sienna"])
  >>, [viewBox |-> "0 0 200 80", width |-> "200", height |-> "80"])
====
"""

PLAIN = "---- MODULE P ----\nVARIABLE x\nInit == x = 0\n===="


def test_animview_is_detected():
    assert defines_animview(CROSS) is True
    assert defines_animview(PLAIN) is False


def test_animview_in_a_comment_does_not_count():
    assert defines_animview("---- MODULE P ----\n\\* AnimView == 1\n====") is False


def test_generated_companion_extends_the_spec_and_names_every_variable():
    src = animation_module("Cross", ["boat", "goat"])
    assert "EXTENDS Cross, TLC, IOUtils" in src
    assert "boat |-> boat," in src and "goat |-> goat," in src
    assert ANIM_ALIAS in src
    assert FRAME_PREFIX in src
    assert src.startswith("---- MODULE Cross_anim ----")


def test_generated_companion_uses_tla_concatenation_not_python():
    src = animation_module("Cross", ["boat"])
    assert '"tlakit_anim_" \\o ToString(TLCGet("level"))' in src


# --- end to end -----------------------------------------------------------


@pytest.fixture
def ready():
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        runner = tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    if runner.community_jar is None:
        pytest.skip("CommunityModules jar not available; SVG.tla is required")


@pytest.mark.java
def test_animating_a_spec_without_animview_says_so(ready):
    import tlakit

    with pytest.raises(ValueError, match="does not define AnimView"):
        tlakit.Spec(source=PLAIN, name="P").check(animate=True)


@pytest.mark.java
def test_frames_are_produced_and_align_with_the_trace(ready):
    import tlakit
    from tlakit.result import Outcome

    result = tlakit.Spec(source=CROSS, name="Cross").check(
        invariants=["Safety"], animate=True
    )
    assert result.outcome is Outcome.INVARIANT_VIOLATION
    assert result.trace is not None
    assert len(result.frames) == len(result.trace.states)
    assert all(f.lstrip().startswith("<svg") for f in result.frames)


@pytest.mark.java
def test_frames_show_the_state_changing(ready):
    import tlakit

    result = tlakit.Spec(source=CROSS, name="Cross").check(
        invariants=["Safety"], animate=True
    )
    assert "boat: left" in result.frames[0]
    assert "boat: right" in result.frames[-1]


@pytest.mark.java
def test_the_alias_field_is_not_reported_as_a_variable(ready):
    """The generated alias adds _tlakit_frame; it is not state."""
    import tlakit

    result = tlakit.Spec(source=CROSS, name="Cross").check(
        invariants=["Safety"], animate=True
    )
    assert "_tlakit_frame" not in result.trace.variables


@pytest.mark.java
def test_the_filmstrip_renders(ready):
    import tlakit

    result = tlakit.Spec(source=CROSS, name="Cross").check(
        invariants=["Safety"], animate=True
    )
    html = result._repr_html_()
    assert "tlakit-film" in html
    assert html.count("<figure") == len(result.frames)
