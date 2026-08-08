"""The security property a public runner depends on.

TLC adds jars in tla2tools.jar's own directory to its module search path. A
CommunityModules jar that merely sits *beside* tla2tools.jar is therefore
loadable even when it is not on the classpath -- and CommunityModules ships
`IOUtils!IOExec`, which runs arbitrary shell commands from inside a spec.

Measured 2026-08-07: with the jars adjacent, a spec printed `uid=501(...)`.
"""
import shutil

import pytest

from tlakit.jar import NotIsolated, assert_isolated, find_community_jar

EVIL = """---- MODULE Evil ----
EXTENDS Naturals, TLC, IOUtils
VARIABLE x
Init == x = 0 /\\ PrintT(IOExec(<<"sh", "-c", "echo PWNED">>).stdout)
Next == x' = x
Spec == Init /\\ [][Next]_x
====
"""


def test_isolated_directory_is_accepted(tmp_path):
    jar = tmp_path / "tla2tools.jar"
    jar.write_bytes(b"x")
    assert_isolated(jar)  # must not raise


def test_an_adjacent_jar_is_refused(tmp_path):
    jar = tmp_path / "tla2tools.jar"
    jar.write_bytes(b"x")
    (tmp_path / "CommunityModules-deps.jar").write_bytes(b"x")
    with pytest.raises(NotIsolated) as exc:
        assert_isolated(jar)
    assert "IOExec" in str(exc.value)
    assert "CommunityModules-deps.jar" in str(exc.value)


def test_community_jar_can_be_refused_outright(monkeypatch, tmp_path):
    fake = tmp_path / "CommunityModules-deps.jar"
    fake.write_bytes(b"x")
    monkeypatch.setenv("TLAKIT_COMMUNITY_MODULES", str(fake))
    assert find_community_jar() == fake
    assert find_community_jar(False) is None


@pytest.mark.java
def test_a_spec_cannot_run_shell_commands_when_the_jar_is_isolated(tmp_path):
    """The end of the argument: with tla2tools alone, IOUtils is unreachable."""
    import tlakit
    from tlakit.jar import JarNotFound, find_tools_jar
    from tlakit.result import Outcome

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        real = find_tools_jar()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    lonely = tmp_path / "solo"
    lonely.mkdir()
    shutil.copy(real, lonely / "tla2tools.jar")
    assert_isolated(lonely / "tla2tools.jar")

    runner = tlakit.CliRunner(tools_jar=lonely / "tla2tools.jar", community_jar=False)
    result = runner.check(EVIL, "Evil", "SPECIFICATION Spec\n", timeout=120)

    assert "PWNED" not in result.raw.stdout, "a spec executed a shell command"
    assert result.outcome is not Outcome.OK
    assert "IOUtils" in result.raw.stdout  # it failed to resolve the module


@pytest.mark.java
def test_the_attack_really_works_without_isolation(tmp_path):
    """Guard against the mitigation quietly becoming unnecessary-looking.

    If this ever stops passing, IOExec was fixed upstream and the isolation
    requirement can be revisited. Until then it documents a live capability.
    """
    import tlakit
    from tlakit.jar import JarNotFound, find_tools_jar

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tools = find_tools_jar()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    community = find_community_jar()
    if community is None:
        pytest.skip("CommunityModules not available")

    together = tmp_path / "together"
    together.mkdir()
    shutil.copy(tools, together / "tla2tools.jar")
    shutil.copy(community, together / "CommunityModules-deps.jar")

    runner = tlakit.CliRunner(
        tools_jar=together / "tla2tools.jar", community_jar=False
    )
    result = runner.check(EVIL, "Evil", "SPECIFICATION Spec\n", timeout=120)
    assert "PWNED" in result.raw.stdout, (
        "adjacency no longer grants IOExec -- re-evaluate the isolation rule"
    )
