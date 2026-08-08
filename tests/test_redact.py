"""Nothing about the host goes out in a response.

No error class leaks a path today -- measured across missing modules, semantic
errors, syntax errors, bad configs, and evaluation errors. This makes it
enforced rather than incidental, so a future TLC message cannot change that
silently.
"""
import json
import shutil

import pytest

from tlakit.serve.redact import PLACEHOLDER, redact, redact_deep


def test_absolute_paths_are_replaced():
    assert redact("Parsing /private/var/folders/2q/x/E.tla failed") == (
        f"Parsing {PLACEHOLDER} failed"
    )
    assert redact("/usr/local/tlakit/jars/tla2tools.jar") == PLACEHOLDER


def test_tla_conjunction_is_not_mistaken_for_a_path():
    """`/\\` is the most common two characters in a TLA+ spec."""
    for text in (r"x = 0 /\ y = 1", r"state ok /\ done", r"a \/ b"):
        assert redact(text) == text


def test_ordinary_diagnostics_survive_untouched():
    for text in (
        "Invariant Inv is violated.",
        'Encountered "====" at line 4, column 1 and token "="',
        "Deadlock reached.",
        "TLC exited with code 12",
        "Unknown operator: `undefinedOp'.",
    ):
        assert redact(text) == text


def test_a_username_is_not_redacted_out_of_a_longer_word(monkeypatch):
    """Replacing bare terms first mangles them: `eric` inside `ericspencer`
    leaves a half-redacted string that still discloses the rest."""
    monkeypatch.setenv("USER", "eric")
    monkeypatch.setenv("HOME", "/Users/eric")
    assert redact("ericspencer is a name") == "ericspencer is a name"
    assert redact("user eric ran it") == f"user {PLACEHOLDER} ran it"


def test_the_home_directory_goes_whole(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    assert "someone" not in redact("/Users/someone/Library/Caches/x/y.jar")


def test_a_configured_jar_path_is_redacted(monkeypatch):
    monkeypatch.setenv("TLAKIT_TLA2TOOLS", "/opt/secret/place/tla2tools.jar")
    assert "secret" not in redact("loading /opt/secret/place/tla2tools.jar")


def test_redact_deep_walks_a_response():
    payload = {
        "diagnostics": [{"message": "at /private/var/x/y/E.tla"}],
        "trace": {"states": [{"path": "/usr/local/tlakit/x"}]},
        "stats": {"generated": 12},
        "flag": True,
        "nothing": None,
    }
    out = redact_deep(payload)
    assert PLACEHOLDER in out["diagnostics"][0]["message"]
    assert out["trace"]["states"][0]["path"] == PLACEHOLDER
    assert out["stats"]["generated"] == 12   # non-strings untouched
    assert out["flag"] is True and out["nothing"] is None


def test_empty_and_none_are_safe():
    assert redact("") == ""
    assert redact_deep(None) is None


# --- the guarantee, over every error class -------------------------------

CASES = {
    "missing module": "---- MODULE A ----\nEXTENDS Nonexistent\nVARIABLE x\nInit == x = 0\n====",
    "semantic":       "---- MODULE B ----\nVARIABLE x\nInit == x = undefinedOp\nNext == x' = x\n====",
    "syntax":         "---- MODULE C ----\nVARIABLE y\nInit == y = \n====",
    "bad config":     "---- MODULE D ----\nVARIABLE x\nInit == x = 0\nNext == x' = x\n====",
}
PROBES = ("/private", "/var/folders", "/usr/local", "/Users", "tlakit-", ".jar")


@pytest.mark.java
@pytest.mark.parametrize("name", list(CASES))
def test_no_error_class_leaks_a_path(name):
    import tlakit
    from tlakit.jar import JarNotFound
    from tlakit.serve import Limits, as_json

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        runner = tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    src = CASES[name]
    module = src.split()[2]
    config = "SPECIFICATION NoSuchSpec\n" if name == "bad config" else "SPECIFICATION Spec\n"
    result = runner.check(src, module, config, timeout=40)
    payload = json.dumps(as_json(result, Limits()))
    leaked = [p for p in PROBES if p in payload]
    assert not leaked, f"{name} leaked {leaked}"


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="POSIX mode bits; Windows ACLs are a different mechanism",
)
def test_the_working_directory_is_private():
    """A submitted spec sits on disk during the check. Other local accounts
    must not be able to read it."""
    import stat
    import tempfile

    with tempfile.TemporaryDirectory(prefix="tlakit-") as tmp:
        import os

        mode = stat.S_IMODE(os.stat(tmp).st_mode)
        assert mode == 0o700, oct(mode)
