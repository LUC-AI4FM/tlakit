"""The `tlakit` console script: check a .tla file without writing Python.

`tlakit.cli` is the `CliRunner` -- the thing that shells out to java -- so the
command line lives here under a different name.

This is a wrapper and nothing more. Every flag maps to an argument `api.Spec`
already takes, and the config is assembled by `api.build_config` rather than by
pasting `.cfg` text together here; a second place that knows how to write a
config is a second place for it to go wrong.

## Exit codes

The one decision this module actually makes, because a Makefile or a CI job
depends on it and it cannot be changed later without breaking them in silence:

    0  the spec checked out
    1  the run succeeded and found something wrong with the spec
    2  the run did not happen

1 and 2 are separated for the reason `grep` separates them. A violated
invariant is a *successful* run -- TLC did its job -- but `tlakit check
Spec.tla && deploy` must not deploy, and the shell has only one word for the
difference. Meanwhile a typo in a filename is not a fact about anyone's spec,
and a CI job that cannot tell the two apart will report the wrong failure.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import __version__
from .api import Raw, Spec, load
from .jar import find_tools_jar
from .result import Outcome

#: See the module docstring; these are a public contract.
OK = 0
FOUND_A_PROBLEM = 1
CANNOT_RUN = 2


def _constant(text: str) -> tuple[str, Any]:
    """Parse `NAME=VALUE` into something `build_config` can render.

    Integers, TRUE/FALSE and quoted strings become Python values. Anything else
    is passed through as literal TLA+ -- `Procs={a, b}` is a set of model
    values, which is how CONSTANTS are ordinarily written and is not any
    serialisation format we could parse instead.
    """
    name, separator, value = text.partition("=")
    if not separator or not name.strip():
        raise ValueError(f"expected NAME=VALUE, got {text!r}")
    name, value = name.strip(), value.strip()
    if value.lstrip("-").isdigit():
        return name, int(value)
    if value in ("TRUE", "FALSE"):
        return name, value == "TRUE"
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return name, value[1:-1]
    # Anything else is TLA+ already: a model value, a set of them, a record.
    return name, Raw(value)


def _build_parser() -> argparse.ArgumentParser:
    try:
        tools_jar = find_tools_jar()
        version_str = f"tlakit {__version__} (using {tools_jar})"
    except Exception:
        version_str = f"tlakit {__version__}"

    parser = argparse.ArgumentParser(
        prog="tlakit",
        description="Check TLA+ specifications from the command line.",
        epilog=(
            "Exit codes: 0 the spec checked out, 1 it did not, "
            "2 the check could not run."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=version_str,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="model-check a module with TLC",
        description=(
            "Model-check a module. With no config flags, a .cfg beside the "
            "spec is used if there is one."
        ),
    )
    check.add_argument("file", help="path to a .tla file")
    check.add_argument("--config", help="path to a .cfg file")
    check.add_argument(
        "--invariant", action="append", default=[], metavar="NAME",
        help="check an invariant; repeatable",
    )
    check.add_argument(
        "--property", action="append", default=[], metavar="NAME",
        dest="property_", help="check a temporal property; repeatable",
    )
    check.add_argument(
        "--constant", action="append", default=[], metavar="NAME=VALUE",
        help="assign a CONSTANT; repeatable",
    )
    check.add_argument(
        "--specification", default="Spec", metavar="NAME",
        help="the SPECIFICATION operator (default: Spec)",
    )
    check.add_argument("--init", metavar="NAME", help="use INIT/NEXT instead")
    check.add_argument("--next", dest="next_", metavar="NAME")
    check.add_argument(
        "--no-deadlock-check", action="store_true",
        help=(
            "turn off TLC's deadlock check. A specification meant to finish "
            "has no successor at the end, which TLC reports as a deadlock."
        ),
    )
    check.add_argument("--timeout", type=float, metavar="SECONDS")
    check.add_argument("--coverage", action="store_true")
    check.add_argument("--graph", action="store_true")

    parse = sub.add_parser(
        "parse",
        help="syntax- and level-check a module with SANY",
        description="Parse a module. No search runs, so there is no config.",
    )
    parse.add_argument("file", help="path to a .tla file")
    return parser


def _exit_code(outcome: Outcome) -> int:
    return OK if outcome is Outcome.OK else FOUND_A_PROBLEM


def _resolve_config(args: argparse.Namespace, spec_path: Path) -> str | None:
    """Which config to use, or None to have one built from the flags.

    Precedence is explicit over implicit: `--config` wins, then a `.cfg` next
    to the spec, then the flags. The one case refused outright is flags *and* a
    neighbouring `.cfg`, because either answer makes the other look ignored.
    """
    built_from_flags = bool(
        args.invariant or args.property_ or args.constant or args.init or args.next_
    )
    if args.config:
        return Path(args.config).read_text(encoding="utf-8")
    beside = spec_path.with_suffix(".cfg")
    if beside.exists():
        if built_from_flags:
            raise ValueError(
                f"{beside.name} sits beside {spec_path.name}, but config flags "
                "were given too. Pass --config to choose a file explicitly, or "
                "drop the flags to use the one already there."
            )
        return beside.read_text(encoding="utf-8")
    return None


def main(argv: list[str] | None = None, runner: Any = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 for a usage error already, which happens to be this
        # module's "could not run" -- but say so rather than relying on it.
        raise SystemExit(CANNOT_RUN if exc.code else exc.code) from None

    path = Path(args.file)
    try:
        spec: Spec = load(path, runner=runner)
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}")
        return CANNOT_RUN
    except ValueError as exc:
        # No `---- MODULE Name ----` header, so there is nothing to check.
        print(f"error: {path}: {exc}")
        return CANNOT_RUN

    try:
        if args.command == "parse":
            result = spec.parse()
        else:
            config = _resolve_config(args, path)
            if config is not None and args.no_deadlock_check:
                raise ValueError(
                    "--no-deadlock-check cannot apply to a raw config; a .cfg "
                    "states its own CHECK_DEADLOCK. Add `CHECK_DEADLOCK FALSE` "
                    "to the file, or drop --config and build one from flags."
                )
            constants = dict(_constant(text) for text in args.constant)
            result = spec.check(
                config=config,
                spec=args.specification,
                init=args.init,
                next_=args.next_,
                constants=constants or None,
                invariants=args.invariant or None,
                properties=args.property_ or None,
                check_deadlock=False if args.no_deadlock_check else None,
                timeout=args.timeout,
                coverage=args.coverage,
                graph=args.graph,
            )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}")
        return CANNOT_RUN
    except Exception as exc:  # a missing JVM, an unreachable service, a bad jar
        print(f"error: {exc}")
        return CANNOT_RUN

    print(result)
    return _exit_code(result.outcome)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
