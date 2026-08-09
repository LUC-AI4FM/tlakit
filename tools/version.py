"""The one place that knows how a tlakit version is decided.

The version is stated twice -- `pyproject.toml` drives the wheel, `__version__`
is what a user reads at runtime -- and `tests/test_version.py` exists because
those two drifted the first time the version was bumped. So anything that
changes a version has to change both, which is why this is a script and not
four lines of `sed` in a workflow.

Two modes, matching the two ways a build starts:

    --check 0.1.0    a tag build: both files must already say 0.1.0
    --write-dev      a push to main: compute the dev version, write it to both
                     files, print it on stdout

The dev version is `<last tag with the patch bumped>.dev<commits on HEAD>` --
`0.1.1.dev47` after `v0.1.0`. It is a valid PEP 440 developmental release, so
pip ignores it unless asked with `--pre`, and the commit count only ever goes
up, so a later push always sorts above an earlier one. Nothing is committed
back to the branch: this rewrites the checkout the runner is about to build
from, and that checkout is thrown away.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "tlakit" / "__init__.py"

# Anchored to the start of a line so a version in a dependency pin cannot match.
PYPROJECT_RE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)
INIT_RE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)

RELEASE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _read(path: pathlib.Path, pattern: re.Pattern[str]) -> str:
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        sys.exit(f"::error::no version line in {path.relative_to(ROOT)}")
    return match.group(1)


def _write(path: pathlib.Path, pattern: re.Pattern[str], version: str) -> None:
    text = path.read_text(encoding="utf-8")
    # `count=1`: the first match is the declaration; anything later would be a
    # coincidence and rewriting it would be a bug.
    new, n = pattern.subn(
        lambda m: m.group(0).replace(m.group(1), version), text, count=1
    )
    if n != 1:
        sys.exit(f"::error::no version line in {path.relative_to(ROOT)}")
    path.write_text(new, encoding="utf-8")


def _git(*args: str) -> str | None:
    """None rather than an exception: every caller has a sane fallback, and a
    shallow clone failing here should not fail the build."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip() or None


def dev_version() -> str:
    declared = _read(PYPROJECT, PYPROJECT_RE)

    # `--match v[0-9]*` skips any non-release tag, and `--abbrev=0` gives the
    # tag alone. Only tags reachable from HEAD count, which is what makes this
    # monotonic on main.
    tag = _git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    match = RELEASE_TAG_RE.match(tag) if tag else None
    if match:
        major, minor, patch = (int(g) for g in match.groups())
        base = f"{major}.{minor}.{patch + 1}"
    else:
        # No release tag yet, or one shaped like `v1.0.0rc1`. Building toward
        # whatever pyproject says is the honest guess, and it still sorts
        # below that release because of the `.dev`.
        print(
            f"::notice::no release tag to bump from; basing the dev version on {declared}",
            file=sys.stderr,
        )
        base = declared

    count = _git("rev-list", "--count", "HEAD") or "0"
    return f"{base}.dev{count}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="VERSION", help="both files must say this")
    group.add_argument(
        "--write-dev", action="store_true", help="write a dev version to both files"
    )
    args = parser.parse_args()

    if args.check:
        declared = _read(PYPROJECT, PYPROJECT_RE)
        runtime = _read(INIT, INIT_RE)
        ok = True
        if declared != args.check:
            print(
                f"::error::tag v{args.check} does not match pyproject version {declared}",
                file=sys.stderr,
            )
            ok = False
        if runtime != args.check:
            print(
                f"::error::tag v{args.check} does not match __version__ {runtime}",
                file=sys.stderr,
            )
            ok = False
        if not ok:
            return 1
        print(args.check)
        return 0

    version = dev_version()
    _write(PYPROJECT, PYPROJECT_RE, version)
    _write(INIT, INIT_RE, version)
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
