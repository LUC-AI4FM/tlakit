"""Keep `lite/wheels/` and `piplite_urls` agreeing with the packaged version.

The JupyterLite site serves tlakit from a wheel committed under `lite/wheels/`,
and `lite/jupyter_lite_config.json` names that wheel *by filename*. So the
version lives in a third place, and unlike the other two nothing was checking
it. Leave a stale wheel there and the build keeps shipping it -- the site
serves an old tlakit to every visitor and says nothing.

That is not hypothetical. Cutting 0.1.0 rebased onto a main that had gained two
features, and the wheel committed minutes earlier silently lost both.

    --check   exactly one tlakit wheel, its version matches pyproject, and
              piplite_urls points at it. This is what CI runs.
    --sync    build the wheel, drop any stale ones, rewrite piplite_urls.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
WHEELS = ROOT / "lite" / "wheels"
CONFIG = ROOT / "lite" / "jupyter_lite_config.json"

PYPROJECT_RE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)

# PEP 427: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl.
# Only the first two fields are needed, and the version is whatever sits
# between the first and second hyphen.
WHEEL_RE = re.compile(r"^tlakit-(?P<version>[^-]+)-.+\.whl$")


def packaged_version() -> str:
    match = PYPROJECT_RE.search(PYPROJECT.read_text(encoding="utf-8"))
    if match is None:
        sys.exit("::error::no version line in pyproject.toml")
    return match.group(1)


def wheels() -> list[pathlib.Path]:
    if not WHEELS.is_dir():
        return []
    return sorted(p for p in WHEELS.iterdir() if WHEEL_RE.match(p.name))


def configured_urls() -> list[str]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return config.get("PipliteAddon", {}).get("piplite_urls", [])


def _rel(path: pathlib.Path) -> str:
    """Repo-relative when it can be, absolute when it cannot. `relative_to`
    raises on anything outside ROOT, and a path in an error message is never
    worth raising over."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def check() -> list[str]:
    """Every problem at once. Reporting only the first would mean three runs to
    learn what one already knows."""
    problems: list[str] = []
    version = packaged_version()
    found = wheels()

    if not found:
        problems.append(f"no tlakit wheel in {_rel(WHEELS)}")
    elif len(found) > 1:
        names = ", ".join(p.name for p in found)
        problems.append(
            f"{len(found)} tlakit wheels in {_rel(WHEELS)} ({names}); "
            "the build would pick by filename, not by date"
        )
    else:
        wheel = found[0]
        got = WHEEL_RE.match(wheel.name)["version"]  # type: ignore[index]
        if got != version:
            problems.append(
                f"{wheel.name} is version {got}, but pyproject.toml says {version}"
            )
        expected = [f"./wheels/{wheel.name}"]
        urls = configured_urls()
        if urls != expected:
            problems.append(
                f"piplite_urls is {urls}, expected {expected}"
            )
    return problems


def build_wheel(into: pathlib.Path) -> pathlib.Path:
    """uv first because that is what lite/README.md tells a contributor to use,
    and it is a great deal faster; `build` is the fallback so this works in a
    plain venv and on a CI runner that has no uv."""
    commands = [
        ["uv", "build", "--wheel", "-o", str(into)],
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(into)],
    ]
    last: Exception | str | None = None
    for command in commands:
        try:
            done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        except FileNotFoundError as exc:
            last = exc
            continue
        if done.returncode == 0:
            break
        last = done.stderr.strip() or done.stdout.strip()
    else:
        sys.exit(f"::error::could not build a wheel: {last}")

    built = [p for p in into.iterdir() if WHEEL_RE.match(p.name)]
    if len(built) != 1:
        sys.exit(f"::error::expected one wheel from the build, got {len(built)}")
    return built[0]


def sync() -> int:
    version = packaged_version()
    WHEELS.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        built = build_wheel(pathlib.Path(tmp))
        got = WHEEL_RE.match(built.name)["version"]  # type: ignore[index]
        if got != version:
            # Only reachable if the build backend stops reading pyproject, but
            # copying a wheel whose name disagrees with the source is the exact
            # failure this script exists to prevent.
            sys.exit(f"::error::built {built.name}, but pyproject says {version}")

        for stale in wheels():
            if stale.name != built.name:
                print(f"removing stale {stale.name}")
            stale.unlink()
        shutil.copy2(built, WHEELS / built.name)
        print(f"wheels/{built.name}")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config.setdefault("PipliteAddon", {})["piplite_urls"] = [f"./wheels/{built.name}"]
    # Trailing newline and two-space indent to match what is committed, so a
    # sync that changes nothing produces no diff.
    CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    problems = check()
    if problems:  # pragma: no cover - would mean sync itself is broken
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        return 1
    print("lite/ is in sync")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="verify, change nothing")
    group.add_argument("--sync", action="store_true", help="build and rewrite")
    args = parser.parse_args()

    if args.sync:
        return sync()

    problems = check()
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    if problems:
        print("fix with: python tools/lite_wheel.py --sync", file=sys.stderr)
        return 1
    print("lite/ is in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
