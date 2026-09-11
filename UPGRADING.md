# Upgrading

What an existing deployment must know when moving to a newer depp. Anything
not listed here keeps working unchanged; the commit history has the rest.

## Unreleased (after 0.0.1)

### Breaking

- `kube.yaml`: a container may no longer declare `hostPort`. It bound on all
  interfaces and bypassed Apache and TLS. Remove it; the pod is reachable
  through `127.0.0.1:<loopback_port>` only.

### Deprecated (still works, warns once per run)

- `[host] caddy_host_port` → `[host] loopback_port`, same meaning. Setting
  both is an error.

### Worth knowing

- Re-running `depp provision` rewrites the Apache vhost: `Define` lines at the
  top, the catch-all `ProxyPass` moved to the end of the block. No functional
  change unless the project ships `deploy/vhost.conf`. It also ensures Apache
  is enabled and running.
- `[host] user` is for hosts that are not provisioned yet. On a host that
  already runs the app under the fqdn-named user it is refused; see
  "Changing the deployment user" in the README. Do not run a 0.0.1 `depp`
  against a host provisioned with `[host] user`: it would create the
  fqdn-named user next to the real one.
