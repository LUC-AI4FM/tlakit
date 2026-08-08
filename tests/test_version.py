"""The version is stated in two places and they must agree.

`pyproject.toml` drives the wheel and the release tag check; `__version__` is
what users read at runtime. They drifted the first time the version was bumped,
which is what this exists to prevent.
"""
from __future__ import annotations

import pathlib
import tomllib

import pytest

import tlakit

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
IN_SOURCE_TREE = PYPROJECT.is_file()


@pytest.mark.skipif(not IN_SOURCE_TREE, reason="no pyproject.toml; installed package")
def test_dunder_version_matches_pyproject():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert tlakit.__version__ == declared


@pytest.mark.skipif(
    IN_SOURCE_TREE,
    # An editable install keeps the metadata it was created with, so in a source
    # tree this compares __version__ against whatever the version was at
    # `pip install -e` time and fails on every bump. Against a built wheel --
    # which is where it matters, in the release smoke test -- it catches a wheel
    # packaged from a stale tree.
    reason="editable installs carry stale metadata; only meaningful for a built wheel",
)
def test_dunder_version_matches_installed_metadata():
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("tlakit")
    except PackageNotFoundError:
        pytest.skip("tlakit is not installed in this environment")
    assert tlakit.__version__ == installed
