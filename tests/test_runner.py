"""Tests for the Ansible playbook subprocess runner."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

import depp
from depp.ansible_common import runner


def test_runner_invokes_only_ansible_playbook(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []\n")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_ANSIBLE_PLAYBOOK", "ansible-playbook")

    result = runner.run_ansible_playbook(
        playbook_path=playbook,
        inventory={"all": {"hosts": {"example.com": {}}}},
        extra_vars={},
    )

    assert result == 0
    assert len(calls) == 1
    assert calls[0][0][0] == "ansible-playbook"


def test_extra_vars_use_private_inventory_not_process_arguments(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []\n")
    inventory = {"all": {"hosts": {"example.com": {"app_name": "example"}}}}
    secret = "do-not-expose"

    def fake_run(command, **_kwargs):
        inventory_path = command[command.index("-i") + 1]
        inventory_mode = os.stat(inventory_path).st_mode & 0o777
        inventory_data = yaml.safe_load(
            Path(inventory_path).read_text(encoding="utf-8")
        )

        assert secret not in " ".join(command)
        assert command[command.index("-e") + 1] == '{"app_version": "abc123"}'
        assert inventory_mode == 0o600
        assert inventory_data["all"]["vars"]["configmap_yaml"] == secret
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_ANSIBLE_PLAYBOOK", "ansible-playbook")

    result = runner.run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={"app_version": "abc123"},
        private_vars={"configmap_yaml": secret},
    )

    assert result == 0
    assert "vars" not in inventory["all"]


def test_configmap_copy_suppresses_sensitive_output():
    playbook_path = Path(depp.__file__).parent / "ansible_deploy" / "deploy.yml"
    play = yaml.safe_load(playbook_path.read_text())[0]
    remote_block = next(
        task["block"]
        for task in play["tasks"]
        if task.get("name", "").startswith("Remote host tasks")
    )
    configmap_task = next(
        task
        for task in remote_block
        if task.get("name") == "Copy ConfigMap to remote config directory"
    )

    assert configmap_task["no_log"] is True


@pytest.mark.parametrize(
    ("policy", "expected_option", "expected_checking"),
    [
        ("strict", "StrictHostKeyChecking=yes", "True"),
        ("accept-new", "StrictHostKeyChecking=accept-new", "True"),
        ("insecure", "StrictHostKeyChecking=no", "False"),
    ],
)
def test_ansible_uses_host_key_policy(
    tmp_path, monkeypatch, policy, expected_option, expected_checking
):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []\n")

    def fake_run(command, **kwargs):
        inventory_path = command[command.index("-i") + 1]
        inventory_data = yaml.safe_load(
            Path(inventory_path).read_text(encoding="utf-8")
        )
        ssh_args = inventory_data["all"]["vars"]["ansible_ssh_common_args"]

        assert expected_option in ssh_args
        assert kwargs["env"]["ANSIBLE_HOST_KEY_CHECKING"] == expected_checking
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_ANSIBLE_PLAYBOOK", "ansible-playbook")

    result = runner.run_ansible_playbook(
        playbook_path=playbook,
        inventory={"all": {"hosts": {"example.com": {}}}},
        extra_vars={},
        host_key_policy=policy,
    )

    assert result == 0


def test_local_connection_omits_ssh_policy(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []\n")
    monkeypatch.setenv("ANSIBLE_HOST_KEY_CHECKING", "False")

    def fake_run(command, **kwargs):
        inventory_path = command[command.index("-i") + 1]
        inventory_data = yaml.safe_load(
            Path(inventory_path).read_text(encoding="utf-8")
        )

        assert "ansible_ssh_common_args" not in inventory_data["all"]["vars"]
        assert "ANSIBLE_HOST_KEY_CHECKING" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_ANSIBLE_PLAYBOOK", "ansible-playbook")

    result = runner.run_ansible_playbook(
        playbook_path=playbook,
        inventory={"all": {"hosts": {"example.com": {"ansible_connection": "local"}}}},
        extra_vars={},
    )

    assert result == 0


def test_ansible_config_is_pinned_to_the_bundled_file(tmp_path, monkeypatch):
    """depp runs inside a project dir, where Ansible would auto-discover
    ./ansible.cfg — a file that can load callback plugins, i.e. run arbitrary
    code from a cloned repo. The config must be pinned to depp's own."""
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []\n")

    captured = {}

    def fake_run(command, **kwargs):
        captured["config"] = kwargs["env"]["ANSIBLE_CONFIG"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_ANSIBLE_PLAYBOOK", "ansible-playbook")

    runner.run_ansible_playbook(
        playbook_path=playbook,
        inventory={"all": {"hosts": {"example.com": {}}}},
        extra_vars={},
    )

    config_path = Path(captured["config"])
    assert config_path == runner._ANSIBLE_CONFIG_PATH
    # Ansible rejects a config file whose extension is not .cfg/.ini, so the
    # bundled file must actually ship with the package.
    assert config_path.is_file()
    assert config_path.suffix == ".cfg"
