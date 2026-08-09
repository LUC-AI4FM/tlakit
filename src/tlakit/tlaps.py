"""Prove a module with TLAPS, the TLA+ proof system.

The other half of #68. TLAPS is different in kind from TLC and Apalache: it
*verifies* rather than searches. There is no state space, no counterexample,
and nothing for a `Trace` to hold -- so it gets its own result type rather than
a `CheckResult` with the interesting fields left empty. A proof does not fail
with a trace; it fails with an obligation it could not discharge, at a
location, from a named backend.

That distinction is the whole reason this is a separate module. The workflow
the TLA+ community actually recommends is "model check the small instance,
prove the general case", and a notebook is a genuinely good place to work
through the second half one obligation at a time.

## Reading tlapm

`--toolbox 0 0` makes tlapm emit machine-readable records instead of prose,
which is how the IDE reads it and is far more stable than parsing the human
output:

    @!!BEGIN
    @!!type:obligation
    @!!id:2
    @!!loc:8:3:8:10
    @!!status:failed
    @!!prover:isabelle
    @!!meth:auto; time-limit: 30; time-used: 3.0 (10%)
    @!!reason:false
    @!!obl:
    \\A n \\in Nat : n + 1 = n

    @!!END

One obligation is reported several times as it moves through `to be proved` ->
`being proved` -> `proved`/`failed`, so records are folded by `id` and the last
status for each wins. Reporting every record would triple-count the work and
make a proved obligation look like it also failed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Overrides the search for a `tlapm` binary.
ENV_TLAPM = "TLAKIT_TLAPM"

#: Statuses tlapm reports that mean the obligation is settled and good.
_PROVED = frozenset({"proved", "trivial", "skipped"})
#: ...and settled and bad. Anything else is in-flight and not a verdict.
_FAILED = frozenset({"failed", "interrupted"})

_RECORD = re.compile(r"@!!BEGIN\n(?P<body>.*?)@!!END", re.DOTALL)
_FIELD = re.compile(r"^@!!(?P<key>\w+):(?P<value>.*)$", re.M)


class TlapmNotFound(FileNotFoundError):
    """No `tlapm` binary could be located."""


def find_tlapm(explicit: str | Path | None = None) -> Path:
    """Locate `tlapm`: explicit argument, then `TLAKIT_TLAPM`, then `PATH`.

    Not fetched by `tlakit.install`. The arm64 macOS build is a 1.1 GB
    download that unpacks to about 3 GB, because it bundles Isabelle -- hiding
    that behind an import would be a genuinely unpleasant surprise.
    """
    for candidate in (explicit, os.environ.get(ENV_TLAPM)):
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_file():
                return path
            raise TlapmNotFound(f"{candidate} is not a file")
    found = shutil.which("tlapm")
    if found:
        return Path(found)
    raise TlapmNotFound(
        "tlapm is not on PATH. Download a build from "
        "https://github.com/tlaplus/tlapm/releases (1.6.0-pre is the first "
        "with an arm64-darwin asset; the older 1.5.0 installers are i386 only "
        f"and will not run on Apple Silicon) and set {ENV_TLAPM} to the binary."
    )


@dataclass(frozen=True)
class Obligation:
    """One proof obligation and what became of it."""

    #: tlapm's own identifier, stable within a run.
    id: int
    #: `proved`, `failed`, `missing`, or whatever tlapm last reported.
    status: str
    #: The obligation's text, as tlapm restated it.
    text: str = ""
    #: The backend that settled it -- `isabelle`, `zenon`, `smt`, ...
    prover: str | None = None
    #: The method and its time budget, verbatim.
    method: str | None = None
    #: Why it failed, when tlapm said.
    reason: str | None = None
    #: 1-based start line, matching every editor and TLC.
    line: int | None = None
    #: 1-based start column.
    column: int | None = None
    #: 1-based end line.
    end_line: int | None = None
    #: 1-based end column.
    end_column: int | None = None

    @property
    def proved(self) -> bool:
        return self.status in _PROVED

    @property
    def failed(self) -> bool:
        return self.status in _FAILED

    def __str__(self) -> str:
        where = f"{self.line}:{self.column}" if self.line is not None else "?"
        detail = f" ({self.reason})" if self.reason else ""
        return f"{where}: {self.status}{detail}"


@dataclass(frozen=True)
class ProofResult:
    """What one `tlapm` run produced.

    Deliberately not a `CheckResult`. A proof has no trace and no state count,
    and a type that pretends otherwise invites code that reads `result.trace`
    and finds None for a reason it will guess wrong about.
    """

    #: The module that was proved.
    module: str
    #: Every obligation, in the order tlapm first reported it.
    obligations: list[Obligation] = field(default_factory=list)
    #: The subprocess invocation and its unparsed output.
    raw: Any = None
    #: True when tlapm ran to completion, whatever the obligations said.
    completed: bool = True

    @property
    def ok(self) -> bool:
        """Every obligation discharged, and at least one existed.

        A module with no obligations is not a proved module -- it is one with
        no theorems, and calling that success would let a typo that deletes a
        proof report as a pass.
        """
        return bool(self.obligations) and all(o.proved for o in self.obligations)

    @property
    def failed(self) -> list[Obligation]:
        return [o for o in self.obligations if o.failed]

    @property
    def unproved(self) -> list[Obligation]:
        """Everything not positively proved, failures and stragglers alike."""
        return [o for o in self.obligations if not o.proved]

    def __len__(self) -> int:
        return len(self.obligations)

    def __str__(self) -> str:
        total = len(self.obligations)
        proved = sum(1 for o in self.obligations if o.proved)
        head = f"{self.module}: {proved}/{total} obligations proved"
        if self.ok:
            return head
        return "\n".join([head, *(f"  {o}" for o in self.unproved)])


def parse_obligations(stdout: str) -> list[Obligation]:
    """Fold tlapm's `@!!` records into one `Obligation` per id.

    An obligation is reported repeatedly as it moves through the backends, so
    the last record for an id wins. Order follows first appearance, which is
    source order, rather than the order things happened to finish in.
    """
    by_id: dict[int, dict[str, str]] = {}
    order: list[int] = []

    for record in _RECORD.finditer(stdout):
        fields = {m.group("key"): m.group("value").strip() for m in _FIELD.finditer(record.group("body"))}
        if fields.get("type") != "obligation":
            continue
        try:
            ident = int(fields.get("id", ""))
        except ValueError:
            continue
        # `obl:` is a header whose value is the following lines, not the rest
        # of its own line, so it is taken from the record body directly.
        body = record.group("body")
        if "@!!obl:" in body:
            fields["obl"] = body.split("@!!obl:", 1)[1].strip()
        if ident not in by_id:
            order.append(ident)
            by_id[ident] = {}
        by_id[ident].update(fields)

    obligations: list[Obligation] = []
    for ident in order:
        fields = by_id[ident]
        location = (fields.get("loc") or "").split(":")
        numbers = [int(p) for p in location if p.isdigit()]
        while len(numbers) < 4:
            numbers.append(None)  # type: ignore[arg-type]
        obligations.append(
            Obligation(
                id=ident,
                status=fields.get("status", "unknown"),
                text=fields.get("obl", ""),
                prover=fields.get("prover") or None,
                method=fields.get("meth") or None,
                reason=fields.get("reason") or None,
                line=numbers[0],
                column=numbers[1],
                end_line=numbers[2],
                end_column=numbers[3],
            )
        )
    return obligations


class TlapsRunner:
    """Run TLAPS over a module and report its obligations.

    `prove` is the only operation: TLAPS neither model-checks nor evaluates, so
    there is nothing else to expose and a `check`-shaped method would only
    invite the wrong expectation.
    """

    def __init__(self, tlapm: str | Path | None = None):
        self.tlapm = find_tlapm(tlapm)

    def prove(
        self,
        source: str,
        module: str,
        *,
        timeout: float | None = None,
        method: str | None = None,
        extra_opts: list[str] | None = None,
    ) -> ProofResult:
        """Prove `source`'s theorems, returning one `Obligation` each.

        A failing proof is a normal result, not an exception -- the same
        contract `check` has. `timeout` bounds the whole tlapm run, not one
        obligation; per-obligation budgets are tlapm's own and come back in
        `Obligation.method`.
        """
        from .result import RawOutput

        with tempfile.TemporaryDirectory(prefix="tlakit-tlaps-") as tmp:
            work = Path(tmp)
            (work / f"{module}.tla").write_text(source, encoding="utf-8")
            argv = [
                str(self.tlapm),
                "--toolbox",
                "0",
                "0",
                *(["--method", method] if method else []),
                *(extra_opts or []),
                f"{module}.tla",
            ]
            try:
                completed = subprocess.run(
                    argv, cwd=work, capture_output=True, text=True, timeout=timeout
                )
                stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
                finished = True
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                code, finished = None, False

            # tlapm writes its `@!!` records to *stderr*, not stdout (verified
            # 2026-08-08, tlapm 1.6.0-pre): stdout carries only the banner and
            # cache notes. Both are scanned so a future version moving them
            # does not silently yield zero obligations -- which is exactly what
            # "parse stdout" produced, and it reads as "the module has no
            # theorems" rather than as a parsing failure.
            return ProofResult(
                module=module,
                obligations=parse_obligations(stderr + "\n" + stdout),
                raw=RawOutput(argv=argv, exit_code=code, stdout=stdout, stderr=stderr),
                completed=finished,
            )
