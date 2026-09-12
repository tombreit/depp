"""Tests for the version reported by `depp --version`.

The version is derived from the git tag by setuptools-scm at build time, so it
changes with every commit and every day. These tests therefore assert its shape
and provenance, never a literal string.
"""

import re

import pytest

from depp.cli import _read_version, parse_args


def test_read_version_comes_from_package_metadata():
    """A miswired packaging change makes `_read_version` fall back to "unknown"."""
    version = _read_version()
    assert version != "unknown"
    assert re.match(r"^\d+\.\d+", version), version


def test_version_flag_prints_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--version"])

    assert excinfo.value.code == 0
    assert re.match(r"^depp \d+\.\d+", capsys.readouterr().out)
