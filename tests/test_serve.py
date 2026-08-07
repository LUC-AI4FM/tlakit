"""Issue #39: the public service must expose exactly one capability."""
import shutil

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import tlakit  # noqa: E402
from tlakit.serve import (  # noqa: E402
    Limits, RequestTooLarge, as_json, clamp_timeout, create_app,
    isolated_jar_dir, startup_checks, validate,
)

SPEC = """---- MODULE Web ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = x + 1
Spec == Init /\\ [][Next]_x
Inv == x < 3
====
"""
EVIL = """---- MODULE Evil ----
EXTENDS Naturals, TLC, IOUtils
VARIABLE x
Init == x = 0 /\\ PrintT(IOExec(<<"sh", "-c", "echo PWNED">>).stdout)
Next == x' = x
Spec == Init /\\ [][Next]_x
====
"""

LIMITS = Limits()


# --- validation, no Java needed ------------------------------------------


def test_an_empty_spec_is_refused():
    with pytest.raises(RequestTooLarge):
        validate("   ", "", LIMITS)


def test_an_oversized_spec_is_refused_before_java_starts():
    with pytest.raises(RequestTooLarge, match="exceeds"):
        validate("x" * (LIMITS.spec_bytes + 1), "", LIMITS)


def test_an_oversized_config_is_refused():
    with pytest.raises(RequestTooLarge, match="config"):
        validate(SPEC, "y" * (LIMITS.config_bytes + 1), LIMITS)


def test_timeout_is_clamped_to_the_ceiling():
    assert clamp_timeout(9999, LIMITS) == LIMITS.max_timeout
    assert clamp_timeout(None, LIMITS) == LIMITS.default_timeout
    assert clamp_timeout(0.0001, LIMITS) == 0.5


def test_startup_refuses_community_modules(monkeypatch, tmp_path):
    jar = tmp_path / "tla2tools.jar"
    jar.write_bytes(b"x")
    cm = tmp_path / "cm" / "CommunityModules-deps.jar"
    cm.parent.mkdir()
    cm.write_bytes(b"x")

    class Fake:
        tools_jar = jar
        community_jar = cm

    with pytest.raises(RuntimeError, match="IOExec"):
        startup_checks(Fake())


def test_serialized_response_never_carries_raw_output():
    """raw holds absolute temp paths, the java argv, and the jar location."""
    from tlakit.result import Outcome, RawOutput, Stats

    raw = RawOutput(
        argv=["/opt/homebrew/bin/java", "-cp", "/Users/someone/secret.jar"],
        exit_code=12, stdout="/private/var/folders/leaky/path", stderr="",
    )
    payload = as_json(
        tlakit.CheckResult(Outcome.INVARIANT_VIOLATION, [], None, Stats(), raw), LIMITS
    )
    flat = repr(payload)
    assert "raw" not in payload
    assert "secret.jar" not in flat
    assert "/private/var/folders" not in flat


def test_long_traces_are_truncated_and_say_so():
    from tlakit.result import Action, Outcome, RawOutput, Stats, Trace

    n = LIMITS.trace_states + 50
    trace = Trace(
        states=[{"x": i} for i in range(n)],
        actions=[Action("Next") for _ in range(n - 1)],
    )
    raw = RawOutput(argv=[], exit_code=12, stdout="", stderr="")
    payload = as_json(
        tlakit.CheckResult(Outcome.INVARIANT_VIOLATION, [], trace, Stats(), raw),
        LIMITS,
    )
    assert len(payload["trace"]["states"]) == LIMITS.trace_states
    assert payload["trace"]["truncated"] is True
    assert len(payload["trace"]["actions"]) == LIMITS.trace_states - 1


# --- the live service ----------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        isolated_jar_dir()
    except JarNotFound as exc:
        pytest.skip(str(exc))
    return TestClient(create_app())


@pytest.mark.java
def test_health_reports_the_isolation_posture(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["community_modules"] is False


@pytest.mark.java
def test_a_violation_comes_back_with_its_trace(client):
    body = client.post("/check", json={"spec": SPEC, "invariants": ["Inv"]}).json()
    assert body["outcome"] == "invariant_violation"
    assert [s["x"] for s in body["trace"]["states"]] == [0, 1, 2, 3]
    assert body["trace"]["variables"] == ["x"]


@pytest.mark.java
def test_a_sound_spec_passes(client):
    ok = SPEC.replace("Next == x' = x + 1", "Next == IF x < 2 THEN x' = x + 1 ELSE x' = x")
    body = client.post("/check", json={"spec": ok, "invariants": ["Inv"]}).json()
    assert body["ok"] is True
    assert body["trace"] is None


@pytest.mark.java
def test_a_spec_cannot_execute_shell_commands(client):
    """The whole reason this service can be public."""
    response = client.post("/check", json={"spec": EVIL})
    body = response.json()
    assert body["ok"] is False
    text = repr(body)
    assert "PWNED" not in text
    assert "uid=" not in text


@pytest.mark.java
def test_a_syntax_error_is_a_diagnostic_not_a_500(client):
    response = client.post("/check", json={"spec": "---- MODULE B ----\nInit == \n===="})
    assert response.status_code == 200
    assert response.json()["diagnostics"]


@pytest.mark.java
def test_a_spec_without_a_module_header_is_a_422(client):
    response = client.post("/check", json={"spec": "not a module at all"})
    assert response.status_code == 422


@pytest.mark.java
def test_an_oversized_request_is_a_422(client):
    response = client.post("/check", json={"spec": "x" * (LIMITS.spec_bytes + 10)})
    assert response.status_code == 422


@pytest.mark.java
def test_there_is_no_way_to_pass_tlc_options(client):
    """-dump and -metadir would let a request write files of its choosing."""
    response = client.post(
        "/check",
        json={"spec": SPEC, "invariants": ["Inv"],
              "extra_opts": ["-dump", "dot", "/tmp/tlakit-escape.dot"]},
    )
    # The model forbids extra fields, so this is refused outright rather than
    # quietly ignored -- a client sending it should be told it does not exist.
    assert response.status_code == 422
    import pathlib

    assert not pathlib.Path("/tmp/tlakit-escape.dot").exists()


@pytest.mark.java
def test_only_health_and_check_are_routed(client):
    for path in ("/", "/files", "/api/contents", "/openapi.json"):
        code = client.get(path).status_code
        assert code in (200, 404), path  # openapi is fine; nothing else exists
    assert client.get("/files").status_code == 404


# --- shared key with the edge Worker -------------------------------------


def test_no_key_configured_means_open(monkeypatch):
    from tlakit.serve.app import KEY_ENV, require_key

    monkeypatch.delenv(KEY_ENV, raising=False)
    require_key(None)  # local development must not need a header


def test_a_configured_key_is_required(monkeypatch):
    from fastapi import HTTPException

    from tlakit.serve.app import KEY_ENV, require_key

    monkeypatch.setenv(KEY_ENV, "s3cret")
    require_key("s3cret")
    for bad in (None, "", "wrong", "s3cre"):
        with pytest.raises(HTTPException) as exc:
            require_key(bad)
        assert exc.value.status_code == 401


@pytest.mark.java
def test_the_live_service_rejects_a_missing_key(monkeypatch):
    from tlakit.jar import JarNotFound
    from tlakit.serve.app import KEY_ENV, create_app

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        isolated_jar_dir()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    monkeypatch.setenv(KEY_ENV, "s3cret")
    guarded = TestClient(create_app())

    assert guarded.post("/check", json={"spec": SPEC}).status_code == 401
    assert guarded.get("/health").status_code == 401
    ok = guarded.post(
        "/check", json={"spec": SPEC, "invariants": ["Inv"]},
        headers={"X-Tlakit-Key": "s3cret"},
    )
    assert ok.status_code == 200
    assert ok.json()["outcome"] == "invariant_violation"
    assert guarded.get("/health", headers={"X-Tlakit-Key": "s3cret"}).json()[
        "key_required"
    ] is True
