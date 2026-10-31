# Upgrading

What an existing deployment must know when moving to a newer depp. Anything
not listed here keeps working unchanged; the commit history has the rest.

## Unreleased

### Worth knowing

- `depp deploy` now builds with `--build-arg VERSION=<image tag>` and
  `--build-arg COMMIT=<full SHA>`, so an image can report what it was built
  from even though `.git` is not in the build context. A Containerfile that
  declares neither is unaffected. One that already uses `ARG VERSION` or
  `ARG COMMIT` for something else, such as a base image tag, now gets depp's
  value: rename that `ARG`.
- depp no longer installs on Python 3.15. Ansible 14 crashes on startup there,
  so use Python 3.12 to 3.14.

## 26.9.12

### Breaking

- `kube.yaml`: a container may no longer declare `hostPort`. It bound on all
  interfaces and bypassed Apache and TLS. Remove it; the pod is reachable
  through `127.0.0.1:<loopback_port>` only.

### Deprecated (still works, warns once per run)

- `[host] caddy_host_port` → `[host] loopback_port`, same meaning. Setting
  both is an error.

### Worth knowing

- `depp backup`, `depp restore` and `depp reset` now work for an image that
  runs as a non-root user: the remote rsync, listing and deletion run under
  `podman unshare`. Before, they failed with "Permission denied" on every
  file the container had written as a subuid. Restore no longer copies
  ownership from the backup (which carries the workstation's uid) but sets the
  volume's owner from `podman volume inspect`.
- Re-running `depp provision` rewrites the Apache vhost: `Define` lines at the
  top, the catch-all `ProxyPass` moved to the end of the block. No functional
  change unless the project ships `deploy/vhost.conf`. It also ensures Apache
  is enabled and running.
- `[host] user` is for hosts that are not provisioned yet. On a host that
  already runs the app under the fqdn-named user it is refused; see
  "Changing the deployment user" in the README. Do not run a 0.0.1 `depp`
  against a host provisioned with `[host] user`: it would create the
  fqdn-named user next to the real one.
