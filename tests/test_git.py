"""Tests for deployment Git provenance."""

from types import SimpleNamespace

import pytest

from depp import cli


def test_get_git_info_uses_full_commit_sha(tmp_path, monkeypatch):
    calls = []
    outputs = iter(["a" * 40, "Release message"])

    def fake_check_output(command, **_kwargs):
        calls.append(command)
        return next(outputs)

    monkeypatch.setattr(cli.subprocess, "check_output", fake_check_output)

    commit_hash, message = cli.get_git_info(tmp_path)

    assert commit_hash == "a" * 40
    assert message == "Release message"
    assert "--short" not in calls[0]


def test_get_git_info_reports_missing_executable(tmp_path, monkeypatch, capsys):
    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(cli.subprocess, "check_output", missing_git)

    with pytest.raises(SystemExit, match="1"):
        cli.get_git_info(tmp_path)

    assert "git executable not found" in capsys.readouterr().err


def test_clean_deployment_version_is_full_commit():
    commit_hash = "a" * 40

    assert cli.deployment_version(commit_hash, git_dirty=False) == commit_hash


def test_dirty_deployment_version_is_unique(monkeypatch):
    monkeypatch.setattr(
        cli.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="1234567890abcdef"),
    )

    assert cli.deployment_version("a" * 40, git_dirty=True) == (
        f"{'a' * 40}-dirty-1234567890ab"
    )
