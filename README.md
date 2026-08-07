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
    print(result.outcome)                 # Outcome.INVARIANT_VIOLATION
    print(result.trace.delta(3))          # frozenset({'radiation'})
    df = result.trace.to_dataframe()      # counterexample as a DataFrame
```

In a notebook:

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

Point tlakit at the TLA+ tools with `TLAKIT_TLA2TOOLS=/path/to/tla2tools.jar`.
`TLAKIT_COMMUNITY_MODULES` optionally locates `CommunityModules-deps.jar`, which
`SVG.tla` and `Json.tla` need.

## License

MIT
