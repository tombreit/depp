"""Where depp looks for a project's deployment files.

depp keeps everything it needs in one directory so it does not creep into the
project it deploys. The canonical layout puts that directory, ``deploy/``,
inside the project with ``depp.toml`` in it::

    myproject/
      Containerfile
      deploy/
        depp.toml      # project_root defaults to ".."
        kube.yaml
        .env           # optional secrets
        vhost.conf     # optional Apache directives

Two older layouts keep working unchanged: ``depp.toml`` at the project root
next to a ``deploy/`` directory, and ``depp.toml`` in a separate configuration
repository pointing at the project with ``project_root``. In both, the files
live in ``<project_root>/deploy/``.

The rule is keyed on the toml's directory *name*, not on whether it differs
from ``project_root``, so a separate configuration repository does not
suddenly have its own files looked up next to the toml.
"""

from pathlib import Path

DEPLOY_DIR_NAME = "deploy"
TOML_FILENAME = "depp.toml"
KUBE_MANIFEST_FILENAME = "kube.yaml"
ENV_FILENAME = ".env"
VHOST_SNIPPET_FILENAME = "vhost.conf"


def is_deploy_dir_layout(toml_path: Path) -> bool:
    """True when depp.toml sits in a directory literally named ``deploy``."""
    return toml_path.resolve().parent.name == DEPLOY_DIR_NAME


def default_project_root(toml_path: Path) -> Path:
    """The project root when ``[app] project_root`` is absent.

    ``..`` for the deploy-dir layout, the toml's own directory otherwise.
    """
    toml_dir = toml_path.resolve().parent
    return toml_dir.parent if is_deploy_dir_layout(toml_path) else toml_dir


def resolve_deploy_dir(toml_path: Path, project_root: Path) -> Path:
    """The directory holding kube.yaml, .env and vhost.conf."""
    if is_deploy_dir_layout(toml_path):
        return toml_path.resolve().parent
    return project_root / DEPLOY_DIR_NAME


def find_default_toml(cwd: Path) -> Path | None:
    """``./deploy/depp.toml``, then ``./depp.toml``; None when neither exists."""
    for candidate in (cwd / DEPLOY_DIR_NAME / TOML_FILENAME, cwd / TOML_FILENAME):
        if candidate.is_file():
            return candidate
    return None


def display_path(path: Path, project_root: Path) -> str:
    """``path`` relative to the project when inside it, absolute otherwise."""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)
