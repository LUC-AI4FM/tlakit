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

## Status

M1 is complete: `CliRunner`, normalized results, `%%tla` / `%%tlc` magics,
and static HTML rendering of counterexamples and diagnostics. See
`docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for the
implementation plan.

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

## License

MIT
