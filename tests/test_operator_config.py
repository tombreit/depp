"""Tests for the operator config (~/.config/depp.toml, depp/cli.py)."""

import pytest

from depp.cli import load_operator_acme, operator_config_path

FQDN = "myapp.example.com"
PROD_CA = "https://acme-v02.api.letsencrypt.org/directory"


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    """Point $XDG_CONFIG_HOME at an empty dir so real operator configs never leak in."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def write_config(tmp_path, content):
    path = tmp_path / "operator.toml"
    path.write_text(content)
    path.chmod(0o600)
    return path


def test_missing_default_path_returns_empty():
    assert load_operator_acme(None) == {}


def test_missing_explicit_path_exits(tmp_path):
    with pytest.raises(SystemExit):
        load_operator_acme(tmp_path / "nope.toml")


def test_acme_table_returned(tmp_path):
    path = write_config(
        tmp_path,
        f'[acme]\nexternal_account_binding = "kid hmac"\n'
        f'certificate_authority = "{PROD_CA}"\n',
    )
    assert load_operator_acme(path) == {
        "external_account_binding": "kid hmac",
        "certificate_authority": PROD_CA,
    }


def test_merge_operator_wins_project_keys_survive(tmp_path):
    path = write_config(
        tmp_path, '[acme]\nexternal_account_binding = "op-kid op-hmac"\n'
    )
    toml_data = {
        "acme": {
            "contact_email": "x@example.com",
            "external_account_binding": "project-kid project-hmac",
        }
    }
    merged = {**toml_data.get("acme", {}), **load_operator_acme(path)}
    assert merged["external_account_binding"] == "op-kid op-hmac"
    assert merged["contact_email"] == "x@example.com"


def test_default_path_used_when_present(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    cfg = xdg / "depp.toml"
    cfg.write_text('[acme]\nexternal_account_binding = "kid hmac"\n')
    cfg.chmod(0o600)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert operator_config_path() == cfg
    assert load_operator_acme(None) == {"external_account_binding": "kid hmac"}


def test_unparsable_file_exits(tmp_path):
    path = write_config(tmp_path, "[unclosed\n")
    with pytest.raises(SystemExit):
        load_operator_acme(path)


def test_non_table_acme_exits(tmp_path):
    path = write_config(tmp_path, 'acme = "invalid"\n')

    with pytest.raises(SystemExit):
        load_operator_acme(path)


def test_world_readable_file_warns(tmp_path, capsys):
    path = write_config(tmp_path, '[acme]\nexternal_account_binding = "kid hmac"\n')
    path.chmod(0o644)
    load_operator_acme(path)
    assert "chmod 600" in capsys.readouterr().err
