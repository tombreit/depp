"""Render a Kubernetes ConfigMap from a dotenv-style file — pure Python.

This deliberately *mimics* the output of::

    kubectl create configmap <name> --from-env-file=<file> --dry-run=client -o yaml

depp previously shelled out to that exact command during ``depp deploy``. Note
the flags: ``--dry-run=client -o yaml`` means kubectl never contacts a cluster —
it is used purely as a *text formatter* that turns ``KEY=VALUE`` lines into a
ConfigMap YAML document. That is a ~15-line job, so this module does it directly
and lets us drop ``kubectl`` as a system dependency entirely: depp already ships
PyYAML, and nothing else in the tool needs kubectl.

The supported grammar is deliberately small: plain ``KEY=value`` lines, blank
lines, ``#`` comments, and one optional layer of matching quotes around values.
Malformed input is rejected before deployment instead of being silently skipped.
"""

import re
from pathlib import Path

import yaml

CONFIGMAP_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_env_file(env_file: Path) -> dict[str, str]:
    """Parse a dotenv-style file into an ordered ``{KEY: VALUE}`` mapping.

    Blank lines and ``#`` comment lines are skipped. Each remaining line is split
    on the first ``=``; the key is stripped of surrounding whitespace and the
    value of surrounding whitespace and a single layer of matching quotes.
    """
    try:
        contents = env_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{env_file} is not valid UTF-8") from error

    data: dict[str, str] = {}
    for line_number, raw_line in enumerate(contents.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"{env_file}:{line_number}: expected a KEY=value assignment"
            )
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{env_file}:{line_number}: variable name is empty")
        if not CONFIGMAP_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"{env_file}:{line_number}: invalid ConfigMap key {key!r}")
        if key in data:
            raise ValueError(f"{env_file}:{line_number}: duplicate variable {key!r}")
        value = value.strip()
        if value and value[0] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(
                    f"{env_file}:{line_number}: value has unmatched quotes"
                )
            value = value[1:-1]
        elif value and value[-1] in ("'", '"'):
            raise ValueError(f"{env_file}:{line_number}: value has unmatched quotes")
        data[key] = value

    if not data:
        raise ValueError(f"{env_file} does not define any variables")
    return data


def render_configmap(name: str, env_file: Path) -> str:
    """Render a v1 ConfigMap YAML document from ``env_file``.

    See the module docstring: this reproduces the relevant behaviour of
    ``kubectl create configmap <name> --from-env-file --dry-run=client -o yaml``.
    """
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name},
        "data": parse_env_file(env_file),
    }
    return yaml.safe_dump(manifest, default_flow_style=False, sort_keys=False)
