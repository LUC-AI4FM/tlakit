"""tlakit — a Python and notebook client for the TLA+ toolchain."""
from __future__ import annotations

from . import api, install, jar, sweep
from .api import Spec, build_config, check_source, default_runner, load
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
    "sweep",
]


def load_ipython_extension(ipython) -> None:
    """Support `%load_ext tlakit`."""
    from .magics import TlaMagics

    ipython.register_magics(TlaMagics)
