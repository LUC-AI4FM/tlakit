"""Issue #72: TLC's state graph, streamed from tlakit's own IStateWriter.

The claim these tests have to hold up is that swapping `-dump dot` for a custom
`IStateWriter` did not change the graph -- only when it arrives. So the central
test here runs one spec both ways with the fingerprint polynomial pinned and
compares the two graphs record for record. Anything weaker (counts, shapes)
would pass while the writer quietly mislabelled every edge.

The rest guard the things the DOT route could not do at all: a graph readable
before the run ends, and a run killed on a budget that still leaves the states
it reached.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from tlakit import statewriter
from tlakit.cli import GRAPH_NDJSON_FILE, CliRunner, _GraphTail
from tlakit.jar import JarNotFound, find_tools_jar
from tlakit.result import Outcome

CORPUS = Path(__file__).parent / "corpus"

CFG = "SPECIFICATION Spec\n"

# A run no budget here finishes: 10^6 states, each costing a 20k-element
# function to compute a successor at all.
#
# Slow *per state* on purpose, rather than merely enormous. A spec that reaches
# a million states quickly would have the writer stream a hundred megabytes
# through a temp directory to prove a point about a handful of them -- this one
# reaches a few thousand states in three seconds and writes under a megabyte.
# The cost has to depend on `x` or TLC evaluates it once and the spec is fast
# again (measured 2026-08-08).
SLOW = """---- MODULE Slow ----
EXTENDS Naturals, Sequences
VARIABLE x
Init == x = 0
Next == /\\ Len([i \\in 1..20000 |-> i + x]) > 0
        /\\ x' = (x + 1) % 1000000
Spec == Init /\\ [][Next]_x
====
"""

# 71 * 71 states: enough to truncate hard, small enough to finish in under a
# second.
MODEST = """---- MODULE Modest ----
EXTENDS Naturals
VARIABLES x, y
Init == x = 0 /\\ y = 0
Next == \\/ x' = (x + 1) % 71 /\\ y' = y
        \\/ y' = (y + 1) % 71 /\\ x' = x
Spec == Init /\\ [][Next]_<<x, y>>
====
"""


def _runner() -> CliRunner:
    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        return CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))


def _needs_javac() -> None:
    if statewriter.javac_executable() is None:
        pytest.skip("no javac: the writer cannot be compiled here")


# --- locating and compiling the writer -----------------------------------


def test_the_writer_source_ships_beside_the_module():
    """It is compiled from the wheel at runtime, so it has to be in the wheel."""
    assert statewriter.SOURCE.is_file()
    assert statewriter.MAIN_CLASS in statewriter.SOURCE.read_text(encoding="utf-8")


def test_the_property_name_matches_the_java_constant():
    """Python passes -D<name> and Java reads it. Two spellings, one string."""
    source = statewriter.SOURCE.read_text(encoding="utf-8")
    assert f'"{statewriter.OUT_PROPERTY}"' in source


def test_javac_comes_from_the_environment_first(monkeypatch, tmp_path):
    fake = tmp_path / "javac"
    fake.write_text("")
    monkeypatch.setenv("TLAKIT_JAVAC", str(fake))
    assert statewriter.javac_executable() == str(fake)


def test_a_javac_that_is_not_there_is_no_javac(monkeypatch, tmp_path):
    """Not a silent fall through to PATH: an explicit setting that is wrong
    should not be answered with a different compiler than the one asked for."""
    monkeypatch.setenv("TLAKIT_JAVAC", str(tmp_path / "absent"))
    assert statewriter.javac_executable() is None


def test_a_jdk_beside_TLAKIT_JAVA_is_preferred_to_PATH(monkeypatch, tmp_path):
    """A pinned java and the javac on PATH can be different installations, and
    the class has to be compiled by the JDK whose JVM will run it."""
    monkeypatch.delenv("TLAKIT_JAVAC", raising=False)
    (tmp_path / "javac").write_text("")
    monkeypatch.setenv("TLAKIT_JAVA", str(tmp_path / "java"))
    assert statewriter.javac_executable() == str(tmp_path / "javac")


def test_without_a_jdk_the_writer_is_unavailable(monkeypatch, tmp_path):
    """A JRE runs TLC but cannot compile the writer. That is a fallback, not a
    failure, so the error names the fallback."""
    jar = tmp_path / "tla2tools.jar"
    jar.write_bytes(b"not really a jar, and never opened")
    monkeypatch.setattr(statewriter, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(statewriter, "_RESOLVED", {})
    monkeypatch.setenv("TLAKIT_JAVAC", str(tmp_path / "absent"))
    with pytest.raises(statewriter.StateWriterUnavailable) as caught:
        statewriter.class_directory(jar)
    assert "javac" in str(caught.value) and "-dump dot" in str(caught.value)


def test_a_jar_that_cannot_be_read_is_a_fallback_too(monkeypatch, tmp_path):
    """`_state_writer` catches one exception, so this must be that one and not
    a bare FileNotFoundError out of the cache key."""
    monkeypatch.setattr(statewriter, "_RESOLVED", {})
    with pytest.raises(statewriter.StateWriterUnavailable):
        statewriter.class_directory(tmp_path / "gone.jar")


def test_an_unavailable_writer_leaves_the_runner_on_the_dump_path(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(
        statewriter,
        "class_directory",
        lambda jar: (_ for _ in ()).throw(statewriter.StateWriterUnavailable("no jdk")),
    )
    assert runner._state_writer() is None


@pytest.mark.java
def test_the_class_is_compiled_once_and_then_read_from_the_cache(monkeypatch):
    _runner()
    _needs_javac()
    jar = find_tools_jar()
    directory = statewriter.class_directory(jar)
    assert (directory / f"{statewriter.MAIN_CLASS}.class").is_file()

    # Forget the in-process memo and take the compiler away. The class is on
    # disk, so the answer must not change -- otherwise every process would
    # recompile, which is what the cache is for.
    monkeypatch.setattr(statewriter, "_RESOLVED", {})
    monkeypatch.setenv("TLAKIT_JAVAC", "/nonexistent/javac")
    assert statewriter.class_directory(jar) == directory


# --- the graph itself ----------------------------------------------------


def _by_id(graph) -> dict[str, dict[str, str]]:
    return {n.id: n.variables for n in graph.nodes}


def _edges(graph) -> set[tuple[str, str, str]]:
    return {(e.source, e.target, e.action) for e in graph.edges}


@pytest.mark.java
@pytest.mark.parametrize("entry", ["die_hard", "lost_update", "microwave"])
def test_the_streamed_graph_is_the_same_graph_dump_dot_produced(entry, monkeypatch):
    """The oracle for this whole change.

    `-fp 0` pins TLC's fingerprint polynomial, without which state ids are
    incomparable between two runs -- TLC picks one at random per run. With it
    pinned the two routes must agree exactly: same ids, same variable text,
    same edges with the same action labels, same initial state.
    """
    runner = _runner()
    _needs_javac()
    source = (CORPUS / entry / "spec.tla").read_text(encoding="utf-8")
    config = (CORPUS / entry / "model.cfg").read_text(encoding="utf-8")
    module = source.split("MODULE", 1)[1].split()[0]

    streamed = runner.check(
        source, module, config, graph=True, extra_opts=["-fp", "0"], timeout=120
    )
    assert statewriter.MAIN_CLASS in streamed.raw.argv
    assert "-dump" not in streamed.raw.argv, "issue #72: not on the default path"

    # The same run again, with the writer unavailable -- which is exactly what
    # a machine with no JDK does.
    monkeypatch.setattr(CliRunner, "_state_writer", lambda self: None)
    dumped = runner.check(
        source, module, config, graph=True, extra_opts=["-fp", "0"], timeout=120
    )
    assert "-dump" in dumped.raw.argv

    assert streamed.outcome is dumped.outcome
    assert streamed.raw.exit_code == dumped.raw.exit_code
    assert _by_id(streamed.graph) == _by_id(dumped.graph)
    assert _edges(streamed.graph) == _edges(dumped.graph)
    assert {n.id for n in streamed.graph.nodes if n.initial} == {
        n.id for n in dumped.graph.nodes if n.initial
    }


@pytest.mark.java
def test_a_sibling_module_still_resolves():
    """`extra_modules` puts a module next to the spec, and every other graph
    test here uses one self-contained module -- so nothing else would notice if
    running TLC through the writer's own `main` changed module lookup."""
    runner = _runner()
    _needs_javac()
    helper = """---- MODULE Helper ----
EXTENDS Naturals
Limit == 3
====
"""
    spec = """---- MODULE Extender ----
EXTENDS Naturals, Helper
VARIABLE x
Init == x = 0
Next == IF x < Limit THEN x' = x + 1 ELSE x' = x
Spec == Init /\\ [][Next]_x
====
"""
    result = runner.check(
        spec,
        "Extender",
        "SPECIFICATION Spec\n",
        graph=True,
        extra_modules={"Helper": helper},
        timeout=60,
    )
    assert result.outcome is Outcome.OK, result.diagnostics
    assert len(result.graph.nodes) == 4  # x = 0, 1, 2, 3


@pytest.mark.java
def test_a_run_killed_on_a_budget_still_yields_the_states_it_reached():
    """The DOT route leaves nothing behind here: TLC writes the file when it
    finishes, and this run never does."""
    runner = _runner()
    _needs_javac()
    result = runner.check(SLOW, "Slow", CFG, graph=True, timeout=3)
    assert result.outcome is Outcome.TIMEOUT
    assert result.graph is not None
    assert len(result.graph.nodes) > 1, "a partial graph is the whole point"
    ids = {n.id for n in result.graph.nodes}
    assert all(e.source in ids and e.target in ids for e in result.graph.edges)


@pytest.mark.java
def test_a_node_limit_is_honoured_on_the_stream():
    """Past the limit a state is never held, rather than being written out in
    full and dropped on the way back in."""
    runner = _runner()
    _needs_javac()
    result = runner.check(MODEST, "Modest", CFG, graph=True, timeout=60, max_graph_nodes=25)
    assert result.outcome is Outcome.OK
    assert len(result.graph.nodes) == 25
    assert result.graph.truncated is True
    ids = {n.id for n in result.graph.nodes}
    assert all(e.source in ids and e.target in ids for e in result.graph.edges)


# --- reading a file another process is still writing ---------------------


def test_the_tail_sees_records_before_it_is_stopped(tmp_path):
    """Streaming, not a read at the end: the graph grows while the file does."""
    path = tmp_path / GRAPH_NDJSON_FILE
    tail = _GraphTail(path)
    tail.start()
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"t":"state","id":"1","initial":true,"vars":{"x":"0"}}\n')
            handle.flush()
            deadline = time.monotonic() + 5
            while not tail.graph().nodes and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(tail.graph().nodes) == 1, "the first state arrived late"

            handle.write('{"t":"state","id":"2","vars":{"x":"1"}}\n')
            handle.write('{"t":"edge","from":"1","to":"2","flag":"unseen","action":"N"}\n')
            handle.flush()
    finally:
        tail.stop()
    # stop() drains what was left rather than dropping it.
    graph = tail.graph()
    assert len(graph.nodes) == 2 and len(graph.edges) == 1


def test_the_tail_reads_a_record_split_across_two_writes(tmp_path):
    path = tmp_path / GRAPH_NDJSON_FILE
    tail = _GraphTail(path)
    tail.start()
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"t":"state","id":"1","vars":{"x":')
            handle.flush()
            time.sleep(3 * _GraphTail.POLL_SECONDS)
            assert tail.graph().nodes == [], "half a record is not a state"
            handle.write('"0"}}\n')
            handle.flush()
    finally:
        tail.stop()
    assert len(tail.graph().nodes) == 1


def test_a_reader_that_failed_falls_back_to_the_finished_file(tmp_path):
    """Opening a file another process holds can fail on its own. Losing the
    graph over that would be worse than reading it late."""
    path = tmp_path / GRAPH_NDJSON_FILE
    path.write_text(
        '{"t":"state","id":"1","initial":true,"vars":{"x":"0"}}\n'
        '{"t":"state","id":"2","vars":{"x":"1"}}\n'
        '{"t":"edge","from":"1","to":"2","flag":"unseen","action":"N"}\n',
        encoding="utf-8",
    )
    tail = _GraphTail(path)
    tail.error = OSError("could not open it")  # never started
    graph = tail.graph()
    assert len(graph.nodes) == 2 and len(graph.edges) == 1


def test_the_tail_survives_a_file_that_never_appears(tmp_path):
    tail = _GraphTail(tmp_path / "nested" / GRAPH_NDJSON_FILE)
    with pytest.raises(OSError):
        tail.start()  # touch() fails loudly rather than tailing nothing


def test_stopping_a_tail_joins_its_thread(tmp_path):
    before = threading.active_count()
    tail = _GraphTail(tmp_path / GRAPH_NDJSON_FILE)
    tail.start()
    tail.stop()
    assert threading.active_count() == before
