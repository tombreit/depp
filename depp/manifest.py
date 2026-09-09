"""Validation and inspection of the developer-authored Pod manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from depp.validation import (
    MAX_DNS_LABEL_LENGTH,
    MAX_DNS_SUBDOMAIN_LENGTH,
    is_valid_dns_name,
)


class ManifestError(ValueError):
    """Raised when deploy/kube.yaml is malformed or unsupported."""


@dataclass(frozen=True)
class ManifestInfo:
    pod_name: str
    container_names: list[str]
    pvc_claim_names: list[str]
    images: list[str]
    configmap_refs: set[str]


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{location} must be a mapping")
    return value


def _name(value: Any, location: str, *, dns_label: bool = False) -> str:
    max_length = MAX_DNS_LABEL_LENGTH if dns_label else MAX_DNS_SUBDOMAIN_LENGTH
    if not is_valid_dns_name(value, max_length=max_length, allow_dots=not dns_label):
        kind = "DNS label" if dns_label else "DNS subdomain"
        raise ManifestError(f"{location} must be a valid lowercase Kubernetes {kind}")
    return value


def inspect_kube_manifest(kube_file: Path) -> ManifestInfo:
    """Parse and validate one Kubernetes Pod plus optional supporting documents."""
    try:
        contents = kube_file.read_text(encoding="utf-8")
    except OSError as error:
        raise ManifestError(f"cannot read {kube_file}: {error}") from error
    except UnicodeDecodeError as error:
        raise ManifestError(f"{kube_file} is not valid UTF-8") from error

    try:
        documents = [doc for doc in yaml.safe_load_all(contents) if doc is not None]
    except yaml.YAMLError as error:
        problem = getattr(error, "problem_mark", None)
        location = (
            f" line {problem.line + 1}, column {problem.column + 1}"
            if problem is not None
            else ""
        )
        raise ManifestError(f"cannot parse {kube_file}{location}: {error}") from error

    for index, document in enumerate(documents, start=1):
        _mapping(document, f"document {index}")

    pods = [document for document in documents if document.get("kind") == "Pod"]
    if len(pods) != 1:
        raise ManifestError(
            f"{kube_file} must contain exactly one Pod document (found {len(pods)})"
        )

    pod = pods[0]
    if pod.get("apiVersion") != "v1":
        raise ManifestError("Pod apiVersion must be 'v1'")
    metadata = _mapping(pod.get("metadata"), "Pod metadata")
    pod_name = _name(metadata.get("name"), "Pod metadata.name")
    spec = _mapping(pod.get("spec"), "Pod spec")

    containers = spec.get("containers")
    if not isinstance(containers, list) or not containers:
        raise ManifestError("Pod spec.containers must be a non-empty list")
    init_containers = spec.get("initContainers", [])
    if not isinstance(init_containers, list):
        raise ManifestError("Pod spec.initContainers must be a list")

    container_names: list[str] = []
    all_container_names: set[str] = set()
    images: list[str] = []
    configmap_refs: set[str] = set()
    volume_mount_names: list[tuple[str, str]] = []
    for section, entries in (
        ("containers", containers),
        ("initContainers", init_containers),
    ):
        for index, raw_container in enumerate(entries, start=1):
            location = f"Pod spec.{section}[{index}]"
            container = _mapping(raw_container, location)
            container_name = _name(
                container.get("name"), f"{location}.name", dns_label=True
            )
            if container_name in all_container_names:
                raise ManifestError(f"duplicate container name {container_name!r}")
            all_container_names.add(container_name)
            if section == "containers":
                container_names.append(container_name)

            image = container.get("image")
            if not isinstance(image, str) or not image.strip():
                raise ManifestError(f"{location}.image must be a non-empty string")
            images.append(image)

            env_from = container.get("envFrom", [])
            if not isinstance(env_from, list):
                raise ManifestError(f"{location}.envFrom must be a list")
            for env_index, raw_source in enumerate(env_from, start=1):
                source = _mapping(raw_source, f"{location}.envFrom[{env_index}]")
                configmap_ref = source.get("configMapRef")
                if configmap_ref is not None:
                    ref = _mapping(
                        configmap_ref,
                        f"{location}.envFrom[{env_index}].configMapRef",
                    )
                    configmap_refs.add(
                        _name(
                            ref.get("name"),
                            f"{location}.envFrom[{env_index}].configMapRef.name",
                        )
                    )

            mounts = container.get("volumeMounts", [])
            if not isinstance(mounts, list):
                raise ManifestError(f"{location}.volumeMounts must be a list")
            for mount_index, raw_mount in enumerate(mounts, start=1):
                mount_location = f"{location}.volumeMounts[{mount_index}]"
                mount = _mapping(raw_mount, mount_location)
                volume_mount_names.append(
                    (
                        _name(
                            mount.get("name"),
                            f"{mount_location}.name",
                            dns_label=True,
                        ),
                        mount_location,
                    )
                )

    volumes = spec.get("volumes", [])
    if not isinstance(volumes, list):
        raise ManifestError("Pod spec.volumes must be a list")
    volume_names: set[str] = set()
    pvc_claim_names: list[str] = []
    for index, raw_volume in enumerate(volumes, start=1):
        location = f"Pod spec.volumes[{index}]"
        volume = _mapping(raw_volume, location)
        volume_name = _name(volume.get("name"), f"{location}.name", dns_label=True)
        if volume_name in volume_names:
            raise ManifestError(f"duplicate volume name {volume_name!r}")
        volume_names.add(volume_name)

        claim = volume.get("persistentVolumeClaim")
        if claim is not None:
            claim_data = _mapping(claim, f"{location}.persistentVolumeClaim")
            claim_name = _name(
                claim_data.get("claimName"),
                f"{location}.persistentVolumeClaim.claimName",
            )
            if claim_name not in pvc_claim_names:
                pvc_claim_names.append(claim_name)

    for mount_name, location in volume_mount_names:
        if mount_name not in volume_names:
            raise ManifestError(
                f"{location} references undefined volume {mount_name!r}"
            )

    return ManifestInfo(
        pod_name=pod_name,
        container_names=container_names,
        pvc_claim_names=pvc_claim_names,
        images=images,
        configmap_refs=configmap_refs,
    )


def validate_deploy_manifest(
    kube_file: Path,
    *,
    image_name: str,
    has_env_file: bool,
) -> ManifestInfo:
    """Validate depp-specific image and generated ConfigMap contracts."""
    info = inspect_kube_manifest(kube_file)
    expected_image = f"localhost/{image_name}:latest"
    if expected_image not in info.images:
        raise ManifestError(
            f"{kube_file} must reference the locally built image "
            f"{expected_image!r} in a container or init container"
        )

    generated_configmap = f"{image_name}-config"
    if has_env_file and generated_configmap not in info.configmap_refs:
        raise ManifestError(
            f"deploy/.env exists but no container references generated ConfigMap "
            f"{generated_configmap!r}"
        )
    if not has_env_file and generated_configmap in info.configmap_refs:
        raise ManifestError(
            f"{kube_file} references generated ConfigMap {generated_configmap!r} "
            "but deploy/.env does not exist"
        )
    return info


def parse_kube_manifest(kube_file: Path) -> tuple[str, list[str], list[str]]:
    """Return the Pod name, regular container names, and PVC claim names."""
    info = inspect_kube_manifest(kube_file)
    return info.pod_name, info.container_names, info.pvc_claim_names
