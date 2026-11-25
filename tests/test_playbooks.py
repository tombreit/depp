"""Run real Ansible, which every other test mocks.

The playbooks are otherwise only exercised against a real host. These tests
notice a syntax error, such as one in reset.yml, which deletes volume contents,
and they notice when Ansible cannot start at all, for example on a Python
version it does not support. The others check that depp starts the Ansible and
the collections installed with it, whatever else is on the machine, and that
project data in the inventory reaches a task verbatim instead of rendered.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import depp
from depp.ansible_common.runner import (
    _ANSIBLE_PLAYBOOK,
    ANSIBLE_DOC,
    Unsafe,
    ansible_env,
    run_ansible_playbook,
)

PLAYBOOKS = sorted(Path(depp.__file__).parent.glob("ansible_*/*.yml"))
BUNDLED_MODULE = "containers.podman.podman_image"


def run_ansible(command, **kwargs):
    return subprocess.run(
        command,
        env=ansible_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        **kwargs,
    )


def test_playbooks_are_found():
    # An empty glob would otherwise turn the test below into a silent no-op.
    assert PLAYBOOKS


@pytest.mark.parametrize(
    "playbook", PLAYBOOKS, ids=lambda path: f"{path.parent.name}/{path.name}"
)
def test_playbook_syntax(playbook):
    result = run_ansible([*_ANSIBLE_PLAYBOOK, "--syntax-check", playbook])

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("variable", ["ANSIBLE_HOME", "ANSIBLE_COLLECTIONS_PATH"])
def test_other_collections_do_not_shadow_the_bundled_ones(
    tmp_path, monkeypatch, variable
):
    monkeypatch.setenv(variable, str(tmp_path))
    # ANSIBLE_HOME is searched as <home>/collections, the other as given.
    root = tmp_path / "collections" if variable == "ANSIBLE_HOME" else tmp_path
    stub = root / "ansible_collections/containers/podman/plugins/modules"
    stub.mkdir(parents=True)
    (stub / "podman_image.py").write_text(
        "DOCUMENTATION = '''\n"
        "module: podman_image\n"
        "short_description: stub\n"
        "description: stub\n"
        "author: stub\n"
        "'''\n"
    )

    result = run_ansible([*ANSIBLE_DOC, "--json", BUNDLED_MODULE])

    assert result.returncode == 0, result.stdout + result.stderr
    filename = json.loads(result.stdout)[BUNDLED_MODULE]["doc"]["filename"]
    assert not filename.startswith(str(tmp_path))


def test_unsafe_values_reach_the_task_verbatim(tmp_path):
    # Without !unsafe, Ansible renders the value: {{ 6 * 7 }} became 42, the
    # comment vanished, and a lookup would run on the control node.
    value = "x={{ 6 * 7 }} y={# gone #} z={% raw %}"
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(
        """\
---
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        content: "{{ host_value }}"
        dest: "{{ out_dir }}/host"
        mode: "0600"
    - ansible.builtin.copy:
        content: "{{ private_value }}"
        dest: "{{ out_dir }}/private"
        mode: "0600"
"""
    )
    inventory = {
        "all": {
            "hosts": {
                "localhost": {
                    "ansible_connection": "local",
                    "ansible_python_interpreter": sys.executable,
                    "host_value": Unsafe(value),
                }
            }
        }
    }

    result = run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={"out_dir": str(tmp_path)},
        private_vars={"private_value": Unsafe(value)},
    )

    assert result == 0
    assert (tmp_path / "host").read_text() == value
    assert (tmp_path / "private").read_text() == value


@pytest.mark.parametrize("name", ["ansible.py", "yaml.py"])
def test_project_files_are_not_imported(tmp_path, name):
    # depp runs inside the project directory, which may be a cloned repo.
    (tmp_path / name).write_text("raise SystemExit('project code ran')\n")

    result = run_ansible([*_ANSIBLE_PLAYBOOK, "--version"], cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
