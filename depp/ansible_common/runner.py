"""Ansible playbook runner module."""

from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from depp.ansible_common.ssh import host_key_options

_ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook") or "ansible-playbook"
_ANSIBLE_CONFIG_PATH = Path(__file__).resolve().parent / "ansible.cfg"


def run_ansible_playbook(
    playbook_path: Path,
    inventory: dict[str, Any],
    extra_vars: dict[str, Any],
    check_mode: bool = False,
    verbose: bool = False,
    ask_become_pass: bool = False,
    private_vars: dict[str, Any] | None = None,
    host_key_policy: str = "strict",
) -> int:
    """Run an Ansible playbook with the given inventory and extra variables.

    Args:
        playbook_path: Path to the Ansible playbook YAML file
        inventory: Ansible inventory dictionary (converted to YAML format)
        extra_vars: Extra variables to pass to the playbook
        check_mode: If True, run in check mode (dry-run without making changes)
        verbose: If True, enable verbose Ansible output (-vvv)
        private_vars: Variables stored in the private inventory instead of argv
        host_key_policy: OpenSSH host-key verification policy

    Returns:
        Exit code from ansible-playbook (0 for success, non-zero for failure)

    Raises:
        FileNotFoundError: If the playbook file does not exist
    """
    if not playbook_path.exists():
        print(f"Error: Could not find playbook at {playbook_path}", file=sys.stderr)
        return 1

    inventory_data = copy.deepcopy(inventory)
    all_vars = inventory_data.setdefault("all", {}).setdefault("vars", {})
    hosts = list(inventory_data["all"].get("hosts", {}).values())
    local_connection = bool(hosts) and all(
        host.get("ansible_connection") == "local" for host in hosts
    )
    if not local_connection:
        all_vars["ansible_ssh_common_args"] = shlex.join(
            host_key_options(host_key_policy)
        )
    if private_vars:
        all_vars.update(private_vars)

    # Create temporary inventory file using YAML. NamedTemporaryFile creates it
    # owner-only, so sensitive variables do not need a second temporary file.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", prefix="podman_deploy_inv_", delete=False
    ) as tmp:
        yaml.safe_dump(inventory_data, tmp, default_flow_style=False)
        tmp_path = tmp.name

    try:
        # Build ansible-playbook command
        cmd = [
            _ANSIBLE_PLAYBOOK,
            "-i",
            tmp_path,
            "-e",
            json.dumps(extra_vars),
        ]

        if verbose:
            cmd.append("-vvv")

        if check_mode:
            cmd.append("--check")

        if ask_become_pass:
            cmd.append("--ask-become-pass")

        cmd.append(str(playbook_path))

        # Run ansible-playbook
        env = os.environ.copy()
        if local_connection:
            env.pop("ANSIBLE_HOST_KEY_CHECKING", None)
        else:
            env["ANSIBLE_HOST_KEY_CHECKING"] = (
                "False" if host_key_policy == "insecure" else "True"
            )
        env["ANSIBLE_NOCOWS"] = "1"
        # depp runs from inside the target project directory, and Ansible would
        # otherwise auto-discover an ansible.cfg sitting there. That file can
        # set library/roles_path/callback plugins, i.e. run arbitrary code from
        # a cloned repo. Pin the config to our own bundled file instead.
        env["ANSIBLE_CONFIG"] = str(_ANSIBLE_CONFIG_PATH)
        result = subprocess.run(cmd, env=env, check=False)
        return result.returncode
    finally:
        # Clean up temporary inventory file
        Path(tmp_path).unlink(missing_ok=True)
