"""Tests for CLI argument and project parsers."""

import sys
from pathlib import Path

import pytest

from depp import cli
from depp.ansible_common.inventory import DeppConfig
from depp.cli import parse_args, parse_kube_manifest
from depp.manifest import ManifestError

MULTI_DOC_KUBE = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: caddy-config
data:
  Caddyfile: |
    :80
---
apiVersion: v1
kind: Pod
metadata:
  name: myapp
spec:
  containers:
    - name: caddy
      image: docker.io/library/caddy:2-alpine
      volumeMounts:
        - name: caddy-config-vol
          mountPath: /etc/caddy
    - name: app
      image: localhost/myapp:latest
      envFrom:
        - configMapRef:
            name: myapp-config
      volumeMounts:
        - name: media
          mountPath: /app/media
    - name: db
      image: docker.io/pgvector/pgvector:pg17
      volumeMounts:
        - name: pgdata
          mountPath: /var/lib/postgresql/data
  volumes:
    - name: caddy-config-vol
      configMap:
        name: caddy-config
    - name: media
      persistentVolumeClaim:
        claimName: myapp-media
    - name: pgdata
      persistentVolumeClaim:
        claimName: myapp-pgdata
    - name: scratch
      emptyDir: {}
"""


@pytest.fixture
def kube_file(tmp_path):
    f = tmp_path / "kube.yaml"
    f.write_text(MULTI_DOC_KUBE)
    return f


def test_parse_kube_manifest(kube_file):
    pod_name, containers, pvcs = parse_kube_manifest(kube_file)
    assert pod_name == "myapp"
    assert containers == ["caddy", "app", "db"]
    assert pvcs == ["myapp-media", "myapp-pgdata"]


def test_parse_kube_manifest_unparsable(tmp_path):
    f = tmp_path / "kube.yaml"
    f.write_text("{unbalanced: [")
    with pytest.raises(ManifestError, match="cannot parse"):
        parse_kube_manifest(f)


def test_parse_kube_manifest_no_pod(tmp_path):
    f = tmp_path / "kube.yaml"
    f.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: only-cm\n")
    with pytest.raises(ManifestError, match="exactly one Pod"):
        parse_kube_manifest(f)


def test_project_root_relative(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    deploy_dir = project / "deploy"
    deploy_dir.mkdir()
    toml_path = deploy_dir / "depp.toml"
    toml_path.write_text("")
    config = DeppConfig.from_mapping(
        toml_path,
        {
            "app": {"name": "example", "project_root": ".."},
            "host": {"fqdn": "example.com", "loopback_port": 8100},
            "acme": {"contact_email": "ops@example.com"},
        },
    )
    assert config.app.project_root == project.resolve()


def test_project_root_absolute(tmp_path):
    toml_path = tmp_path / "depp.toml"
    config = DeppConfig.from_mapping(
        toml_path,
        {
            "app": {"name": "example", "project_root": str(tmp_path)},
            "host": {"fqdn": "example.com", "loopback_port": 8100},
            "acme": {"contact_email": "ops@example.com"},
        },
    )
    assert config.app.project_root == tmp_path.resolve()


def test_project_root_defaults_from_layout(tmp_path):
    config = DeppConfig.from_mapping(
        tmp_path / "depp.toml",
        {
            "app": {"name": "example"},
            "host": {"fqdn": "example.com", "loopback_port": 8100},
            "acme": {"contact_email": "ops@example.com"},
        },
    )
    assert config.app.project_root == tmp_path.resolve()


def test_project_root_rejects_empty_value(tmp_path):
    with pytest.raises(ValueError, match="project_root"):
        DeppConfig.from_mapping(
            tmp_path / "depp.toml",
            {
                "app": {"name": "example", "project_root": " "},
                "host": {"fqdn": "example.com", "loopback_port": 8100},
                "acme": {"contact_email": "ops@example.com"},
            },
        )


def test_project_root_nonexistent_exits(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        DeppConfig.from_mapping(
            tmp_path / "depp.toml",
            {
                "app": {"name": "example", "project_root": "does/not/exist"},
                "host": {"fqdn": "example.com", "loopback_port": 8100},
                "acme": {"contact_email": "ops@example.com"},
            },
        )


@pytest.mark.parametrize(
    "command_args",
    [
        ["provision", "depp.toml"],
        ["deploy", "depp.toml"],
        ["backup", "depp.toml"],
        ["restore", "depp.toml", "backup"],
        ["reset", "depp.toml"],
        ["doctor", "depp.toml"],
        ["exec", "depp.toml"],
    ],
)
def test_host_key_policy_defaults_to_strict(monkeypatch, command_args):
    monkeypatch.setattr(sys, "argv", ["depp", *command_args])

    args = parse_args()
    assert args.host_key_policy == "strict"
    assert args.connection == "ssh"


def test_connection_can_be_local():
    assert parse_args(["deploy", "depp.toml", "--connection", "local"]).connection == (
        "local"
    )


def test_host_key_policy_can_accept_new_hosts(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["depp", "deploy", "depp.toml", "--host-key-policy", "accept-new"],
    )

    assert parse_args().host_key_policy == "accept-new"


def test_host_key_policy_rejects_unknown_value(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["depp", "deploy", "depp.toml", "--host-key-policy", "unknown"],
    )

    with pytest.raises(SystemExit, match="2"):
        parse_args()


def test_deploy_allows_explicit_dirty_source(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["depp", "deploy", "depp.toml", "--allow-dirty"],
    )

    assert parse_args().allow_dirty is True


@pytest.mark.parametrize(
    ("command_args", "handler"),
    [
        (["provision", "depp.toml"], cli.run_provisioning),
        (["deploy", "depp.toml"], cli.run_deployment),
        (["backup", "depp.toml"], cli.run_backup),
        (["restore", "depp.toml", "backup"], cli.run_restore),
        (["reset", "depp.toml"], cli.run_reset),
        (["doctor", "depp.toml"], cli.run_doctor),
        (["exec", "depp.toml"], cli.run_exec_command),
    ],
)
def test_subcommand_registers_handler(command_args, handler):
    assert parse_args(command_args).handler is handler


def test_config_path_is_optional():
    assert parse_args(["deploy"]).toml_file is None
    assert parse_args(["deploy", "x.toml"]).toml_file == Path("x.toml")


def test_restore_path_without_toml():
    args = parse_args(["restore", "backups/x"])
    assert args.toml_file is None
    assert args.restore_path == Path("backups/x")


def test_exec_double_dash_without_toml():
    args = parse_args(["exec", "--", "ls", "-la"])
    assert args.toml_file is None
    assert args.exec_command == ["ls", "-la"]


def test_exec_double_dash_with_toml_and_options():
    args = parse_args(["exec", "x.toml", "--host", "--", "systemctl", "--user"])
    assert args.toml_file == Path("x.toml")
    assert args.host is True
    assert args.exec_command == ["systemctl", "--user"]


def test_exec_without_double_dash_binds_first_word_to_toml():
    args = parse_args(["exec", "x.toml", "ls"])
    assert args.toml_file == Path("x.toml")
    assert args.exec_command == ["ls"]
