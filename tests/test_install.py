"""Issue #12: fetch the pinned jars, and never cache one that fails its checksum."""
import hashlib
import io
from pathlib import Path

import pytest

from tlakit import install as inst

PAYLOAD = b"pretend this is a jar"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "cache_dir", lambda: tmp_path)
    return tmp_path


def fake_urlopen(payload: bytes):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    return lambda url: Response(payload)


def pin(sha: str = DIGEST) -> inst.PinnedJar:
    return inst.PinnedJar(
        filename="thing.jar", tag="v9.9.9", url="https://example/thing.jar", sha256=sha
    )


def test_downloads_into_a_version_scoped_path(cache, monkeypatch):
    monkeypatch.setattr(inst.urllib.request, "urlopen", fake_urlopen(PAYLOAD))
    path = inst.fetch(pin())
    assert path == cache / "v9.9.9" / "thing.jar"
    assert path.read_bytes() == PAYLOAD


def test_second_call_does_not_redownload(cache, monkeypatch):
    calls = []

    def counting(url):
        calls.append(url)
        return fake_urlopen(PAYLOAD)(url)

    monkeypatch.setattr(inst.urllib.request, "urlopen", counting)
    inst.fetch(pin())
    inst.fetch(pin())
    assert len(calls) == 1


def test_a_bad_checksum_is_refused(cache, monkeypatch):
    monkeypatch.setattr(inst.urllib.request, "urlopen", fake_urlopen(b"tampered"))
    with pytest.raises(inst.ChecksumMismatch) as exc:
        inst.fetch(pin())
    assert "expected" in str(exc.value)


def test_a_refused_download_leaves_nothing_behind(cache, monkeypatch):
    """A jar is code handed to java. A failed verification must not become the
    cached jar, and must not leave a partial file either."""
    monkeypatch.setattr(inst.urllib.request, "urlopen", fake_urlopen(b"tampered"))
    with pytest.raises(inst.ChecksumMismatch):
        inst.fetch(pin())
    assert not (cache / "v9.9.9" / "thing.jar").exists()
    leftovers = list(cache.rglob("*.partial"))
    assert leftovers == [], f"partial files left behind: {leftovers}"


def test_a_new_pin_does_not_overwrite_an_older_one(cache, monkeypatch):
    monkeypatch.setattr(inst.urllib.request, "urlopen", fake_urlopen(PAYLOAD))
    old = inst.fetch(pin())

    newer_payload = b"a later release"
    newer = inst.PinnedJar(
        filename="thing.jar",
        tag="v10.0.0",
        url="https://example/thing.jar",
        sha256=hashlib.sha256(newer_payload).hexdigest(),
    )
    monkeypatch.setattr(inst.urllib.request, "urlopen", fake_urlopen(newer_payload))
    fresh = inst.fetch(newer)

    assert old.exists() and fresh.exists()
    assert old != fresh
    assert old.read_bytes() == PAYLOAD


def test_force_redownloads(cache, monkeypatch):
    calls = []

    def counting(url):
        calls.append(url)
        return fake_urlopen(PAYLOAD)(url)

    monkeypatch.setattr(inst.urllib.request, "urlopen", counting)
    inst.fetch(pin())
    inst.fetch(pin(), force=True)
    assert len(calls) == 2


def test_sha256_of_matches_hashlib(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(PAYLOAD)
    assert inst.sha256_of(f) == DIGEST


def test_the_pinned_tools_release_is_new_enough_for_dumptrace():
    """v1.7.4 ships TLC 2.19, which has no -dumpTrace. The pin must be past it."""
    assert inst.TOOLS_TAG == "v1.8.0"
    assert len(inst.TOOLS.sha256) == 64
    assert len(inst.COMMUNITY.sha256) == 64


def test_cli_reports_the_resolved_paths(cache, monkeypatch, capsys):
    monkeypatch.setattr(inst.urllib.request, "urlopen", fake_urlopen(PAYLOAD))
    monkeypatch.setattr(
        inst, "TOOLS", pin()._replace() if hasattr(pin(), "_replace") else pin()
    )
    monkeypatch.setattr(inst, "COMMUNITY", pin())
    assert inst.main([]) == 0
    assert "thing.jar" in capsys.readouterr().out


def test_cli_reports_a_checksum_failure_without_raising(cache, monkeypatch, capsys):
    monkeypatch.setattr(inst.urllib.request, "urlopen", fake_urlopen(b"tampered"))
    monkeypatch.setattr(inst, "TOOLS", pin())
    monkeypatch.setattr(inst, "COMMUNITY", pin())
    assert inst.main([]) == 1
    assert "error:" in capsys.readouterr().out
