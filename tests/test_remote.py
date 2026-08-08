"""The remote runner never touches the network in these tests.

`transport` is injected, so what is under test is the contract with the
service's JSON -- which is the part that breaks silently when the service
changes shape.
"""
from __future__ import annotations

import json

import pytest

from tlakit import api
from tlakit.remote import (
    DEFAULT_ENDPOINT,
    RemoteError,
    RemoteRunner,
    Unsupported,
    default_transport,
    from_json,
)
from tlakit.result import Outcome, Severity


def transport_returning(status: int, body):
    """A transport that records what it was asked to send."""
    sent: dict[str, object] = {}

    def transport(method: str, url: str, payload: bytes | None, timeout: float):
        sent["method"] = method
        sent["url"] = url
        sent["payload"] = json.loads(payload) if payload else None
        sent["timeout"] = timeout
        return status, body if isinstance(body, str) else json.dumps(body)

    transport.sent = sent  # type: ignore[attr-defined]
    return transport


SUCCESS = {
    "outcome": "ok",
    "ok": True,
    "diagnostics": [],
    "trace": None,
    "graph": None,
    "stats": {"generated": 13, "distinct": 5, "depth": 3, "duration_ms": 900},
}

VIOLATION = {
    "outcome": "invariant_violation",
    "ok": False,
    "diagnostics": [
        {
            "severity": "error",
            "message": "Invariant Correct is violated.",
            "module": "Counter",
            "line": 12,
            "column": 1,
        }
    ],
    "trace": {
        "states": [{"x": 0}, {"x": 1}],
        "truncated": False,
        "actions": [{"name": "Inc", "module": "Counter", "line": 8, "column": 3}],
        "loop_start": None,
        "variables": ["x"],
    },
    "graph": None,
    "stats": {"generated": 3, "distinct": 2, "depth": 2, "duration_ms": 120},
}


def test_check_posts_the_spec_and_config():
    transport = transport_returning(200, SUCCESS)
    runner = RemoteRunner(transport=transport)

    result = runner.check("---- MODULE M ----\n====", "M", "SPECIFICATION Spec\n")

    assert transport.sent["url"] == f"{DEFAULT_ENDPOINT}/check"
    assert transport.sent["payload"] == {
        "spec": "---- MODULE M ----\n====",
        "config": "SPECIFICATION Spec\n",
    }
    assert result.outcome is Outcome.OK
    assert result.ok
    assert result.stats.generated == 13


def test_violation_rebuilds_diagnostics_and_trace():
    runner = RemoteRunner(transport=transport_returning(200, VIOLATION))

    result = runner.check("spec", "Counter", "cfg")

    assert result.outcome is Outcome.INVARIANT_VIOLATION
    assert not result.ok
    assert result.diagnostics[0].line == 12
    assert result.trace is not None
    assert len(result.trace) == 2
    assert result.trace.actions[0].name == "Inc"
    # begin_line is what the local runner populates, so a trace from either
    # source renders identically.
    assert result.trace.actions[0].begin_line == 8
    assert result.trace.variables == ["x"]


def test_coverage_flag_is_translated_not_forwarded():
    """`-coverage 1` is how TLC spells it; the service takes a boolean."""
    transport = transport_returning(200, SUCCESS)
    RemoteRunner(transport=transport).check(
        "spec", "M", "cfg", extra_opts=["-coverage", "1"]
    )
    assert transport.sent["payload"]["coverage"] is True
    assert "extra_opts" not in transport.sent["payload"]


def test_graph_and_timeout_are_forwarded():
    transport = transport_returning(200, SUCCESS)
    RemoteRunner(transport=transport).check(
        "spec", "M", "cfg", graph=True, timeout=5
    )
    assert transport.sent["payload"]["graph"] is True
    assert transport.sent["payload"]["timeout"] == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"heap": "2G"},
        {"collect": "*.svg"},
        {"extra_modules": {"Other": "---- MODULE Other ----\n===="}},
        {"extra_opts": ["-metadir", "/tmp/x"]},
        {"max_graph_nodes": 50},
    ],
)
def test_options_the_service_cannot_honour_raise(kwargs):
    """Dropping these silently is worse than failing.

    A spec that needs `heap="2G"` would otherwise look like it deadlocked, and
    a forwarded `-metadir` is exactly what the service refuses on purpose.
    """
    runner = RemoteRunner(transport=transport_returning(200, SUCCESS))
    with pytest.raises(Unsupported):
        runner.check("spec", "M", "cfg", **kwargs)


def test_truncated_trace_becomes_a_warning():
    payload = json.loads(json.dumps(VIOLATION))
    payload["trace"]["truncated"] = True
    runner = RemoteRunner(transport=transport_returning(200, payload))

    result = runner.check("spec", "M", "cfg")

    warnings = [d for d in result.diagnostics if d.severity is Severity.WARNING]
    assert warnings, "a truncated trace must say so; it looks complete otherwise"
    assert "truncated" in warnings[0].message


def test_rate_limit_says_how_long_to_wait():
    runner = RemoteRunner(
        transport=transport_returning(429, {"retry_after_seconds": 60})
    )
    with pytest.raises(RemoteError, match="60") as caught:
        runner.check("spec", "M", "cfg")
    assert caught.value.status == 429


def test_validation_error_surfaces_the_services_detail():
    runner = RemoteRunner(
        transport=transport_returning(422, {"detail": "spec exceeds 65536 bytes"})
    )
    with pytest.raises(RemoteError, match="exceeds 65536"):
        runner.check("spec", "M", "cfg")


def test_non_json_body_is_reported_with_the_status():
    """Cloudflare error pages are HTML; the message must not be a JSONDecodeError."""
    runner = RemoteRunner(transport=transport_returning(502, "<html>bad gateway"))
    with pytest.raises(RemoteError, match="502"):
        runner.check("spec", "M", "cfg")


def test_parse_is_refused_with_a_pointer_to_check():
    runner = RemoteRunner(transport=transport_returning(200, SUCCESS))
    with pytest.raises(Unsupported, match="check"):
        runner.parse("spec", "M")


def test_raw_explains_why_it_is_empty():
    result = from_json(SUCCESS)
    assert result.raw.exit_code is None, "there was no local process to exit"
    assert "paths" in result.raw.stdout


def test_graph_is_rebuilt_from_json():
    payload = dict(SUCCESS)
    payload["graph"] = {
        "nodes": [
            {"id": "-1", "vars": {"x": "0"}, "initial": True},
            {"id": "2", "vars": {"x": "1"}, "initial": False},
        ],
        "edges": [{"from": "-1", "to": "2", "action": "Inc"}],
        "variables": ["x"],
        "truncated": True,
    }
    result = from_json(payload)
    assert len(result.graph) == 2
    assert result.graph.edges[0].action == "Inc"
    assert result.graph.truncated
    assert result.graph.nodes[0].initial


def test_default_transport_is_urllib_off_emscripten(monkeypatch):
    from tlakit import remote

    monkeypatch.setattr(remote.sys, "platform", "darwin")
    assert default_transport() is remote._request_urllib
    monkeypatch.setattr(remote.sys, "platform", "emscripten")
    assert default_transport() is remote._request_xhr


def test_use_remote_redirects_the_default_runner():
    try:
        runner = api.use_remote("https://example.invalid")
        assert api.default_runner() is runner
        assert runner.endpoint == "https://example.invalid"
    finally:
        api.use_local()


def test_use_remote_makes_spec_check_go_remote():
    """The whole point: Spec and the magics stay unchanged."""
    transport = transport_returning(200, SUCCESS)
    try:
        api.use_remote(transport=transport)
        spec = api.Spec(source="---- MODULE M ----\n====", name="M")
        result = spec.check(invariants=["Inv"])
        assert result.outcome is Outcome.OK
        assert "INVARIANT Inv" in transport.sent["payload"]["config"]
    finally:
        api.use_local()


def test_use_local_restores_jar_resolution():
    api.use_remote()
    api.use_local()
    assert api._override is None


def test_health_is_a_get_with_no_body():
    """A GET is what /health accepts; a POST there is a 405."""
    transport = transport_returning(200, {"ok": True, "key_required": False})
    assert RemoteRunner(transport=transport).health()["ok"] is True
    assert transport.sent["method"] == "GET"
    assert transport.sent["payload"] is None


def test_check_is_a_post():
    transport = transport_returning(200, SUCCESS)
    RemoteRunner(transport=transport).check("spec", "M", "cfg")
    assert transport.sent["method"] == "POST"


def test_eval_is_refused_with_an_explanation_not_an_attributeerror():
    """`%tla_eval` reaches for `runner.eval`, which the service has no route for.

    Absent the method the browser notebook raises AttributeError naming an
    internal class, which tells the reader nothing. Refusing on purpose is the
    same contract every other unsupported option here follows.
    """
    with pytest.raises(Unsupported, match="tlc2.REPL"):
        RemoteRunner().eval("1 + 1")


def test_the_magic_surfaces_that_refusal(monkeypatch):
    """End of the chain: the magic must not turn it into something opaque."""
    IPython = pytest.importorskip("IPython")
    from IPython.core.interactiveshell import InteractiveShell

    InteractiveShell.clear_instance()
    shell = InteractiveShell.instance()
    shell.run_line_magic("load_ext", "tlakit")
    monkeypatch.setattr(api, "_override", RemoteRunner())
    try:
        with pytest.raises(Unsupported, match="INVARIANT"):
            shell.run_line_magic("tla_eval", "1 + 1")
    finally:
        InteractiveShell.clear_instance()
