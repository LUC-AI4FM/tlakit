"""Run vscode-tlaplus's MCP server without VSCode.

    python -m tlakit.mcp.serve --port 8931

The extension's MCP server is a plain express app, but it lives inside a VSCode
extension, so importing it drags in the TLAPS language client and the React
webviews. `js/` holds what it takes to run it anyway: a `vscode` shim, a stub
for the language client, a bootstrap, and an esbuild config. This module stages
those into an extension checkout, bundles them, and launches the result.

Three things this does that the M0 spike did not, each because the spike was
wrong about it:

- **`activate()` failing is fatal.** It creates the diagnostic collection the
  MCP handlers read through `getDiagnostic()`, so a shim that half-activates
  returns empty diagnostics and looks like a working server.
- **The server is started by `activate()`**, not constructed separately.
  `activate()` starts one whenever `tlaplus.mcp.port` is a number; doing both
  binds two ports.
- **TLC is tlakit's.** The extension resolves `../tools/tla2tools.jar` relative
  to its bundle, and ships TLC 2026.03.19 where tlakit pins 2026.07.31. The
  bundle is therefore built into a tlakit-owned directory whose `tools/` holds
  the jar `find_tools_jar` returns -- otherwise `McpRunner` and `CliRunner`
  would run different checkers and the first disagreement would look like a
  tlakit bug.

Network and npm are opt-in, `--install`, on the same principle as
`tlakit.install`: cloning a repository and running 450 packages' install
scripts is not something a `serve` command should do as a side effect. Without
it, an extension checkout has to already be there.

**This server has no authentication and its tools run TLC on any path they are
given.** It binds loopback only (see `js/bootstrap.ts`), which is the whole of
its access control. It is a local developer tool, not `tlakit.serve`.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from ..jar import cache_dir, find_community_jar, find_tools_jar

REPO_URL = "https://github.com/tlaplus/vscode-tlaplus.git"

#: Pinned rather than "whatever main is today". The shim mirrors an API surface
#: that this commit's `activate()` touches; a silent upstream bump should show up
#: as a deliberate change here, the way the jar pins do.
#: vscode-tlaplus 1.7.0, 2026-08-02.
REF = "87b4e7b25fb89cc017aa9e26ac7c7de3b6fe19ff"

ENV_EXT_ROOT = "TLAKIT_VSCODE_TLAPLUS"

#: Where the staged shim, the pinned jars and the bundle live inside a checkout.
#: A dot directory, so it cannot be mistaken for part of the extension.
STAGE_DIR = ".tlakit-mcp"
JS_DIR = Path(__file__).parent / "js"

#: The bootstrap's first line of stdout, carrying the port actually bound.
READY = re.compile(r"^\[tlakit\] mcp ready on (?P<host>[^\s:]+):(?P<port>\d+)")

DEFAULT_PORT = 8931
DEFAULT_HOST = "127.0.0.1"


class McpServeError(RuntimeError):
    """The server could not be prepared or started."""


def _run(argv: list[str], cwd: Path, what: str, timeout: float = 900) -> None:
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise McpServeError(f"{argv[0]} is not installed, so {what} cannot run.") from exc
    except subprocess.TimeoutExpired as exc:
        raise McpServeError(f"{what} did not finish within {timeout}s.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise McpServeError(f"{what} failed:\n{detail}")


def default_ext_root() -> Path:
    """Where `--install` puts a checkout, and where `serve` looks for one."""
    return cache_dir() / "vscode-tlaplus"


def find_extension(explicit: Path | None = None) -> Path:
    """Locate an extension checkout: explicit, then the environment, then cache.

    Raises `McpServeError` naming `--install` rather than cloning, so no command
    reaches the network without being asked to.
    """
    tried: list[Path] = []
    for candidate in (
        explicit,
        Path(os.environ[ENV_EXT_ROOT]) if os.environ.get(ENV_EXT_ROOT) else None,
        default_ext_root(),
    ):
        if candidate is None:
            continue
        tried.append(candidate)
        if (candidate / "src" / "lm" / "MCPServer.ts").is_file():
            return candidate
    raise McpServeError(
        "No vscode-tlaplus checkout found. Looked in: "
        + ", ".join(str(p) for p in tried)
        + f". Run `python -m tlakit.mcp.serve --install` to clone {REPO_URL} at "
        f"the pinned commit and build it, or set {ENV_EXT_ROOT} to a checkout "
        "you already have."
    )


def clone(target: Path, ref: str = REF) -> Path:
    """Clone the extension at the pinned commit.

    A shallow clone of one commit rather than of a branch: the pin is the point,
    and `--depth 1` of a moving branch would not be reproducible.
    """
    if (target / ".git").is_dir():
        _run(["git", "fetch", "--depth", "1", "origin", ref], target, "git fetch")
        _run(["git", "checkout", "--force", ref], target, "git checkout")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--quiet", str(target.name)], target.parent, "git init")
    _run(["git", "remote", "add", "origin", REPO_URL], target, "git remote add")
    _run(["git", "fetch", "--depth", "1", "origin", ref], target, "git fetch")
    _run(["git", "checkout", "--force", "FETCH_HEAD"], target, "git checkout")
    return target


def npm_install(ext_root: Path) -> None:
    """`npm install` in the checkout, for esbuild and the extension's deps."""
    _run(
        ["npm", "install", "--no-audit", "--no-fund"],
        ext_root,
        "npm install",
    )


def stage(ext_root: Path, community: bool = True) -> Path:
    """Copy the shim and tlakit's jars into `<ext_root>/.tlakit-mcp/`.

    The jars are copied rather than symlinked: this directory is a build output
    the extension reads by relative path, and a symlink into platformdirs would
    turn a cache eviction into a server that runs no TLC at all.

    `community=False` leaves CommunityModules out. It ships `IOUtils!IOExec`,
    which runs shell commands from inside a specification -- and TLC loads every
    jar next to tla2tools.jar whatever the classpath says, so leaving the file
    out is the only way to deny it.
    """
    stage_dir = ext_root / STAGE_DIR
    tools = stage_dir / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    for source in sorted(JS_DIR.iterdir()):
        if source.is_file():
            shutil.copy2(source, stage_dir / source.name)

    tools_jar = find_tools_jar()
    shutil.copy2(tools_jar, tools / "tla2tools.jar")
    community_jar = find_community_jar() if community else None
    target = tools / "CommunityModules-deps.jar"
    if community_jar is not None:
        shutil.copy2(community_jar, target)
    else:
        target.unlink(missing_ok=True)
    return stage_dir


def bundle_path(ext_root: Path) -> Path:
    return ext_root / STAGE_DIR / "out" / "server.js"


def _newest_input(ext_root: Path) -> float:
    """When the newest thing the bundle is built from last changed."""
    times = [p.stat().st_mtime for p in JS_DIR.iterdir() if p.is_file()]
    times += [
        p.stat().st_mtime for p in (ext_root / "src").rglob("*.ts") if p.is_file()
    ]
    return max(times, default=0.0)


def is_stale(ext_root: Path) -> bool:
    """Whether the bundle predates the shim or the extension sources.

    Without this an upgraded tlakit keeps running the bundle the *previous*
    shim produced, and a checkout moved to another ref keeps running the old
    one -- both silently, since a bundle that exists looks like a bundle that is
    current.
    """
    out = bundle_path(ext_root)
    if not out.is_file():
        return True
    return _newest_input(ext_root) > out.stat().st_mtime


def build(ext_root: Path) -> Path:
    """Bundle the server. Local only -- no network, no npm.

    Run from the checkout, because that is where `require('esbuild')` and the
    extension's own sources resolve from.
    """
    if not (ext_root / "node_modules" / "esbuild").is_dir():
        raise McpServeError(
            f"{ext_root} has no node_modules/esbuild, so the server cannot be "
            "bundled. Run `python -m tlakit.mcp.serve --install`, or `npm "
            f"install` in {ext_root} yourself."
        )
    stage(ext_root)
    _run(["node", str(Path(STAGE_DIR) / "build.js")], ext_root, "esbuild")
    out = bundle_path(ext_root)
    if not out.is_file():
        raise McpServeError(f"esbuild reported success but {out} is not there.")
    return out


def prepare(
    ext_root: Path | None = None, install: bool = False, rebuild: bool = False
) -> Path:
    """Get a checkout to the point of having a bundle, and return the bundle."""
    if install:
        root = ext_root or default_ext_root()
        clone(root)
        npm_install(root)
    else:
        root = find_extension(ext_root)
    if rebuild or is_stale(root):
        build(root)
    return bundle_path(root)


class McpServer:
    """A running MCP server process.

    Use it as a context manager; the port is only known once the process says so
    (`--port 0` picks a free one), so `url` is not available before `start`.
    """

    def __init__(
        self,
        bundle: Path,
        port: int = DEFAULT_PORT,
        host: str = DEFAULT_HOST,
        workspace: Path | None = None,
    ) -> None:
        self.bundle = Path(bundle)
        self.requested_port = port
        self.host = host
        self.workspace = Path(workspace or Path.cwd())
        self.port: int | None = None
        self.process: subprocess.Popen[str] | None = None
        self._log: list[str] = []

    @property
    def url(self) -> str:
        if self.port is None:
            raise McpServeError("The server is not running yet.")
        return f"http://{self.host}:{self.port}/mcp"

    def start(self, timeout: float = 120) -> McpServer:
        env = dict(os.environ)
        env["TLA_WORKSPACE"] = str(self.workspace)
        env["TLA_MCP_PORT"] = str(self.requested_port)
        env["TLAKIT_MCP_HOST"] = self.host
        self.process = subprocess.Popen(
            ["node", str(self.bundle)],
            cwd=self.bundle.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        # Wait for the bootstrap's ready line rather than sleeping: it is how the
        # port is learned at all when one was not asked for.
        ready = threading.Event()
        def pump() -> None:
            assert self.process is not None and self.process.stdout is not None
            for line in self.process.stdout:
                self._log.append(line.rstrip("\n"))
                match = READY.match(line)
                if match and self.port is None:
                    self.port = int(match.group("port"))
                    ready.set()
            ready.set()  # the process ended; stop waiting for a line

        self._pump = threading.Thread(target=pump, name="tlakit-mcp-log", daemon=True)
        self._pump.start()
        if not ready.wait(timeout) or self.port is None:
            log = "\n".join(self._log[-30:])
            self.stop()
            raise McpServeError(
                f"The MCP server did not report itself ready within {timeout}s."
                + (f" Its output was:\n{log}" if log else "")
            )
        return self

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=15)
        self.process = None

    @property
    def log(self) -> str:
        return "\n".join(self._log)

    def __enter__(self) -> McpServer:
        return self if self.process is not None else self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tlakit.mcp.serve",
        description=(
            "Run vscode-tlaplus's MCP server headless, against the TLA+ tools "
            "jar tlakit pins."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="0 picks any free port (default: %(default)s)")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="bind address; loopback is the only access control "
                             "this server has (default: %(default)s)")
    parser.add_argument("--workspace", type=Path, default=None,
                        help="the directory the server treats as its workspace "
                             "(default: the current one)")
    parser.add_argument("--ext-root", type=Path, default=None,
                        help=f"a vscode-tlaplus checkout (default: ${ENV_EXT_ROOT}, "
                             "then tlakit's cache)")
    parser.add_argument("--install", action="store_true",
                        help=f"clone {REPO_URL} at the pinned commit and npm "
                             "install it. Reaches the network and runs the "
                             "packages' install scripts, so it is opt-in.")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-bundle even if a bundle is already there")
    parser.add_argument("--build-only", action="store_true",
                        help="prepare and bundle, then stop without serving")
    args = parser.parse_args(argv)

    try:
        bundle = prepare(args.ext_root, install=args.install, rebuild=args.rebuild)
    except McpServeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"bundle: {bundle}")
    if args.build_only:
        return 0

    server = McpServer(bundle, port=args.port, host=args.host, workspace=args.workspace)
    try:
        server.start()
    except McpServeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"serving: {server.url}")
    print(f"workspace: {server.workspace}")
    print("This server is unauthenticated and runs TLC on any path it is given.")
    try:
        assert server.process is not None
        return server.process.wait()
    except KeyboardInterrupt:
        server.stop()
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
