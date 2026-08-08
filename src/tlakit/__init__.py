"""tlakit — a Python and notebook client for the TLA+ toolchain."""
from __future__ import annotations

from . import api, install, jar, sweep
from .api import (
    Spec,
    build_config,
    check_source,
    default_runner,
    load,
    use_local,
    use_remote,
)
from .cli import CliRunner, JavaNotFound, java_executable
from .jar import JarNotFound
from .result import (
    flatten_state,
    Action,
    CheckResult,
    Diagnostic,
    Outcome,
    RawOutput,
    Severity,
    Stats,
    Trace,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "Action",
    "CheckResult",
    "CliRunner",
    "Diagnostic",
    "JarNotFound",
    "JavaNotFound",
    "Outcome",
    "RawOutput",
    "Severity",
    "Spec",
    "Stats",
    "Trace",
    "api",
    "build_config",
    "check_source",
    "default_runner",
    "flatten_state",
    "install",
    "jar",
    "java_executable",
    "load",
    "remote",
    "sweep",
    "use_local",
    "use_remote",
]


def load_ipython_extension(ipython) -> None:
    """Support `%load_ext tlakit`.

    In a browser this also switches to the remote runner and turns on TLA+ cell
    routing, because there is no other way to work there: Pyodide has no JVM and
    no `subprocess`. Locally it registers the magics only -- redirecting a local
    user's checks to someone else's server would be surprising, and their own
    TLC is faster and unmetered.
    """
    from .magics import TlaMagics

    ipython.register_magics(TlaMagics)

    from .notebook import in_browser, setup

    if in_browser():
        setup(shell=ipython)
