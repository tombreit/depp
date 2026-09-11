"""Validated project configuration and Ansible inventory generation."""

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from depp.layout import (
    ENV_FILENAME,
    KUBE_MANIFEST_FILENAME,
    VHOST_SNIPPET_FILENAME,
    default_project_root,
    display_path,
    resolve_deploy_dir,
)
from depp.validation import has_control_characters, is_valid_dns_name

DEPP_SSH_KEY_PATH = "~/.ssh/id_ed25519.depp"
DEFAULT_ACME_CERTIFICATE_AUTHORITY = (
    "https://acme-staging-v02.api.letsencrypt.org/directory"
)

# After the pod is (re)started, depp polls the app over HTTP and only reports
# the deploy as successful once it actually serves. These are the defaults for
# the optional [deploy] section of depp.toml.
DEFAULT_HEALTH_PATH = "/"
DEFAULT_HEALTH_TIMEOUT = 30

# The port the application (or its Caddy sidecar) binds inside the pod. The
# generated unit publishes 127.0.0.1:<loopback_port>:<listen_port>.
DEFAULT_LISTEN_PORT = 80
CADDY_HOST_PORT_DEPRECATION = (
    "[host] caddy_host_port is deprecated; rename it to loopback_port "
    "(same meaning: the 127.0.0.1 port Apache proxies to)."
)
CONNECTION_MODES = ("ssh", "local")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The deployment user on the host defaults to the fqdn itself (one app, one
# user, one vhost). useradd caps names at 32 characters, so the fqdn inherits
# that cap unless [host] user names the account explicitly.
LINUX_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MAX_FQDN_LENGTH_AS_USER = 32
MAX_FQDN_LENGTH = 253


@dataclass(frozen=True)
class AppConfig:
    name: str
    project_root: Path
    image_name: str
    containerfile: str
    listen_port: int


@dataclass(frozen=True)
class HostConfig:
    fqdn: str
    # The Linux account depp deploys as; the fqdn unless [host] user says so.
    user: str
    loopback_port: int
    server_aliases: tuple[str, ...]


@dataclass(frozen=True)
class AcmeConfig:
    contact_email: str
    certificate_authority: str
    external_account_binding: str | None


@dataclass(frozen=True)
class DeployConfig:
    health_path: str
    health_timeout: int


@dataclass(frozen=True)
class DeppConfig:
    source_path: Path
    # Where kube.yaml, .env and vhost.conf live; see depp.layout.
    deploy_dir: Path
    app: AppConfig
    host: HostConfig
    acme: AcmeConfig
    deploy: DeployConfig
    # Human-readable notices about accepted-but-deprecated keys, printed once
    # by the CLI so a legacy depp.toml keeps working while saying what to rename.
    deprecations: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, source_path: Path, data: dict[str, Any]) -> "DeppConfig":
        source_path = source_path.resolve()
        if not isinstance(data, dict):
            raise ValueError("depp.toml root must be a table")
        _reject_unknown(data, {"app", "host", "acme", "deploy"}, "depp.toml")
        host_data = _table(data, "host")
        app_data = _table(data, "app")
        acme_data = _table(data, "acme")
        deploy_data = _table(data, "deploy", required=False)
        _reject_unknown(
            app_data,
            {"name", "project_root", "image_name", "containerfile", "listen_port"},
            "[app]",
        )
        _reject_unknown(
            host_data,
            {"fqdn", "user", "loopback_port", "caddy_host_port", "server_aliases"},
            "[host]",
        )
        _reject_unknown(
            acme_data,
            {"contact_email", "certificate_authority", "external_account_binding"},
            "[acme]",
        )
        _reject_unknown(deploy_data, {"health_path", "health_timeout"}, "[deploy]")

        user = parse_host_user(host_data)
        fqdn = parse_host_fqdn(
            host_data,
            max_length=MAX_FQDN_LENGTH if user else MAX_FQDN_LENGTH_AS_USER,
        )
        app_values = parse_app_config(app_data)
        project_root_value = app_data.get("project_root")
        if project_root_value is None:
            project_root = default_project_root(source_path)
        else:
            if (
                not isinstance(project_root_value, str)
                or not project_root_value.strip()
            ):
                raise ValueError("'project_root' in [app] must be a non-empty path")
            project_root = Path(project_root_value)
            if not project_root.is_absolute():
                project_root = source_path.parent / project_root
            project_root = project_root.resolve()
        if not project_root.is_dir():
            raise ValueError(f"project_root does not exist: {project_root}")
        deploy_dir = resolve_deploy_dir(source_path, project_root)

        image_name = app_values.get("image_name", app_values["name"])
        containerfile = app_values.get("containerfile", "Containerfile")
        acme_values = parse_acme_config(acme_data)
        deploy_values = parse_deploy_config(deploy_data)
        deprecations = []
        if "caddy_host_port" in host_data:
            deprecations.append(CADDY_HOST_PORT_DEPRECATION)

        return cls(
            source_path=source_path,
            deploy_dir=deploy_dir,
            app=AppConfig(
                name=app_values["name"],
                project_root=project_root,
                image_name=image_name,
                containerfile=containerfile,
                listen_port=parse_listen_port(app_data),
            ),
            host=HostConfig(
                fqdn=fqdn,
                user=user or fqdn,
                loopback_port=parse_loopback_port(host_data),
                server_aliases=tuple(parse_server_aliases(host_data)),
            ),
            acme=AcmeConfig(
                contact_email=acme_values["acme_contact_email"],
                certificate_authority=acme_values["acme_certificate_authority"],
                external_account_binding=acme_values["acme_external_account_binding"],
            ),
            deploy=DeployConfig(**deploy_values),
            deprecations=tuple(deprecations),
        )

    @property
    def kube_manifest(self) -> Path:
        return self.deploy_dir / KUBE_MANIFEST_FILENAME

    @property
    def env_file(self) -> Path:
        """Optional; its presence decides whether depp renders a ConfigMap."""
        return self.deploy_dir / ENV_FILENAME

    @property
    def vhost_snippet(self) -> Path:
        """Optional Apache directives installed by ``depp provision``."""
        return self.deploy_dir / VHOST_SNIPPET_FILENAME

    def display(self, path: Path) -> str:
        """A path as the operator knows it: relative to the project when inside."""
        return display_path(path, self.app.project_root)

    def with_acme_overrides(self, overrides: dict[str, Any]) -> "DeppConfig":
        _reject_unknown(
            overrides,
            {"contact_email", "certificate_authority", "external_account_binding"},
            "operator [acme]",
        )
        values = parse_acme_config(
            {
                "contact_email": self.acme.contact_email,
                "certificate_authority": self.acme.certificate_authority,
                "external_account_binding": self.acme.external_account_binding,
                **overrides,
            }
        )
        return replace(
            self,
            acme=AcmeConfig(
                contact_email=values["acme_contact_email"],
                certificate_authority=values["acme_certificate_authority"],
                external_account_binding=values["acme_external_account_binding"],
            ),
        )


def _table(data: dict[str, Any], name: str, *, required: bool = True) -> dict[str, Any]:
    value = data.get(name)
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        requirement = "is required and" if required else ""
        raise ValueError(f"[{name}] {requirement} must be a table")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown key(s) in {location}: {', '.join(unknown)}")


def parse_host_fqdn(host_cfg: dict[str, Any], *, max_length: int) -> str:
    """Parse and validate [host].fqdn, the inventory hostname.

    ``max_length`` is 32 while the fqdn doubles as the Linux username and
    253 once [host] user names the account explicitly.
    """
    fqdn = host_cfg.get("fqdn", "")
    if not isinstance(fqdn, str) or not fqdn:
        raise ValueError("'fqdn' is required in [host] section of depp.toml.")
    if not is_valid_dns_name(fqdn, max_length=max_length, allow_dots=True):
        hint = ""
        if max_length == MAX_FQDN_LENGTH_AS_USER and len(fqdn) > max_length:
            hint = (
                f" of at most {max_length} characters, because it doubles as the "
                "deployment username; set [host] user to lift that limit"
            )
        raise ValueError(f"'fqdn' in [host] must be a lowercase DNS name{hint}")
    return fqdn


def parse_host_user(host_cfg: dict[str, Any]) -> str | None:
    """Parse the optional [host].user, the Linux account depp deploys as."""
    if "user" not in host_cfg:
        return None
    user = host_cfg["user"]
    if not isinstance(user, str) or not LINUX_USERNAME.fullmatch(user):
        raise ValueError(
            "'user' in [host] must be a Linux username matching "
            f"{LINUX_USERNAME.pattern} (got {user!r})."
        )
    return user


def parse_app_config(app_cfg: dict[str, Any]) -> dict[str, str]:
    """Parse and validate [app] section from TOML.

    Returns:
        Dictionary with 'name' and optional 'image_name'/'containerfile'.

    Raises:
        ValueError: If required fields are missing.
    """
    name = app_cfg.get("name", "")
    if not isinstance(name, str) or not name:
        raise ValueError("'name' is required in [app] section of depp.toml.")
    _validate_dns_name(name, "'name' in [app]", max_length=63, allow_dots=False)
    result: dict[str, str] = {"name": name}
    image_name = app_cfg.get("image_name")
    if "image_name" in app_cfg:
        if not isinstance(image_name, str) or not image_name:
            raise ValueError("'image_name' in [app] must be a non-empty string")
        _validate_dns_name(
            image_name, "'image_name' in [app]", max_length=63, allow_dots=False
        )
        result["image_name"] = image_name
    containerfile = app_cfg.get("containerfile")
    if "containerfile" in app_cfg:
        if not isinstance(containerfile, str) or not containerfile.strip():
            raise ValueError("'containerfile' in [app] must be a non-empty string")
        result["containerfile"] = containerfile
    return result


def parse_acme_config(acme_cfg: dict[str, Any]) -> dict[str, str | None]:
    """Parse and validate [acme] section from TOML.

    Required:
        contact_email

    Optional:
        certificate_authority (defaults to Let's Encrypt staging endpoint)
        external_account_binding
    """
    contact_email = acme_cfg.get("contact_email", "")
    if not isinstance(contact_email, str) or not EMAIL.fullmatch(contact_email):
        raise ValueError("'contact_email' is required in [acme] section of depp.toml.")

    certificate_authority = acme_cfg.get(
        "certificate_authority", DEFAULT_ACME_CERTIFICATE_AUTHORITY
    )
    if not isinstance(certificate_authority, str):
        raise ValueError("'certificate_authority' in [acme] must be a URL")
    # urlparse strips ASCII newlines before parsing, so a value that embeds one
    # would validate here and still reach the Apache vhost intact. Both ACME
    # strings are templated into that root-owned config, so reject control
    # characters before the URL shape is even considered.
    if has_control_characters(certificate_authority):
        raise ValueError(
            "'certificate_authority' in [acme] must not contain control characters"
        )
    parsed_authority = urlparse(certificate_authority)
    if parsed_authority.scheme not in {"http", "https"} or not parsed_authority.netloc:
        raise ValueError("'certificate_authority' in [acme] must be an HTTP(S) URL")
    eab = acme_cfg.get("external_account_binding")
    if eab is not None and (not isinstance(eab, str) or not eab.strip()):
        raise ValueError("'external_account_binding' in [acme] must be a string")
    if eab is not None and has_control_characters(eab):
        raise ValueError(
            "'external_account_binding' in [acme] must not contain control characters"
        )

    return {
        "acme_contact_email": contact_email,
        "acme_certificate_authority": certificate_authority,
        "acme_external_account_binding": eab,
    }


def parse_deploy_config(deploy_cfg: dict[str, Any]) -> dict[str, Any]:
    """Parse the optional [deploy] section from TOML.

    Controls the post-restart health gate. All fields are optional and fall
    back to safe defaults, so an absent [deploy] section is fine.

    Optional:
        health_path     — URL path polled on the host loopback port
                          (default ``/``).
        health_timeout  — seconds to keep polling before the deploy is
                          declared failed (default ``30``).
    """
    health_path = deploy_cfg.get("health_path", DEFAULT_HEALTH_PATH)
    if not isinstance(health_path, str) or not health_path.startswith("/"):
        raise ValueError(
            "'health_path' in [deploy] section must be a string starting with '/' "
            f"(got {health_path!r})."
        )

    health_timeout = deploy_cfg.get("health_timeout", DEFAULT_HEALTH_TIMEOUT)
    if not isinstance(health_timeout, int) or health_timeout <= 0:
        raise ValueError(
            "'health_timeout' in [deploy] section must be a positive integer "
            f"(got {health_timeout!r})."
        )

    return {
        "health_path": health_path,
        "health_timeout": health_timeout,
    }


def _validate_port(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not (1 <= value <= 65535)
    ):
        raise ValueError(
            f"{field} must be an integer port number between 1 and 65535 "
            f"(got {value!r})."
        )
    return value


def parse_loopback_port(host_cfg: dict[str, Any]) -> int:
    """Parse [host].loopback_port: the 127.0.0.1 port Apache proxies to.

    ``caddy_host_port`` is the 0.0.1 name for the same value and is still
    accepted, so a depp.toml written for an already-deployed app keeps
    working; ``DeppConfig.deprecations`` carries the rename notice.
    """
    has_new = "loopback_port" in host_cfg
    has_old = "caddy_host_port" in host_cfg
    if has_new and has_old:
        raise ValueError(
            "'loopback_port' and its deprecated alias 'caddy_host_port' are both "
            "set in [host]; keep only loopback_port."
        )
    if has_new:
        return _validate_port(host_cfg["loopback_port"], "'loopback_port' in [host]")
    if has_old:
        return _validate_port(
            host_cfg["caddy_host_port"], "'caddy_host_port' in [host]"
        )
    raise ValueError("'loopback_port' is required in [host] section of depp.toml.")


def parse_listen_port(app_cfg: dict[str, Any]) -> int:
    """Parse the optional [app].listen_port: the port bound inside the pod."""
    if "listen_port" not in app_cfg:
        return DEFAULT_LISTEN_PORT
    return _validate_port(app_cfg["listen_port"], "'listen_port' in [app]")


def parse_server_aliases(host_cfg: dict[str, Any]) -> list[str]:
    """Parse and validate the optional [host].server_aliases from TOML.

    Additional hostnames the Apache vhost should answer to, on top of ``fqdn``.
    Use this when an upstream proxy forwards a friendlier public name here while
    preserving the Host header.

    The aliases are deliberately kept out of the mod_md managed domain (the
    vhost template pins ``MDMembers manual``): such a name usually resolves to
    the upstream proxy, not to this host, so letting mod_md try to include it in
    the certificate would fail ACME validation and can break renewal for the
    working domain.
    """
    aliases = host_cfg.get("server_aliases", [])
    if not isinstance(aliases, list):
        raise ValueError(
            "'server_aliases' in [host] section must be a list of hostnames "
            f"(got {aliases!r})."
        )
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError(
                "each entry in 'server_aliases' in [host] section must be a "
                f"non-empty hostname string (got {alias!r})."
            )
        _validate_dns_name(alias.strip(), "server alias", max_length=253)
    return [alias.strip() for alias in aliases]


def _validate_dns_name(
    value: str,
    field: str,
    *,
    max_length: int,
    allow_dots: bool = True,
) -> None:
    if not is_valid_dns_name(value, max_length=max_length, allow_dots=allow_dots):
        raise ValueError(f"{field} must be a lowercase DNS name")


def build_base_inventory(hostname: str, host_vars: dict[str, Any]) -> dict[str, Any]:
    """Build base Ansible inventory structure."""
    return {
        "all": {
            "hosts": {
                hostname: host_vars,
            }
        }
    }


def _apply_connection(
    host_vars: dict[str, Any],
    deploy_user: str,
    connection: str,
    *,
    as_deploy_user: bool,
) -> None:
    if connection == "local":
        host_vars["ansible_connection"] = "local"
    elif connection == "ssh":
        if as_deploy_user:
            host_vars["ansible_user"] = deploy_user
            host_vars["ansible_ssh_private_key_file"] = DEPP_SSH_KEY_PATH
    else:
        raise ValueError(f"unsupported connection mode {connection!r}")


def build_inventory(config: DeppConfig, connection: str = "ssh") -> dict[str, Any]:
    """Produce a deploy Ansible inventory dict from depp.toml configuration."""
    hostname = config.host.fqdn

    host_vars: dict[str, Any] = {
        "app_name": config.app.name,
        "image_name": config.app.image_name,
        "containerfile": config.app.containerfile,
        "app_listen_port": config.app.listen_port,
        "host_loopback_port": config.host.loopback_port,
        "health_path": config.deploy.health_path,
        "health_timeout": config.deploy.health_timeout,
    }
    _apply_connection(host_vars, config.host.user, connection, as_deploy_user=True)

    return build_base_inventory(hostname, host_vars)


def build_provisioning_inventory(
    config: DeppConfig, connection: str = "ssh"
) -> dict[str, Any]:
    """Produce a provisioning Ansible inventory dict from depp.toml configuration."""
    hostname = config.host.fqdn

    host_vars: dict[str, Any] = {
        # Not ``deploy_user_name``: provision.yml declares that as a play var,
        # which outranks inventory, so it reads this one with a default.
        "deploy_user": config.host.user,
        "app_name": config.app.name,
        "host_loopback_port": config.host.loopback_port,
        "server_aliases": list(config.host.server_aliases),
        "acme_contact_email": config.acme.contact_email,
        "acme_certificate_authority": config.acme.certificate_authority,
        "acme_external_account_binding": config.acme.external_account_binding,
    }
    _apply_connection(host_vars, config.host.user, connection, as_deploy_user=False)

    return build_base_inventory(hostname, host_vars)


def _build_backup_restore_host_vars(
    config: DeppConfig, volumes: list, pod_name: str, connection: str
) -> tuple[str, dict[str, Any]]:
    """Shared host-vars builder for backup and restore inventories.

    ``volumes`` is the resolved list of named podman volumes to operate on
    and ``pod_name`` the pod's name from kube.yaml metadata.name (both
    derived by the caller from kube.yaml). The pod name may differ from
    app_name, which still names the systemd service.
    """
    hostname = config.host.fqdn

    host_vars: dict[str, Any] = {
        "app_name": config.app.name,
        "pod_name": pod_name or config.app.name,
        "backup_dirs": volumes,
    }
    _apply_connection(host_vars, config.host.user, connection, as_deploy_user=True)
    return hostname, host_vars


def build_backup_inventory(
    config: DeppConfig,
    backup_local_dest: str,
    volumes: list,
    pod_name: str,
    connection: str = "ssh",
) -> dict[str, Any]:
    """Produce a backup Ansible inventory dict from depp.toml configuration."""
    hostname, host_vars = _build_backup_restore_host_vars(
        config, volumes, pod_name, connection
    )
    host_vars["backup_local_dest"] = backup_local_dest
    return build_base_inventory(hostname, host_vars)


def build_restore_inventory(
    config: DeppConfig,
    restore_path: str,
    restore_dirs: list,
    pod_name: str,
    connection: str = "ssh",
) -> dict[str, Any]:
    """Produce a restore Ansible inventory dict from depp.toml configuration.

    restore_dirs is the filtered subset of the kube.yaml volumes whose
    directories are actually present in restore_path.
    """
    hostname, host_vars = _build_backup_restore_host_vars(
        config, restore_dirs, pod_name, connection
    )
    host_vars["restore_path"] = restore_path
    return build_base_inventory(hostname, host_vars)
