# Security

## What this project runs

tlakit executes TLA⁺ specifications with TLC. `tlakit.serve` accepts
specifications over HTTP, which means it runs code a stranger wrote.

The property that makes that safe is **jar isolation**: with `tla2tools.jar`
alone in its directory, TLA⁺ has no I/O primitives. The standard modules are
pure and `TLC!PrintT` writes only to stdout.

CommunityModules breaks that property, because `IOUtils!IOExec` runs arbitrary
shell commands from inside a specification. Leaving it off the classpath is
**not** sufficient — TLC also loads jars sitting *beside* `tla2tools.jar`. See
`jar.assert_isolated()` and `tests/test_isolation.py`, which asserts both that
isolation blocks the attack and that the attack still works without it.

**If you deploy `tlakit.serve`, do not put CommunityModules anywhere near the
jar it uses.** `startup_checks()` refuses to serve otherwise.

## Reporting a vulnerability

Open a [private security advisory](../../security/advisories/new). Please do not
open a public issue for anything exploitable.

Useful things to include: the specification or request that triggers it, what
you expected, and what happened.

## Scope

In scope: sandbox escape from a submitted specification, reading or writing
files outside the per-check working directory, resource exhaustion beyond the
documented limits, and anything that discloses host details.

Out of scope: TLC finding a genuine bug in your own specification, and the
absence of authentication on a deployment configured without a key.
