"""Compile the Java class TLC streams its state graph through.

`TlakitStateWriter.java` is an `IStateWriter` plus a `main` that sets it on a
`tlc2.TLC` instance -- a plugin on a public seam, not a fork. It has to be
compiled against the very tla2tools.jar the run will use, which is why the
source ships in the wheel and is compiled here, on demand, rather than built
ahead of time: the jar tlakit resolves can be the pinned download, an
environment variable, or an explicit path, and only one of those is known at
build time.

The result is cached under platformdirs, keyed on the source and the jar it was
compiled against, so the cost is paid once per (tlakit, jar) pair and not once
per check.

Compiling needs a JDK. `java` alone is enough to *run* TLC, so a machine with
only a JRE is a real case, and it is not an error: `cli` falls back to
`-dump dot` there and produces the same graph, just not while TLC is running.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .jar import cache_dir

#: The compiled class, in the default package.
MAIN_CLASS = "TlakitStateWriter"
#: The system property the writer reads its output path from. Kept in step with
#: the constant of the same name in the Java source.
OUT_PROPERTY = "tlakit.graph.out"
#: The Java source, shipped in the wheel beside this module.
SOURCE = Path(__file__).parent / "java" / f"{MAIN_CLASS}.java"

#: tla2tools.jar is compiled for Java 11, so nothing newer can be required of
#: the JVM that runs the class either. Pinning the release also means a class
#: compiled by a newer JDK still runs under an older `java` -- which happens,
#: because TLAKIT_JAVA and PATH need not agree.
RELEASE = "11"

#: Everything compiled so far, and every reason a compile failed, keyed on the
#: cache key and the compiler. A machine without a JDK must not shell out to
#: javac once per check just to be told so again -- but pointing TLAKIT_JAVAC at
#: one afterwards has to be enough to change the answer, so the compiler is part
#: of the key rather than only the artifact it would produce.
_RESOLVED: dict[tuple[str, str | None], Path | str] = {}


class StateWriterUnavailable(RuntimeError):
    """The writer could not be compiled, so the graph must come from DOT."""


def javac_executable() -> str | None:
    """Path to `javac`, or None when there is no JDK to compile with.

    Honours TLAKIT_JAVAC, then a JDK sitting beside TLAKIT_JAVA -- a pinned
    `java` and the `javac` on PATH can be different installations, and the
    class must be compiled by the JDK whose JVM will run it -- then PATH.
    """
    explicit = os.environ.get("TLAKIT_JAVAC")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    java = os.environ.get("TLAKIT_JAVA")
    if java:
        sibling = Path(java).with_name("javac" + Path(java).suffix)
        if sibling.is_file():
            return str(sibling)
    return shutil.which("javac")


def _cache_key(tools_jar: Path) -> str:
    """Identify a (source, jar) pair.

    The jar's size and mtime stand in for its contents: hashing 15 MB on the
    way into every check would cost more than the compile it is meant to skip,
    and a jar that changed in place without changing either is a jar someone
    edited by hand.
    """
    digest = hashlib.sha256()
    digest.update(SOURCE.read_bytes())
    digest.update(str(Path(tools_jar).resolve()).encode("utf-8"))
    stat = Path(tools_jar).stat()
    digest.update(f"{stat.st_size}:{int(stat.st_mtime)}:{RELEASE}".encode("utf-8"))
    return digest.hexdigest()[:16]


def class_directory(tools_jar: Path) -> Path:
    """Where the class compiled against `tools_jar` lives, compiling if needed.

    Raises `StateWriterUnavailable` -- never `subprocess` errors -- so a caller
    has exactly one thing to catch when deciding whether to fall back.
    """
    try:
        key = _cache_key(tools_jar)
    except OSError as exc:
        # The jar was there when it was resolved and is not now, or cannot be
        # read. Every other failure here answers "use the dump instead", and so
        # does this one -- the caller has one exception to catch, not two.
        raise StateWriterUnavailable(f"Cannot read {tools_jar}: {exc}") from exc
    javac = javac_executable()
    memo = (key, javac)
    cached = _RESOLVED.get(memo)
    if isinstance(cached, Path):
        return cached
    if isinstance(cached, str):
        raise StateWriterUnavailable(cached)

    try:
        resolved = _compile(tools_jar, key, javac)
    except StateWriterUnavailable as exc:
        _RESOLVED[memo] = str(exc)
        raise
    _RESOLVED[memo] = resolved
    return resolved


def _compile(tools_jar: Path, key: str, javac: str | None) -> Path:
    target = cache_dir() / "statewriter" / key
    compiled = target / f"{MAIN_CLASS}.class"
    if compiled.is_file():
        return target

    if not SOURCE.is_file():
        raise StateWriterUnavailable(
            f"{SOURCE} is missing, so the state writer cannot be compiled."
        )
    if javac is None:
        raise StateWriterUnavailable(
            "No `javac` executable found, so TLC's state graph cannot be "
            "streamed. A JRE runs TLC but cannot compile the writer: install a "
            "JDK (for example `brew install temurin`) or set TLAKIT_JAVAC to "
            "one. The graph is still produced, from `-dump dot` after the run."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    # Compile into a staging directory and move it into place, so two processes
    # compiling at once cannot leave a half-written class behind for a third.
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=".build-"))
    try:
        argv = [
            javac,
            "--release",
            RELEASE,
            "-cp",
            str(tools_jar),
            "-d",
            str(staging),
            str(SOURCE),
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except OSError as exc:
            raise StateWriterUnavailable(f"Could not run {javac}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise StateWriterUnavailable(
                f"{javac} did not finish within 120s."
            ) from exc
        if proc.returncode != 0 or not (staging / f"{MAIN_CLASS}.class").is_file():
            detail = (proc.stderr or proc.stdout or "").strip()
            raise StateWriterUnavailable(
                f"Compiling {SOURCE.name} against {tools_jar} failed"
                + (f": {detail}" if detail else ".")
            )
        try:
            staging.replace(target)
        except OSError:
            # Another process got there first. Its class was compiled from the
            # same source against the same jar, so it is this one.
            if not compiled.is_file():
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target
