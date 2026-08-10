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


# --- animate and graph are not in conflict (#93) --------------------------

#: What only the animation branch adds. Everything else must be identical
#: between the two calls, which is what the test below is really pinning.
ANIMATION_ONLY = {"extra_modules", "collect", "declared"}


class KwargRecorder:
    """Answers OK, remembering the keyword arguments it was called with."""

    can_parse = True
    tools_jar = None
    community_jar = None

    def __init__(self):
        self.calls: list[dict] = []

    def check(self, source, module, config, **kwargs):
        from tlakit.result import CheckResult, Outcome, RawOutput, Stats

        self.calls.append(kwargs)
        return CheckResult(
            Outcome.OK, [], None, Stats(),
            RawOutput(argv=["java"], exit_code=0, stdout="", stderr=""),
        )


def test_both_branches_of_check_forward_the_same_arguments():
    """The duplication is the defect, not this instance of it (#93).

    `graph` and `max_graph_nodes` had already fallen out of the animation
    call; two nearly identical calls will fall out of step again the next time
    the runner grows an argument. A fake runner catches every future variant
    in milliseconds, which a real model check with animation would not.
    """
    import tlakit

    runner = KwargRecorder()
    for animate in (False, True):
        tlakit.Spec(source=CROSS, name="Cross", runner=runner).check(
            invariants=["Safety"], animate=animate, graph=True, max_graph_nodes=50,
            timeout=12.5, heap="2G",
        )

    plain, animated = runner.calls
    assert set(animated) - set(plain) == ANIMATION_ONLY
    assert plain == {k: v for k, v in animated.items() if k not in ANIMATION_ONLY}


def test_animating_with_a_graph_asks_for_one():
    """`check(animate=True, graph=True)` used to drop both arguments, and
    nothing in the result told the caller: `graph` defaults to None whether or
    not one was asked for, and the frames arrived as requested."""
    import tlakit

    runner = KwargRecorder()
    tlakit.Spec(source=CROSS, name="Cross", runner=runner).check(
        invariants=["Safety"], animate=True, graph=True, max_graph_nodes=50
    )

    assert runner.calls[0]["graph"] is True
    assert runner.calls[0]["max_graph_nodes"] == 50


@pytest.mark.java
def test_animating_with_a_graph_returns_both(ready):
    """TLC's -dump dot and an ALIAS that writes SVG frames are independent, so
    there is no reason to refuse the combination either."""
    import tlakit

    result = tlakit.Spec(source=CROSS, name="Cross").check(
        invariants=["Safety"], animate=True, graph=True
    )

    assert result.frames
    assert result.graph is not None
    assert result.graph.nodes


def test_the_remote_runner_still_refuses_animation_rather_than_ignoring_it():
    """Animation is local-only either way: the service runs one self-contained
    module, so `extra_modules` has nowhere to go. The refactor above must not
    turn that refusal into a silent drop."""
    import tlakit
    from tlakit.remote import RemoteRunner, Unsupported

    spec = tlakit.Spec(source=CROSS, name="Cross", runner=RemoteRunner())
    with pytest.raises(Unsupported, match="extra_modules"):
        spec.check(invariants=["Safety"], animate=True, graph=True)
