"""Direct SSH execution for ad-hoc commands on remote hosts and containers."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from depp.ansible_common.inventory import DEPP_SSH_KEY_PATH
from depp.ansible_common.ssh import host_key_options


def build_exec_command(
    fqdn: str,
    container_name: str,
    command: list[str],
    allocate_tty: bool,
    host_only: bool = False,
    host_key_policy: str = "strict",
    connection: str = "ssh",
) -> list[str]:
    """Build the local ``ssh`` argv for one ``depp exec`` invocation.

    Args:
        fqdn: Fully qualified domain name (used as both SSH user and host).
        container_name: Name of the podman container to exec into.
        command: Command and arguments to run. Empty for interactive shell.
        allocate_tty: If True, allocate a TTY on both hops (ssh and podman).
        host_only: If True, run on the host directly instead of in a container.
        host_key_policy: OpenSSH host-key verification policy.
        connection: Use SSH or execute directly on the local host.

    Returns:
        The full argv, ready for ``subprocess.run``.
    """
    if connection == "local":
        if host_only:
            return command or [os.environ.get("SHELL") or "/bin/sh"]
        podman_cmd = ["podman", "exec", "-i"]
        if allocate_tty:
            podman_cmd.append("-t")
        podman_cmd.append(container_name)
        podman_cmd.extend(command if command else ["/bin/sh"])
        return podman_cmd
    if connection != "ssh":
        raise ValueError(f"unsupported connection mode {connection!r}")

    ssh_key = str(Path(DEPP_SSH_KEY_PATH).expanduser())
    ssh_cmd = [
        "ssh",
        "-i",
        ssh_key,
        *host_key_options(host_key_policy),
    ]

    # -tt, not -t: plain -t is a no-op when our own stdin is not a terminal,
    # which would silently defeat an explicit --tty.
    if allocate_tty:
        ssh_cmd.append("-tt")

    ssh_cmd.append(f"{fqdn}@{fqdn}")

    # The remote sshd hands the command to a shell, which re-splits words —
    # quote everything with shlex so arguments containing spaces, quotes or
    # globs survive the ssh hop intact.
    if host_only:
        if command:
            ssh_cmd.append(shlex.join(command))
        # else: interactive shell — SSH opens a login shell by default
    else:
        podman_cmd = ["podman", "exec", "-i"]
        if allocate_tty:
            podman_cmd.append("-t")
        podman_cmd.append(container_name)
        podman_cmd.extend(command if command else ["/bin/sh"])
        ssh_cmd.append(shlex.join(podman_cmd))

    return ssh_cmd


def run_exec(
    fqdn: str,
    container_name: str,
    command: list[str],
    tty: bool | None = None,
    host_only: bool = False,
    host_key_policy: str = "strict",
    connection: str = "ssh",
) -> int:
    """Run a command on the remote host or inside a container via SSH.

    Args:
        fqdn: Fully qualified domain name (used as both SSH user and host).
        container_name: Name of the podman container to exec into.
        command: Command and arguments to run. Empty for interactive shell.
        tty: Force TTY allocation on (True) or off (False). None auto-detects:
            a TTY is allocated when this process' stdin *and* stdout are both
            terminals, so prompting commands work while pipes and redirects
            keep clean, un-mangled byte streams.
        host_only: If True, run on the host directly instead of in a container.
        host_key_policy: OpenSSH host-key verification policy.
        connection: Use SSH or execute directly on the local host.

    Returns:
        Exit code from the remote command.
    """
    if tty is None:
        allocate_tty = sys.stdin.isatty() and sys.stdout.isatty()
    else:
        allocate_tty = tty

    result = subprocess.run(
        build_exec_command(
            fqdn=fqdn,
            container_name=container_name,
            command=command,
            allocate_tty=allocate_tty,
            host_only=host_only,
            host_key_policy=host_key_policy,
            connection=connection,
        ),
        check=False,
    )
    return result.returncode
