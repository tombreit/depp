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
- The pod is defined by a developer-authored `kube.yaml` in the project's
  deploy directory (`deploy/` next to `depp.toml` in the canonical layout,
  `<project_root>/deploy/` for a root-level or external `depp.toml`; see
  `depp/layout.py`). An optional `vhost.conf` there is root-parsed Apache
  config installed by `provision`. depp generates only the systemd unit and
  the Apache vhost, from Jinja2 templates pinned by expected renders in
  `tests/expected/`.
- Several apps are deployed with depp already: config keys stay accepted
  (deprecate, do not remove) and the generated unit must not change for a
  project that uses no new option.

## Conventions

- Prefer Ansible modules over the `command` and `shell` modules.
- Never pass secrets on a command line — they belong in the generated inventory
  or a ConfigMap, and tasks that touch them need `no_log: true`.
- Build subprocess calls as argument lists; no `shell=True`.
- Run `pytest`, `ruff check .`, `ruff format --check .` and `ansible-lint depp/`
  before finishing. See the Development section
  of the README for setup.
