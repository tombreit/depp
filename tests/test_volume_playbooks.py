"""The volume playbooks must work for a container that runs as a non-root uid.

Rootless podman maps that uid to a subuid of the deploy user, so anything the
container writes with mode 0600/0700 is unreadable from outside the user
namespace. Every read, write and delete of volume contents therefore has to go
through `podman unshare`. These tests pin that down structurally; the CI
syntax check and ansible-lint cover the rest.
"""

from pathlib import Path

import pytest
import yaml

import depp

PLAYBOOKS = Path(depp.__file__).parent / "ansible_backup"
UNSHARE_RSYNC = "podman unshare rsync"


def _tasks(playbook: str) -> list[dict]:
    play = yaml.safe_load((PLAYBOOKS / playbook).read_text())[0]
    return play["tasks"]


def _module_tasks(playbook: str, module: str) -> list[dict]:
    return [task for task in _tasks(playbook) if module in task]


@pytest.mark.parametrize("playbook", ["backup.yml", "restore.yml"])
def test_rsync_runs_inside_the_user_namespace(playbook):
    tasks = _module_tasks(playbook, "ansible.posix.synchronize")

    assert tasks, f"{playbook} has no synchronize task"
    for task in tasks:
        module = task["ansible.posix.synchronize"]
        assert module["rsync_path"] == UNSHARE_RSYNC
        # synchronize passes --archive itself; a second copy is noise.
        assert "--archive" not in module.get("rsync_opts", [])


def test_restore_sets_ownership_to_the_volume_owner():
    (task,) = _module_tasks("restore.yml", "ansible.posix.synchronize")
    opts = task["ansible.posix.synchronize"]["rsync_opts"]
    chown = [opt for opt in opts if opt.startswith("--chown=")]

    assert len(chown) == 1
    assert "item.volumes[0].UID | default(0)" in chown[0]
    assert "item.volumes[0].GID | default(0)" in chown[0]


def test_reset_lists_and_deletes_inside_the_user_namespace():
    tasks = _tasks("reset.yml")

    # The find/file modules run as the plain deploy user and cannot enter a
    # subuid-owned directory; nothing may use them any more.
    assert not _module_tasks("reset.yml", "ansible.builtin.find")
    assert not _module_tasks("reset.yml", "ansible.builtin.file")

    commands = _module_tasks("reset.yml", "ansible.builtin.command")
    assert len(commands) == 2
    for task in commands:
        argv = task["ansible.builtin.command"]["argv"]
        assert argv[:3] == ["podman", "unshare", "find"]
        assert argv[4:6] == ["-mindepth", "1"]

    listing, deletion = commands
    assert "-delete" not in listing["ansible.builtin.command"]["argv"]
    assert listing["changed_when"] is False
    assert listing.get("when") is None

    assert deletion["ansible.builtin.command"]["argv"][-1] == "-delete"
    assert deletion["changed_when"] is True
    assert deletion["when"] == "do_delete | bool"

    # The listing is what the operator sees before confirming the deletion.
    display = next(
        task
        for task in tasks
        if task.get("name") == "Display volume contents as container-internal paths"
    )
    assert "item.stdout_lines" in display["ansible.builtin.debug"]["msg"]
