"""Locate the TLA+ tool jars.

Resolution order: explicit path -> environment variable -> platformdirs cache.
Downloading a pinned release is deliberately left to M2; until then a missing
jar is a clear, actionable error rather than a silent fetch.
"""
from __future__ import annotations

import os
from pathlib import Path

import platformdirs

TOOLS_JAR = "tla2tools.jar"
COMMUNITY_JAR = "CommunityModules-deps.jar"
ENV_TOOLS = "TLAKIT_TLA2TOOLS"
ENV_COMMUNITY = "TLAKIT_COMMUNITY_MODULES"


class JarNotFound(FileNotFoundError):
    """Raised when tla2tools.jar cannot be located."""


class NotIsolated(RuntimeError):
    """Raised when tla2tools.jar shares a directory with other jars."""


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("tlakit"))


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
        found.extend(sorted(root.glob(f"*/{filename}"), reverse=True))
    found.append(root / filename)
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

