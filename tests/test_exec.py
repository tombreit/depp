"""Tests for the ``depp exec`` command builder in depp/console/exec.py."""

import importlib
import shlex
from argparse import Namespace

import pytest

from depp import cli
from depp.console.exec import build_exec_command

exec_module = importlib.import_module("depp.console.exec")

FQDN = "app.example.com"
CONTAINER = "myapp-app"


def build(**overrides):
    kwargs = {
        "fqdn": FQDN,
        "container_name": CONTAINER,
        "command": ["python", "manage.py", "createsuperuser"],
        "allocate_tty": False,
        "host_only": False,
    }
    kwargs.update(overrides)
    return build_exec_command(**kwargs)


def remote_part(argv):
    """The single string sshd hands to the remote shell (last argv element)."""
    return argv[-1]


def test_tty_allocated_on_both_hops():
    argv = build(allocate_tty=True)

    assert "-tt" in argv
    # -tt must be an ssh option, i.e. before the user@host target
    assert argv.index("-tt") < argv.index(f"{FQDN}@{FQDN}")
    assert shlex.split(remote_part(argv))[:4] == ["podman", "exec", "-i", "-t"]


def test_no_tty_keeps_stdin_only():
    argv = build(allocate_tty=False)

    assert "-tt" not in argv
    assert "-t" not in argv
    remote = shlex.split(remote_part(argv))
    assert remote[:3] == ["podman", "exec", "-i"]
    assert "-t" not in remote


def test_container_and_command_are_passed_through():
    argv = build(allocate_tty=False)

    assert shlex.split(remote_part(argv)) == [
        "podman",
        "exec",
        "-i",
        CONTAINER,
        "python",
        "manage.py",
        "createsuperuser",
    ]


def test_empty_command_falls_back_to_shell():
    argv = build(command=[], allocate_tty=True)

    assert shlex.split(remote_part(argv))[-1] == "/bin/sh"


def test_host_only_with_command_skips_podman():
    argv = build(command=["systemctl", "--user", "status"], host_only=True)

    assert "podman" not in remote_part(argv)
    assert shlex.split(remote_part(argv)) == ["systemctl", "--user", "status"]


def test_host_only_can_allocate_tty():
    argv = build(command=["sudo", "true"], host_only=True, allocate_tty=True)

    assert "-tt" in argv


def test_host_only_without_command_is_a_login_shell():
    argv = build(command=[], host_only=True, allocate_tty=True)

    # Nothing after the ssh target — sshd opens the login shell itself.
    assert argv[-1] == f"{FQDN}@{FQDN}"


def test_arguments_with_spaces_and_quotes_survive_the_ssh_hop():
    command = ["python", "-c", "print('hello world')", "--msg=a b"]
    argv = build(command=command)

    # The remote shell re-splits the string; it must yield the original argv
    # after the `podman exec -i <container>` prefix.
    assert shlex.split(remote_part(argv))[4:] == command


def test_ssh_target_uses_fqdn_as_user_and_host():
    argv = build()

    assert f"{FQDN}@{FQDN}" in argv
    assert argv[0] == "ssh"
    assert "StrictHostKeyChecking=yes" in argv


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("strict", "StrictHostKeyChecking=yes"),
        ("accept-new", "StrictHostKeyChecking=accept-new"),
        ("insecure", "StrictHostKeyChecking=no"),
    ],
)
def test_host_key_policy(policy, expected):
    argv = build(host_key_policy=policy)

    assert expected in argv
    assert ("UserKnownHostsFile=/dev/null" in argv) is (policy == "insecure")


def test_local_container_command_skips_ssh():
    argv = build(connection="local", command=["echo", "hello world"])

    assert argv == ["podman", "exec", "-i", CONTAINER, "echo", "hello world"]


def test_local_container_shell_supports_tty():
    argv = build(connection="local", command=[], allocate_tty=True)

    assert argv == ["podman", "exec", "-i", "-t", CONTAINER, "/bin/sh"]


def test_local_host_command_runs_directly():
    argv = build(
        connection="local", host_only=True, command=["systemctl", "--user", "status"]
    )

    assert argv == ["systemctl", "--user", "status"]


def test_local_host_shell_uses_shell_environment(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")

    assert build(connection="local", host_only=True, command=[]) == ["/bin/zsh"]


def test_cli_forwards_local_connection_to_exec(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (tmp_path / "depp.toml", {}, "example.com", "example"),
    )

    def fake_run_exec(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(exec_module, "run_exec", fake_run_exec)

    result = cli.run_exec_command(
        Namespace(
            exec_command=["echo", "ok"],
            host=True,
            container=None,
            tty=False,
            host_key_policy="strict",
            connection="local",
        )
    )

    assert result == 0
    assert captured["connection"] == "local"
