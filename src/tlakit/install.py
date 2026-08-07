"""Fetch the pinned TLA+ tool jars.

Downloading is explicit — `python -m tlakit.install`, never a surprise network
call inside `check()`. Versions are pinned and checksummed: a jar is code, and
tlakit hands it to `java` with the user's privileges.

The pin also enforces a real floor. TLA+ tools v1.7.4 ships TLC 2.19, which has
no `-dumpTrace` option, and tlakit passes that on every run.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .jar import COMMUNITY_JAR, TOOLS_JAR, cache_dir

#: First TLA+ tools release with `-dumpTrace`.
TOOLS_TAG = "v1.8.0"
COMMUNITY_TAG = "202607311834"

_TOOLS_URL = (
    f"https://github.com/tlaplus/tlaplus/releases/download/{TOOLS_TAG}/{TOOLS_JAR}"
)
_COMMUNITY_URL = (
    "https://github.com/tlaplus/CommunityModules/releases/download/"
    f"{COMMUNITY_TAG}/{COMMUNITY_JAR}"
)


@dataclass(frozen=True)
class PinnedJar:
    filename: str
    tag: str
    url: str
    sha256: str

    @property
    def path(self) -> Path:
        # Version in the path, so a new pin is a new file. Nothing is ever
        # overwritten, and an older pin stays reproducible.
        return cache_dir() / self.tag / self.filename


TOOLS = PinnedJar(
    filename=TOOLS_JAR,
    tag=TOOLS_TAG,
    url=_TOOLS_URL,
    sha256="e22f8ffb4bacdea0a871f444dd94fe5fb0d8013b3388ae39e82e26f852c735d5",
)
COMMUNITY = PinnedJar(
    filename=COMMUNITY_JAR,
    tag=COMMUNITY_TAG,
    url=_COMMUNITY_URL,
    sha256="6703730d475c60741624e4dbcfaa9456477a53c970c7c100b531682d0c1a0f8f",
)


class ChecksumMismatch(RuntimeError):
    """The downloaded file is not what the pin says it should be."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(jar: PinnedJar, force: bool = False) -> Path:
    """Download `jar` into the cache if it is not already there.

    Returns the cached path. Verifies the checksum before the file is moved
    into place, so a failed or tampered download never becomes the cached jar.
    """
    target = jar.path
    if target.is_file() and not force:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, suffix=".partial", delete=False
    ) as tmp:
        staging = Path(tmp.name)
    try:
        with urllib.request.urlopen(jar.url) as response, open(staging, "wb") as out:
            shutil.copyfileobj(response, out)
        actual = sha256_of(staging)
        if actual != jar.sha256:
            raise ChecksumMismatch(
                f"{jar.filename} from {jar.url} has sha256 {actual}, expected "
                f"{jar.sha256}. Refusing to cache it."
            )
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return target


def install(force: bool = False) -> dict[str, Path]:
    """Fetch both jars. Returns the resolved paths by filename."""
    return {
        TOOLS.filename: fetch(TOOLS, force=force),
        COMMUNITY.filename: fetch(COMMUNITY, force=force),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m tlakit.install",
        description="Download the pinned TLA+ tool jars into tlakit's cache.",
    )
    parser.add_argument(
        "--force", action="store_true", help="re-download even if already cached"
    )
    args = parser.parse_args(argv)

    try:
        paths = install(force=args.force)
    except ChecksumMismatch as exc:
        print(f"error: {exc}")
        return 1
    except OSError as exc:
        print(f"error: could not download the TLA+ tools: {exc}")
        return 1

    for name, path in paths.items():
        print(f"{name}  ->  {path}")
    print("\nReady. tlakit will find these automatically.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
