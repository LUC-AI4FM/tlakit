"""Locate the TLA+ tool jars.

Resolution order: explicit path -> environment variable -> platformdirs cache.

Within the cache, the newest version directory wins. That is a comparison of
version *numbers*, not of strings: sorting the names lexically puts `v1.9.0`
above `v1.10.0`, which would pin a user to an older toolchain every time the
minor version reached double digits (#91).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

TOOLS_JAR = "tla2tools.jar"
COMMUNITY_JAR = "CommunityModules-deps.jar"
ENV_TOOLS = "TLAKIT_TLA2TOOLS"
ENV_COMMUNITY = "TLAKIT_COMMUNITY_MODULES"


class JarNotFound(FileNotFoundError):
    """Raised when tla2tools.jar cannot be located."""


class NotIsolated(RuntimeError):
    """Raised when tla2tools.jar shares a directory with other jars."""


def cache_dir() -> Path:
    """Where `python -m tlakit.install` puts a downloaded jar.

    platformdirs is imported here rather than at module scope so that importing
    tlakit needs no third-party package at all. That matters in a browser: a
    Pyodide kernel checks specs over HTTP and never resolves a jar, so making it
    fetch a wheel just to reach an unused code path would be waste.
    """
    import platformdirs

    return Path(platformdirs.user_cache_dir("tlakit"))


def _version_key(path: Path) -> tuple[int, ...]:
    """Parse a version directory tag into a tuple of ints for version-based sorting.

    Handles 'v1.10.0', 'v1.9.0', '202607311834', etc.
    Unparseable tags return (-1,) so they sort last.
    """
    tag = path.parent.name
    parts = re.findall(r"\d+", tag)
    if not parts:
        return (-1,)
    # No try/except around int(): `\d+` only ever yields digits, and Python
    # integers do not overflow, so there is nothing here that can raise.
    return tuple(int(p) for p in parts)


def _candidates(explicit: Path | None, env_var: str, filename: str) -> list[Path]:
    found: list[Path] = []
    if explicit is not None:
        found.append(Path(explicit))
    from_env = os.environ.get(env_var)
    if from_env:
        found.append(Path(from_env))
    # Cached jars live under a version directory so a new pin never overwrites
    # an older one; newest tag wins when several are present.
    root = cache_dir()
    if root.is_dir():
        globbed = sorted(root.glob(f"*/{filename}"), key=_version_key, reverse=True)
        for p in globbed:
            if p not in found:
                found.append(p)
    legacy = root / filename
    if legacy not in found:
        found.append(legacy)
    return found


def find_tools_jar(explicit: Path | None = None) -> Path:
    tried = _candidates(explicit, ENV_TOOLS, TOOLS_JAR)
    for path in tried:
        if path.is_file():
            return path
    raise JarNotFound(
        f"Could not find {TOOLS_JAR}. Looked in: "
        + ", ".join(str(p) for p in tried)
        + ". Run `python -m tlakit.install` to fetch the pinned release, set "
        f"the {ENV_TOOLS} environment variable to an existing jar, or pass an "
        "explicit path. Note that tlakit needs TLA+ tools v1.8.0 or newer: "
        "v1.7.4 has no -dumpTrace option."
    )


def find_community_jar(explicit: Path | None | bool = None) -> Path | None:
    """CommunityModules is optional; SVG.tla and Json.tla need it.

    Pass `False` to refuse it outright. That matters for untrusted specs:
    CommunityModules ships `IOUtils!IOExec`, which runs arbitrary shell
    commands from inside a specification.
    """
    if explicit is False:
        return None
    for path in _candidates(explicit or None, ENV_COMMUNITY, COMMUNITY_JAR):
        if path.is_file():
            return path
    return None


def assert_isolated(tools_jar: Path) -> None:
    """Fail if any other jar sits beside `tools_jar`.

    TLC adds jars in tla2tools.jar's own directory to its module search path,
    so a CommunityModules jar that is merely *adjacent* is loadable even when
    it is not on the classpath -- verified 2026-08-07. Keeping the jar alone in
    its directory is the only reliable way to deny a spec those operators.
    """
    directory = Path(tools_jar).parent
    neighbours = sorted(
        p.name for p in directory.glob("*.jar") if p.name != Path(tools_jar).name
    )
    if neighbours:
        raise NotIsolated(
            f"{directory} also contains {', '.join(neighbours)}. TLC loads jars "
            "adjacent to tla2tools.jar, so a specification could reach "
            "IOUtils!IOExec and run shell commands. Put tla2tools.jar in a "
            "directory of its own before serving untrusted specs."
        )

