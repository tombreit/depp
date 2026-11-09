"""Tests for deployment orchestration."""

from argparse import Namespace
from pathlib import Path

import pytest
import yaml

import depp
from depp import cli
from depp.ansible_common.inventory import DeppConfig

DEPLOY_MANIFEST = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": "example"},
    "spec": {
        "containers": [
            {
                "name": "app",
                "image": "localhost/example:latest",
                "envFrom": [{"configMapRef": {"name": "example-config"}}],
            }
        ]
    },
}


def write_project(project_root, env_contents):
    deploy_dir = project_root / "deploy"
    deploy_dir.mkdir(parents=True)
    (project_root / "Containerfile").write_text("FROM scratch\n")
    (deploy_dir / "kube.yaml").write_text(yaml.safe_dump(DEPLOY_MANIFEST))
    (deploy_dir / ".env").write_text(env_contents)


def make_config(toml_path, data):
    merged = {
        "host": {"fqdn": "example.com", "loopback_port": 8100},
        "acme": {"contact_email": "ops@example.com"},
        **data,
    }
    return DeppConfig.from_mapping(toml_path, merged)


def test_deployment_routes_configmap_through_private_vars(
    tmp_path, monkeypatch, capsys
):
    project_root = tmp_path / "project"
    write_project(project_root, "SECRET_KEY=do-not-expose\n")

    toml_path = tmp_path / "depp.toml"
    toml_data = {
        "app": {
            "name": "example",
            "image_name": "example",
            "project_root": str(project_root),
        },
        "host": {"fqdn": "example.com", "loopback_port": 8100},
    }
    inventory = {"all": {"hosts": {"example.com": {"health_path": "/"}}}}
    captured = {}

    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (
            toml_path,
            make_config(toml_path, toml_data),
            "example.com",
            "example",
        ),
    )
    monkeypatch.setattr(cli, "get_git_info", lambda _root: ("abc123", "Test"))
    monkeypatch.setattr(cli, "check_git_dirty", lambda _root: False)
    monkeypatch.setattr(cli, "build_inventory_or_exit", lambda *_args: inventory)

    def fake_run_ansible_playbook(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_ansible_playbook", fake_run_ansible_playbook)

    result = cli.run_deployment(
        Namespace(
            toml_file=toml_path,
            check=True,
            yes=True,
            verbose=False,
            host_key_policy="strict",
            connection="ssh",
            allow_dirty=False,
        )
    )

    assert result == 0
    assert "configmap_yaml" not in captured["extra_vars"]
    assert captured["extra_vars"]["local_kube_file"] == str(
        project_root / "deploy" / "kube.yaml"
    )
    assert "do-not-expose" in captured["private_vars"]["configmap_yaml"]
    assert captured["host_key_policy"] == "strict"
    assert "chmod 600" in capsys.readouterr().err


@pytest.mark.parametrize("dirty", [False, True])
def test_deployment_passes_commit_and_image_version(tmp_path, monkeypatch, dirty):
    commit = "a" * 40
    project_root = tmp_path / "project"
    write_project(project_root, "SECRET_KEY=irrelevant\n")

    toml_path = tmp_path / "depp.toml"
    toml_data = {
        "app": {
            "name": "example",
            "image_name": "example",
            "project_root": str(project_root),
        },
    }
    inventory = {"all": {"hosts": {"example.com": {"health_path": "/"}}}}
    captured = {}

    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (
            toml_path,
            make_config(toml_path, toml_data),
            "example.com",
            "example",
        ),
    )
    monkeypatch.setattr(cli, "get_git_info", lambda _root: (commit, "Test"))
    monkeypatch.setattr(cli, "check_git_dirty", lambda _root: dirty)
    monkeypatch.setattr(cli, "build_inventory_or_exit", lambda *_args: inventory)

    def fake_run_ansible_playbook(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_ansible_playbook", fake_run_ansible_playbook)

    result = cli.run_deployment(
        Namespace(
            toml_file=toml_path,
            check=True,
            yes=True,
            verbose=False,
            host_key_policy="strict",
            connection="ssh",
            allow_dirty=dirty,
        )
    )

    assert result == 0
    # The commit stays a bare hash even when dirty; the version says so instead.
    assert captured["extra_vars"]["app_commit"] == commit
    if dirty:
        assert captured["extra_vars"]["app_version"].startswith(f"{commit}-dirty-")
    else:
        assert captured["extra_vars"]["app_version"] == commit


def test_image_build_receives_version_and_commit():
    playbook = Path(depp.__file__).parent / "ansible_deploy" / "deploy.yml"
    tasks = yaml.safe_load(playbook.read_text())[0]["tasks"]
    (build,) = [
        task["containers.podman.podman_image"]
        for block in tasks
        for task in block.get("block", [])
        if "containers.podman.podman_image" in task
    ]

    extra_args = build["build"]["extra_args"]
    assert "--build-arg VERSION={{ app_version }}" in extra_args
    assert "--build-arg COMMIT={{ app_commit }}" in extra_args


def test_check_mode_does_not_tag_the_unbuilt_image():
    # Check mode builds and loads nothing; podman_tag fails on a missing
    # source image, so `depp deploy --check` must skip the tag.
    playbook = Path(depp.__file__).parent / "ansible_deploy" / "deploy.yml"
    tasks = yaml.safe_load(playbook.read_text())[0]["tasks"]
    (tag,) = [
        task
        for block in tasks
        for task in block.get("block", [])
        if "containers.podman.podman_tag" in task
    ]

    when = tag.get("when", [])
    conditions = [when] if isinstance(when, str) else when
    assert "not ansible_check_mode" in conditions


def test_dirty_deployment_requires_explicit_override(tmp_path, monkeypatch, capsys):
    toml_path = tmp_path / "depp.toml"
    toml_data = {"app": {"name": "example", "project_root": str(tmp_path)}}

    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (
            toml_path,
            make_config(toml_path, toml_data),
            "example.com",
            "example",
        ),
    )
    monkeypatch.setattr(cli, "get_git_info", lambda _root: ("a" * 40, "Test"))
    monkeypatch.setattr(cli, "check_git_dirty", lambda _root: True)

    result = cli.run_deployment(
        Namespace(
            toml_file=toml_path,
            check=False,
            yes=True,
            verbose=False,
            host_key_policy="strict",
            connection="ssh",
            allow_dirty=False,
        )
    )

    assert result == 1
    assert "--allow-dirty" in capsys.readouterr().err


def test_invalid_env_fails_before_allocating_archive(tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "project"
    write_project(project_root, "INVALID LINE\n")

    toml_path = tmp_path / "depp.toml"
    toml_data = {
        "app": {"name": "example", "project_root": str(project_root)},
        "host": {"fqdn": "example.com", "loopback_port": 8100},
    }

    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (
            toml_path,
            make_config(toml_path, toml_data),
            "example.com",
            "example",
        ),
    )
    monkeypatch.setattr(cli, "get_git_info", lambda _root: ("a" * 40, "Test"))
    monkeypatch.setattr(cli, "check_git_dirty", lambda _root: False)
    monkeypatch.setattr(
        cli,
        "build_inventory_or_exit",
        lambda *_args: {"all": {"hosts": {"example.com": {}}}},
    )
    monkeypatch.setattr(
        cli.tempfile,
        "mkdtemp",
        lambda **_kwargs: pytest.fail("archive allocated before env validation"),
    )

    result = cli.run_deployment(
        Namespace(
            toml_file=toml_path,
            check=False,
            yes=True,
            verbose=False,
            host_key_policy="strict",
            connection="ssh",
            allow_dirty=False,
        )
    )

    assert result == 1
    assert "deploy/.env" in capsys.readouterr().err


def test_missing_containerfile_is_clean_deployment_error(tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "project"
    deploy_dir = project_root / "deploy"
    deploy_dir.mkdir(parents=True)
    (deploy_dir / "kube.yaml").write_text(yaml.safe_dump(DEPLOY_MANIFEST))
    (deploy_dir / ".env").write_text("KEY=value\n")
    toml_path = tmp_path / "depp.toml"
    toml_data = {
        "app": {"name": "example", "project_root": str(project_root)},
        "host": {"fqdn": "example.com", "loopback_port": 8100},
    }

    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (
            toml_path,
            make_config(toml_path, toml_data),
            "example.com",
            "example",
        ),
    )
    monkeypatch.setattr(cli, "get_git_info", lambda _root: ("a" * 40, "Test"))
    monkeypatch.setattr(cli, "check_git_dirty", lambda _root: False)
    monkeypatch.setattr(
        cli,
        "build_inventory_or_exit",
        lambda *_args: {"all": {"hosts": {"example.com": {}}}},
    )

    result = cli.run_deployment(
        Namespace(
            toml_file=toml_path,
            check=True,
            yes=True,
            verbose=False,
            host_key_policy="strict",
            connection="ssh",
            allow_dirty=False,
        )
    )

    assert result == cli.EXIT_ERROR
    assert "Containerfile not found" in capsys.readouterr().err


def test_deploy_dir_layout_resolves_files_next_to_the_toml(
    tmp_path, monkeypatch, capsys
):
    """deploy/depp.toml with kube.yaml and .env beside it, no project_root."""
    project_root = tmp_path / "project"
    write_project(project_root, "KEY=value\n")
    toml_path = project_root / "deploy" / "depp.toml"
    captured = {}

    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (
            toml_path,
            make_config(toml_path, {"app": {"name": "example"}}),
            "example.com",
            "example",
        ),
    )
    monkeypatch.setattr(cli, "get_git_info", lambda _root: ("a" * 40, "Test"))
    monkeypatch.setattr(cli, "check_git_dirty", lambda _root: False)
    monkeypatch.setattr(
        cli,
        "build_inventory_or_exit",
        lambda *_args: {"all": {"hosts": {"example.com": {}}}},
    )

    def fake_run_ansible_playbook(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_ansible_playbook", fake_run_ansible_playbook)

    result = cli.run_deployment(
        Namespace(
            toml_file=toml_path,
            check=True,
            yes=True,
            verbose=False,
            host_key_policy="strict",
            connection="ssh",
            allow_dirty=False,
        )
    )

    assert result == 0
    assert captured["extra_vars"]["local_repo_path"] == str(project_root.resolve())
    assert captured["extra_vars"]["local_kube_file"] == str(
        (project_root / "deploy" / "kube.yaml").resolve()
    )
    out = capsys.readouterr().out
    assert "Env file:   deploy/.env" in out
    assert "Kube:       deploy/kube.yaml" in out
