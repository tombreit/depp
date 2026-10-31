"""Run the real ansible-playbook binary, which every other test mocks.

The playbooks are otherwise only exercised against a real host. These tests
notice a syntax error, such as one in reset.yml, which deletes volume contents,
and they notice when Ansible cannot start at all, for example on a Python
version it does not support.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import depp
from depp.ansible_common.runner import _ANSIBLE_CONFIG_PATH

# Next to the interpreter, not on PATH: the pre-push hook runs .venv/bin/pytest
# without an activated virtualenv.
ANSIBLE_PLAYBOOK = Path(sys.executable).parent / "ansible-playbook"
PLAYBOOKS = sorted(Path(depp.__file__).parent.glob("ansible_*/*.yml"))


def test_playbooks_are_found():
    # An empty glob would otherwise turn the test below into a silent no-op.
    assert PLAYBOOKS


@pytest.mark.parametrize(
    "playbook", PLAYBOOKS, ids=lambda path: f"{path.parent.name}/{path.name}"
)
def test_playbook_syntax(playbook):
    result = subprocess.run(
        [ANSIBLE_PLAYBOOK, "--syntax-check", playbook],
        env={**os.environ, "ANSIBLE_CONFIG": str(_ANSIBLE_CONFIG_PATH)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
