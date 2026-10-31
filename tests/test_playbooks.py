"""Run real Ansible, which every other test mocks.

The playbooks are otherwise only exercised against a real host. These tests
notice a syntax error, such as one in reset.yml, which deletes volume contents,
and they notice when Ansible cannot start at all, for example on a Python
version it does not support. The others check that depp starts the Ansible and
the collections installed with it, whatever else is on the machine.
"""

import json
import subprocess
from pathlib import Path

import pytest

import depp
from depp.ansible_common.runner import _ANSIBLE_PLAYBOOK, ANSIBLE_DOC, ansible_env

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


@pytest.mark.parametrize("name", ["ansible.py", "yaml.py"])
def test_project_files_are_not_imported(tmp_path, name):
    # depp runs inside the project directory, which may be a cloned repo.
    (tmp_path / name).write_text("raise SystemExit('project code ran')\n")

    result = run_ansible([*_ANSIBLE_PLAYBOOK, "--version"], cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
