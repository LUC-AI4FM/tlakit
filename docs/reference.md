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

---

## Environment variables

Resolution order for the jars is always: an explicit argument, then the
environment variable, then the platformdirs cache that `tlakit.install` writes.

| Variable | Read by | Effect |
| --- | --- | --- |
| `TLAKIT_TLA2TOOLS` | `tlakit.jar.find_tools_jar` | Path to `tla2tools.jar`. Re-read on every `default_runner()` call, so changing it takes effect immediately rather than being masked by a cached runner. |
| `TLAKIT_COMMUNITY_MODULES` | `tlakit.jar.find_community_jar` | Path to `CommunityModules-deps.jar`. Optional: without it, `SVG.tla`, `IOUtils`, and the rest of the community modules are simply unavailable. |
| `TLAKIT_JAVA` | `tlakit.cli.java_executable` | Path to a `java` binary, overriding `PATH`. For a machine with several JVMs where the first on `PATH` is the wrong one. |
| `TLAKIT_SERVE_KEY` | `tlakit.serve.app` | Shared secret required on requests to a `tlakit.serve` instance. Unset means the service is unauthenticated, which is only appropriate behind something else that authenticates. |
| `TLAKIT_SERVE_KEY_FILE` | `tlakit.serve.app` | Path to a file holding that secret, for keeping it out of the process environment. Takes precedence over `TLAKIT_SERVE_KEY`. |

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

---

## Notebook magics

Load with `%load_ext tlakit`. In a browser (JupyterLite/Pyodide) this also
switches to the remote runner and turns on TLA+ cell routing, because there is
no JVM and no `subprocess` there; locally it only registers the magics.

### `%%tla ModuleName [--no-parse]`

Define a TLA+ module for the session. The cell body is the module source.

`ModuleName` may be omitted when the cell contains a `---- MODULE Name ----`
header, which it normally does. Returns the `CheckResult` from a SANY parse so
a syntax error shows up in the cell that caused it; `--no-parse` skips that and
returns nothing.

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

---

## Public names

Everything in `tlakit.__all__`, grouped by what it is for. Follow a name to its
docstring for the detail.

**Entry points** — `load`, `check_source`, `Spec`, `build_config`

**Results** — `CheckResult`, `Outcome`, `Trace`, `Action`, `Diagnostic`,
`Severity`, `Stats`, `RawOutput`, `flatten_state`

**Runners** — `CliRunner`, `default_runner`, `use_remote`, `use_local`,
`java_executable`

**Errors** — `JarNotFound`, `JavaNotFound`

**Submodules** — `api`, `install`, `jar`, `remote`, `sweep`

Names not in `__all__` — `tlakit.serve`, `tlakit.kernel`, `tlakit.render`,
`tlakit.parse`, `tlakit.trace` — are real and documented, but their signatures
are not covered by the same stability expectation.
