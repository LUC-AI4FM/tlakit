# Reference

Everything tlakit exposes on purpose. The API pages are generated from the
docstrings in `src/tlakit/`, so this file is the place for the things that
have no docstring to live in: environment variables and notebook magics.

`tests/test_public_api.py` fails if a public name loses its docstring, if a
`TLAKIT_*` variable is read in `src/` without appearing below, or if a magic is
registered without being listed below. This page cannot quietly go stale.

---

## Getting a result

Three entry points, in rough order of how most code reaches them:

```python
import tlakit

spec = tlakit.load("DieHard.tla")          # from a file
result = spec.check(invariants=["Inv"])

result = tlakit.check_source(source_text)   # from a string, e.g. from a model
```

A failing check is a **return value, not an exception**. `result.outcome` says
what happened and `result.ok` is the boolean shorthand. Only things that stop a
run from happening at all — no `java`, no `tla2tools.jar` — raise
(`JavaNotFound`, `JarNotFound`).

That distinction is the whole point of the result objects: a spec violating its
invariant is the tool working, and code that wraps every check in `try` to find
that out has been made worse by the library.

One outcome surprises everybody once. A specification meant to *terminate* ends
in a state with no successor, and so does one that is stuck — TLC cannot tell
them apart, so it reports `Outcome.DEADLOCK` for both. If yours is supposed to
finish, say so:

```python
result = spec.check(invariants=["Inv"], check_deadlock=False)
```

or `CHECK_DEADLOCK FALSE` in a raw config. Passing both is an error rather than
a silent preference for one.

---

## Environment variables

Resolution order for the jars is always: an explicit argument, then the
environment variable, then the platformdirs cache that `tlakit.install` writes.

| Variable | Read by | Effect |
| --- | --- | --- |
| `TLAKIT_TLA2TOOLS` | `tlakit.jar.find_tools_jar` | Path to `tla2tools.jar`. Re-read on every `default_runner()` call, so changing it takes effect immediately rather than being masked by a cached runner. |
| `TLAKIT_COMMUNITY_MODULES` | `tlakit.jar.find_community_jar` | Path to `CommunityModules-deps.jar`. Optional: without it, `SVG.tla`, `IOUtils`, and the rest of the community modules are simply unavailable. |
| `TLAKIT_JAVA` | `tlakit.cli.java_executable` | Path to a `java` binary, overriding `PATH`. For a machine with several JVMs where the first on `PATH` is the wrong one. |
| `TLAKIT_JAVAC` | `tlakit.statewriter.javac_executable` | Path to a `javac` binary, used once to compile the `IStateWriter` that streams the state graph. Unset, tlakit looks for a `javac` beside `TLAKIT_JAVA` and then on `PATH`; finding none is not an error — the graph comes from `-dump dot` instead. |
| `TLAKIT_SERVE_KEY` | `tlakit.serve.app` | Shared secret required on requests to a `tlakit.serve` instance. Unset means the service is unauthenticated, which is only appropriate behind something else that authenticates. |
| `TLAKIT_APALACHE` | `tlakit.apalache.find_apalache` | Path to the `apalache-mc` launcher, overriding `PATH`. Apalache is a 180 MB tarball rather than a jar, so `tlakit.install` does not fetch it — this is how you point at your own. |
| `TLAKIT_TLAPM` | `tlakit.tlaps.find_tlapm` | Path to the `tlapm` binary. TLAPS is a 1.1 GB download unpacking to ~3 GB (it bundles Isabelle), so `tlakit.install` does not fetch it. On Apple Silicon you need the `arm64-darwin` asset from the `1.6.0-pre` release — the 1.5.0 installers are i386 and will not run. |
| `TLAKIT_SERVE_KEY_FILE` | `tlakit.serve.app` | Path to a file holding that secret, for keeping it out of the process environment. Takes precedence over `TLAKIT_SERVE_KEY`. |
| `TLAKIT_VSCODE_TLAPLUS` | `tlakit.mcp.serve.find_extension` | Path to a vscode-tlaplus checkout, for running its MCP server headless. Without it, `tlakit.mcp.serve` looks in its own cache and tells you to run `--install` rather than cloning behind your back. |
| `TLAKIT_MCP_HOST` | `tlakit/mcp/js/bootstrap.ts` | Bind address for that MCP server. Defaults to loopback at both layers — the bootstrap and `tlakit.mcp.serve --host` — because loopback is the only access control it has. |

`TLAKIT_TLA2TOOLS` and `TLAKIT_COMMUNITY_MODULES` are also scrubbed from any
output `tlakit.serve` returns — see `tlakit.serve.redact` — because their values
are absolute paths on the host.

### Jar isolation

`find_community_jar` returning nothing is not the same as the community modules
being unavailable to TLC: **TLC loads every jar sitting next to `tla2tools.jar`**,
whatever the classpath says. That matters because CommunityModules ships
`IOUtils!IOExec`, which runs shell commands from inside a spec.

Anything running untrusted specs must therefore isolate the *directory*, not
just the classpath. `tlakit.jar.assert_isolated()` and
`CliRunner(community_jar=False)` are the supported way; `tlakit.serve` uses
both.

### The state graph

`check(..., graph=True)` fills `CheckResult.graph`. The graph comes from
tlakit's own `tlc2.util.IStateWriter` — `src/tlakit/java/TlakitStateWriter.java`
— which TLC hands each state and edge to as it generates them, and which writes
one NDJSON record per state and per edge. tlakit reads that file while TLC is
still running, so:

- a run stopped by `timeout` still returns the states it reached, where
  `-dump dot` would have left nothing at all;
- `max_graph_nodes` stops holding states at the limit rather than after the
  whole graph has been written out and read back.

The writer is compiled once with `javac`, against the same `tla2tools.jar` the
run uses, and cached under platformdirs beside the jars. A JRE can run TLC but
cannot compile it; **that is a fallback, not an error** — tlakit passes
`-dump dot` instead and parses the file afterwards. The graph is identical
either way (`tests/test_statewriter.py` compares the two routes state for
state); only the streaming and the partial-result behaviour are lost. Set
`TLAKIT_JAVAC` to point at a compiler, or read `CheckResult.raw.argv` to see
which route a given run took.

State ids are TLC's own fingerprints, and are only comparable *within* one run
unless `-fp` is passed: TLC picks its fingerprint polynomial per run. Variable
values are TLA+ source text, not decoded JSON — use `result.trace` for
structured values.

---

## The MCP backend

`tlakit.mcp` is an optional third backend, beside `CliRunner` and
`RemoteRunner`: vscode-tlaplus's own MCP server, which is what an editor or
agent already talks to, and which does PlusCal transpilation and the
extension's diagnostic post-processing.

```bash
python -m tlakit.mcp.serve --install     # clone at the pinned commit, npm install, build
python -m tlakit.mcp.serve --port 8931 --workspace .
```

```python
from tlakit.mcp import McpRunner

runner = McpRunner(url="http://127.0.0.1:8931/mcp", workspace=".")
result = runner.check(source, "DieHard", config)   # a CheckResult, as ever
```

Three things are worth knowing before reaching for it.

**It has to share the workspace.** MCP tools take file paths rather than module
text, and the server refuses any path outside the workspace it was started
with. So `workspace=` must be the directory the server was given — a server on
another machine cannot work at all, and `RemoteRunner` is the client for that.

**Results are prose, so structure is asked for rather than parsed out.** The
tools answer with TLC's console output wrapped in sentences; `McpRunner` passes
`-dumpTrace json` through the tool's `extraOpts` and reads the file, and hands
the console output to the same parser `CliRunner` uses. Nothing here
re-implements a TLC output parser.

**`timeout` bounds the request, not the run.** There is no tool for cancelling
a check, so an overrunning run keeps going on the server while `McpRunner`
returns `Outcome.TIMEOUT`. A hard budget needs `CliRunner`, which owns the
process it started.

Two smaller consequences: `eval` raises `Unsupported`, because the server
exposes no `tlc2.REPL` tool; and `graph=True` comes back from `-dump dot`,
because the server invokes `tlc2.TLC` itself and there is nowhere to put a
custom `IStateWriter`.

### Running an extension headless

The server ships inside a VSCode extension, so `tlakit/mcp/js/` carries a
`vscode` shim, a stub for the TLAPS language client, a bootstrap and an esbuild
config. `serve` stages those into a checkout and bundles them. Two details of
that are load-bearing rather than incidental:

- **The bundle is built into `<checkout>/.tlakit-mcp/out/`.** The extension
  resolves its jars as `../tools/tla2tools.jar` relative to its own bundle, and
  ships TLC 2026.03.19 where tlakit pins 2026.07.31. Building into a
  tlakit-owned directory whose `tools/` holds tlakit's jar is what makes
  `McpRunner` and `CliRunner` the same checker — without it, the first
  disagreement between them would look like a tlakit bug and be a version
  difference.
- **A failed `activate()` is fatal.** It creates the diagnostic collection the
  MCP handlers read through `getDiagnostic()`, so a shim that half-activates
  returns empty diagnostics and looks like a working server.

The extension is pinned to a commit rather than a branch: the shim mirrors an
API surface, and `tests/test_mcp_serve.py` compares the two, so an upstream
change fails a test rather than a startup.

Cloning and `npm install` are opt-in, `--install`, for the same reason
`tlakit.install` is: neither belongs in a `serve` command as a side effect.

**This server is unauthenticated and its tools run TLC on any path inside its
workspace.** Binding to loopback is the whole of its access control. It is a
local developer tool; `tlakit.serve` is the one built to face a network.

---

## Notebook magics

Load with `%load_ext tlakit`. In a browser (JupyterLite/Pyodide) this also
switches to the remote runner and turns on TLA+ cell routing, because there is
no JVM and no `subprocess` there; locally it only registers the magics.

### `%%tla ModuleName [--no-parse]`

Define a TLA+ module for the session. The cell body is the module source.

`ModuleName` may be omitted when the cell contains a `---- MODULE Name ----`
header, which it normally does. Returns the `CheckResult` from a SANY parse so
a syntax error shows up in the cell that caused it.

`--no-parse` skips the parse, and so does any runner without a SANY to call —
the remote one, which is the only runner in a browser. Both return a
`ModuleDefined` naming the module and its variables, rather than nothing at
all: the cell still did something, and in a browser there is no filesystem to
go and check. A syntax error then surfaces from the first `%%tlc`, since TLC
parses before it explores.

Modules accumulate in `tlakit.magics.MODULES` for the rest of the session,
which is how `%%tlc` and `%tla_eval` find them.

### `%%tlc ModuleName [--timeout=SECONDS]`

Model-check a module defined earlier by `%%tla`. **The cell body is the `.cfg`
file**, not TLA+ — this is the usual first surprise.

`--timeout` is in seconds and bounds the whole run, including PlusCal
translation when the module has an algorithm block. Returns a `CheckResult`,
which renders itself as HTML in a notebook.

### `%tla_eval <expression>`

Evaluate one constant TLA+ expression with `tlc2.REPL`, returning the value as
a Python object. The expression may use operators from any module defined
earlier with `%%tla`.

Constant-level only — no `VARIABLE`, no primed expressions — because that is
what the REPL itself is. An evaluation error raises `TlaMagicError` with TLC's
own message rather than returning a sentinel.

Local only. The public service exposes checking and no REPL, so in a browser
this raises `Unsupported`; express the value as an operator and check it as an
`INVARIANT` instead.

---

## Public names

Everything in `tlakit.__all__`, grouped by what it is for. Follow a name to its
docstring for the detail.

**Entry points** — `load`, `check_source`, `Spec`, `build_config`, `Raw`

**Command line** — `tlakit check <file>.tla` and `tlakit parse <file>.tla`.
Exit `0` when the spec checked out, `1` when the run found something wrong with
it, `2` when the run could not happen. The last two are separate so that
`tlakit check Spec.tla && deploy` is meaningful and a CI job can still tell a
violated invariant from a mistyped path.

**Results** — `CheckResult`, `Outcome`, `Trace`, `Action`, `Diagnostic`,
`Severity`, `Stats`, `RawOutput`, `flatten_state`

**Runners** — `CliRunner`, `default_runner`, `use_remote`, `use_local`,
`java_executable`

**Errors** — `JarNotFound`, `JavaNotFound`

**Submodules** — `api`, `install`, `jar`, `remote`, `sweep`

Names not in `__all__` — `tlakit.serve`, `tlakit.kernel`, `tlakit.render`,
`tlakit.parse`, `tlakit.trace` — are real and documented, but their signatures
are not covered by the same stability expectation.
