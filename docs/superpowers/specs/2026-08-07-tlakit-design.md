# tlakit — a Python and notebook client for the TLA+ toolchain

**Date:** 2026-08-07
**Status:** Approved design
**Target org:** LUC-AI4FM

## Problem

TLA+ has good tools. None of them are reachable from Python, and none of them
compose with a notebook.

TLC, SANY, the TLA+ Debugger, the animation modules, and the MCP server all
assume one of two clients: a human in VSCode, or an LLM agent. A user who wants
to check a spec from Python — to generate it, to sweep constants over it, to put
the counterexample in a DataFrame, to hand a colleague one file that re-runs
end to end — has nothing.

Two consequences:

1. **Teaching.** Setting up TLA+ for a class means Java, an IDE, an extension,
   and a model configuration before a student sees a single state. That is the
   friction Läufer and Thiruvathukal describe in *TLA+ for All* (2025 TLA+
   Community Event).
2. **Research.** A generate → check → inspect → regenerate loop over
   machine-produced specs has no natural artifact. LUC-AI4FM runs exactly this
   loop in `tla-generator`, `TLA-Prove`, and `tla_benchmark`, each with its own
   ad-hoc subprocess glue.

## Prior art, and what tlakit will not rebuild

The survey below is why this design is small. Everything in the left column
already exists and works; tlakit consumes it.

| Capability | Existing tool | tlakit's relationship |
| --- | --- | --- |
| Parsing, level checking | SANY (`tla2sany.SANY`) | invoke |
| Model checking, simulation | TLC (`tlc2.TLC`) | invoke |
| Structured traces | `-dumpTrace json` | consume as-is |
| State graph export | `-dump dot`, `-dump json` | consume as-is |
| TLA+ value ↔ JSON | `Json.tla` (CommunityModules) | consume as-is |
| Animation DSL | `SVG.tla` + `IOUtils.tla`, `AnimView` | consume as-is |
| Interactive stepping | TLA+ Debugger (DAP, state-space exploration since Jan 2026) | speak DAP to it (M4) |
| Browser interpreter, trace sharing | Spectacle (`will62794/spectacle`) | out of scope for v1 |
| Structured tool API | `vscode-tlaplus` MCP server (`src/lm/MCPServer.ts`) | optional backend |
| Interactive lessons | `tlaplus/tla-by-example` (learning.tlapl.us) | not overlapping; complementary |
| Counterexample visualization, state-graph folding, LLM digest | ModelWisdom (FM 2026 tool track, arXiv 2602.12058) | **do not compete**; see below |
| Agentic spec generation and bug reproduction | Specula (`specula-org/Specula`, Apache-2.0, arXiv 2607.25333) | prospective *consumer* of tlakit |
| TLA+ parser and syntax tree in Python | `tla` on PyPI (`johnyf/tla`, Ioannis Filippidis) | complementary; owns the AST niche |

### tlakit is not an umbrella project

The temptation is to position tlakit as a unifying toolkit for TLA+. It should
not be, for two reasons.

First, the umbrella already exists: the TLA+ Foundation under the Linux
Foundation, with AWS, Oracle, and Microsoft as inaugural members, and
`tlaplus/devkit` as the official guidance for building TLA+ tools. A
declaration of umbrella status from outside that structure would be a claim
nobody granted.

Second, every position an umbrella would occupy is already held by a project
with a publication behind it: ModelWisdom for visualization and repair,
Specula for agentic bug finding, Spectacle for interactive exploration,
vscode-tlaplus for the IDE and the MCP surface, learning.tlapl.us for teaching.
Competing with all of them at once is how a project earns no users and several
annoyed maintainers.

The defensible position is narrower and more useful: **tlakit is a substrate,
not an umbrella.** It is the boring shared layer that runs the tools and
returns structured results, so that other projects stop writing their own.
Umbrella status, if it ever arrives, is earned by adoption rather than claimed
in a README.

The evidence that the substrate is wanted is inside Specula, which contains
three separate, unshared TLC integrations:

- `tools/inv_checking_tool/src/tlc_output_reader.py` — 787 lines, including its
  own `-dumpTrace json` unwrapping
- `tools/trace_debugger/src/executor/tlc_process.py` — 159 lines, drives TLC's
  DAP debugger on port 4712
- `tools/spec_analyzer/src/spec_mcp/handlers/vav_handler.py`

One repository, three answers to the same question. That is the gap, and it is
a contribution to Specula rather than a competitor to it.

Two earlier attempts at the notebook problem specifically:

- `kelvich/tlaplus_jupyter` (122★) — a from-scratch Jupyter kernel. Last commit
  September 2022; the open issue "doesn't work with Python 3.12" has been
  unaddressed since January 2024. It died of kernel maintenance, not of anything
  TLA+-related.
- Läufer & Thiruvathukal, *TLA+ for All* (ETAPS 2025) — deliberately no kernel;
  a Colab notebook that shells out to `tla2tools.jar`, with roughly 30 lines of
  Python providing a `%%tla_repl` magic. Their stated future work is state-graph
  visualization, `tlatex` pretty-printing, and Alloy support.

tlakit follows the second approach and takes its lesson: **the kernel is not the
foundation.** It is a thin, optional shim derived from the magics.

## Findings from the M0 spike

Run on 2026-08-07 against `tlaplus/vscode-tlaplus` at master, Java 25,
TLC 2026.03.19, arm64 macOS. Artifacts in the session scratchpad.

1. **The extension's MCP server runs outside VSCode.** A ~180-line `vscode`
   shim (workspace folders, configuration, output channel, `Uri`, diagnostic
   collection) plus a stub for `vscode-languageclient` is sufficient. All nine
   tools respond. `vscode.lm` is used only to advertise the server and is
   feature-guarded.
2. **MCP tool results are unstructured text.** `tlaplus_mcp_tlc_check` returns
   the raw TLC log. Structure must come from TLC's own dump flags.
3. **`-dumpTrace json` passes through `extraOpts`** and yields action names,
   source locations, and typed variable values. No output parsing required.
4. **CommunityModules resolve automatically** from the bundled
   `CommunityModules-deps.jar`. `SVG.tla`, `IOUtils.tla`, and `Json.tla` work
   with no module search path configuration.
5. **The animation path works end to end.** A spec defining `AnimView` and
   serializing via `IOUtils!Serialize` produced four valid standalone SVG
   frames during a single TLC run that also emitted a JSON counterexample.

**Consequence for the design:** the MCP server costs an unofficial shim and
returns text; the CLI costs nothing and returns JSON. The original
MCP-first plan is inverted. tlakit is **CLI-first, MCP optional.**

## Architecture

Four layers. Each is usable without the one above it.

### 1. `tlakit.core` — runners

Pure Python, no Jupyter dependency, fully testable headless.

- `Spec` — a module's source, name, and resolved dependencies.
- `Runner` protocol — `parse()`, `check()`, `simulate()`, `eval()`.
- `CliRunner` (default) — subprocess over `tla2tools.jar`. Owns jar discovery,
  `.cfg` synthesis, `-dumpTrace json` wiring, timeouts, and process teardown.
- `McpRunner` (optional) — HTTP client for the MCP server when one is reachable.
  Gains PlusCal transpilation and the extension's diagnostic post-processing.

Jar resolution order: explicit path → `TLAKIT_TLA2TOOLS` env var → a pinned
release downloaded to a `platformdirs` cache and verified by SHA-256. Never
silently upgraded. `CommunityModules-deps.jar` is resolved the same way, since
`SVG.tla` and `Json.tla` are load-bearing.

### 2. `tlakit.result` — normalized results

A thin dataclass layer, **not** a new interchange format. TLC's
`-dumpTrace json` is the source of truth for traces; this layer names its parts
and adds only what TLC does not provide.

```python
@dataclass
class CheckResult:
    outcome: Outcome              # OK | INVARIANT_VIOLATION | DEADLOCK
                                  # | TEMPORAL_VIOLATION | PARSE_ERROR | TIMEOUT
    diagnostics: list[Diagnostic] # severity, message, module, line, column
    trace: Trace | None           # states + actions from -dumpTrace json
    stats: Stats                  # generated, distinct, depth, duration_ms
    graph: StateGraph | None      # from -dump dot/json, when requested
    raw: RawOutput                # stdout, stderr, argv, exit code
```

`Trace.states` are dicts of TLA+ values as TLC serializes them.
`Trace.actions` carry `name` and `location`. `Trace.delta(i)` returns the
variables that changed at step `i` — the one genuinely new thing this layer
computes.

`raw` is always populated. Any output tlakit cannot interpret stays reachable.

### 3. `tlakit.widgets` — renderers

`anywidget`, so a single ESM + CSS file works in Jupyter, JupyterLab, Colab,
VSCode notebooks, and Marimo with no separate labextension to publish. That
publishing burden is what killed the previous kernel; it is not re-incurred.

Widgets consume `CheckResult` and never call a runner. This is what makes them
testable against fixtures.

1. `Diagnostics` — SANY and TLC errors against the offending source line,
   instead of a Java stack trace.
2. `TraceView` — the counterexample as a table, changed variables highlighted
   per step, action name and source location per transition.
3. `AnimPlayer` — pages through `AnimView` SVG frames with a scrubber, joined
   to the trace's variable table by step number.
4. `StateGraph` — from `-dump dot`. Above a node threshold it degrades to a
   summary plus the DOT file rather than rendering an unreadable hairball.

### 4. `tlakit.magics` — notebook front-end

`%load_ext tlakit` registers:

- `%%tla ModuleName` — write and parse a module; renders `Diagnostics`.
- `%%tlc ModuleName` — cell body is the `.cfg`; runs TLC; renders `TraceView`
  on violation, `Stats` otherwise. Flags: `--timeout`, `--workers`, `--dump-graph`.
- `%tla_eval EXPR` — constant expression evaluation via `tlc2.REPL`.
- `%%tla_anim ModuleName` — run and render `AnimPlayer`.

Modules live in a session working directory on disk, so specs stay real files
that TLC, the debugger, and version control can all see. A notebook is a client,
never the source of truth.

**Python interop** is the layer's reason to exist, not a convenience:

```python
spec = tlakit.load("Raft.tla")
for n in range(3, 6):
    r = spec.check(constants={"Servers": set(range(n))}, timeout=120)
    print(n, r.outcome, r.stats.distinct)

df = r.trace.to_dataframe()          # counterexample as a DataFrame
tlakit.check_source(llm_output)      # generated spec, checked in-loop
```

Nothing in the ecosystem does this, because everything assumes a human in an IDE.

### Kernel (M4, optional)

A subclass of `ipykernel.IPythonKernel` that preloads the extension, sets
`language_info` to TLA+ for syntax highlighting, and treats bare cells as
`%%tla`. Roughly 200 lines, no parser, no wire-protocol implementation. If it
rots, the magics are unaffected.

## Error handling

- Runner failures become `Diagnostic` entries. Exceptions never reach the cell
  for anything that is a spec-level problem.
- Java stack traces and unparsed output go to `result.raw`, surfaced behind a
  disclosure in the widget.
- `--timeout` is first-class. On timeout, return a `CheckResult` with
  `outcome=TIMEOUT` and whatever stats were reached. A partial answer beats a
  hang.
- Missing Java produces one actionable message naming the install step, not a
  `FileNotFoundError`.
- Every subprocess is killed on cell interrupt. A stranded TLC JVM will consume
  the machine.

## Testing

- **Core:** pytest against a spec corpus — Microwave, DieHard, a Raft slice, and
  one deliberately broken spec per error class. Golden `CheckResult` files.
- **Widgets:** playwright DOM snapshots against fixed `CheckResult` fixtures.
  Widgets never touch a runner in tests.
- **Backends:** when an MCP server is reachable, assert `CliRunner` and
  `McpRunner` agree on outcome and trace for the corpus. Skipped otherwise.
- **Notebooks:** `nbmake` executes every example notebook in CI on
  Linux/macOS/Windows × Python 3.11–3.13. The Python 3.12 break that killed
  `tlaplus_jupyter` would have been caught here.

## Milestones

| | Scope | Estimate |
| --- | --- | --- |
| M0 | Feasibility spike | **done 2026-08-07** |
| M1 | `tlakit.core` + `CheckResult` + `%%tla` / `%%tlc` + `Diagnostics` | 1 week |
| M2 | `TraceView` + `AnimPlayer` + example notebooks; PyPI release | 1 week |
| M3 | Constant injection, DataFrame export, generate→check loop wired to `tla-generator` | 1 week |
| M4 | `StateGraph`, `McpRunner`, DAP client, thin kernel | later |

M1 alone is already more than any maintained tool offers from Python.

## Risks

1. **Overlap with Läufer & Thiruvathukal.** They are at LUC, their ETAPS 2025
   talk is the closest prior art, and their stated future work is a subset of
   M2. Mitigation: contact them before M1 lands. Their notebook becomes an
   example in this repo; co-authorship on a TLA+ Community Event submission is
   the natural outcome. Cheapest risk to eliminate, most expensive to ignore.
2. **TLC output format drift.** `-dumpTrace json` is stable but the state-graph
   export is under active redesign (`tlaplus/tlaplus#1073`). Mitigation:
   `graph` is optional and isolated in M4; `raw` is always retained.
3. **Widget maintenance.** Mitigated by anywidget and by widgets depending only
   on `CheckResult`, never on a runner or on Jupyter internals.
4. **Scope creep toward reimplementation.** Every capability in the prior-art
   table is a standing decision not to build. Changing one is a design change,
   not an implementation detail.
5. **Collision with ModelWisdom over visualization.** ModelWisdom (FM 2026 tool
   track) already does colorized violation highlighting, clickable
   transitions into source, state-graph folding, and LLM-based subgraph digest.
   The M2 `StateGraph` widget as originally scoped overlaps it directly.
   Mitigation: keep tlakit's rendering to what a notebook cell needs — the
   trace table and the animation player — and treat large-state-graph
   exploration as ModelWisdom's ground. Revisit only if they publish an API
   worth calling.

## Upstream contributions

1. **Headless entry point for the MCP server.** `MCPServer.ts` transitively
   pulls in the TLAPS language client and React webview code purely because it
   lives inside the extension. The M0 shim is a working proof that decoupling is
   small. File against `tlaplus/vscode-tlaplus` with the spike attached.
2. **Python MCP client.** If the above lands, the client written for `McpRunner`
   is useful to anyone driving the toolchain from Python.
3. **Deduplicate Specula's TLC integrations.** Replace the `-dumpTrace json`
   handling in `inv_checking_tool` with tlakit as a first, narrow pull request.
   It is small, it is obviously an improvement to them rather than a land grab,
   and it is the only credible way to find out whether the substrate framing
   survives contact with a real consumer.

## Out of scope for v1

TLAPS and proof obligations. Alloy. Spectacle embedding. Collaborative editing.
A spec-editing UI. Replacing the VSCode extension, the debugger, or
learning.tlapl.us for the workflows they already serve.

## Success criteria

1. A student runs a TLA+ model check in a notebook with `pip install tlakit` and
   nothing else installed but Java.
2. An engineer sweeps a constant across five values and gets five outcomes in a
   DataFrame without writing subprocess code.
3. `tla-generator` replaces its bespoke TLC glue with `tlakit.check_source()`.
