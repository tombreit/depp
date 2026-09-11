"""Tests for the inventory builders (depp/ansible_common/inventory.py)."""

from pathlib import Path

import pytest

from depp.ansible_common.inventory import (
    CADDY_HOST_PORT_DEPRECATION,
    DEFAULT_ACME_CERTIFICATE_AUTHORITY,
    DEPP_SSH_KEY_PATH,
    DeppConfig,
    build_backup_inventory,
    build_inventory,
    build_provisioning_inventory,
    build_restore_inventory,
    parse_acme_config,
    parse_deploy_config,
    parse_listen_port,
    parse_loopback_port,
    parse_server_aliases,
)

TOML = {
    "host": {"fqdn": "app.example.com", "loopback_port": 8300},
    "app": {"name": "myapp", "project_root": "."},
    "acme": {"contact_email": "ops@example.com"},
}
SOURCE_PATH = Path(__file__).parents[1] / "depp.toml"


def config(data=TOML):
    return DeppConfig.from_mapping(SOURCE_PATH, data)


def test_build_inventory_host_vars():
    inv = build_inventory(config())
    host_vars = inv["all"]["hosts"]["app.example.com"]
    assert host_vars["ansible_user"] == "app.example.com"
    assert host_vars["ansible_ssh_private_key_file"] == DEPP_SSH_KEY_PATH
    assert host_vars["app_name"] == "myapp"
    assert host_vars["image_name"] == "myapp"  # falls back to name
    assert host_vars["host_loopback_port"] == 8300
    assert host_vars["app_listen_port"] == 80
    assert host_vars["health_path"] == "/"
    assert host_vars["health_timeout"] == 30
    assert "caddy_host_port" not in host_vars


def test_build_inventory_image_name_and_containerfile():
    toml = {
        **TOML,
        "app": {
            **TOML["app"],
            "image_name": "img",
            "containerfile": "Dockerfile",
        },
    }
    host_vars = build_inventory(config(toml))["all"]["hosts"]["app.example.com"]
    assert host_vars["image_name"] == "img"
    assert host_vars["containerfile"] == "Dockerfile"


def test_build_inventory_local_connection_omits_ssh_identity():
    host_vars = build_inventory(config(), "local")["all"]["hosts"]["app.example.com"]

    assert host_vars["ansible_connection"] == "local"
    assert "ansible_user" not in host_vars
    assert "ansible_ssh_private_key_file" not in host_vars


def test_build_inventory_missing_fqdn():
    with pytest.raises(ValueError, match="fqdn"):
        config({**TOML, "host": {"loopback_port": 8300}})


def test_build_inventory_missing_name():
    with pytest.raises(ValueError, match="name"):
        config({**TOML, "app": {}})


def test_build_inventory_missing_port():
    with pytest.raises(ValueError, match="loopback_port"):
        config({**TOML, "host": {"fqdn": "app.example.com"}})


@pytest.mark.parametrize("bad_port", ["8300", 0, -1, 65536, True, 3.14])
def test_parse_loopback_port_rejects_non_port_values(bad_port):
    with pytest.raises(ValueError, match="loopback_port"):
        parse_loopback_port({"loopback_port": bad_port})


def test_caddy_host_port_alias_is_accepted_with_deprecation():
    """A depp.toml written for 0.0.1 keeps working and says what to rename."""
    cfg = config({**TOML, "host": {"fqdn": "app.example.com", "caddy_host_port": 8300}})
    assert cfg.host.loopback_port == 8300
    assert cfg.deprecations == (CADDY_HOST_PORT_DEPRECATION,)
    assert config().deprecations == ()


def test_loopback_port_and_alias_together_is_an_error():
    with pytest.raises(ValueError, match="both"):
        parse_loopback_port({"loopback_port": 8300, "caddy_host_port": 8300})


def test_listen_port_defaults_to_80():
    assert parse_listen_port({}) == 80
    cfg = config({**TOML, "app": {**TOML["app"], "listen_port": 8080}})
    assert cfg.app.listen_port == 8080
    host_vars = build_inventory(cfg)["all"]["hosts"]["app.example.com"]
    assert host_vars["app_listen_port"] == 8080


@pytest.mark.parametrize("bad_port", ["80", 0, 65536, True, None])
def test_listen_port_rejects_non_port_values(bad_port):
    with pytest.raises(ValueError, match="listen_port"):
        parse_listen_port({"listen_port": bad_port})


def test_parse_deploy_config_defaults():
    assert parse_deploy_config({}) == {"health_path": "/", "health_timeout": 30}


@pytest.mark.parametrize("bad_path", ["healthz", 42, None])
def test_parse_deploy_config_rejects_bad_health_path(bad_path):
    with pytest.raises(ValueError, match="health_path"):
        parse_deploy_config({"health_path": bad_path})


@pytest.mark.parametrize("bad_timeout", [0, -5, "30", 1.5])
def test_parse_deploy_config_rejects_bad_health_timeout(bad_timeout):
    with pytest.raises(ValueError, match="health_timeout"):
        parse_deploy_config({"health_timeout": bad_timeout})


def test_build_provisioning_inventory_defaults_to_staging_ca():
    host_vars = build_provisioning_inventory(config())["all"]["hosts"][
        "app.example.com"
    ]
    assert host_vars["acme_certificate_authority"] == DEFAULT_ACME_CERTIFICATE_AUTHORITY
    assert host_vars["acme_contact_email"] == "ops@example.com"
    assert host_vars["host_loopback_port"] == 8300
    assert host_vars["app_name"] == "myapp"


def test_build_provisioning_inventory_supports_local_connection():
    host_vars = build_provisioning_inventory(config(), "local")["all"]["hosts"][
        "app.example.com"
    ]

    assert host_vars["ansible_connection"] == "local"


def test_build_provisioning_inventory_requires_contact_email():
    with pytest.raises(ValueError, match="contact_email"):
        config({**TOML, "acme": {}})


@pytest.mark.parametrize(
    "field",
    ["certificate_authority", "external_account_binding"],
)
@pytest.mark.parametrize(
    "payload", ["a\nMDContactEmail evil@example.com", "a\tb", "a\rb"]
)
def test_parse_acme_config_rejects_control_characters(field, payload):
    """Both values are templated into the root-owned Apache vhost.

    urlparse() strips ASCII newlines before validating, so a URL carrying one
    used to pass the shape check and still reach the config file intact.
    """
    acme = {"contact_email": "ops@example.com"}
    acme[field] = (
        f"https://acme.example/directory{payload}"
        if field == "certificate_authority"
        else f"kid hmac{payload}"
    )
    with pytest.raises(ValueError, match="control characters"):
        parse_acme_config(acme)


def test_parse_acme_config_accepts_plain_values():
    parsed = parse_acme_config(
        {
            "contact_email": "ops@example.com",
            "certificate_authority": "https://acme.example/directory",
            "external_account_binding": "kid hmac",
        }
    )
    assert parsed["acme_certificate_authority"] == "https://acme.example/directory"
    assert parsed["acme_external_account_binding"] == "kid hmac"


def test_parse_server_aliases_defaults_to_empty():
    assert parse_server_aliases({}) == []


def test_parse_server_aliases_strips_whitespace():
    assert parse_server_aliases({"server_aliases": [" www.example.com "]}) == [
        "www.example.com"
    ]


@pytest.mark.parametrize("bad", ["www.example.com", 42, {"a": 1}])
def test_parse_server_aliases_rejects_non_list(bad):
    with pytest.raises(ValueError, match="server_aliases"):
        parse_server_aliases({"server_aliases": bad})


@pytest.mark.parametrize("bad_entry", ["", "   ", 42, None])
def test_parse_server_aliases_rejects_bad_entries(bad_entry):
    with pytest.raises(ValueError, match="server_aliases"):
        parse_server_aliases({"server_aliases": [bad_entry]})


def test_build_provisioning_inventory_without_aliases():
    host_vars = build_provisioning_inventory(config())["all"]["hosts"][
        "app.example.com"
    ]
    assert host_vars["server_aliases"] == []


def test_build_provisioning_inventory_with_aliases():
    toml = {
        **TOML,
        "host": {**TOML["host"], "server_aliases": ["public.example.org"]},
    }
    host_vars = build_provisioning_inventory(config(toml))["all"]["hosts"][
        "app.example.com"
    ]
    assert host_vars["server_aliases"] == ["public.example.org"]


def test_build_backup_inventory():
    inv = build_backup_inventory(config(), "/backups/x", ["vol-a", "vol-b"], "mypod")
    host_vars = inv["all"]["hosts"]["app.example.com"]
    assert host_vars["backup_dirs"] == ["vol-a", "vol-b"]
    assert host_vars["backup_local_dest"] == "/backups/x"
    assert host_vars["app_name"] == "myapp"
    assert host_vars["pod_name"] == "mypod"


def test_build_backup_inventory_pod_name_falls_back_to_app_name():
    inv = build_backup_inventory(config(), "/backups/x", ["vol-a"], "")
    host_vars = inv["all"]["hosts"]["app.example.com"]
    assert host_vars["pod_name"] == "myapp"


def test_build_restore_inventory():
    inv = build_restore_inventory(config(), "/backups/x", ["vol-a"], "mypod")
    host_vars = inv["all"]["hosts"]["app.example.com"]
    assert host_vars["backup_dirs"] == ["vol-a"]
    assert host_vars["restore_path"] == "/backups/x"
    assert host_vars["pod_name"] == "mypod"


@pytest.mark.parametrize("builder", [build_backup_inventory, build_restore_inventory])
def test_data_inventory_supports_local_connection(builder):
    inv = builder(config(), "/backups/x", ["vol-a"], "mypod", "local")
    host_vars = inv["all"]["hosts"]["app.example.com"]

    assert host_vars["ansible_connection"] == "local"
    assert "ansible_user" not in host_vars
    assert "ansible_ssh_private_key_file" not in host_vars


def test_inventories_use_the_explicit_deploy_user():
    cfg = config({**TOML, "host": {**TOML["host"], "user": "surl"}})

    deploy_vars = build_inventory(cfg)["all"]["hosts"]["app.example.com"]
    assert deploy_vars["ansible_user"] == "surl"

    provision_vars = build_provisioning_inventory(cfg)["all"]["hosts"][
        "app.example.com"
    ]
    assert provision_vars["deploy_user"] == "surl"
    assert "ansible_user" not in provision_vars

    backup_vars = build_backup_inventory(cfg, "/tmp/b", ["vol"], "pod")["all"]["hosts"][
        "app.example.com"
    ]
    assert backup_vars["ansible_user"] == "surl"


def test_provisioning_inventory_defaults_deploy_user_to_fqdn():
    host_vars = build_provisioning_inventory(config())["all"]["hosts"][
        "app.example.com"
    ]
    assert host_vars["deploy_user"] == "app.example.com"
