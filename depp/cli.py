#!/usr/bin/env python3
"""
depp — DEployment and Provisioning Python wrapper

Usage:
    depp provision [DEPP_TOML]          # provision host
    depp provision [DEPP_TOML] --check  # dry-run (no changes)
    depp deploy [DEPP_TOML]             # deploy
    depp deploy [DEPP_TOML] --check     # dry-run (no changes on remote)
    depp backup [DEPP_TOML]             # pull volume contents to local backups/
    depp restore [DEPP_TOML] PATH       # push a local backup back to the host
    depp reset [DEPP_TOML]              # delete volume contents (backup taken first)
    depp doctor [DEPP_TOML]             # validate project and target readiness
    depp exec [DEPP_TOML] -- CMD        # run command in container (TTY if we have one)
    depp exec [DEPP_TOML]               # interactive shell in container
    depp exec [DEPP_TOML] --host        # interactive shell on host

DEPP_TOML defaults to ./deploy/depp.toml, then ./depp.toml.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from importlib.metadata import version as _pkg_version
from pathlib import Path

from depp.ansible_common.inventory import (
    CONNECTION_MODES,
    DEFAULT_ACME_CERTIFICATE_AUTHORITY,
    DEPP_SSH_KEY_PATH,
    DeppConfig,
    build_backup_inventory,
    build_inventory,
    build_provisioning_inventory,
    build_restore_inventory,
)
from depp.ansible_common.runner import run_ansible_playbook
from depp.ansible_common.ssh import HOST_KEY_POLICIES, host_key_options
from depp.configmap import render_configmap
from depp.doctor import DoctorCheck, control_node_checks
from depp.layout import DEPLOY_DIR_NAME, TOML_FILENAME, find_default_toml
from depp.manifest import ManifestError, parse_kube_manifest, validate_deploy_manifest

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class ConfirmationUnavailableError(RuntimeError):
    """Raised when a command needs confirmation but stdin cannot provide it."""


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def _read_version() -> str:
    try:
        return _pkg_version("depp")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def load_toml_config(toml_path: Path) -> dict:
    """Load and parse the TOML configuration file.

    Raises:
        SystemExit: If the file is missing or cannot be parsed.
    """
    if not toml_path.exists():
        print(f"Error: Configuration file not found: {toml_path}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    try:
        with toml_path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as e:
        print(f"Error: Failed to parse TOML file: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


def build_inventory_or_exit(builder, *args):
    """Call an inventory builder, turning config ValueErrors into clean exits.

    The builders in ``ansible_common.inventory`` raise ``ValueError`` for
    missing or invalid depp.toml fields; without this wrapper the user would
    see a raw traceback instead of an error message.
    """
    try:
        return builder(*args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


def resolve_toml_path(args: argparse.Namespace) -> Path:
    """The depp.toml to use: the given path, else the first default found."""
    if args.toml_file is not None:
        return args.toml_file.resolve()
    found = find_default_toml(Path.cwd())
    if found is None:
        print(
            "Error: no configuration file given and none found at "
            f"{DEPLOY_DIR_NAME}/{TOML_FILENAME} or {TOML_FILENAME}",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)
    return found.resolve()


def operator_config_path() -> Path:
    """``$XDG_CONFIG_HOME/depp.toml``, falling back to ``~/.config/depp.toml``."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "depp.toml"


def load_operator_acme(path: Path | None) -> dict:
    """Return the ``[acme]`` table of the operator config, or ``{}`` if absent.

    The operator config (default ``~/.config/depp.toml``) holds per-user ACME
    settings — e.g. the External Account Binding secret — that span projects
    and should not live in a project repo. Its keys are merged as-is over the
    project's ``[acme]`` section, the operator value winning.

    ``path=None`` uses the default location and returns ``{}`` when the file
    does not exist; an explicitly given path (``--secrets``) must exist.
    """
    explicit = path is not None
    cfg = (path or operator_config_path()).expanduser()
    if not cfg.is_file():
        if explicit:
            print(f"Error: operator config not found: {cfg}", file=sys.stderr)
            sys.exit(EXIT_ERROR)
        return {}
    if cfg.stat().st_mode & 0o077:
        print(
            f"Warning: {cfg} is accessible by group/others but holds secrets; "
            f"consider: chmod 600 {cfg}",
            file=sys.stderr,
        )
    try:
        with cfg.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as e:
        print(f"Error: failed to parse operator config {cfg}: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    acme = data.get("acme", {})
    if not isinstance(acme, dict):
        print(
            f"Error: [acme] in operator config {cfg} must be a table", file=sys.stderr
        )
        sys.exit(EXIT_ERROR)
    return acme


# Deprecation notices already printed in this process. ``restore`` and
# ``reset`` load the project twice (once for the pre-flight backup), and the
# operator should read each notice once, not once per playbook.
_PRINTED_DEPRECATIONS: set[str] = set()


def load_project(args: argparse.Namespace) -> tuple[Path, DeppConfig, str, str]:
    """Resolve and parse the project depp.toml every subcommand starts from.

    Returns ``(toml_path, config, fqdn, app_name)``; exits with a validation
    error when the unified configuration model cannot be constructed.
    """
    toml_path = resolve_toml_path(args)
    toml_data = load_toml_config(toml_path)
    try:
        config = DeppConfig.from_mapping(toml_path, toml_data)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    for notice in config.deprecations:
        if notice not in _PRINTED_DEPRECATIONS:
            _PRINTED_DEPRECATIONS.add(notice)
            print(f"Warning: {notice}", file=sys.stderr)
    return toml_path, config, config.host.fqdn, config.app.name


def print_summary(title: str, lines: list[str]) -> None:
    """Print a boxed summary: banner, title, banner, indented lines, banner."""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)
    for line in lines:
        print(f"  {line}" if line else "")
    print("=" * 60)


def ssh_connection_string(
    fqdn: str,
    host_key_policy: str = "strict",
    connection: str = "ssh",
    user: str | None = None,
) -> str:
    """The ssh invocation Ansible derives: depp key, deploy user, host."""
    if connection == "local":
        return "local (current user)"
    return shlex.join(
        [
            "ssh",
            "-i",
            DEPP_SSH_KEY_PATH,
            *host_key_options(host_key_policy),
            f"{user or fqdn}@{fqdn}",
        ]
    )


def confirm(prompt: str) -> bool:
    """Ask a y/N question on stdin; True only on an explicit 'y'."""
    if not sys.stdin.isatty():
        raise ConfirmationUnavailableError(
            "confirmation requires an interactive terminal; rerun with --yes"
        )
    print()
    print(f"{prompt} [y/N] ", end="", flush=True)
    answer = sys.stdin.readline()
    if answer == "":
        print()
        raise ConfirmationUnavailableError(
            "confirmation input closed; rerun with --yes"
        )
    return answer.strip().lower() == "y"


def require_confirmation_input(*, skip: bool) -> None:
    """Fail before side effects when a command will need an interactive answer."""
    if not skip and not sys.stdin.isatty():
        raise ConfirmationUnavailableError(
            "confirmation requires an interactive terminal; rerun with --yes"
        )


# ---------------------------------------------------------------------------
# Provisioning helpers
# ---------------------------------------------------------------------------


def run_provisioning(args: argparse.Namespace) -> int:
    require_confirmation_input(skip=args.check or args.yes)
    toml_path, config, hostname, _app_name = load_project(args)

    acme = load_operator_acme(args.secrets)
    if acme:
        try:
            config = config.with_acme_overrides(acme)
        except ValueError as error:
            print(f"Error: {error}", file=sys.stderr)
            return EXIT_ERROR

    inventory = build_inventory_or_exit(
        build_provisioning_inventory, config, args.connection
    )
    host_vars = inventory["all"]["hosts"][hostname]

    deploy_user = config.host.user
    connection = ssh_connection_string(
        hostname, args.host_key_policy, args.connection, deploy_user
    )
    lines = [
        f"Configuration file: {toml_path}",
        f"Deployment user: {deploy_user}",
        f"Connection: {connection}",
        "",
        "What will be done:",
        f"  ✓ Create deployment user {deploy_user}",
        f"  ✓ Create SSH key for user {deploy_user} at {DEPP_SSH_KEY_PATH}",
        "  ✓ Install podman for rootless containers",
        "  ✓ Configure Apache reverse proxy → "
        f"127.0.0.1:{host_vars['host_loopback_port']}",
    ]
    if host_vars.get("acme_external_account_binding"):
        lines.append("  ✓ Configure ACME External Account Binding")
    if (
        host_vars.get("acme_certificate_authority")
        != DEFAULT_ACME_CERTIFICATE_AUTHORITY
    ):
        lines.append(
            f"  ✓ Use ACME certificate authority "
            f"{host_vars['acme_certificate_authority']}"
        )
    else:
        lines += [
            "",
            "⚠ ACME certificate authority: Let's Encrypt STAGING (the default).",
            "  Certificates will NOT be trusted by browsers. For production, set:",
            "    [acme]",
            '    certificate_authority = "https://acme-v02.api.letsencrypt.org/directory"',
        ]
    lines.append("")
    print_summary(f"Host Provisioning Summary for {hostname}", lines)

    if not args.check and not args.yes:
        if not confirm(
            f"Do you want to provision {hostname}? This requires elevated privileges."
        ):
            print("Provisioning cancelled.")
            return EXIT_SUCCESS

    playbook = (
        Path(__file__).resolve().parent / "ansible_provisioning" / "provision.yml"
    )

    print()
    print(f"Provisioning {hostname} with elevated privileges...")
    print()

    return run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={},
        check_mode=args.check,
        verbose=args.verbose,
        ask_become_pass=args.ask_become_pass,
        host_key_policy=args.host_key_policy,
    )


# ---------------------------------------------------------------------------
# Deployment helpers
# ---------------------------------------------------------------------------


def get_git_info(project_root: Path) -> tuple[str, str]:
    """Get the full git SHA and commit message from the project repository."""
    try:
        commit_hash = subprocess.check_output(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        commit_message = subprocess.check_output(
            ["git", "-C", str(project_root), "log", "-1", "--pretty=%s", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        return commit_hash, commit_message
    except FileNotFoundError:
        print("Error: git executable not found.", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    except subprocess.CalledProcessError:
        print(
            "Error: Could not determine git HEAD in project directory.",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)


def check_git_dirty(project_root: Path) -> bool:
    """Return True if the git working tree has uncommitted changes.

    If git status cannot be determined at all, warn and err on the side of
    "dirty" so deployment is rejected unless the user explicitly allows an
    uncommitted source tree.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(
            "Warning: git not found; treating working tree as dirty.",
            file=sys.stderr,
        )
        return True
    if result.returncode != 0:
        print(
            "Warning: could not determine git status "
            f"({result.stderr.strip() or 'unknown error'}); "
            "treating working tree as dirty.",
            file=sys.stderr,
        )
        return True
    return bool(result.stdout.strip())


def deployment_version(commit_hash: str, git_dirty: bool) -> str:
    """Return an immutable image tag component for the current source tree."""
    if not git_dirty:
        return commit_hash
    return f"{commit_hash}-dirty-{uuid.uuid4().hex[:12]}"


def resolve_containerfile(project_root: Path, config: DeppConfig) -> Path:
    """Resolve and validate the build file inside ``project_root``."""
    configured = config.app.containerfile

    relative_path = Path(configured)
    if relative_path.is_absolute():
        raise ValueError("'containerfile' in [app] must be relative to project_root")

    containerfile = (project_root / relative_path).resolve()
    if not containerfile.is_relative_to(project_root):
        raise ValueError("'containerfile' in [app] must stay inside project_root")
    if not containerfile.is_file():
        raise ValueError(f"Containerfile not found: {containerfile}")
    return containerfile


def validate_project_inputs(config: DeppConfig):
    """Validate deployment files once for deploy and doctor."""
    project_root = config.app.project_root
    kube_file = config.kube_manifest
    env_path = config.env_file
    containerfile = resolve_containerfile(project_root, config)
    manifest = validate_deploy_manifest(
        kube_file,
        image_name=config.app.image_name,
        has_env_file=env_path.exists(),
        env_label=config.display(env_path),
    )
    configmap_yaml = None
    if env_path.exists():
        configmap_yaml = render_configmap(f"{config.app.image_name}-config", env_path)
    return containerfile, kube_file, env_path, manifest, configmap_yaml


def run_deployment(args: argparse.Namespace) -> int:
    require_confirmation_input(skip=args.check or args.yes)
    toml_path, config, fqdn, app_name = load_project(args)
    image_name = config.app.image_name

    project_root = config.app.project_root
    commit_hash, app_message = get_git_info(project_root)
    git_dirty = check_git_dirty(project_root)

    if git_dirty and not args.check and not args.allow_dirty:
        print(
            "Error: The project has uncommitted changes. Commit or stash them, "
            "or pass --allow-dirty to deploy this source tree explicitly.",
            file=sys.stderr,
        )
        return 1

    app_version = deployment_version(commit_hash, git_dirty)

    inventory = build_inventory_or_exit(build_inventory, config, args.connection)
    try:
        (
            containerfile,
            kube_file,
            env_path,
            manifest,
            configmap_yaml,
        ) = validate_project_inputs(config)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    if env_path.exists() and env_path.stat().st_mode & 0o077:
        print(
            f"Warning: {env_path} contains deployment secrets but is "
            "accessible by group/others; consider: chmod 600 "
            f"{env_path}",
            file=sys.stderr,
        )
    pod_name = manifest.pod_name
    containers = manifest.container_names
    pvc_volumes = manifest.pvc_claim_names

    max_message_length = 60
    display_message = (
        app_message[:max_message_length] + "..."
        if len(app_message) > max_message_length
        else app_message
    )
    connection = ssh_connection_string(
        fqdn, args.host_key_policy, args.connection, config.host.user
    )
    lines = [f"App:        {app_name}"]
    if image_name and image_name != app_name:
        lines.append(f"Image:      {image_name}")
    lines += [
        f"Git:        {app_version} ({'dirty' if git_dirty else 'clean'}) "
        f"{display_message}",
        f"Project:    {project_root}",
        f"Build file: {containerfile.relative_to(project_root)}",
        f"Target:     {fqdn}",
        f"Connection: {connection}",
        f"Env file:   {config.display(env_path) if env_path.exists() else 'none'}",
        f"Kube:       {config.display(kube_file)} (systemd unit generated by depp)",
    ]
    print_summary(f"Deployment Summary for {fqdn}", lines)

    if not args.check and not args.yes:
        if not confirm(f"Do you want to deploy to {fqdn}?"):
            print("Deployment cancelled.")
            return EXIT_SUCCESS

    playbook = Path(__file__).resolve().parent / "ansible_deploy" / "deploy.yml"

    private_vars = {}
    if configmap_yaml is not None:
        private_vars["configmap_yaml"] = configmap_yaml

    # Private per-run directory for the image archive: deploy.yml writes the
    # tar here instead of a predictable, shared /tmp path. Removed after the
    # playbook run (deploy.yml also deletes the tar itself in its always:
    # section).
    local_archive_dir = tempfile.mkdtemp(prefix="depp-image-")

    extra_vars = {
        "app_version": app_version,
        "local_repo_path": str(project_root),
        "local_kube_file": str(kube_file),
        "local_archive_dir": local_archive_dir,
    }

    # Pre-create the named volumes the pod declares (framework-neutral; no
    # hardcoded -private/-media assumption). podman kube play would auto-create
    # missing PVCs anyway, but pre-creating surfaces problems earlier.
    if pvc_volumes:
        extra_vars["pvc_volumes"] = pvc_volumes

    print()
    print(f"Deploying {app_name}@{app_version} to {fqdn}...")
    print()

    try:
        rc = run_ansible_playbook(
            playbook_path=playbook,
            inventory=inventory,
            extra_vars=extra_vars,
            check_mode=args.check,
            verbose=args.verbose,
            private_vars=private_vars,
            host_key_policy=args.host_key_policy,
        )
    finally:
        shutil.rmtree(local_archive_dir, ignore_errors=True)

    if rc == 0 and not args.check:
        health_path = inventory["all"]["hosts"][fqdn]["health_path"]
        pod_line = f"Pod:         {pod_name or app_name}"
        if containers:
            pod_line += f" — containers: {', '.join(containers)}"
        health_line = "Health:      OK"
        if health_path != "/":
            health_line += f" ({health_path})"
        print_summary(
            "Deployment successful",
            [
                f"App:         {app_name} @ {app_version}",
                f"Service:     {app_name}.service (systemd user unit)",
                pod_line,
                health_line,
                f"URL:         https://{fqdn}",
            ],
        )

    return rc


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------


def backup_volumes(toml_path: Path, config: DeppConfig) -> tuple[str, list[str]]:
    """Resolve the pod name and the named volumes to back up / restore / reset.

    kube.yaml is the single source of truth: the pod is named after the
    manifest's metadata.name (that is what podman kube play uses, which may
    differ from [app].name), and depp backs up every persistentVolumeClaim
    the pod declares. Ephemeral volumes (emptyDir, configMap) are not PVCs
    and are therefore naturally excluded. Reuses ``parse_kube_manifest``.
    """
    try:
        pod_name, _containers, pvcs = parse_kube_manifest(config.kube_manifest)
    except ManifestError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    return pod_name, pvcs


def pvc_volumes_or_exit(
    toml_path: Path, config: DeppConfig, verb: str
) -> tuple[str, list[str]]:
    """Resolve the pod name and PVC volumes, exiting when the pod declares none."""
    pod_name, volumes = backup_volumes(toml_path, config)
    if not volumes:
        print(
            "Error: No persistentVolumeClaims declared in "
            f"{config.display(config.kube_manifest)} — nothing to {verb}.",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)
    return pod_name, volumes


def run_backup(args: argparse.Namespace) -> int:
    import datetime

    toml_path, config, fqdn, app_name = load_project(args)

    pod_name, backup_dirs = backup_volumes(toml_path, config)
    if not backup_dirs:
        print(
            "Warning: No persistentVolumeClaims declared in "
            f"{config.display(config.kube_manifest)} — nothing to back up.",
            file=sys.stderr,
        )
        return 0

    # UTC with an explicit Z suffix, so backup directories sort consistently
    # regardless of the workstation's timezone or DST changes.
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H%M%SZ")
    # Anchored to depp.toml rather than the process working directory, so a
    # backup always lands next to the project it came from instead of wherever
    # the operator happened to be. Backups hold whole volumes — databases and
    # uploaded media — so the tree is owner-only.
    backup_local_dest = toml_path.parent / "backups" / fqdn / timestamp
    # mkdir(mode=...) only applies to the leaf, so create each level we own
    # explicitly — otherwise "backups/" and the per-host directory below it
    # stay world-listable and leak which hosts have been backed up.
    for directory in (
        backup_local_dest.parent.parent,
        backup_local_dest.parent,
        backup_local_dest,
    ):
        directory.mkdir(exist_ok=True, mode=0o700)

    inventory = build_inventory_or_exit(
        build_backup_inventory,
        config,
        str(backup_local_dest.resolve()),
        backup_dirs,
        pod_name,
        args.connection,
    )

    connection = ssh_connection_string(
        fqdn, args.host_key_policy, args.connection, config.host.user
    )
    print_summary(
        f"Backup Summary for {fqdn}",
        [
            f"Configuration file: {toml_path}",
            f"App:                {app_name}",
            f"Connection:         {connection}",
            f"Destination:        {backup_local_dest}",
            "Volumes to back up:",
            *[f"  - {d}" for d in backup_dirs],
        ],
    )

    playbook = Path(__file__).resolve().parent / "ansible_backup" / "backup.yml"

    print()
    print(f"Backing up {app_name} from {fqdn} into {backup_local_dest} ...")
    print()

    rc = run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={},
        verbose=args.verbose,
        host_key_policy=args.host_key_policy,
    )
    if rc != 0:
        # Don't leave an empty timestamped dir behind after a failed run.
        # rmdir only removes empty directories, so partial data survives.
        try:
            backup_local_dest.rmdir()
        except OSError:
            pass
    return rc


# ---------------------------------------------------------------------------
# Restore helpers
# ---------------------------------------------------------------------------


def run_restore(args: argparse.Namespace) -> int:
    require_confirmation_input(skip=args.yes)
    toml_path, config, fqdn, app_name = load_project(args)

    pod_name, backup_dirs = pvc_volumes_or_exit(toml_path, config, "restore")

    restore_path = args.restore_path.resolve()
    if not restore_path.is_dir():
        print(f"Error: Restore path not found: {restore_path}", file=sys.stderr)
        return 1

    restore_dirs = [entry for entry in backup_dirs if (restore_path / entry).is_dir()]
    skipped_dirs = [entry for entry in backup_dirs if entry not in restore_dirs]

    if not restore_dirs:
        print(
            f"Error: Restore path contains none of the expected volume directories "
            f"({', '.join(backup_dirs)}).",
            file=sys.stderr,
        )
        return 1

    if skipped_dirs:
        print(
            f"Warning: Skipping volumes not found in restore path: "
            f"{', '.join(skipped_dirs)}",
            file=sys.stderr,
        )

    connection = ssh_connection_string(
        fqdn, args.host_key_policy, args.connection, config.host.user
    )
    lines = [
        f"Configuration file: {toml_path}",
        f"App:                {app_name}",
        f"Connection:         {connection}",
        f"Restore from:       {restore_path}",
        "Volumes to restore:",
        *[f"  - {d}" for d in restore_dirs],
    ]
    if skipped_dirs:
        lines.append("Volumes skipped (not found in restore path):")
        lines += [f"  - {d}" for d in skipped_dirs]
    lines += [
        "",
        "A backup will be taken before restoring.",
        "The pod will be stopped during the restore and restarted after.",
    ]
    print_summary(f"Restore Summary for {fqdn}", lines)

    if not args.yes and not confirm(
        f"Restore will OVERWRITE volume contents on {fqdn}. Continue?"
    ):
        print("Restore cancelled.")
        return EXIT_SUCCESS

    print()
    print("Running pre-restore backup...")
    print()
    rc = run_backup(args)
    if rc != 0:
        print(
            "Error: Pre-restore backup failed. Aborting restore.",
            file=sys.stderr,
        )
        return 1

    inventory = build_inventory_or_exit(
        build_restore_inventory,
        config,
        str(restore_path),
        restore_dirs,
        pod_name,
        args.connection,
    )
    playbook = Path(__file__).resolve().parent / "ansible_backup" / "restore.yml"

    print()
    print(f"Restoring {app_name} on {fqdn} from {restore_path} ...")
    print()

    return run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={},
        verbose=args.verbose,
        host_key_policy=args.host_key_policy,
    )


# ---------------------------------------------------------------------------
# Reset helpers
# ---------------------------------------------------------------------------


def run_reset(args: argparse.Namespace) -> int:
    require_confirmation_input(skip=args.yes)
    toml_path, config, fqdn, app_name = load_project(args)

    pod_name, backup_dirs = pvc_volumes_or_exit(toml_path, config, "reset")

    connection = ssh_connection_string(
        fqdn, args.host_key_policy, args.connection, config.host.user
    )
    print_summary(
        f"Reset Summary for {fqdn}",
        [
            f"Configuration file: {toml_path}",
            f"App:                {app_name}",
            f"Connection:         {connection}",
            "Volumes to reset:",
            *[f"  - {d}" for d in backup_dirs],
            "",
            "A backup will be taken before resetting.",
            "All contents of the above volumes will be DELETED.",
        ],
    )

    print()
    print("Running pre-reset backup...")
    print()
    rc = run_backup(args)
    if rc != 0:
        print(
            "Error: Pre-reset backup failed. Aborting reset.",
            file=sys.stderr,
        )
        return 1

    inventory = build_inventory_or_exit(
        build_backup_inventory,
        config,
        "",
        backup_dirs,
        pod_name,
        args.connection,
    )
    playbook = Path(__file__).resolve().parent / "ansible_backup" / "reset.yml"

    print()
    print(f"Listing volume contents on {fqdn} ...")
    print()

    rc = run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={"do_delete": False},
        verbose=True,
        host_key_policy=args.host_key_policy,
    )
    if rc != 0:
        return rc

    if not args.yes and not confirm(
        f"WARNING: This will permanently delete all volume contents on {fqdn}. "
        f"Continue?"
    ):
        print("Reset cancelled.")
        return EXIT_SUCCESS

    print()
    print(f"Resetting volumes on {fqdn} ...")
    print()

    return run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={"do_delete": True},
        verbose=True,
        host_key_policy=args.host_key_policy,
    )


# ---------------------------------------------------------------------------
# Exec helpers
# ---------------------------------------------------------------------------


def resolve_exec_container(
    toml_path: Path, config: DeppConfig, requested: str | None
) -> str:
    """Resolve the full podman container name for ``depp exec``.

    ``podman kube play`` names containers ``{pod_name}-{container_name}``;
    both parts come from a validated kube.yaml. Invalid manifests fail instead
    of falling back to a guessed container name.
    """
    try:
        pod_name, containers, _pvcs = parse_kube_manifest(config.kube_manifest)
    except ManifestError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    if requested:
        if requested not in containers:
            print(
                f"Error: container '{requested}' not found in "
                f"{config.display(config.kube_manifest)}. "
                f"Available containers: {', '.join(containers)}",
                file=sys.stderr,
            )
            sys.exit(EXIT_ERROR)
        return f"{pod_name}-{requested}"

    if len(containers) == 1:
        return f"{pod_name}-{containers[0]}"
    if "app" in containers:
        return f"{pod_name}-app"

    print(
        "Error: the pod defines several containers and none is named 'app'; "
        f"pick one with --container. Available: {', '.join(containers)}",
        file=sys.stderr,
    )
    sys.exit(EXIT_ERROR)


def run_exec_command(args: argparse.Namespace) -> int:
    from depp.console.exec import run_exec

    toml_path, config, fqdn, _app_name = load_project(args)

    command = list(args.exec_command)

    container_name = ""
    if not args.host:
        container_name = resolve_exec_container(toml_path, config, args.container)

    return run_exec(
        fqdn=fqdn,
        container_name=container_name,
        command=command,
        tty=args.tty,
        host_only=args.host,
        host_key_policy=args.host_key_policy,
        connection=args.connection,
        user=config.host.user,
    )


def run_doctor(args: argparse.Namespace) -> int:
    """Validate control node, project inputs, connection, and target runtime."""
    toml_path, config, fqdn, app_name = load_project(args)
    checks = control_node_checks(connection=args.connection)

    try:
        containerfile, kube_file, env_path, manifest, _configmap = (
            validate_project_inputs(config)
        )
        commit_hash, _message = get_git_info(config.app.project_root)
        git_dirty = check_git_dirty(config.app.project_root)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    if env_path.exists():
        secure_env = not bool(env_path.stat().st_mode & 0o077)
        env_label = config.display(env_path)
        checks.append(
            DoctorCheck(
                name=f"{env_label} permissions",
                ok=secure_env,
                detail="owner-only" if secure_env else f"run chmod 600 {env_label}",
            )
        )

    connection = ssh_connection_string(
        fqdn, args.host_key_policy, args.connection, config.host.user
    )
    lines = [
        f"Configuration: {toml_path}",
        f"App:           {app_name}",
        f"Project:       {config.app.project_root}",
        f"Build file:    {containerfile.relative_to(config.app.project_root)}",
        f"Manifest:      {kube_file}",
        f"Pod:           {manifest.pod_name}",
        f"Env file:      {env_path if env_path.exists() else 'none'}",
        f"Git:           {commit_hash} ({'dirty' if git_dirty else 'clean'})",
        f"Target:        {fqdn}",
        f"Deploy user:   {config.host.user}",
        f"Connection:    {connection}",
        "",
        "Control node:",
        *[
            f"  [{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}"
            for check in checks
        ],
    ]
    print_summary("depp doctor", lines)

    if any(not check.ok for check in checks):
        print("Error: Control-node requirements are not satisfied.", file=sys.stderr)
        return EXIT_ERROR
    if git_dirty and not args.allow_dirty:
        print(
            "Error: Git working tree is dirty; rerun doctor with --allow-dirty "
            "if this is intentional.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    inventory = build_inventory_or_exit(build_inventory, config, args.connection)
    playbook = Path(__file__).resolve().parent / "ansible_common" / "doctor.yml"
    print("\nChecking target capabilities...\n")
    return run_ansible_playbook(
        playbook_path=playbook,
        inventory=inventory,
        extra_vars={},
        verbose=args.verbose,
        host_key_policy=args.host_key_policy,
    )


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _add_host_key_policy_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host-key-policy",
        choices=HOST_KEY_POLICIES,
        default="strict",
        help="SSH host-key verification policy (default: strict)",
    )


def _add_connection_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--connection",
        choices=CONNECTION_MODES,
        default="ssh",
        help="Target connection mode (default: ssh)",
    )


def _add_toml_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "toml_file",
        type=Path,
        nargs="?",
        default=None,
        help=(
            "Path to the depp.toml configuration file "
            f"(default: ./{DEPLOY_DIR_NAME}/{TOML_FILENAME}, then ./{TOML_FILENAME})"
        ),
        metavar="DEPLOY_TOML",
    )


def _add_common_args(
    parser: argparse.ArgumentParser, *, check: bool = False, yes: bool = False
) -> None:
    """Add the arguments shared by the subcommands."""
    _add_toml_arg(parser)
    if check:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Run ansible-playbook in check mode (dry-run, no changes on remote)",
        )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose ansible output",
    )
    _add_connection_arg(parser)
    _add_host_key_policy_arg(parser)
    if yes:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation (required for non-interactive execution)",
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    version = _read_version()

    parser = argparse.ArgumentParser(
        prog="depp",
        description="DEployment and Provisioning Python wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- provision ---
    provision_parser = subparsers.add_parser(
        "provision",
        help="Prepare a remote host for rootless-podman deployments",
        description=(
            "Reads a depp.toml file and runs the Ansible provisioning playbook "
            "with elevated privileges (become: true).\n\n"
            "Note: SSH infrastructure (keys, authentication) is assumed to be "
            "configured externally and is out of scope for this tool."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_common_args(provision_parser, check=True, yes=True)
    provision_parser.set_defaults(handler=run_provisioning)
    provision_parser.add_argument(
        "--secrets",
        type=Path,
        default=None,
        metavar="SECRETS_TOML",
        help="Path to the operator config holding secrets like the ACME "
        "external_account_binding (default: ~/.config/depp.toml)",
    )
    provision_parser.add_argument(
        "--ask-become-pass",
        "-K",
        action="store_true",
        dest="ask_become_pass",
        help="Ask for privilege escalation password (passed to ansible-playbook -K)",
    )

    # --- backup ---
    backup_parser = subparsers.add_parser(
        "backup",
        help="Back up named podman volumes from a remote host",
        description=(
            "Reads a depp.toml file and fetches the contents of every volume the "
            "pod declares as a persistentVolumeClaim in kube.yaml into a "
            "backups/<fqdn>/<timestamp>/ directory next to the depp.toml."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_common_args(backup_parser)
    backup_parser.set_defaults(handler=run_backup)

    # --- restore ---
    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore named podman volumes from a local backup directory",
        description=(
            "Reads a depp.toml file, takes a pre-restore backup, stops the pod, "
            "pushes the contents of each volume directory from RESTORE_PATH to the "
            "remote host, then restarts the pod."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_common_args(restore_parser, yes=True)
    restore_parser.set_defaults(handler=run_restore)
    restore_parser.add_argument(
        "restore_path",
        type=Path,
        help=(
            "Path to the backup directory to restore from "
            "(e.g. deploy/backups/host/2026-03-14T103045Z/)"
        ),
        metavar="RESTORE_PATH",
    )

    # --- reset ---
    reset_parser = subparsers.add_parser(
        "reset",
        help="Delete all contents of named podman volumes on a remote host",
        description=(
            "Reads a depp.toml file, takes a backup, lists all files inside the "
            "configured volumes (shown as container-internal paths), then asks for "
            "confirmation before permanently deleting all volume contents.\n\n"
            "The volumes themselves are preserved; only their contents are removed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_common_args(reset_parser, yes=True)
    reset_parser.set_defaults(handler=run_reset)

    # --- deploy ---
    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Deploy the application to a remote host via rootless podman",
        description=(
            "Reads a depp.toml file, copies the Pod manifest (kube.yaml) "
            "and config, and runs the bundled Ansible playbook."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_common_args(deploy_parser, check=True, yes=True)
    deploy_parser.set_defaults(handler=run_deployment)
    deploy_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Deploy uncommitted source using a unique dirty release tag",
    )

    # --- doctor ---
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Validate project, control node, connection, and target readiness",
        description=(
            "Runs non-mutating checks for local dependencies, deployment inputs, "
            "Git state, SSH connectivity, rootless Podman, rsync, and user systemd."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_common_args(doctor_parser)
    doctor_parser.set_defaults(handler=run_doctor)
    doctor_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Treat an uncommitted project working tree as intentional",
    )

    # --- exec ---
    exec_parser = subparsers.add_parser(
        "exec",
        help="Run a command in a deployed container or on the remote host",
        description=(
            "Executes a command inside the running application container on the "
            "remote host via SSH. If no command is given, opens an interactive "
            "shell (/bin/sh in the container, or a login shell with --host). "
            "A TTY is allocated when this terminal has one, so prompting "
            "commands work; override with --tty / --no-tty."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_toml_arg(exec_parser)
    exec_parser.add_argument(
        "--host",
        action="store_true",
        help="Run command on the remote host instead of inside the container",
    )
    exec_parser.add_argument(
        "--container",
        metavar="NAME",
        help="Container (as named in kube.yaml) to exec into. "
        "Default: the pod's only container, or the one named 'app'.",
    )
    _add_connection_arg(exec_parser)
    _add_host_key_policy_arg(exec_parser)
    tty_group = exec_parser.add_mutually_exclusive_group()
    tty_group.add_argument(
        "-t",
        "--tty",
        dest="tty",
        action="store_const",
        const=True,
        help="Force TTY allocation (default: auto — a TTY is allocated when "
        "both stdin and stdout are terminals).",
    )
    tty_group.add_argument(
        "-T",
        "--no-tty",
        dest="tty",
        action="store_const",
        const=False,
        help="Never allocate a TTY, even from an interactive terminal.",
    )
    exec_parser.set_defaults(handler=run_exec_command, tty=None)
    exec_parser.add_argument(
        "exec_command",
        nargs="*",
        help="Command to execute; put -- before it (e.g. -- ls -la), always "
        "when DEPLOY_TOML is omitted. Default: /bin/sh for interactive shell.",
        metavar="COMMAND",
    )

    if argv is None:
        argv = sys.argv[1:]
    # With DEPLOY_TOML optional, `depp exec -- ls` would otherwise bind `ls`
    # to the toml positional. Everything after `--` is the command, full stop.
    if argv[:1] == ["exec"] and "--" in argv:
        separator = argv.index("--")
        args = parser.parse_args(argv[:separator])
        args.exec_command = argv[separator + 1 :]
        return args
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        args = parse_args()
        exit_code = args.handler(args)
    except ConfirmationUnavailableError as error:
        print(f"Error: {error}", file=sys.stderr)
        exit_code = EXIT_ERROR
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        exit_code = EXIT_INTERRUPTED
    sys.exit(exit_code)
