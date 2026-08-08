"""The public surface: Spec, load, check_source."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cli import CliRunner
from .result import CheckResult
from .source import declared_variables, defines_animview

_MODULE_HEADER = re.compile(r"^-{4,}\s*MODULE\s+(\w+)\s*-{4,}", re.M)

#: Names used by the generated animation companion module.
ANIM_ALIAS = "TlakitAnimAlias"
FRAME_PREFIX = "tlakit_anim_"


def animation_module(module: str, variables: list[str]) -> str:
    """A companion module that writes one SVG per state.

    Generated rather than required of the user: the alias has to name every
    variable, EXTEND SVG and IOUtils, and call Serialize with the right
    options. That is boilerplate nobody should retype per spec, and getting it
    subtly wrong yields an `Unknown operator` error that says nothing useful.
    """
    fields = "".join(f"    {name} |-> {name},\n" for name in variables)
    return f"""---- MODULE {module}_anim ----
EXTENDS {module}, TLC, IOUtils

{ANIM_ALIAS} ==
  [
{fields}    _tlakit_frame |-> Serialize(
        SVGElemToString(AnimView),
        "{FRAME_PREFIX}" \\o ToString(TLCGet("level")) \\o ".svg",
        [format |-> "TXT", charset |-> "UTF-8",
         openOptions |-> <<"WRITE", "CREATE", "TRUNCATE_EXISTING">>]).exitValue
  ]
====
"""

#: Cached runners, keyed on the jars they actually resolved to. A single global
#: would let the first caller in a process fix the toolchain for everyone else,
#: which matters now that other projects embed tlakit alongside their own code.
_runners: dict[tuple[Path, Path | None], CliRunner] = {}

#: Set by `use_remote`. A process-wide override is the right scope here: it
#: exists for environments with no Java at all, where every runner has to be
#: remote, and it keeps `%%tlc` and `Spec.check()` working untouched.
_override: Any = None


def use_remote(endpoint: str | None = None, **kwargs: Any) -> Any:
    """Send every check to a remote service instead of a local JVM.

    For Pyodide and other places with no Java. Returns the runner so a caller
    can inspect `.health()`.
    """
    from .remote import DEFAULT_ENDPOINT, RemoteRunner

    global _override
    _override = RemoteRunner(endpoint=endpoint or DEFAULT_ENDPOINT, **kwargs)
    return _override


def use_local() -> None:
    """Undo `use_remote`."""
    global _override
    _override = None


def default_runner(
    tools_jar: Path | None = None, community_jar: Path | None = None
) -> CliRunner:
    """A runner for this configuration, built lazily.

    Importing tlakit needs no Java; only calling this does. Resolution runs on
    every call, so changing TLAKIT_TLA2TOOLS takes effect immediately instead
    of being masked by a cached runner.
    """
    if _override is not None:
        return _override
    from .jar import find_community_jar, find_tools_jar

    key = (find_tools_jar(tools_jar), find_community_jar(community_jar))
    if key not in _runners:
        _runners[key] = CliRunner(*key)
    return _runners[key]


def module_name_of(source: str) -> str:
    """Read the module name out of a `---- MODULE Name ----` header."""
    match = _MODULE_HEADER.search(source)
    if match is None:
        raise ValueError(
            "Could not find a `---- MODULE Name ----` header in the source. "
            "Pass module= explicitly."
        )
    return match.group(1)


def tla_value(value: Any) -> str:
    """Render a Python value as TLA+ syntax."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (set, frozenset)):
        return "{" + ", ".join(tla_value(v) for v in value) + "}"
    if isinstance(value, (list, tuple)):
        return "<<" + ", ".join(tla_value(v) for v in value) + ">>"
    if isinstance(value, dict):
        body = ", ".join(f"{k} |-> {tla_value(v)}" for k, v in value.items())
        return f"[{body}]"
    raise TypeError(
        f"Cannot render {type(value).__name__} as a TLA+ value. Supported: "
        "bool, int, str, set, frozenset, list, tuple, dict."
    )


def build_config(
    spec: str | None = None,
    init: str | None = None,
    next_: str | None = None,
    constants: dict[str, Any] | None = None,
    invariants: list[str] | None = None,
    properties: list[str] | None = None,
) -> str:
    """Assemble a TLC `.cfg` file."""
    lines: list[str] = []
    if constants:
        lines.append("CONSTANT")
        lines += [
            f"    {name} = {tla_value(value)}" for name, value in constants.items()
        ]
    if spec:
        lines.append(f"SPECIFICATION {spec}")
    else:
        if init:
            lines.append(f"INIT {init}")
        if next_:
            lines.append(f"NEXT {next_}")
    lines += [f"INVARIANT {name}" for name in (invariants or [])]
    lines += [f"PROPERTY {name}" for name in (properties or [])]
    return "\n".join(lines) + "\n"


@dataclass
class Spec:
    """A TLA+ module held in memory."""

    source: str
    name: str
    path: Path | None = None
    runner: CliRunner | None = None

    def _runner(self) -> CliRunner:
        return self.runner or default_runner()

    def parse(self) -> CheckResult:
        """Syntax- and level-check with SANY."""
        return self._runner().parse(self.source, self.name)

    def check(
        self,
        config: str | None = None,
        *,
        spec: str = "Spec",
        init: str | None = None,
        next_: str | None = None,
        constants: dict[str, Any] | None = None,
        invariants: list[str] | None = None,
        properties: list[str] | None = None,
        timeout: float | None = None,
        coverage: bool = False,
        animate: bool = False,
        graph: bool = False,
        max_graph_nodes: int | None = None,
        heap: str | None = None,
        extra_opts: list[str] | None = None,
    ) -> CheckResult:
        """Model-check with TLC.

        Pass `config` for raw `.cfg` text, or any of `spec`/`init`/`next_`/
        `constants`/`invariants`/`properties` to have one built.
        """
        if config is None:
            config = build_config(
                spec=None if (init or next_) else spec,
                init=init,
                next_=next_,
                constants=constants,
                invariants=invariants,
                properties=properties,
            )
        options = list(extra_opts or [])
        if coverage and "-coverage" not in options:
            # TLC gathers no coverage unless asked, and it is not free.
            options += ["-coverage", "1"]

        if not animate:
            return self._runner().check(
                self.source,
                self.name,
                config,
                timeout=timeout,
                extra_opts=options,
                heap=heap,
                graph=graph,
                max_graph_nodes=max_graph_nodes,
            )

        if not defines_animview(self.source):
            raise ValueError(
                f"{self.name} does not define AnimView, so there is nothing to "
                "animate. Define `AnimView == Svg(<<...>>, [...])` using the "
                "SVG.tla operators from CommunityModules."
            )
        companion = f"{self.name}_anim"
        return self._runner().check(
            animation_module(self.name, declared_variables(self.source)),
            companion,
            config + f"ALIAS {ANIM_ALIAS}\n",
            timeout=timeout,
            extra_opts=options,
            extra_modules={self.name: self.source},
            collect=f"{FRAME_PREFIX}*.svg",
            declared=declared_variables(self.source),
            heap=heap,
        )

    def sweep(
        self,
        grid: dict[str, Any],
        *,
        workers: int = 1,
        **check_kwargs: Any,
    ):
        """Check this spec at every point in a grid of constants.

            sweep = spec.sweep({"Servers": [3, 4, 5]}, invariants=["Inv"])
            sweep.to_dataframe()
            sweep.first_failure()

        `workers` runs points concurrently. It defaults to 1 because each point
        is a separate JVM: five at once will ask for more memory than most
        machines have. Pass `heap="2G"` alongside when raising it.
        """
        from .sweep import run_sweep

        return run_sweep(self.check, grid, workers=workers, **check_kwargs)


def load(path: str | Path, runner: CliRunner | None = None) -> Spec:
    """Read a `.tla` file from disk."""
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    return Spec(
        source=source, name=module_name_of(source), path=path, runner=runner
    )


def check_source(source: str, module: str | None = None, **kwargs: Any) -> CheckResult:
    """Check a spec that exists only as a string."""
    spec = Spec(source=source, name=module or module_name_of(source))
    return spec.check(**kwargs)

    def sweep(
        self,
        grid: dict[str, Any],
        *,
        workers: int = 1,
        **check_kwargs: Any,
    ):
        """Check this spec at every point in a grid of constants.

        ```python
        sweep = spec.sweep({"Servers": [3, 4, 5]}, invariants=["Inv"])
        sweep.to_dataframe()
        sweep.first_failure()
        ```

        `workers` runs points concurrently. It defaults to 1 because each point
        is a separate JVM: five at once will ask for more memory than most
        machines have. Pass `heap="2G"` alongside when raising it.
        """
        from .sweep import run_sweep

        return run_sweep(self.check, grid, workers=workers, **check_kwargs)
