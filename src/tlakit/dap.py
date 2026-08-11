"""Drive the TLA+ Debugger from Python over the Debug Adapter Protocol.

Issue #24. TLC has had an interactive debugger since 2021 and interactive
state-space exploration since January 2026, and its only client is an IDE --
because it speaks DAP, and DAP clients are debuggers. Speaking DAP from Python
is how a notebook gets stepping without reimplementing any of it.

Nothing here evaluates TLA+. TLC does the exploring; this reads what it says.
Two pieces of that reading are borrowed rather than rewritten: the debugger
reports a state as a TLA+ record (`[x |-> 0, y |-> {1, 2}]`), which
`tlakit.trace.parse_tla_value` already parses, and it names a frame exactly the
way `-dumpTrace json` names an action, which `tlakit.trace` already turns into
an `Action`. A stepped trace and a dumped one therefore come out as the same
`Trace` object, and everything built on `Trace` -- `delta`, `to_dataframe`,
`TraceView` -- works on a live session with no special case.

## How stepping actually works

Not with `next`. TLC suspends before the first ASSUME, and `next` walks the
*evaluation* of an expression, not the state space; run past the initial
predicate and TLC simply carries on to completion. What advances one state at a
time is a breakpoint on the next-state relation: each `continue` runs until
TLC begins evaluating the next transition, and the debugger's `Trace` scope
then holds the behaviour so far.

So `DebugSession` finds the next-state operator (from the config's `NEXT`, or
from the `[][Next]_vars` inside the operator its `SPECIFICATION` names) and
sets breakpoints on it. That is why a session needs the module source and not
just a running TLC.

One breakpoint is not enough, though, and this is the part that is easy to get
wrong. **The debugger breaks where an action's body is evaluated, not where it
is named.** Measured against TLC 2026.07.31:

    Next == x' = (x + 1) % 4            breakpoint on Next   -> fires
    Next == Open \\/ Close \\/ Start       breakpoint on Next   -> never fires
                                        on Open/Close/Start -> fires

A spec written the second way -- which is the usual way -- would step zero
times and look like an empty state space. So the breakpoint set is the
next-state relation *plus every operator it transitively references*, and the
leaves are where it actually stops.

That also means a stop is one *evaluation*, not one new state: TLC evaluates
every disjunct at every state, so a four-state Microwave stops eight times.
`step()` reports each stop; `walk()` collapses them to distinct behaviours.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .result import Action, Trace
from .trace import UNKNOWN_ACTION, TlaValueError, parse_tla_value

#: Long enough for a JVM to start and load the spec on a cold cache.
DEFAULT_STARTUP_TIMEOUT = 60.0

#: A single request should be answered promptly; only `continue` waits on real
#: work, and that waits on an event rather than a response.
DEFAULT_REQUEST_TIMEOUT = 30.0


class DebuggerError(RuntimeError):
    """The debugger could not be started, or refused a request."""


class DebuggerTimeout(DebuggerError):
    """The debugger did not answer in time."""


# --- protocol ---------------------------------------------------------------


class DapClient:
    """A minimal DAP client: framed JSON over a socket, requests and events.

    Deliberately not a general DAP implementation. It sends requests, matches
    responses by `request_seq`, and queues events -- which is all a stepper
    needs, and all that can be tested against the one adapter that matters
    here.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 4712, timeout: float = 30.0):
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b""
        self._seq = 0
        self._events: list[dict[str, Any]] = []
        self._responses: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_forever, daemon=True)
        self._reader.start()

    # -- transport

    def _read_forever(self) -> None:
        while not self._closed.is_set():
            try:
                chunk = self._socket.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            self._buffer += chunk
            self._drain()

    def _drain(self) -> None:
        """Split the byte stream into messages.

        DAP frames each message with a `Content-Length` header, so a read can
        land mid-message or hold several at once. Both are normal.
        """
        while True:
            if b"\r\n\r\n" not in self._buffer:
                return
            header, rest = self._buffer.split(b"\r\n\r\n", 1)
            length = None
            for line in header.decode("utf-8", "replace").splitlines():
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            if length is None:
                # Unparseable header: drop it rather than spin forever on it.
                self._buffer = rest
                continue
            if len(rest) < length:
                return
            message = json.loads(rest[:length].decode("utf-8"))
            self._buffer = rest[length:]
            with self._lock:
                if message.get("type") == "response":
                    self._responses[message["request_seq"]] = message
                else:
                    self._events.append(message)

    # -- requests and events

    def send(self, command: str, **arguments: Any) -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
        message: dict[str, Any] = {"seq": seq, "type": "request", "command": command}
        if arguments:
            message["arguments"] = arguments
        payload = json.dumps(message).encode("utf-8")
        self._socket.sendall(b"Content-Length: %d\r\n\r\n" % len(payload) + payload)
        return seq

    def request(
        self, command: str, timeout: float = DEFAULT_REQUEST_TIMEOUT, **arguments: Any
    ) -> dict[str, Any]:
        """Send a request and return its response body.

        Raises on an unsuccessful response rather than returning an empty body,
        because every caller here would otherwise have to check.
        """
        seq = self.send(command, **arguments)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                response = self._responses.pop(seq, None)
            if response is not None:
                if not response.get("success", False):
                    raise DebuggerError(
                        f"{command} failed: {response.get('message') or 'no reason given'}"
                    )
                return response.get("body") or {}
            time.sleep(0.01)
        raise DebuggerTimeout(f"no response to {command!r} within {timeout}s")

    def wait_for_event(self, name: str, timeout: float = 30.0) -> dict[str, Any] | None:
        """Pull the first queued event called `name`, or None on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for index, event in enumerate(self._events):
                    if event.get("event") == name:
                        return self._events.pop(index)
            time.sleep(0.01)
        return None

    def close(self) -> None:
        self._closed.set()
        try:
            self._socket.close()
        except OSError:
            pass


# --- locating the next-state relation ---------------------------------------

_NEXT_DIRECTIVE = re.compile(r"^\s*NEXT\s+(?P<name>[A-Za-z_]\w*)", re.M)
_SPEC_DIRECTIVE = re.compile(r"^\s*SPECIFICATION\s+(?P<name>[A-Za-z_]\w*)", re.M)
#: `[][Next]_vars` inside a temporal formula.
_BOXED_ACTION = re.compile(r"\[\]\s*\[\s*(?P<name>[A-Za-z_]\w*)\s*\]_")


def next_relation(source: str, config: str) -> str | None:
    """The name of the spec's next-state relation, or None.

    `NEXT X` names it outright. A `SPECIFICATION S` does not, so `S`'s own
    definition is read for the `[][Next]_vars` inside it -- which is where a
    spec written in the usual `Init /\\ [][Next]_vars` style keeps it.
    """
    direct = _NEXT_DIRECTIVE.search(config)
    if direct is not None:
        return direct.group("name")

    named = _SPEC_DIRECTIVE.search(config)
    if named is None:
        return None

    from .symbols import definitions

    by_name = {symbol.name: symbol for symbol in definitions(source)}
    spec = by_name.get(named.group("name"))
    if spec is None or spec.line is None:
        return None

    # Read from the definition's own line to the start of the next one, so a
    # `[][...]_` belonging to some later operator is not picked up instead.
    lines = source.splitlines()
    following = [
        s.line for s in by_name.values() if s.line is not None and s.line > spec.line
    ]
    end = min(following) - 1 if following else len(lines)
    boxed = _BOXED_ACTION.search("\n".join(lines[spec.line - 1 : end]))
    return boxed.group("name") if boxed else None


def relation_lines(source: str, config: str) -> list[int]:
    """Lines to break on so every transition evaluation stops.

    The next-state relation plus every operator it transitively references,
    because the debugger breaks on the body that is evaluated rather than on
    the name that refers to it. Referencing is decided by identifier match
    against the module's own definitions, which is enough here: a name that is
    not defined in this module has no line in this module to break on.
    """
    from .symbols import definitions

    root = next_relation(source, config)
    if root is None:
        return []

    # Operators and functions only. A VARIABLES line is a declaration, never
    # evaluated, so a breakpoint on it can only ever be noise -- and the
    # identifier scan below matches variable names constantly.
    by_name = {
        symbol.name: symbol
        for symbol in definitions(source)
        if symbol.kind in ("operator", "function")
    }
    if root not in by_name:
        return []

    lines = source.splitlines()
    ordered = sorted(s.line for s in by_name.values() if s.line is not None)

    def body_of(name: str) -> str:
        symbol = by_name.get(name)
        if symbol is None or symbol.line is None:
            return ""
        after = [line for line in ordered if line > symbol.line]
        end = (after[0] - 1) if after else len(lines)
        return "\n".join(lines[symbol.line - 1 : end])

    found: set[str] = set()
    pending = [root]
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        for identifier in re.findall(r"[A-Za-z_]\w*", body_of(name)):
            if identifier in by_name and identifier not in found:
                pending.append(identifier)

    return sorted(
        {by_name[name].line for name in found if by_name[name].line is not None}
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- a session --------------------------------------------------------------


@dataclass
class Step:
    """One stop: the behaviour TLC has built so far, at that moment."""

    #: States in order, oldest first, as plain dicts.
    states: list[dict[str, Any]] = field(default_factory=list)
    #: The action producing each state after the first.
    actions: list[Action] = field(default_factory=list)

    def as_trace(self, declared: list[str] | None = None) -> Trace:
        """The same `Trace` a completed run would have produced."""
        return Trace(
            states=self.states,
            actions=self.actions,
            declared=list(declared or []),
        )


#: `1: <Init line 4, col 9 to line 4, col 13 of module Spike>` -- the debugger's
#: name for one entry in the Trace scope. The leading number is its position.
_TRACE_ENTRY = re.compile(r"^(?P<index>\d+):\s*<(?P<header>.*)>$")


def _action_of(header: str) -> Action:
    from .trace import _action_from_header

    if header in ("Initial predicate", "???"):
        return Action(name=UNKNOWN_ACTION)
    return _action_from_header(header)


class DebugSession:
    """A running TLC under the debugger, stepped one transition at a time.

    Use it as a context manager; leaving the block terminates TLC. A session
    holds a JVM open, so leaking one leaks a JVM.

        with DebugSession(source, "Spike", "SPECIFICATION Spec\\n") as session:
            while (step := session.step()) is not None:
                print(step.states[-1])
    """

    def __init__(
        self,
        source: str,
        module: str,
        config: str,
        *,
        runner: Any = None,
        breakpoint_lines: list[int] | None = None,
        port: int | None = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ):
        from .api import default_runner
        from .cli import _java

        self._runner = runner or default_runner()
        self._source = source
        self._module = module
        self._config = config
        self._work = Path(
            __import__("tempfile").mkdtemp(prefix="tlakit-dap-")
        )
        (self._work / f"{module}.tla").write_text(source, encoding="utf-8")
        (self._work / f"{module}.cfg").write_text(config, encoding="utf-8")

        self._lines = breakpoint_lines or self._resolve_breakpoint_lines()
        self._port = port or _free_port()
        self._client: DapClient | None = None
        self._terminated = False
        self._exhausted = False
        self._thread_id = 0

        self._process = subprocess.Popen(
            [
                _java(),
                "-cp",
                self._runner._classpath(),
                "tlc2.TLC",
                "-debugger",
                f"port={self._port}",
                "-config",
                f"{module}.cfg",
                f"{module}.tla",
            ],
            cwd=self._work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._connect(startup_timeout)

    # -- setup

    def _resolve_breakpoint_lines(self) -> list[int]:
        name = next_relation(self._source, self._config)
        if name is None:
            raise DebuggerError(
                "Could not find the next-state relation. The config names "
                "neither `NEXT` nor a `SPECIFICATION` whose definition "
                "contains `[][Next]_vars`. Pass breakpoint_lines= explicitly."
            )
        lines = relation_lines(self._source, self._config)
        if not lines:
            raise DebuggerError(
                f"The config's next-state relation {name!r} is not defined in "
                f"module {self._module}. Pass breakpoint_lines= explicitly."
            )
        return lines

    def _connect(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last: OSError | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise DebuggerError(
                    "TLC exited before the debugger accepted a connection:\n"
                    + (self._process.stdout.read() if self._process.stdout else "")
                )
            try:
                self._client = DapClient(port=self._port)
                break
            except OSError as exc:  # not listening yet
                last = exc
                time.sleep(0.2)
        if self._client is None:
            self.close()
            raise DebuggerTimeout(f"debugger did not open port {self._port}: {last}")

        self._client.request(
            "initialize",
            adapterID="tlakit",
            linesStartAt1=True,
            columnsStartAt1=True,
        )
        self._client.request(
            "setBreakpoints",
            source={
                "path": str(self._work / f"{self._module}.tla"),
                "name": f"{self._module}.tla",
            },
            breakpoints=[{"line": line} for line in self._lines],
        )
        self._client.request("launch")
        # TLC suspends before the first ASSUME. That stop is not a state, so
        # clear it before the caller's first `step()`.
        #
        # `wait_for_event` returns None rather than raising, and dropping that
        # None is worse than useless: the stop stays queued, so the caller's
        # first `step()` consumes it as though `continue` had produced it and
        # reports success while TLC has not moved. Everything after reads one
        # stop ahead of the debugger, and the first visible symptom is an
        # evaluation in a frame from before the spec's variables exist -- an
        # "undefined identifier" a long way from its cause. Fail here instead.
        stop_event = self._client.wait_for_event("stopped", timeout=timeout)
        if stop_event is None:
            self.close()
            raise DebuggerTimeout(
                f"the debugger did not suspend before the first ASSUME within "
                f"{timeout}s, so nothing after this could be read in step with "
                f"TLC. Pass a larger startup_timeout= if the machine is slow."
            )
        self._note_stop(stop_event)

    # -- stepping

    @property
    def lines(self) -> list[int]:
        """The lines breakpoints were set on."""
        return list(self._lines)

    @property
    def exhausted(self) -> bool:
        """True once TLC has finished exploring and will not stop again."""
        return self._exhausted

    @property
    def running(self) -> bool:
        """Whether the TLC process is still alive."""
        return self._process.poll() is None

    def _require_stopped(self) -> bool:
        """Whether it is safe to issue a request that needs a stopped thread.

        Once TLC finishes, it answers nothing -- so a `stackTrace` after the
        state space is exhausted blocks until the request timeout rather than
        failing. Callers check here first and get None instead of a 30-second
        stall.
        """
        return self._client is not None and not self._exhausted and self.running

    def _note_stop(self, event: dict[str, Any]) -> None:
        """Remember which thread a stop was announced for.

        Every later `continue`, `stepBack` and `stackTrace` has to name a
        thread, and the id is TLC's to choose -- it is not always 1, and the
        hardcoded 0 this replaces made the Windows runner read a stop that
        carried no states at all, a `list index out of range` several frames
        from its cause.

        `threadId` is *optional* on a DAP `stopped` event, so a missing one
        must not be read as thread 0: that would put the original bug back the
        moment TLC announces a stop without naming a thread. A stop that names
        no thread says nothing about the thread, so keep the last id that did.
        """
        thread_id = event.get("body", {}).get("threadId")
        if thread_id is not None:
            self._thread_id = thread_id

    def _await_stop(self, timeout: float) -> bool:
        """Wait for the next stop, giving up as soon as TLC is done.

        Waiting only on the `stopped` event means the *normal* end of a walk
        -- the state space running out -- costs a full timeout every time, and
        is indistinguishable from a hang. TLC exits when it finishes, so the
        wait races the event against the process, and exhaustion is detected
        the moment it happens rather than 30 seconds later.
        """
        assert self._client is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self._client.wait_for_event("stopped", timeout=0.05)
            if event is not None:
                self._note_stop(event)
                return True
            if self._process.poll() is not None:
                # Drain a stop that was genuinely delivered just before the
                # exit -- dropping it loses a real state. If TLC is already too
                # far gone to answer for it, `_request` fails fast and `step`
                # treats that as exhaustion.
                event = self._client.wait_for_event("stopped", timeout=0.1)
                if event is not None:
                    self._note_stop(event)
                    return True
                return False
        return False

    def _request(self, command: str, timeout: float = 15.0, **arguments: Any) -> dict[str, Any]:
        """A request that gives up as soon as TLC dies.

        `DapClient.request` can only wait for its timeout; it has no view of
        the process. TLC can exit between announcing a stop and answering for
        it, and then a `stackTrace` waits the full 30 seconds and fails -- which
        is what broke this on CI while passing locally, because it is a race
        and the timing differs per machine.
        """
        assert self._client is not None
        seq = self._client.send(command, **arguments)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._client._lock:
                response = self._client._responses.pop(seq, None)
            if response is not None:
                if not response.get("success", False):
                    raise DebuggerError(
                        f"{command} failed: {response.get('message') or 'no reason given'}"
                    )
                return response.get("body") or {}
            if self._process.poll() is not None:
                raise DebuggerTimeout(f"TLC exited while awaiting {command!r}")
            time.sleep(0.01)
        raise DebuggerTimeout(f"no response to {command!r} within {timeout}s")

    def step(self, timeout: float = 30.0) -> Step | None:
        """Advance to the next transition and read the behaviour so far.

        Returns None when TLC has finished exploring -- the state space is
        exhausted, not an error.
        """
        if not self._require_stopped():
            return None
        assert self._client is not None
        try:
            self._request("continue", threadId=self._thread_id)
            if not self._await_stop(timeout):
                self._exhausted = True
                return None
            return self._read_trace()
        except DebuggerTimeout:
            # Anywhere along here TLC may simply have finished: while answering
            # `continue`, between announcing a stop and answering for it, or
            # mid-read. All of them are the state space running out, which is a
            # normal end rather than something to propagate.
            self._exhausted = True
            return None

    def step_back(self, timeout: float = 30.0) -> Step | None:
        """Ask the debugger to step backwards.

        TLC advertises `supportsStepBack`, so this is its own feature and not
        a replay: the caller gets whatever state the debugger returns to.
        """
        if not self._require_stopped():
            return None
        assert self._client is not None
        try:
            self._request("stepBack", threadId=self._thread_id)
            if not self._await_stop(timeout):
                return None
            return self._read_trace()
        except DebuggerTimeout:
            self._exhausted = True
            return None

    def evaluate(self, expression: str, timeout: float = 30.0) -> str | None:
        """Evaluate an expression in the stopped frame, as a watch would."""
        if not self._require_stopped():
            return None
        assert self._client is not None
        frame = self._top_frame()
        if frame is None:
            return None
        body = self._request(
            "evaluate",
            timeout=timeout,
            expression=expression,
            frameId=frame["id"],
            context="watch",
        )
        return body.get("result")

    # -- reading the stop

    def _top_frame(self) -> dict[str, Any] | None:
        assert self._client is not None
        frames = self._request("stackTrace", threadId=self._thread_id).get("stackFrames", [])
        return frames[0] if frames else None

    def _scope_variables(self, name: str) -> list[dict[str, Any]]:
        assert self._client is not None
        frame = self._top_frame()
        if frame is None:
            return []
        for scope in self._request("scopes", frameId=frame["id"]).get("scopes", []):
            if scope.get("name") == name:
                return self._request(
                    "variables", variablesReference=scope["variablesReference"]
                ).get("variables", [])
        return []

    def _read_trace(self) -> Step:
        """Turn the debugger's `Trace` scope into states and actions.

        The scope lists entries newest-first and numbers them from 1, so they
        are sorted by that number rather than trusted in arrival order.
        """
        entries: list[tuple[int, str, str]] = []
        for variable in self._scope_variables("Trace"):
            match = _TRACE_ENTRY.match(variable.get("name", "").strip())
            if match is None:
                continue
            entries.append(
                (int(match.group("index")), match.group("header"), variable.get("value", ""))
            )
        entries.sort()

        states: list[dict[str, Any]] = []
        actions: list[Action] = []
        for position, (_, header, value) in enumerate(entries):
            try:
                parsed = parse_tla_value(value)
            except TlaValueError:
                # A value shape the reader does not know yet. Keep the text
                # rather than dropping the whole state.
                parsed = {"<unparsed>": value}
            states.append(parsed if isinstance(parsed, dict) else {"value": parsed})
            if position:
                actions.append(_action_of(header))
        return Step(states=states, actions=actions)

    # -- teardown

    def close(self) -> None:
        """Terminate TLC and release the port and working directory."""
        if self._terminated:
            return
        self._terminated = True
        if self._client is not None:
            try:
                self._client.send("terminate")  # best effort; TLC may be gone
            except OSError:
                pass
            self._client.close()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._process.stdout is not None:
            self._process.stdout.close()
        __import__("shutil").rmtree(self._work, ignore_errors=True)

    def __enter__(self) -> "DebugSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def walk(
    source: str,
    module: str,
    config: str,
    *,
    limit: int = 100,
    distinct: bool = True,
    **kwargs: Any,
) -> list[Step]:
    """Step through a spec and collect the behaviours it builds.

    The batch form of `DebugSession`, for a caller that wants the behaviour
    rather than the stepping.

    With `distinct` (the default), a stop whose trace is identical to the one
    before it is dropped. TLC evaluates every disjunct of `Next` at every
    state, so most stops repeat the behaviour unchanged -- a four-state
    Microwave stops eight times. `distinct=False` reports each stop, which is
    what you want if you are looking at evaluation rather than at states.

    `limit` bounds the number of *stops* consumed, not the number returned. It
    is a guard, not a preference: a spec with a large state space stops a great
    many times.
    """
    collected: list[Step] = []
    with DebugSession(source, module, config, **kwargs) as session:
        for _ in range(limit):
            step = session.step()
            if step is None:
                break
            if not step.states:
                # A stop that is not a state: TLC suspends before the first
                # ASSUME, and a breakpoint can also land somewhere no behaviour
                # exists yet. `walk` reports behaviours, and a behaviour with no
                # states is not one -- it read as "the spec has an empty state
                # space", which is what a wrongly-placed breakpoint looks like.
                continue
            if distinct and collected and collected[-1].states == step.states:
                continue
            collected.append(step)
    return collected
