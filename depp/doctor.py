"""Non-mutating control-node checks for ``depp doctor``."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

REQUIRED_MODULES = (
    "ansible.posix.authorized_key",
    "ansible.posix.synchronize",
    "community.crypto.openssh_keypair",
    "community.general.apache2_module",
    "containers.podman.podman_container_info",
    "containers.podman.podman_image",
    "containers.podman.podman_image_info",
    "containers.podman.podman_load",
    "containers.podman.podman_pod_info",
    "containers.podman.podman_prune",
    "containers.podman.podman_save",
    "containers.podman.podman_system_info",
    "containers.podman.podman_tag",
    "containers.podman.podman_volume",
    "containers.podman.podman_volume_info",
)
# A floor, not an exact match: depp uses stable collection APIs, so a newer
# Ansible major should not make `depp doctor` report a hard failure.
MINIMUM_ANSIBLE_MAJOR = 14


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def control_node_checks(*, connection: str) -> list[DoctorCheck]:
    """Check required executables and Ansible modules without changing state."""
    executable_names = ["git", "podman", "ansible-playbook", "ansible-doc", "rsync"]
    if connection == "ssh":
        executable_names.append("ssh")

    checks = []
    try:
        ansible_version = version("ansible")
        ansible_major = int(ansible_version.split(".", 1)[0])
        checks.append(
            DoctorCheck(
                name="Ansible package",
                ok=ansible_major >= MINIMUM_ANSIBLE_MAJOR,
                detail=(
                    ansible_version
                    if ansible_major >= MINIMUM_ANSIBLE_MAJOR
                    else f"{ansible_version}; needs {MINIMUM_ANSIBLE_MAJOR}.x or newer"
                ),
            )
        )
    except (PackageNotFoundError, ValueError):
        checks.append(DoctorCheck("Ansible package", False, "not installed"))

    for executable in executable_names:
        path = shutil.which(executable)
        checks.append(
            DoctorCheck(
                name=executable,
                ok=path is not None,
                detail=path or "not found in PATH",
            )
        )

    ansible_doc = shutil.which("ansible-doc")
    if ansible_doc:
        for module in REQUIRED_MODULES:
            result = subprocess.run(
                [ansible_doc, "--type", "module", module],
                capture_output=True,
                text=True,
                check=False,
            )
            checks.append(
                DoctorCheck(
                    name=module,
                    ok=result.returncode == 0,
                    detail="available" if result.returncode == 0 else "not available",
                )
            )
    return checks
