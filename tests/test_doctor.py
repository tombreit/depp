"""Tests for depp doctor checks and orchestration."""

import subprocess
from argparse import Namespace
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace

from depp import cli, doctor
from depp.ansible_common import runner
from depp.ansible_common.inventory import DeppConfig


def make_config(tmp_path):
    return DeppConfig.from_mapping(
        tmp_path / "depp.toml",
        {
            "app": {"name": "example", "project_root": "."},
            "host": {"fqdn": "example.com", "loopback_port": 8100},
            "acme": {"contact_email": "ops@example.com"},
        },
    )


def test_control_node_checks_include_ssh_only_for_ssh(monkeypatch):
    looked_up = []

    def fake_which(name):
        looked_up.append(name)
        return f"/usr/bin/{name}"

    monkeypatch.setattr(doctor.shutil, "which", fake_which)
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )

    checks = doctor.control_node_checks(connection="ssh")

    assert "ssh" in looked_up
    assert all(check.ok for check in checks)
    assert any(check.name == "containers.podman.podman_system_info" for check in checks)


def test_control_node_checks_report_missing_executable(monkeypatch):
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda name: None if name == "rsync" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )

    checks = doctor.control_node_checks(connection="local")

    assert not next(check for check in checks if check.name == "rsync").ok


def test_control_node_checks_query_the_ansible_a_deploy_uses(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    checks = doctor.control_node_checks(connection="local")

    assert not {"ansible-playbook", "ansible-doc"} & {check.name for check in checks}
    assert calls
    for command, kwargs in calls:
        assert command[: len(runner.ANSIBLE_DOC)] == runner.ANSIBLE_DOC
        assert kwargs["env"]["ANSIBLE_COLLECTIONS_PATH"]


def test_control_node_checks_skip_modules_without_ansible(monkeypatch):
    def missing(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(doctor, "version", missing)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: calls.append(a))

    checks = doctor.control_node_checks(connection="local")

    assert not next(check for check in checks if check.name == "Ansible package").ok
    assert not calls


def doctor_args(tmp_path, *, allow_dirty=False):
    return Namespace(
        toml_file=tmp_path / "depp.toml",
        connection="ssh",
        host_key_policy="strict",
        verbose=False,
        allow_dirty=allow_dirty,
    )


def prepare_doctor(monkeypatch, tmp_path, *, checks=None, dirty=False):
    config = make_config(tmp_path)
    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (config.source_path, config, "example.com", "example"),
    )
    monkeypatch.setattr(
        cli,
        "validate_project_inputs",
        lambda _config: (
            tmp_path / "Containerfile",
            tmp_path / "deploy/kube.yaml",
            tmp_path / "deploy/.env",
            SimpleNamespace(pod_name="example"),
            None,
        ),
    )
    monkeypatch.setattr(cli, "get_git_info", lambda _root: ("a" * 40, "Test"))
    monkeypatch.setattr(cli, "check_git_dirty", lambda _root: dirty)
    monkeypatch.setattr(
        cli,
        "control_node_checks",
        lambda **_kwargs: (
            checks
            if checks is not None
            else [doctor.DoctorCheck("git", True, "/usr/bin/git")]
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_inventory_or_exit",
        lambda *_args: {"all": {"hosts": {"example.com": {}}}},
    )


def test_doctor_runs_target_checks_after_local_success(tmp_path, monkeypatch):
    prepare_doctor(monkeypatch, tmp_path)
    captured = {}

    def fake_run_ansible_playbook(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_ansible_playbook", fake_run_ansible_playbook)

    assert cli.run_doctor(doctor_args(tmp_path)) == 0
    assert captured["playbook_path"].name == "doctor.yml"
    assert captured["host_key_policy"] == "strict"


def test_doctor_stops_before_target_when_requirement_missing(tmp_path, monkeypatch):
    prepare_doctor(
        monkeypatch,
        tmp_path,
        checks=[doctor.DoctorCheck("rsync", False, "not found in PATH")],
    )
    monkeypatch.setattr(
        cli,
        "run_ansible_playbook",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("target check ran")),
    )

    assert cli.run_doctor(doctor_args(tmp_path)) == cli.EXIT_ERROR


def test_doctor_rejects_dirty_tree_unless_allowed(tmp_path, monkeypatch):
    prepare_doctor(monkeypatch, tmp_path, dirty=True)
    monkeypatch.setattr(cli, "run_ansible_playbook", lambda **_kwargs: 0)

    assert cli.run_doctor(doctor_args(tmp_path)) == cli.EXIT_ERROR
    assert cli.run_doctor(doctor_args(tmp_path, allow_dirty=True)) == 0


def test_doctor_rejects_permissive_env_file(tmp_path, monkeypatch):
    prepare_doctor(monkeypatch, tmp_path)
    env_path = tmp_path / "deploy/.env"
    env_path.parent.mkdir()
    env_path.write_text("KEY=value\n")
    env_path.chmod(0o644)
    monkeypatch.setattr(
        cli,
        "validate_project_inputs",
        lambda _config: (
            tmp_path / "Containerfile",
            tmp_path / "deploy/kube.yaml",
            env_path,
            SimpleNamespace(pod_name="example"),
            "configmap",
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_ansible_playbook",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("target check ran")),
    )

    assert cli.run_doctor(doctor_args(tmp_path)) == cli.EXIT_ERROR
