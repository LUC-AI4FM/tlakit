"""Issue #22: McpRunner, a CliRunner-shaped client for the extension's MCP server.

Everything here runs without a server. The wire is injectable for exactly that
reason -- the interesting failures are in reading what the server says, and
those are text, reproduced from real replies. The conformance test that needs a
live server lives in `test_mcp_serve.py`.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tlakit.cli import CliRunner
from tlakit.mcp.runner import (
    McpRunner,
    McpUnavailable,
    Unsupported,
    _sany_diagnostics,
    _tlc_output,
)
from tlakit.result import Outcome

# A real `tlaplus_mcp_tlc_check` reply, trimmed: prose, then TLC's own output.
CHECK_TEXT = """Model check completed with exit code 12.

Output:
TLC2 Version 2026.07.31.184830 (rev: 30cc360)
Running breadth-first search Model-Checking with fp 130 and seed 1 with 1 worker
Starting SANY...
Error: Invariant NotSolved is violated.
Error: The behavior up to this point is:
State 1: <Initial predicate>
/\\ big = 0
/\\ small = 0

73 states generated, 14 distinct states found, 0 states left on queue.
The depth of the complete state graph search is 7.
Finished in 00s at (2026-08-08 19:12:00)
"""

# The extension reports one bad module three times.
PARSE_FAILED = (
    "Parsing of file /ws/Broken.tla failed at line 4 with error: "
    "'Encountered \"====\" at line 4, column 1 and token \"=\"'\n"
    "Parsing of file /ws/Broken.tla failed at line 1 with error: "
    "'Fatal errors while parsing TLA+ spec in file Broken.tla\n"
    "In module Broken\n"
    "Could not parse module Broken from file Broken.tla'\n"
    "Parsing of file /ws/Broken.tla failed at line 1 with error: "
    "'In module Broken\nCould not parse module Broken from file Broken.tla'\n"
)
PARSE_OK = "No errors found in the TLA+ specification /ws/DieHard.tla.\n"

SPEC = """---- MODULE M ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = x
Spec == Init /\\ [][Next]_x
====
"""


def fake_transport(replies: list[dict], record: list | None = None):
    """A transport answering with successive JSON-RPC results."""
    queue = list(replies)

    def transport(url, body, timeout):
        message = json.loads(body)
        if record is not None:
            record.append((message, timeout))
        if message["method"] == "initialize":
            payload = {"result": {"protocolVersion": "2025-06-18"}}
        else:
            payload = queue.pop(0)
        payload = {"jsonrpc": "2.0", "id": message["id"], **payload}
        return 200, "application/json", json.dumps(payload)

    return transport


def tool_reply(text: str, is_error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return {"result": result}


# --- the interface -------------------------------------------------------


@pytest.mark.parametrize("method", ["parse", "check", "eval"])
def test_it_takes_every_argument_CliRunner_takes(method):
    """"The same interface as CliRunner" has to mean something checkable.

    Every parameter of the local runner must be accepted here, by name -- code
    written against one runner should not stop working when handed the other.
    """
    theirs = inspect.signature(getattr(CliRunner, method)).parameters
    ours = inspect.signature(getattr(McpRunner, method)).parameters
    missing = [name for name in theirs if name not in ours]
    assert not missing, f"McpRunner.{method} does not accept {missing}"


def test_it_looks_like_a_runner_to_code_that_inspects_one():
    runner = McpRunner()
    assert runner.can_parse is True
    assert runner.tools_jar is None and runner.community_jar is None


def test_eval_says_what_to_use_instead():
    with pytest.raises(Unsupported) as caught:
        McpRunner().eval("1 + 1")
    assert "REPL" in str(caught.value) and "CliRunner" in str(caught.value)


# --- reading what the server says ----------------------------------------


def test_the_exit_code_comes_off_the_prose(tmp_path):
    runner = McpRunner(workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)]))
    result = runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    assert result.raw.exit_code == 12


def test_the_outcome_and_stats_come_from_the_same_parser_CliRunner_uses(tmp_path):
    """Not a second TLC output parser: `parse_tlc`, on the text under `Output:`."""
    runner = McpRunner(workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)]))
    result = runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    assert result.outcome is Outcome.INVARIANT_VIOLATION
    assert (result.stats.generated, result.stats.distinct, result.stats.depth) == (73, 14, 7)


def test_the_prose_wrapper_is_not_fed_to_the_parser():
    assert _tlc_output(CHECK_TEXT).startswith("TLC2 Version")
    # No `Output:` header: the whole thing is the best guess available.
    assert _tlc_output("something else entirely") == "something else entirely"


def test_a_trace_is_read_from_the_dump_not_the_text(tmp_path):
    """#22's requirement: structure comes from `-dumpTrace json` via extraOpts."""
    sent: list = []
    runner = McpRunner(
        workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)], sent)
    )
    runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    options = sent[-1][0]["params"]["arguments"]["extraOpts"]
    assert options[:2] == ["-dumpTrace", "json"]
    assert options[2].endswith("trace.json")


def test_extra_opts_are_appended_not_replaced(tmp_path):
    sent: list = []
    runner = McpRunner(
        workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)], sent)
    )
    runner.check(SPEC, "M", "SPECIFICATION Spec\n", extra_opts=["-workers", "2"])
    options = sent[-1][0]["params"]["arguments"]["extraOpts"]
    assert "-dumpTrace" in options and options[-2:] == ["-workers", "2"]


def test_heap_becomes_a_java_option(tmp_path):
    sent: list = []
    runner = McpRunner(
        workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)], sent)
    )
    runner.check(SPEC, "M", "SPECIFICATION Spec\n", heap="2G")
    assert sent[-1][0]["params"]["arguments"]["extraJavaOpts"] == ["-Xmx2G"]


def test_the_text_mode_trace_is_the_fallback(tmp_path):
    """No dump file is written by a fake transport, so the printed states are
    all there is -- and #4's reader handles exactly that shape."""
    runner = McpRunner(workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)]))
    result = runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    assert result.trace is not None
    assert result.trace.states[0]["big"] == 0


# --- SANY ---------------------------------------------------------------


def test_a_clean_parse_is_OK(tmp_path):
    runner = McpRunner(workspace=tmp_path, transport=fake_transport([tool_reply(PARSE_OK)]))
    assert runner.parse(SPEC, "M").outcome is Outcome.OK


def test_a_parse_failure_is_reported_once_with_its_line(tmp_path):
    """The extension says it three times. A reader who has been told does not
    need it twice -- the same problem #74 fixed in rendering."""
    runner = McpRunner(
        workspace=tmp_path, transport=fake_transport([tool_reply(PARSE_FAILED)])
    )
    result = runner.parse(SPEC, "Broken")
    assert result.outcome is Outcome.PARSE_ERROR
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].line == 4
    assert result.diagnostics[0].module == "Broken"
    assert "Encountered" in result.diagnostics[0].message


def test_the_followups_are_what_gets_dropped():
    messages = [d.message for d in _sany_diagnostics(PARSE_FAILED)]
    assert not any(m.lower().startswith("fatal errors") for m in messages)
    assert not any(m.lower().startswith("in module") for m in messages)


def test_an_unreadable_parse_result_is_not_called_OK(tmp_path):
    """Reporting success for something unparsed is the one wrong answer."""
    runner = McpRunner(
        workspace=tmp_path, transport=fake_transport([tool_reply("who knows")])
    )
    result = runner.parse(SPEC, "M")
    assert result.outcome is Outcome.ERROR
    assert "neither" in result.diagnostics[0].message


# --- failures -----------------------------------------------------------


def test_a_tool_error_raises_rather_than_passing_for_output(tmp_path):
    runner = McpRunner(
        workspace=tmp_path,
        transport=fake_transport([tool_reply("Something broke", is_error=True)]),
    )
    with pytest.raises(McpUnavailable, match="Something broke"):
        runner.check(SPEC, "M", "SPECIFICATION Spec\n")


def test_a_rejected_path_explains_the_workspace(tmp_path):
    """The server's own message reads like a security incident. It is a
    mismatched argument, and the error should say so."""
    denied = (
        "Access denied: Path /tmp/x/M.tla is outside the workspace "
        "(path traversal detected)"
    )
    runner = McpRunner(
        workspace=tmp_path, transport=fake_transport([tool_reply(denied, is_error=True)])
    )
    with pytest.raises(McpUnavailable) as caught:
        runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    assert "workspace=" in str(caught.value) and str(tmp_path) in str(caught.value)


def test_no_server_says_how_to_start_one():
    def refuse(url, body, timeout):
        raise OSError("Connection refused")

    with pytest.raises(McpUnavailable) as caught:
        McpRunner(transport=refuse).tools()
    assert "tlakit.mcp.serve" in str(caught.value)


def test_a_request_that_times_out_says_TLC_is_still_running(tmp_path):
    """A timeout here is not a kill: the server has no cancel tool, so claiming
    the run stopped would be a lie."""
    def slow(url, body, timeout):
        if json.loads(body)["method"] == "initialize":
            return 200, "application/json", json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": {}}
            )
        raise TimeoutError("timed out")

    result = McpRunner(workspace=tmp_path, transport=slow).check(
        SPEC, "M", "SPECIFICATION Spec\n", timeout=1
    )
    assert result.outcome is Outcome.TIMEOUT
    assert "still going" in result.diagnostics[0].message


def test_an_http_error_is_not_mistaken_for_a_result():
    def five_hundred(url, body, timeout):
        return 500, "text/plain", "upstream exploded"

    with pytest.raises(McpUnavailable, match="500"):
        McpRunner(transport=five_hundred).tools()


# --- the wire -----------------------------------------------------------


def test_the_reply_is_picked_out_of_an_SSE_stream_by_id():
    """Streamable HTTP interleaves notifications with the answer, so "the last
    message" is not the answer."""
    def sse(url, body, timeout):
        rid = json.loads(body)["id"]
        stream = (
            'data: {"jsonrpc":"2.0","method":"notifications/message",'
            '"params":{"level":"info"}}\n\n'
            f'data: {{"jsonrpc":"2.0","id":{rid},"result":{{"tools":'
            '[{"name":"tlaplus_mcp_sany_parse"}]}}\n\n'
            'data: {"jsonrpc":"2.0","method":"notifications/message",'
            '"params":{"level":"info"}}\n\n'
        )
        return 200, "text/event-stream", stream

    assert McpRunner(transport=sse).tools() == ["tlaplus_mcp_sany_parse"]


def test_a_json_rpc_error_is_raised_with_its_message():
    def failing(url, body, timeout):
        return 200, "application/json", json.dumps({
            "jsonrpc": "2.0", "id": json.loads(body)["id"],
            "error": {"code": -32601, "message": "Method not found"},
        })

    with pytest.raises(McpUnavailable, match="Method not found"):
        McpRunner(transport=failing).tools()


def test_specs_are_written_inside_the_workspace(tmp_path):
    """Not a detail: anywhere else and the server refuses the path."""
    sent: list = []
    runner = McpRunner(
        workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)], sent)
    )
    runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    written = Path(sent[-1][0]["params"]["arguments"]["fileName"])
    assert tmp_path in written.parents


def test_each_run_gets_its_own_directory(tmp_path):
    """TLC leaves `<Module>_TTrace_*.tla` behind, and a leftover from another
    module makes the next run fail with exit 255."""
    sent: list = []
    runner = McpRunner(
        workspace=tmp_path,
        transport=fake_transport([tool_reply(CHECK_TEXT), tool_reply(CHECK_TEXT)], sent),
    )
    runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    first, second = (Path(s[0]["params"]["arguments"]["fileName"]).parent for s in sent[-2:])
    assert first != second


def test_the_working_directory_does_not_survive_the_call(tmp_path):
    runner = McpRunner(workspace=tmp_path, transport=fake_transport([tool_reply(CHECK_TEXT)]))
    runner.check(SPEC, "M", "SPECIFICATION Spec\n")
    assert list(tmp_path.iterdir()) == []
