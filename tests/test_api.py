import pytest

import tlakit
from tlakit.api import build_config, module_name_of, tla_value


def test_tla_value_renders_scalars():
    assert tla_value(3) == "3"
    assert tla_value("abc") == '"abc"'
    assert tla_value(True) == "TRUE"
    assert tla_value(False) == "FALSE"


def test_tla_value_renders_collections():
    assert tla_value([1, 2]) == "<<1, 2>>"
    assert tla_value({1}) == "{1}"
    assert tla_value({"a": 1}) == "[a |-> 1]"


def test_tla_value_escapes_strings():
    assert tla_value('he said "hi"') == '"he said \\"hi\\""'


def test_tla_value_rejects_unknown_types():
    with pytest.raises(TypeError):
        tla_value(object())


def test_build_config_emits_constants_and_invariants():
    cfg = build_config(spec="Spec", constants={"N": 3}, invariants=["Inv"])
    assert "SPECIFICATION Spec" in cfg
    assert "CONSTANT" in cfg and "N = 3" in cfg
    assert "INVARIANT Inv" in cfg


def test_build_config_uses_init_next_when_no_spec():
    cfg = build_config(init="Init", next_="Next", invariants=["Inv"])
    assert "INIT Init" in cfg and "NEXT Next" in cfg
    assert "SPECIFICATION" not in cfg


def test_load_reads_module_name_from_disk(tmp_path):
    p = tmp_path / "Widget.tla"
    p.write_text("---- MODULE Widget ----\nVARIABLE x\n====\n")
    spec = tlakit.load(p)
    assert spec.name == "Widget"
    assert "MODULE Widget" in spec.source
    assert spec.path == p


def test_module_name_is_inferred_from_source():
    assert module_name_of("---- MODULE Inferred ----\nVARIABLE x\n====\n") == (
        "Inferred"
    )


def test_module_name_of_raises_when_absent():
    with pytest.raises(ValueError):
        module_name_of("no module header here")


def test_check_deadlock_false_reaches_the_config():
    from tlakit.api import build_config

    assert "CHECK_DEADLOCK FALSE" in build_config(spec="Spec", check_deadlock=False)
    assert "CHECK_DEADLOCK TRUE" in build_config(spec="Spec", check_deadlock=True)


def test_check_deadlock_is_absent_unless_asked():
    """Omitted, not defaulted: TLC's own default is what an unset flag means."""
    from tlakit.api import build_config

    assert "CHECK_DEADLOCK" not in build_config(spec="Spec")


def test_check_deadlock_alongside_a_raw_config_is_refused():
    """Silently ignoring it would report a deadlock the caller asked to skip."""
    from tlakit.api import Spec

    spec = Spec(source="---- MODULE M ----\nVARIABLE x\n====\n", name="M")
    with pytest.raises(ValueError, match="not both"):
        spec.check(config="SPECIFICATION Spec\n", check_deadlock=False)


def test_both_runners_can_parse_now():
    """#67 exposed SANY on the service, so the remote runner stopped being the
    one that could only check. `can_parse` stays on the runner rather than
    being assumed, because a future runner may again lack it."""
    from tlakit import api
    from tlakit.api import Spec
    from tlakit.remote import RemoteRunner

    assert Spec(source="", name="M", runner=RemoteRunner()).can_parse is True
    assert api.CliRunner.can_parse is True


# --- config= and the arguments that build one (#87) -----------------------

MODULE = "---- MODULE M ----\nVARIABLE x\n====\n"

#: One value per `build_config` argument that is *not* its default in
#: `Spec.check`. Keyed by argument name so the test below can assert this
#: covers `build_config`'s signature rather than a list copied out of it --
#: copying is how the six unenforced arguments got there in the first place.
NON_DEFAULT = {
    "spec": "Other",
    "init": "Init",
    "next_": "Next",
    "constants": {"N": 3},
    "invariants": ["Safety"],
    "properties": ["Liveness"],
    "check_deadlock": False,
}


class RecordingRunner:
    """Answers every check with OK, remembering the config it was handed."""

    can_parse = True
    tools_jar = None
    community_jar = None

    def __init__(self):
        self.configs: list[str] = []

    def check(self, source, module, config, **kwargs):
        from tlakit.result import CheckResult, Outcome, RawOutput, Stats

        self.configs.append(config)
        return CheckResult(
            Outcome.OK, [], None, Stats(),
            RawOutput(argv=["java"], exit_code=0, stdout="", stderr=""),
        )


def test_the_refused_arguments_are_exactly_build_configs_signature():
    """Derived, not repeated (#87).

    The six arguments this issue is about were unenforced because the rule was
    written out by hand for `check_deadlock` alone. A `build_config` argument
    added later must land on one side of this deliberately.
    """
    import inspect

    from tlakit.api import CONFIG_ARGUMENTS, build_config

    assert set(CONFIG_ARGUMENTS) == set(inspect.signature(build_config).parameters)
    assert set(NON_DEFAULT) == set(CONFIG_ARGUMENTS)


@pytest.mark.parametrize("argument", sorted(NON_DEFAULT))
def test_config_alongside_a_config_building_argument_is_refused(argument):
    """Every one of them, not just `check_deadlock`.

    `spec.check(config=..., invariants=["Safety"])` used to check nothing named
    Safety and return `Outcome.OK` -- a silent wrong answer, and the worst kind
    for `check_source`, whose caller in the repair loop is a program.
    """
    from tlakit.api import CONFIG_ARGUMENTS, Spec

    spec = Spec(source=MODULE, name="M", runner=RecordingRunner())
    with pytest.raises(ValueError, match="not both") as caught:
        spec.check(config="SPECIFICATION Spec\n", **{argument: NON_DEFAULT[argument]})
    message = str(caught.value)
    assert f"{argument}=" in message
    # And it names the .cfg line to write instead, which is the teaching half.
    assert CONFIG_ARGUMENTS[argument] in message


def test_a_raw_config_on_its_own_still_works():
    """`spec` has a default, so *every* `check(config=...)` call passes it.

    Refusing on that would make raw configs unusable -- a worse bug than the
    one being fixed -- so the check compares against the signature's default.
    """
    from tlakit.api import Spec

    runner = RecordingRunner()
    Spec(source=MODULE, name="M", runner=runner).check(config="SPECIFICATION Spec\n")
    assert runner.configs == ["SPECIFICATION Spec\n"]


def test_check_source_refuses_the_combination_too():
    """The call in the LLM repair loop, where nobody is reading the output."""
    from tlakit.api import check_source

    with pytest.raises(ValueError, match="not both"):
        check_source(MODULE, config="SPECIFICATION Spec\n", invariants=["Safety"])
