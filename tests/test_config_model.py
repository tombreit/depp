"""Tests for the unified validated depp.toml model."""

from argparse import Namespace

import pytest

from depp import cli
from depp.ansible_common.inventory import DeppConfig


def mapping(project_root="."):
    return {
        "app": {"name": "example", "project_root": project_root},
        "host": {
            "fqdn": "app.example.com",
            "caddy_host_port": 8100,
            "server_aliases": ["public.example.com"],
        },
        "acme": {"contact_email": "ops@example.com"},
        "deploy": {"health_path": "/health/", "health_timeout": 45},
    }


def test_model_resolves_fields_and_defaults(tmp_path):
    config = DeppConfig.from_mapping(tmp_path / "depp.toml", mapping())

    assert config.app.project_root == tmp_path
    assert config.app.image_name == "example"
    assert config.app.containerfile == "Containerfile"
    assert config.host.server_aliases == ("public.example.com",)
    assert config.deploy.health_path == "/health/"
    assert config.deploy.health_timeout == 45


@pytest.mark.parametrize("section", ["app", "host", "acme", "deploy"])
def test_model_rejects_non_table_sections(tmp_path, section):
    data = mapping()
    data[section] = "invalid"

    with pytest.raises(ValueError, match=rf"\[{section}\].*table"):
        DeppConfig.from_mapping(tmp_path / "depp.toml", data)


@pytest.mark.parametrize(
    ("section", "key"),
    [
        (None, "unknown"),
        ("app", "imag_name"),
        ("host", "port"),
        ("acme", "email"),
        ("deploy", "timeout"),
    ],
)
def test_model_rejects_unknown_keys(tmp_path, section, key):
    data = mapping()
    target = data if section is None else data[section]
    target[key] = "value"

    with pytest.raises(ValueError, match=rf"unknown key.*{key}"):
        DeppConfig.from_mapping(tmp_path / "depp.toml", data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fqdn", "Upper.example.com"),
        ("fqdn", f"{'a' * 25}.example.com"),
        ("server_aliases", ["invalid_alias.example.com"]),
    ],
)
def test_model_rejects_invalid_host_identity(tmp_path, field, value):
    data = mapping()
    data["host"][field] = value

    with pytest.raises(ValueError, match="DNS name"):
        DeppConfig.from_mapping(tmp_path / "depp.toml", data)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("contact_email", "invalid", "contact_email"),
        ("certificate_authority", "ftp://ca.example.com", "HTTP"),
        ("external_account_binding", 42, "external_account_binding"),
    ],
)
def test_model_rejects_invalid_acme_values(tmp_path, field, value, message):
    data = mapping()
    data["acme"][field] = value

    with pytest.raises(ValueError, match=message):
        DeppConfig.from_mapping(tmp_path / "depp.toml", data)


def test_operator_acme_override_returns_new_validated_model(tmp_path):
    config = DeppConfig.from_mapping(tmp_path / "depp.toml", mapping())

    overridden = config.with_acme_overrides(
        {"certificate_authority": "https://ca.example.com/directory"}
    )

    assert config.acme.certificate_authority != overridden.acme.certificate_authority
    assert overridden.acme.contact_email == config.acme.contact_email


def test_operator_acme_override_rejects_unknown_key(tmp_path):
    config = DeppConfig.from_mapping(tmp_path / "depp.toml", mapping())

    with pytest.raises(ValueError, match="unknown key"):
        config.with_acme_overrides({"certificate_url": "https://example.com"})


def test_load_project_reports_model_error(tmp_path, capsys):
    config_path = tmp_path / "depp.toml"
    config_path.write_text("[app]\nname = 'Invalid_Name'\nproject_root = '.'\n")

    with pytest.raises(SystemExit, match=str(cli.EXIT_ERROR)):
        cli.load_project(Namespace(toml_file=config_path))

    assert "Error:" in capsys.readouterr().err
