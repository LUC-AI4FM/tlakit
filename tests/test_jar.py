import pytest

from tlakit import jar


def test_env_var_wins(tmp_path, monkeypatch):
    fake = tmp_path / "tla2tools.jar"
    fake.write_bytes(b"not really a jar")
    monkeypatch.setenv("TLAKIT_TLA2TOOLS", str(fake))
    assert jar.find_tools_jar() == fake


def test_missing_jar_raises_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TLAKIT_TLA2TOOLS", str(tmp_path / "nope.jar"))
    monkeypatch.setattr(jar, "cache_dir", lambda: tmp_path / "cache")
    with pytest.raises(jar.JarNotFound) as exc:
        jar.find_tools_jar()
    assert "TLAKIT_TLA2TOOLS" in str(exc.value)


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    env_jar = tmp_path / "env.jar"
    env_jar.write_bytes(b"x")
    explicit = tmp_path / "explicit.jar"
    explicit.write_bytes(b"x")
    monkeypatch.setenv("TLAKIT_TLA2TOOLS", str(env_jar))
    assert jar.find_tools_jar(explicit) == explicit


def test_community_jar_is_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("TLAKIT_COMMUNITY_MODULES", raising=False)
    monkeypatch.setattr(jar, "cache_dir", lambda: tmp_path / "cache")
    assert jar.find_community_jar() is None


def test_tlakit_imports_without_platformdirs():
    """The browser build ships tlakit alone, so import must need no third party.

    platformdirs is only needed to locate a downloaded jar, which a Pyodide
    kernel never does. Run in a subprocess: blocking an import in-process would
    leave sys.modules poisoned for every test that follows.
    """
    import subprocess
    import sys

    program = """
import sys

class Blocked:
    def find_module(self, name, path=None):
        if name == "platformdirs":
            raise ImportError("platformdirs is not available in this test")
        return None

    def find_spec(self, name, path=None, target=None):
        if name == "platformdirs":
            raise ImportError("platformdirs is not available in this test")
        return None

sys.meta_path.insert(0, Blocked())
import tlakit
import tlakit.remote
import tlakit.notebook
assert "platformdirs" not in sys.modules
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
