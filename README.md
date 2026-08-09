# tlakit

[![CI](https://github.com/LUC-AI4FM/tlakit/actions/workflows/ci.yml/badge.svg)](https://github.com/LUC-AI4FM/tlakit/actions/workflows/ci.yml)

A Python and notebook client for the TLA+ toolchain.

TLA+ has good tools — TLC, SANY, the TLA+ Debugger, the animation modules. None
of them are reachable from Python, and none of them compose with a notebook.
tlakit is the missing client. It does not reimplement any of them.

```python
import tlakit

spec = tlakit.load("Microwave.tla")
result = spec.check(invariants=["Safety"])

if not result.ok:
    print(result.outcome)                       # Outcome.INVARIANT_VIOLATION
    print(result.trace.delta(3))                # frozenset({'radiation'})
    result.trace.to_dataframe(flatten=True)     # nested records as columns
```

Sweep a constant and get the smallest configuration that breaks:

```python
sweep = spec.sweep({"Servers": [3, 4, 5]}, invariants=["Inv"],
                   workers=3, heap="2G")
sweep.first_failure().constants     # {'Servers': 4}
sweep.to_dataframe()                # one row per configuration
```

## As a Jupyter kernel

```bash
pip install "tlakit[kernel]"
python -m tlakit.kernel.install
```

Then pick **TLA⁺ (tlakit)** from Jupyter's kernel list and write TLA+ directly —
no magics, no Python wrapper:

```tla
---- MODULE Microwave ----
EXTENDS Naturals
VARIABLES door, radiation
...
====
```

```
SPECIFICATION Spec
INVARIANT Safety
```

Python still works in the same notebook, which is the point of building on
IPython rather than replacing it:

```python
result.trace.to_dataframe()
```

## Or as magics in an ordinary Python kernel

```
%load_ext tlakit
```

```
%%tla Microwave
---- MODULE Microwave ----
...
====
```

```
%%tlc Microwave
SPECIFICATION Spec
INVARIANT Safety
```

## Or from a shell, with no Python at all

```bash
tlakit check Counter.tla                      # uses Counter.cfg if it is there
tlakit check Counter.tla --invariant Inv      # or build the config from flags
tlakit check Counter.tla --no-deadlock-check  # a spec that is meant to finish
tlakit parse Counter.tla                      # SANY only, no search
```

Exit codes make it composable, and they distinguish two things a shell
otherwise cannot:

| Code | Meaning |
| --- | --- |
| `0` | the spec checked out |
| `1` | the run succeeded and found something wrong with the spec |
| `2` | the run did not happen — bad flags, missing file, no JVM |

So `tlakit check Spec.tla && deploy` does not deploy on a violated invariant,
and a CI job can still tell a real failure from a typo in a path.

`--no-deadlock-check` is worth knowing about early: a specification meant to
*finish* has no successor state at the end, and TLC reports that as
`DEADLOCK` — correctly, since it cannot know termination was intended.

## Or from curl directly

If you want to check a specification from a machine where `tlakit` (or Python)
is not installed, you can upload the files directly to the public web runner
using `multipart/form-data`:

```bash
curl -s -F spec=@Counter.tla -F cfg=@Counter.cfg https://tla-runner.ericspencer.us/check
```

However, if you have `tlakit` installed, you do not need `curl` at all: the
`tlakit check` command handles this natively (see above).

## Related work

tlakit is the next evolution of Läufer and Thiruvathukal's *TLA+ for All: Model
Checking in a Python Notebook* (TLA+ Community Event, 2025), which established
that a Python notebook driving `tla2tools.jar` is a good way to teach and use
TLA+. That result is the starting point here, not a competitor.

What tlakit adds is a difference in kind rather than in polish: **TLA+ is the
cell language.** `tlakit.kernel` is a Jupyter kernel, so a notebook is a TLA+
artifact rather than a Python file holding TLA+ strings — TLA+ `language_info`,
module cells that need no magic, and completion and hover answered *in TLA+*,
from operators defined in the session and from the `.tla` files inside
`tla2tools.jar` itself. A Python kernel cannot answer those: asked what `Su`
completes to, the only honest answer it has is a Python one.

The kernel is deliberately thin. All the behaviour lives in `tlakit.api` and
`tlakit.magics`; the kernel subclasses `IPythonKernel` and adds routing,
completion, and inspection on top. The previous from-scratch TLA+ kernel,
[kelvich/tlaplus_jupyter](https://github.com/kelvich/tlaplus_jupyter), died of
kernel and packaging maintenance rather than of anything TLA+-related — so the
one thing this kernel must not do is own the protocol. If it rots, the magics
keep working.

## Status

0.1.1 is the first release. Everything shown above is implemented: the Python
API and `sweep`, the `tlakit` command, the `%%tla` / `%%tlc` magics, the Jupyter
kernel, and HTML rendering of counterexamples and diagnostics.

Every push to `main` also publishes a dev build to
[TestPyPI](https://test.pypi.org/project/tlakit/), so a fix is installable
before it is released:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ --pre tlakit
```

## Requirements

- Python 3.10+
- Java
- TLA+ tools **v1.8.0 or newer** — tlakit runs TLC with `-dumpTrace json`;
  v1.7.4 (TLC 2.19) does not have that option

Fetch the pinned, checksummed tools:

```bash
python -m tlakit.install
```

Or point tlakit at jars you already have with
`TLAKIT_TLA2TOOLS=/path/to/tla2tools.jar`.
`TLAKIT_COMMUNITY_MODULES` optionally locates `CommunityModules-deps.jar`, which
`SVG.tla` and `Json.tla` need.

## Documentation

[`docs/reference.md`](docs/reference.md) is the reference: every environment
variable, every magic and its arguments, and a map of the public names.
[`docs/api.md`](docs/api.md) generates the API from the docstrings.

Build the site locally with:

```bash
pip install -e ".[docs]" && mkdocs serve
```

## License

MIT
