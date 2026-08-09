# Where you run tlakit changes what it can do

The same three lines behave differently in a browser notebook, in Colab, and in
a terminal — not because tlakit is inconsistent, but because one of those
places has no Java and no `subprocess`, and the other two do. This page is the
map, because the differences are otherwise discovered one confusing error at a
time.

The single fact everything follows from:

```python
tlakit.notebook.in_browser()   # sys.platform == "emscripten"
```

Under Pyodide there is no JVM to start and no process to spawn. So `%load_ext
tlakit` switches to `RemoteRunner` *automatically* there, and only there.
Anywhere else it registers the magics and leaves the runner alone — silently
sending a local user's specs to someone else's server would be a surprise, and
their own TLC is faster and unmetered.

## The comparison

| | **tlakit.pages.dev** (JupyterLite / Pyodide) | **Google Colab** | **Terminal / local Jupyter** |
| --- | --- | --- | --- |
| Where Python runs | your browser tab | Google's VM | your machine |
| Where TLC runs | the public runner, over HTTPS | your Colab VM, after you install Java | your machine |
| Install needed | none | `pip install tlakit` + a JRE + `python -m tlakit.install` | same as Colab, once |
| Runner in use | `RemoteRunner` (automatic) | `CliRunner` | `CliRunner` |
| `%%tla` parses with SANY | ✓ — over HTTPS, via `/parse` | ✓ | ✓ |
| `%tla_eval` | ✗ — no REPL on the service | ✓ | ✓ |
| PlusCal translation | ✗ | ✓ | ✓ |
| CommunityModules (`SVG`, `IOUtils`, `Json`) | ✗ — deliberately absent | ✓ if you fetch them | ✓ if you fetch them |
| `spec.sweep(...)` | ✗ — needs local processes | ✓ | ✓ |
| Apalache, TLA+ Debugger stepping | ✗ | ✓ if installed | ✓ if installed |
| Limits | 30 checks/min, 300/hr; 120 parses/min; 30 s and 64 KiB each | none but the VM's | none |
| Survives a page reload | ✗ | ✗ (VM recycles) | ✓ |

The browser column is short on capability *by construction*, not by neglect.
Everything absent there is absent because it needs a local process, or because
exposing it would mean running arbitrary specs' I/O on someone else's machine.

Parsing used to be in that list and is not any more (#67). It came back because
it is the one operation with no state space to explore: SANY reads the module
and stops, so it costs a fraction of a check and can carry a budget four times
larger. A module cell in the browser now reports what is actually wrong with a
spec, on the line it is wrong on, instead of only that it was stored. If the
service cannot be reached, `%%tla` falls back to storing the module rather than
failing the cell — a network problem is not a fact about the spec.

## What each one is actually for

**The browser page** is for the first five minutes. Nothing installed, nothing
to configure, a link you can send someone. Take it as a demo and a teaching
tool, not a workbench: the limits are real, a reload loses your session, and
the missing pieces (SANY, PlusCal, sweeps) are the ones you want as soon as
you are doing more than reading.

**Colab** is the one people reach for and set up wrong, so, concretely:

```python
!pip install -q tlakit
!apt-get -qq install -y default-jre    # Colab has no JVM by default
!python -m tlakit.install              # fetches the pinned, checksummed jars

import tlakit
result = tlakit.check_source(SPEC, invariants=["Inv"], check_deadlock=False)
```

Three things worth knowing before you write the version you would have written:

- **You do not need to write the spec to a file.** `check_source` takes the
  text. `tlakit.load` exists for a `.tla` file you already have, not as the way
  in.
- **`spec.check()` with no arguments checks no invariant.** It builds a config
  naming your `Spec` and nothing else, so it will happily report success
  without ever evaluating the property you care about. Pass `invariants=[...]`.
- **A terminating spec reports `DEADLOCK`.** TLC cannot tell "finished" from
  "stuck" — both end in a state with no successor. If yours is meant to finish,
  pass `check_deadlock=False`.

Colab is a full local install that happens to be rented, so everything works —
but the VM is recycled, and you pay the install cost on every fresh session.

**A terminal or local Jupyter** is the real thing: no limits, no round trip, a
JVM that stays warm, and the only place the debugger stepping (`tlakit.dap`)
and Apalache (`tlakit.apalache`) can run. If you are going to use tlakit more
than once, this is where.

## Choosing on purpose

`use_remote()` and `use_local()` override the automatic choice from anywhere:

```python
tlakit.use_remote()          # send checks to the public runner
tlakit.use_local()           # back to your own TLC
```

`use_remote()` from a terminal is occasionally useful — checking that the
service agrees with your local TLC, or reproducing what a visitor to the page
would see. It is not a way around a slow local run: the service is smaller than
your laptop and rate-limited besides.

The thing to hold on to: **the result object is the same everywhere.** A
`CheckResult` from the browser and one from your terminal carry the same
outcome, the same `Trace`, and render the same way. Only the runner underneath
differs, which is what makes it reasonable to prototype on the page and then
move to a real install without rewriting anything.
