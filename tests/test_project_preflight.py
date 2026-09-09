"""Tests for local project deployment preflight."""

import pytest

from depp.ansible_common.inventory import DeppConfig
from depp.cli import resolve_containerfile


def config(tmp_path, containerfile="Containerfile"):
    return DeppConfig.from_mapping(
        tmp_path / "depp.toml",
        {
            "app": {
                "name": "example",
                "project_root": ".",
                "containerfile": containerfile,
            },
            "host": {"fqdn": "example.com", "caddy_host_port": 8100},
            "acme": {"contact_email": "ops@example.com"},
        },
    )


def test_containerfile_defaults_to_project_root(tmp_path):
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text("FROM scratch\n", encoding="utf-8")

    assert resolve_containerfile(tmp_path, config(tmp_path)) == containerfile


def test_containerfile_allows_nested_relative_path(tmp_path):
    build_dir = tmp_path / "containers"
    build_dir.mkdir()
    containerfile = build_dir / "web.Containerfile"
    containerfile.write_text("FROM scratch\n", encoding="utf-8")

    assert (
        resolve_containerfile(
            tmp_path, config(tmp_path, "containers/web.Containerfile")
        )
        == containerfile
    )


@pytest.mark.parametrize("configured", ["", "   ", 42, True])
def test_containerfile_rejects_invalid_value(tmp_path, configured):
    with pytest.raises(ValueError, match="containerfile"):
        config(tmp_path, configured)


def test_containerfile_rejects_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="relative to project_root"):
        resolve_containerfile(
            tmp_path, config(tmp_path, str(tmp_path / "Containerfile"))
        )


def test_containerfile_rejects_path_outside_project(tmp_path):
    outside = tmp_path.parent / "Containerfile"
    outside.write_text("FROM scratch\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside project_root"):
        resolve_containerfile(tmp_path, config(tmp_path, "../Containerfile"))


def test_containerfile_rejects_symlink_outside_project(tmp_path):
    outside = tmp_path.parent / "outside.Containerfile"
    outside.write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "Containerfile").symlink_to(outside)

    with pytest.raises(ValueError, match="inside project_root"):
        resolve_containerfile(tmp_path, config(tmp_path))


def test_containerfile_must_exist(tmp_path):
    with pytest.raises(ValueError, match="Containerfile not found"):
        resolve_containerfile(tmp_path, config(tmp_path))
