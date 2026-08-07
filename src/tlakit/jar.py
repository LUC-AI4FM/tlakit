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


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("tlakit"))


def _candidates(explicit: Path | None, env_var: str, filename: str) -> list[Path]:
    found: list[Path] = []
    if explicit is not None:
        found.append(Path(explicit))
    from_env = os.environ.get(env_var)
    if from_env:
        found.append(Path(from_env))
    found.append(cache_dir() / filename)
    return found


def find_tools_jar(explicit: Path | None = None) -> Path:
    tried = _candidates(explicit, ENV_TOOLS, TOOLS_JAR)
    for path in tried:
        if path.is_file():
            return path
    raise JarNotFound(
        f"Could not find {TOOLS_JAR}. Looked in: "
        + ", ".join(str(p) for p in tried)
        + f". Set the {ENV_TOOLS} environment variable to its location, or pass "
        "an explicit path."
    )


def find_community_jar(explicit: Path | None = None) -> Path | None:
    """CommunityModules is optional; SVG.tla and Json.tla need it."""
    for path in _candidates(explicit, ENV_COMMUNITY, COMMUNITY_JAR):
        if path.is_file():
            return path
    return None
