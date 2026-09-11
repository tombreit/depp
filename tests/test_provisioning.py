"""Tests for provisioning orchestration (depp provision)."""

from argparse import Namespace

from depp import cli
from depp.ansible_common.inventory import DeppConfig


def make_config(toml_path, data=None):
    merged = {
        "app": {"name": "example", "project_root": "."},
        "host": {"fqdn": "example.com", "loopback_port": 8100},
        "acme": {"contact_email": "ops@example.com"},
        **(data or {}),
    }
    return DeppConfig.from_mapping(toml_path, merged)


def provision_args(toml_path, **overrides):
    values = {
        "toml_file": toml_path,
        "check": True,
        "yes": True,
        "verbose": False,
        "host_key_policy": "strict",
        "connection": "ssh",
        "secrets": None,
        "ask_become_pass": False,
    }
    values.update(overrides)
    return Namespace(**values)


def prepare_provisioning(monkeypatch, tmp_path, data=None):
    toml_path = tmp_path / "depp.toml"
    config = make_config(toml_path, data)
    captured = {}

    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: (toml_path, config, config.host.fqdn, config.app.name),
    )
    monkeypatch.setattr(cli, "load_operator_acme", lambda _path: {})

    def fake_run_ansible_playbook(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_ansible_playbook", fake_run_ansible_playbook)
    return toml_path, config, captured


def test_provisioning_invokes_playbook_with_required_arguments(
    tmp_path, monkeypatch, capsys
):
    """Regression: the call once omitted ``extra_vars`` and raised TypeError
    after the operator had already confirmed."""
    toml_path, _config, captured = prepare_provisioning(monkeypatch, tmp_path)

    result = cli.run_provisioning(provision_args(toml_path))

    assert result == 0
    assert captured["playbook_path"].name == "provision.yml"
    assert captured["extra_vars"] == {}
    assert captured["check_mode"] is True
    assert captured["ask_become_pass"] is False
    host_vars = captured["inventory"]["all"]["hosts"]["example.com"]
    assert host_vars["acme_contact_email"] == "ops@example.com"
    assert "STAGING" in capsys.readouterr().out


def test_provisioning_summary_names_the_explicit_user(tmp_path, monkeypatch, capsys):
    toml_path, _config, captured = prepare_provisioning(
        monkeypatch,
        tmp_path,
        {"host": {"fqdn": "example.com", "loopback_port": 8100, "user": "surl"}},
    )

    assert cli.run_provisioning(provision_args(toml_path)) == 0

    out = capsys.readouterr().out
    assert "Deployment user: surl" in out
    assert "surl@example.com" in out
    host_vars = captured["inventory"]["all"]["hosts"]["example.com"]
    assert host_vars["deploy_user"] == "surl"


def write_snippet(tmp_path, contents):
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir(exist_ok=True)
    snippet = deploy_dir / "vhost.conf"
    snippet.write_text(contents)
    return snippet


def test_provisioning_passes_snippet_path_as_extra_var(tmp_path, monkeypatch, capsys):
    toml_path, _config, captured = prepare_provisioning(monkeypatch, tmp_path)
    snippet = write_snippet(tmp_path, "Protocols http/1.1\n")

    assert cli.run_provisioning(provision_args(toml_path)) == 0

    assert captured["extra_vars"] == {"vhost_extra_src": str(snippet)}
    out = capsys.readouterr().out
    assert "deploy/vhost.conf" in out
    assert "/etc/apache2/depp/example.com.vhost.conf" in out


def test_provisioning_without_snippet_passes_no_extra_var(tmp_path, monkeypatch):
    toml_path, _config, captured = prepare_provisioning(monkeypatch, tmp_path)

    assert cli.run_provisioning(provision_args(toml_path)) == 0

    assert captured["extra_vars"] == {}


def test_provisioning_rejects_snippet_opening_a_vhost(tmp_path, monkeypatch, capsys):
    toml_path, _config, captured = prepare_provisioning(monkeypatch, tmp_path)
    write_snippet(tmp_path, "<VirtualHost *:443>\n</VirtualHost>\n")

    assert cli.run_provisioning(provision_args(toml_path)) == cli.EXIT_ERROR

    assert captured == {}
    assert "VirtualHost" in capsys.readouterr().err
