"""Tests for the project layout rules in depp/layout.py."""

from pathlib import Path

from depp import layout
from depp.ansible_common.inventory import DeppConfig


def base_mapping(**app):
    return {
        "app": {"name": "example", **app},
        "host": {"fqdn": "example.com", "loopback_port": 8100},
        "acme": {"contact_email": "ops@example.com"},
    }


def test_default_project_root_is_parent_for_deploy_dir_layout(tmp_path):
    toml = tmp_path / "project" / "deploy" / "depp.toml"
    toml.parent.mkdir(parents=True)

    assert layout.default_project_root(toml) == (tmp_path / "project").resolve()


def test_default_project_root_is_toml_dir_otherwise(tmp_path):
    toml = tmp_path / "project" / "depp.toml"
    toml.parent.mkdir(parents=True)

    assert layout.default_project_root(toml) == (tmp_path / "project").resolve()


def test_deploy_dir_is_toml_dir_in_deploy_layout(tmp_path):
    project = tmp_path / "project"
    (project / "deploy").mkdir(parents=True)
    config = DeppConfig.from_mapping(project / "deploy" / "depp.toml", base_mapping())

    assert config.app.project_root == project.resolve()
    assert config.deploy_dir == (project / "deploy").resolve()
    assert config.kube_manifest == config.deploy_dir / "kube.yaml"
    assert config.env_file == config.deploy_dir / ".env"
    assert config.vhost_snippet == config.deploy_dir / "vhost.conf"


def test_deploy_dir_is_project_root_deploy_for_root_toml(tmp_path):
    """The 0.0.1 layout: depp.toml at the project root, files in deploy/."""
    project = tmp_path / "project"
    project.mkdir()
    config = DeppConfig.from_mapping(
        project / "depp.toml", base_mapping(project_root=".")
    )

    assert config.deploy_dir == (project / "deploy").resolve()


def test_separate_config_repo_keeps_project_root_deploy(tmp_path):
    """A toml outside the project still finds the files in the project."""
    src = tmp_path / "src" / "app"
    cfg = tmp_path / "cfg" / "app"
    src.mkdir(parents=True)
    cfg.mkdir(parents=True)
    config = DeppConfig.from_mapping(
        cfg / "depp.toml", base_mapping(project_root=str(src))
    )

    assert config.app.project_root == src.resolve()
    assert config.deploy_dir == (src / "deploy").resolve()


def test_find_default_toml_prefers_deploy_dir(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "depp.toml").write_text("")
    (tmp_path / "depp.toml").write_text("")

    assert layout.find_default_toml(tmp_path) == tmp_path / "deploy" / "depp.toml"

    (tmp_path / "deploy" / "depp.toml").unlink()
    assert layout.find_default_toml(tmp_path) == tmp_path / "depp.toml"


def test_find_default_toml_none(tmp_path):
    assert layout.find_default_toml(tmp_path) is None


def test_display_path_inside_and_outside_root(tmp_path):
    root = tmp_path / "project"
    assert layout.display_path(root / "deploy" / ".env", root) == "deploy/.env"
    outside = Path("/elsewhere/deploy/.env")
    assert layout.display_path(outside, root) == str(outside)
