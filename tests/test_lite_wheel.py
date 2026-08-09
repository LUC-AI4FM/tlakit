"""The version lives in a third place, and this is what watches it.

`lite/wheels/` holds the wheel the JupyterLite site serves, and
`lite/jupyter_lite_config.json` names it by filename. `tests/test_version.py`
keeps `pyproject.toml` and `__version__` honest; nothing kept these honest, and
a stale wheel there is invisible -- the build succeeds and every visitor gets
an old tlakit.

Cutting 0.1.0 did exactly that: the wheel was committed, then the branch was
rebased onto a main that had gained two features, and the committed wheel
silently had neither.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# The sdist ships /src, /tests and /docs but deliberately excludes /lite/wheels,
# so an installed package running its own tests has no wheel to check. Skipping
# is right: there is nothing here that a wheel could get wrong.
IN_SOURCE_TREE = (TOOLS / "lite_wheel.py").is_file() and (ROOT / "lite").is_dir()

pytestmark = pytest.mark.skipif(
    not IN_SOURCE_TREE, reason="no lite/ or tools/; installed package"
)


@pytest.fixture(scope="module")
def lite_wheel():
    sys.path.insert(0, str(TOOLS))
    try:
        import lite_wheel

        return lite_wheel
    finally:
        sys.path.remove(str(TOOLS))


def test_lite_wheel_matches_the_packaged_version(lite_wheel):
    problems = lite_wheel.check()
    assert not problems, "\n".join(
        ["lite/ is out of sync; run `python tools/lite_wheel.py --sync`", *problems]
    )


def test_check_notices_a_stale_wheel(tmp_path, monkeypatch, lite_wheel):
    """The guard above only helps if it can fail. A wheel one version behind is
    the case that actually happened, so that is the one worth pinning."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "tlakit-0.0.9-py3-none-any.whl").touch()
    config = tmp_path / "jupyter_lite_config.json"
    config.write_text(
        '{"PipliteAddon": {"piplite_urls": ["./wheels/tlakit-0.0.9-py3-none-any.whl"]}}'
    )
    monkeypatch.setattr(lite_wheel, "WHEELS", wheels)
    monkeypatch.setattr(lite_wheel, "CONFIG", config)
    monkeypatch.setattr(lite_wheel, "packaged_version", lambda: "0.1.0")

    problems = lite_wheel.check()

    assert len(problems) == 1
    assert "0.0.9" in problems[0] and "0.1.0" in problems[0]


def test_check_notices_a_leftover_second_wheel(tmp_path, monkeypatch, lite_wheel):
    """Copying the new wheel in without deleting the old one leaves two, and
    which one the build picks is not worth finding out the hard way."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "tlakit-0.0.9-py3-none-any.whl").touch()
    (wheels / "tlakit-0.1.0-py3-none-any.whl").touch()
    config = tmp_path / "jupyter_lite_config.json"
    config.write_text(
        '{"PipliteAddon": {"piplite_urls": ["./wheels/tlakit-0.1.0-py3-none-any.whl"]}}'
    )
    monkeypatch.setattr(lite_wheel, "WHEELS", wheels)
    monkeypatch.setattr(lite_wheel, "CONFIG", config)
    monkeypatch.setattr(lite_wheel, "packaged_version", lambda: "0.1.0")

    problems = lite_wheel.check()

    assert len(problems) == 1
    assert "2 tlakit wheels" in problems[0]


def test_check_notices_piplite_urls_left_behind(tmp_path, monkeypatch, lite_wheel):
    """The wheel swapped, the config not -- the JupyterLite build then asks for
    a file that is not there."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "tlakit-0.1.0-py3-none-any.whl").touch()
    config = tmp_path / "jupyter_lite_config.json"
    config.write_text(
        '{"PipliteAddon": {"piplite_urls": ["./wheels/tlakit-0.0.9-py3-none-any.whl"]}}'
    )
    monkeypatch.setattr(lite_wheel, "WHEELS", wheels)
    monkeypatch.setattr(lite_wheel, "CONFIG", config)
    monkeypatch.setattr(lite_wheel, "packaged_version", lambda: "0.1.0")

    problems = lite_wheel.check()

    assert len(problems) == 1
    assert "piplite_urls" in problems[0]
