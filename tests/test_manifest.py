"""Tests for strict deploy/kube.yaml validation."""

from pathlib import Path

import pytest

from depp.manifest import ManifestError, inspect_kube_manifest, validate_deploy_manifest

PROJECT_ROOT = Path(__file__).parents[1]

VALID_MANIFEST = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: caddy-config
---
apiVersion: v1
kind: Pod
metadata:
  name: example
spec:
  initContainers:
    - name: prepare
      image: localhost/example:latest
      volumeMounts:
        - name: data
          mountPath: /data
  containers:
    - name: app
      image: localhost/example:latest
      envFrom:
        - configMapRef:
            name: example-config
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: example-data
"""


def write_manifest(tmp_path: Path, contents: str = VALID_MANIFEST) -> Path:
    manifest = tmp_path / "kube.yaml"
    manifest.write_text(contents, encoding="utf-8")
    return manifest


def test_valid_manifest_exposes_running_containers_and_pvcs(tmp_path):
    info = validate_deploy_manifest(
        write_manifest(tmp_path), image_name="example", has_env_file=True
    )

    assert info.pod_name == "example"
    assert info.container_names == ["app"]
    assert info.pvc_claim_names == ["example-data"]
    assert info.images.count("localhost/example:latest") == 2


def test_wagtail_example_satisfies_deployment_contract():
    info = validate_deploy_manifest(
        PROJECT_ROOT / "examples/wagtail-postgres/deploy/kube.yaml",
        image_name="wagtail-demo",
        has_env_file=True,
    )

    assert info.container_names == ["caddy", "app", "db"]
    assert info.pvc_claim_names == ["wagtail-demo-media", "wagtail-demo-pgdata"]


def test_manifest_requires_exactly_one_pod(tmp_path):
    manifest = write_manifest(tmp_path, VALID_MANIFEST + "---\n" + VALID_MANIFEST)

    with pytest.raises(ManifestError, match="exactly one Pod"):
        inspect_kube_manifest(manifest)


def test_manifest_requires_v1_pod_api(tmp_path):
    manifest = write_manifest(
        tmp_path,
        VALID_MANIFEST.replace(
            "apiVersion: v1\nkind: Pod", "apiVersion: v2\nkind: Pod"
        ),
    )

    with pytest.raises(ManifestError, match="apiVersion must be 'v1'"):
        inspect_kube_manifest(manifest)


def test_manifest_requires_valid_pod_name(tmp_path):
    manifest = write_manifest(
        tmp_path, VALID_MANIFEST.replace("name: example\n", "name: Invalid_Name\n", 1)
    )

    with pytest.raises(ManifestError, match="metadata.name"):
        inspect_kube_manifest(manifest)


def test_manifest_rejects_invalid_dns_subdomain_segment(tmp_path):
    manifest = write_manifest(
        tmp_path,
        VALID_MANIFEST.replace("name: example\n", "name: invalid-.name\n", 1),
    )

    with pytest.raises(ManifestError, match="DNS subdomain"):
        inspect_kube_manifest(manifest)


def test_manifest_rejects_overlong_container_name(tmp_path):
    manifest = write_manifest(
        tmp_path,
        VALID_MANIFEST.replace("name: app", f"name: {'a' * 64}"),
    )

    with pytest.raises(ManifestError, match="DNS label"):
        inspect_kube_manifest(manifest)


def test_manifest_rejects_duplicate_container_names(tmp_path):
    manifest = write_manifest(
        tmp_path,
        VALID_MANIFEST.replace("name: prepare", "name: app"),
    )

    with pytest.raises(ManifestError, match="duplicate container name"):
        inspect_kube_manifest(manifest)


def test_manifest_rejects_undefined_volume_mount(tmp_path):
    manifest = write_manifest(
        tmp_path,
        VALID_MANIFEST.replace(
            "name: data\n          mountPath", "name: missing\n          mountPath", 1
        ),
    )

    with pytest.raises(ManifestError, match="undefined volume 'missing'"):
        inspect_kube_manifest(manifest)


def test_deploy_manifest_requires_local_image(tmp_path):
    manifest = write_manifest(
        tmp_path, VALID_MANIFEST.replace("localhost/example:latest", "example:dev")
    )

    with pytest.raises(ManifestError, match="locally built image"):
        validate_deploy_manifest(manifest, image_name="example", has_env_file=True)


def test_deploy_manifest_requires_generated_configmap_reference(tmp_path):
    manifest = write_manifest(
        tmp_path, VALID_MANIFEST.replace("name: example-config", "name: other-config")
    )

    with pytest.raises(ManifestError, match="no container references"):
        validate_deploy_manifest(manifest, image_name="example", has_env_file=True)


def test_deploy_manifest_rejects_generated_configmap_without_env_file(tmp_path):
    manifest = write_manifest(tmp_path)

    with pytest.raises(ManifestError, match="deploy/.env does not exist"):
        validate_deploy_manifest(manifest, image_name="example", has_env_file=False)


def test_manifest_rejects_host_port(tmp_path):
    manifest = write_manifest(
        tmp_path,
        VALID_MANIFEST.replace(
            "    - name: app\n",
            "    - name: app\n      ports:\n"
            "        - containerPort: 8000\n          hostPort: 8000\n",
        ),
    )

    with pytest.raises(ManifestError, match="hostPort is not supported"):
        inspect_kube_manifest(manifest)


def test_manifest_accepts_container_port_without_host_port(tmp_path):
    manifest = write_manifest(
        tmp_path,
        VALID_MANIFEST.replace(
            "    - name: app\n",
            "    - name: app\n      ports:\n        - containerPort: 8000\n",
        ),
    )

    assert inspect_kube_manifest(manifest).pod_name == "example"
