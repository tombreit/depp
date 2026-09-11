"""Shared Jinja2 rendering for the bundled templates.

Ansible's template module renders with ``trim_blocks=True`` and keeps the
trailing newline, so tests render the same way to compare byte for byte.
"""

from pathlib import Path

import jinja2

import depp

PACKAGE_DIR = Path(depp.__file__).parent
VHOST_TEMPLATE_DIR = PACKAGE_DIR / "ansible_provisioning" / "templates"
VHOST_TEMPLATE = "apache-vhost.conf.j2"
UNIT_TEMPLATE_DIR = PACKAGE_DIR / "ansible_deploy" / "templates"
UNIT_TEMPLATE = "kube-pod.service.j2"


def render_template(template_dir: Path, name: str, variables: dict) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        keep_trailing_newline=True,
        # Ansible fails on an undefined variable; a test render must too, or a
        # template that references a variable the playbook never sets passes
        # here and breaks on the host.
        undefined=jinja2.StrictUndefined,
    )
    return env.get_template(name).render(variables)
