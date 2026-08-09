"""Issue #23: run vscode-tlaplus's MCP server without VSCode, and #22's conformance.

Most of this needs a vscode-tlaplus checkout and node, and skips without them:

    python -m tlakit.mcp.serve --install        # clone at the pinned commit
    TLAKIT_VSCODE_TLAPLUS=/path/to/checkout python -m pytest -m mcp

The one that matters is `test_both_runners_agree_on_the_corpus`. Two checkers
that disagree about a spec are worth investigating only once they are known to
be the same checker, so it asserts the TLC versions match first and skips
rather than fails when they do not -- a version difference reported as a
conformance failure would send someone looking for a tlakit bug that is not
there. Getting the versions to match at all is the reason `serve` builds into
its own directory: the extension ships TLC 2026.03.19 where tlakit pins
2026.07.31.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tlakit.cli import CliRunner
from tlakit.jar import JarNotFound, find_tools_jar
from tlakit.mcp import serve
from tlakit.mcp.runner import McpRunner
from tlakit.result import Outcome

CORPUS = Path(__file__).parent / "corpus"

#: Every `vscode.<namespace>.<member>` the extension reaches for. The shim has to
#: answer all of them, because `activate()` constructs the objects that register
#: them and a missing name is a startup failure.
_VSCODE_MEMBER = re.compile(
    r"vscode\.(window|workspace|languages|commands|debug|tasks|extensions|env|lm)"
    r"\.([a-zA-Z]+)"
)

#: Absent on purpose -- see the stub's header.
_DELIBERATELY_ABSENT = {"registerMcpServerDefinitionProvider"}


def _extension() -> Path:
    try:
        return serve.find_extension()
    except serve.McpServeError as exc:
        pytest.skip(str(exc))


def _bundle(ext_root: Path) -> Path:
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    try:
        return serve.prepare(ext_root)
    except serve.McpServeError as exc:
        pytest.skip(str(exc))


# --- what ships ----------------------------------------------------------


def test_the_shim_ships_with_the_package():
    """It is copied out of the wheel into a checkout at build time, so it has to
    be in the wheel."""
    names = {p.name for p in serve.JS_DIR.iterdir()}
    assert {"vscode-stub.js", "lsp-stub.js", "bootstrap.ts", "build.js"} <= names


def test_the_extension_is_pinned_to_a_commit():
    """A shim mirrors an API surface. Tracking a moving branch would break it
    one morning for a reason nobody in this repo caused."""
    assert re.fullmatch(r"[0-9a-f]{40}", serve.REF)


def test_the_bootstrap_pins_the_bind_address():
    """The extension calls `app.listen(port)` with no host, which headless means
    every interface -- and these tools run TLC on any path they are given."""
    bootstrap = (serve.JS_DIR / "bootstrap.ts").read_text(encoding="utf-8")
    assert "127.0.0.1" in bootstrap
    assert "net.Server.prototype.listen" in bootstrap


def test_the_bootstrap_treats_a_failed_activate_as_fatal():
    """A shim that half-activates returns empty diagnostics and looks like a
    working server, which is worse than one that refuses."""
    bootstrap = (serve.JS_DIR / "bootstrap.ts").read_text(encoding="utf-8")
    assert "process.exit(1)" in bootstrap
    assert "activate() failed" in bootstrap


def test_the_bundle_is_placed_so_the_jars_resolve_to_tlakit_s():
    """The extension reads `../tools/tla2tools.jar` relative to its bundle, so
    where the bundle goes decides which TLC runs."""
    build = (serve.JS_DIR / "build.js").read_text(encoding="utf-8")
    assert "'out', 'server.js'" in build
    assert serve.bundle_path(Path("/x")) == Path("/x") / serve.STAGE_DIR / "out" / "server.js"


def test_a_missing_checkout_names_the_flag_that_gets_one(monkeypatch, tmp_path):
    monkeypatch.delenv(serve.ENV_EXT_ROOT, raising=False)
    monkeypatch.setattr(serve, "default_ext_root", lambda: tmp_path / "absent")
    with pytest.raises(serve.McpServeError) as caught:
        serve.find_extension()
    assert "--install" in str(caught.value)


def test_nothing_reaches_the_network_without_install(monkeypatch, tmp_path):
    """`serve` locating and building is local. Cloning and npm are opt-in, the
    same way `tlakit.install` is."""
    called: list[str] = []
    monkeypatch.setattr(serve, "clone", lambda *a, **k: called.append("clone"))
    monkeypatch.setattr(serve, "npm_install", lambda *a: called.append("npm"))
    monkeypatch.setattr(serve, "find_extension", lambda root: tmp_path)
    monkeypatch.setattr(serve, "build", lambda root: serve.bundle_path(tmp_path))
    bundle = serve.bundle_path(tmp_path)
    bundle.parent.mkdir(parents=True)
    bundle.write_text("//")
    serve.prepare(tmp_path)
    assert called == []


def test_a_bundle_older_than_the_shim_is_stale(tmp_path):
    """An upgraded tlakit must not keep running the bundle the previous shim
    produced. A bundle that exists looks current, which is the trap.

    Both mtimes are set here rather than taken from the working tree: in a fresh
    clone every file is checked out at the same moment, so which of them is
    "newer" would decide whether this test means anything.
    """
    import os

    bundle = serve.bundle_path(tmp_path)
    bundle.parent.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    bundle.write_text("//")

    newest = serve._newest_input(tmp_path)
    os.utime(bundle, (newest - 10, newest - 10))
    assert serve.is_stale(tmp_path) is True
    os.utime(bundle, (newest + 10, newest + 10))
    assert serve.is_stale(tmp_path) is False


def test_a_bundle_older_than_the_extension_sources_is_stale(tmp_path):
    """A checkout moved to another ref keeps running the old bundle otherwise."""
    import os

    bundle = serve.bundle_path(tmp_path)
    bundle.parent.mkdir(parents=True)
    bundle.write_text("//")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "main.ts"
    source.write_text("// newer than the bundle")

    when = serve._newest_input(tmp_path)
    os.utime(bundle, (when + 10, when + 10))
    assert serve.is_stale(tmp_path) is False
    os.utime(source, (when + 100, when + 100))
    assert serve.is_stale(tmp_path) is True


def test_a_missing_bundle_is_stale(tmp_path):
    (tmp_path / "src").mkdir()
    assert serve.is_stale(tmp_path) is True


def test_building_without_node_modules_says_so(tmp_path):
    with pytest.raises(serve.McpServeError) as caught:
        serve.build(tmp_path)
    assert "--install" in str(caught.value) or "npm install" in str(caught.value)


# --- staging -------------------------------------------------------------


@pytest.mark.java
def test_staging_copies_the_jar_tlakit_pins(tmp_path):
    """Not a symlink into platformdirs: this is a build output the extension
    reads by relative path, and a cache eviction would leave a server with no
    TLC at all."""
    try:
        find_tools_jar()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    stage = serve.stage(tmp_path)
    jar = stage / "tools" / "tla2tools.jar"
    assert jar.is_file() and not jar.is_symlink()
    assert jar.stat().st_size == find_tools_jar().stat().st_size


@pytest.mark.java
def test_community_modules_can_be_left_out(tmp_path):
    """TLC loads every jar next to tla2tools.jar whatever the classpath says, so
    leaving the file out is the only way to deny `IOUtils!IOExec`."""
    try:
        find_tools_jar()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    serve.stage(tmp_path, community=True)
    serve.stage(tmp_path, community=False)
    assert not (tmp_path / serve.STAGE_DIR / "tools" / "CommunityModules-deps.jar").exists()


# --- the shim against the extension it shims -----------------------------


@pytest.mark.mcp
def test_the_shim_covers_every_vscode_name_the_extension_uses():
    """The spike shipped a stub built from `main.ts` alone and was missing
    `window.onDidChangeTextEditorSelection`, which TlapsClient's constructor
    registers. This is that audit, as a test: a new extension version that
    reaches for something new fails here rather than at startup.
    """
    ext_root = _extension()
    stub = (serve.JS_DIR / "vscode-stub.js").read_text(encoding="utf-8")
    used = {
        match.group(2)
        for path in (ext_root / "src").rglob("*.ts")
        for match in _VSCODE_MEMBER.finditer(path.read_text(encoding="utf-8"))
    }
    missing = sorted(
        name
        for name in used - _DELIBERATELY_ABSENT
        if not re.search(rf"\b{re.escape(name)}\s*:", stub)
    )
    assert not missing, (
        f"{ext_root} uses vscode names the shim does not provide: {missing}. "
        "Add them to src/tlakit/mcp/js/vscode-stub.js -- activate() is fatal, so "
        "the server will not start without them."
    )


@pytest.mark.mcp
def test_the_server_starts_and_offers_its_nine_tools(tmp_path):
    bundle = _bundle(_extension())
    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        assert server.port and server.port != 0
        tools = McpRunner(url=server.url, workspace=tmp_path).tools()
    assert len(tools) == 9
    assert "tlaplus_mcp_tlc_check" in tools and "tlaplus_mcp_sany_parse" in tools


@pytest.mark.mcp
def test_the_server_starts_only_one_of_itself(tmp_path):
    """`activate()` starts an MCP server whenever `tlaplus.mcp.port` is a number.
    The spike constructed a second one and got away with it only because its
    `activate()` threw first."""
    bundle = _bundle(_extension())
    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        McpRunner(url=server.url, workspace=tmp_path).tools()
        listening = [
            line for line in server.log.splitlines()
            if "MCP server listening" in line
        ]
    assert len(listening) == 1, f"expected one server, got:\n{server.log}"


@pytest.mark.mcp
def test_the_server_runs_the_TLC_tlakit_pins(tmp_path):
    """Finding 2 on #23: the extension resolves its own jar and ships an older
    TLC. If this regresses, every conformance comparison below is between two
    different checkers."""
    bundle = _bundle(_extension())
    try:
        local = CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        served = McpRunner(url=server.url, workspace=tmp_path).tlc_version()
    assert served is not None
    result = local.check(
        "---- MODULE V ----\nVARIABLE x\nInit == x = 0\nNext == x' = x\n"
        "Spec == Init /\\ [][Next]_x\n====\n",
        "V", "SPECIFICATION Spec\n", timeout=60,
    )
    match = re.search(r"TLC2 Version (\S+)", result.raw.stdout)
    assert match, "the local runner printed no version"
    assert served == match.group(1)


# --- #22's conformance test ----------------------------------------------


#: Entry, and how many trace states it must produce. Pinned rather than left to
#: "whatever both runners said": comparing `theirs.trace == ours.trace` passes
#: for free when both are None, so a change that stopped either runner
#: recovering a trace would look like agreement.
CONFORMANCE = [
    ("die_hard", 7),
    ("lost_update", 5),
    ("broken_deadlock", 2),
    ("microwave", None),          # no violation, so no counterexample
]


@pytest.mark.mcp
@pytest.mark.parametrize("entry,trace_states", CONFORMANCE, ids=lambda v: str(v))
def test_both_runners_agree_on_the_corpus(entry, trace_states, tmp_path):
    """#22's acceptance criterion, with the amendment from its own comments:
    assert both runners report the same TLC before comparing any results, and
    skip rather than fail when they do not.
    """
    bundle = _bundle(_extension())
    try:
        local = CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    source = (CORPUS / entry / "spec.tla").read_text(encoding="utf-8")
    config = (CORPUS / entry / "model.cfg").read_text(encoding="utf-8")
    module = source.split("MODULE", 1)[1].split()[0]

    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        remote = McpRunner(url=server.url, workspace=tmp_path)
        served_version = remote.tlc_version()
        theirs = remote.check(source, module, config, timeout=300)

    ours = local.check(source, module, config, timeout=300)
    local_version = re.search(r"TLC2 Version (\S+)", ours.raw.stdout)
    if served_version is None or local_version is None:
        pytest.skip("could not read a TLC version from both runners")
    if served_version != local_version.group(1):
        pytest.skip(
            f"different checkers: the server runs TLC {served_version}, the "
            f"local runner {local_version.group(1)}. A disagreement between "
            "these would be a version difference, not a tlakit bug."
        )

    assert theirs.outcome is ours.outcome
    assert theirs.raw.exit_code == ours.raw.exit_code
    # Agreement on nothing is not agreement: both runners have to have recovered
    # the counterexample this entry is known to have.
    if trace_states is None:
        assert theirs.trace is None and ours.trace is None
    else:
        assert ours.trace is not None and theirs.trace is not None
        assert len(ours.trace.states) == trace_states
        assert theirs.trace.variables == ours.trace.variables
        assert theirs.trace.states == ours.trace.states
        assert theirs.trace.loop_start == ours.trace.loop_start


@pytest.mark.mcp
def test_a_parse_error_reads_the_same_through_both_runners(tmp_path):
    bundle = _bundle(_extension())
    try:
        local = CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    broken = "---- MODULE Broken ----\nVARIABLE x\nInit == x = \n====\n"

    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        theirs = McpRunner(url=server.url, workspace=tmp_path).parse(broken, "Broken")
    ours = local.parse(broken, "Broken")

    assert theirs.outcome is ours.outcome is Outcome.PARSE_ERROR
    # Both find the same line. The messages differ -- the extension rewrites
    # SANY's -- so the line is what can honestly be compared.
    assert {d.line for d in theirs.diagnostics} & {d.line for d in ours.diagnostics}


@pytest.mark.mcp
def test_a_graph_comes_back_from_a_dump(tmp_path):
    """The server invokes `tlc2.TLC` itself, so tlakit's streaming state writer
    has nowhere to go -- `-dump dot` and `parse_dot` are what is left, which is
    why #72 kept them."""
    bundle = _bundle(_extension())
    source = (CORPUS / "die_hard" / "spec.tla").read_text(encoding="utf-8")
    config = (CORPUS / "die_hard" / "model.cfg").read_text(encoding="utf-8")
    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        result = McpRunner(url=server.url, workspace=tmp_path).check(
            source, "DieHard", config, graph=True, timeout=300
        )
    assert result.graph is not None and len(result.graph.nodes) == 14
    assert any(n.initial for n in result.graph.nodes)


@pytest.mark.mcp
def test_the_server_binds_loopback_only(tmp_path):
    """The whole of this server's access control."""
    import socket

    bundle = _bundle(_extension())
    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        assert server.port is not None
        # Reachable on loopback.
        with socket.create_connection(("127.0.0.1", server.port), timeout=10):
            pass
        # Not on a routable address of this host. Read from the routing table
        # rather than from the hostname, which often resolves to 127.0.0.1.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))  # RFC 5737, and UDP sends nothing
            host = probe.getsockname()[0]
        except OSError:
            pytest.skip("no route, so no non-loopback address to test against")
        finally:
            probe.close()
        if host.startswith("127."):
            pytest.skip("this host has no non-loopback address to test against")
        with pytest.raises(OSError):
            with socket.create_connection((host, server.port), timeout=5):
                pass


@pytest.mark.mcp
def test_the_ready_line_reports_the_port_that_was_actually_bound(tmp_path):
    """`--port 0` is how a test gets a free port, and the only way to learn it."""
    bundle = _bundle(_extension())
    with serve.McpServer(bundle, port=0, workspace=tmp_path) as server:
        assert server.port not in (0, None)
        assert f":{server.port}" in server.log
        assert server.url.endswith(f":{server.port}/mcp")


@pytest.mark.mcp
def test_build_only_stops_before_serving(tmp_path):
    ext_root = _extension()
    _bundle(ext_root)
    assert serve.main(["--build-only", "--ext-root", str(ext_root)]) == 0
