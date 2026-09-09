# AGENTS.md

Instructions for AI coding agents working on depp. User-facing documentation
lives in [README.md](README.md), which is the accurate source for how depp
behaves — prefer it over this file when the two disagree.

## What depp is

A CLI that provisions a remote host and deploys a locally built container image
to it as a rootless Podman pod, using bundled Ansible playbooks. No registry is
involved: the image is transferred directly.

## Constraints

- Target host: Debian 13 Trixie, rootless Podman 5.4+, Apache 2 as the
  host-level reverse proxy.
- Control node: Python 3.12+, Ansible 14+.
- Deployment runs entirely as a non-privileged user; only provisioning uses
  `become`.
- The pod is defined by a developer-authored `deploy/kube.yaml`; depp generates
  only the systemd unit, from a Jinja2 template.

## Conventions

- Prefer Ansible modules over the `command` and `shell` modules.
- Never pass secrets on a command line — they belong in the generated inventory
  or a ConfigMap, and tasks that touch them need `no_log: true`.
- Build subprocess calls as argument lists; no `shell=True`.
- Run `pytest` and `ruff check .` before finishing. See the Development section
  of the README for setup.
