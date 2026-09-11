# depp

An opinionated rootless Podman provisioning and deployment tool for
containerized projects.

## Overview

Deploy Git-backed, locally built container images in isolated rootless Podman
pods on remote hosts using Ansible. Images are pushed directly to the target
host, so no registry is required. The pod is defined as a Kubernetes Pod
manifest (`kube.yaml`) and run via `podman kube play`. depp generates the
systemd service unit from a bundled template.

### Highlights

- Rootless podman pods via `podman kube play` (systemd-native)
- Developer-authored `kube.yaml` — depp generates the service unit
- Direct image transfer, no container registry needed
- `.env` files for secrets, rendered into a Kubernetes ConfigMap at runtime
- Framework-agnostic application image built from the local project
- Project pod serves plain HTTP on `[app] listen_port` (default 80), directly
  or through a Caddy sidecar; host Apache terminates TLS
- Per-project Apache directives (IP allow-lists, HSTS, WebSockets) via an
  optional `deploy/vhost.conf`
- Systemd integration via a generated service unit
- Backup and restore of named podman volumes

## Quick Start

### 1. Install depp

Requires Python 3.12+ and Podman:

```bash
pip install .
# For editable development installs:
pip install -e .
```

Or install straight from this repo without cloning it. Replace `@v0.0.1` with
another tag, a branch, or a commit SHA to pin a different version:

```bash
pip install "git+https://github.com/tombreit/depp.git@v0.0.1"
```

depp depends on the batteries-included Ansible community package, which
supplies `ansible-core` and the collections used by the bundled playbooks.
Installation is larger than an `ansible-core`-only setup, but commands never
download Galaxy collections at runtime. No `kubectl` is required; the env
ConfigMap is rendered in pure Python.

### 2. Create deployment files

Everything depp needs lives in one `deploy/` directory, so depp does not creep
into the project it ships:

```txt
myproject/
  Containerfile
  deploy/
    depp.toml    # deployment configuration (this is what depp reads)
    kube.yaml    # pod definition (dev + production)
    .env         # optional secrets (gitignored)
    vhost.conf   # optional Apache directives for this project
```

Projects that keep `depp.toml` at the project root next to `deploy/`, or in a
separate configuration repository, keep working; see
[Project Layout](#project-layout).

### 3. Create `depp.toml`

```toml
[app]
# Required project slug; also the default image name.
name = "myapp"
# Optional. Defaults to ".." when this file lives in deploy/, else to its own
# directory. Relative to this file, or absolute.
# project_root = ".."
# image_name = "myapp"
# containerfile = "Dockerfile"  # Defaults to Containerfile.
# The port the pod serves plain HTTP on (the app itself or a Caddy sidecar).
# listen_port = 80

[host]
# Required hostname: the Apache vhost, the certificate, the SSH target.
fqdn = "myapp.example.com"
# Required. A free 127.0.0.1 port on the host: the pod is published there and
# Apache proxies to it. Check with:  ss -ltnp | grep 127.0.0.1
loopback_port = 8100
# Optional. The Linux account depp creates and deploys as; defaults to fqdn.
# user = "myapp"
# server_aliases = ["www.myapp.example.com"]

[acme]
contact_email = "admin@example.com"  # Required for Apache mod_md.
# Defaults to Let's Encrypt staging.
certificate_authority = "https://acme-staging-v02.api.letsencrypt.org/directory"
# Secret; prefer the operator config instead of committing it here.
# external_account_binding = "kid hmac"
```

There is no `[backup]` section: depp backs up every volume your `kube.yaml`
declares (see [Backups](#depp-backup)).

depp loads this file into one immutable validated configuration model before a
command runs. Required tables, field types, paths, names, ports, health
settings, ACME email and URL values, and server aliases therefore fail with a
configuration error before Ansible starts. Defaults such as `image_name`,
`containerfile`, and health settings are resolved once and shared by summaries
and every inventory builder.

depp runs one app per Linux user. By default that user *is* the `fqdn`, which
keeps every name on the host (user, home, vhost, logs) derivable from one
value — but `useradd` caps names at 32 characters, so the fqdn inherits that
cap. Set `[host] user` to name the account yourself; the fqdn may then be any
lowercase DNS name up to 253 characters. Choose the user before the first
`depp provision`: depp records which account it provisioned for a host and
refuses to create a second one (see
[Changing the deployment user](#changing-the-deployment-user)). App and image
names must be lowercase DNS labels.

`caddy_host_port` is the 0.0.1 name of `loopback_port`. It is still accepted
with a warning, so a `depp.toml` written for an already-deployed app keeps
working unchanged; see [Upgrading from 0.0.1](#upgrading-from-001).

### Serving additional hostnames

`[host] fqdn` is the name depp provisions, gets a certificate for, and deploys as.
`StrictHostCheck On` means Apache answers **only** that name — any other `Host:`
gets a 403. If a friendlier public name should reach the app, list it under
`server_aliases`:

```toml
[host]
fqdn = "myapp.apps.example.com"
loopback_port = 8100
server_aliases = ["myapp.example.com"]
```

depp then emits `ServerAlias` in both vhosts. The plain-HTTP redirect preserves
the requested hostname instead of bouncing visitors onto `fqdn`.

Two things worth knowing:

- **Aliases are not added to the certificate.** The managed domain pins
  `MDMembers manual`, so mod_md requests a cert for `fqdn` only. This is
  deliberate: an alias normally points at an upstream TLS-terminating proxy, so
  its DNS does not resolve to this host. ACME validation would therefore fail.
- **The upstream must preserve the Host header.** If another proxy fronts the
  alias, it must use `ProxyPreserveHost On` instead of issuing a redirect.

### 4. Provision the host

This one-time operation needs sudo on the remote host:

```bash
depp provision            # reads ./deploy/depp.toml, then ./depp.toml
depp provision --check
```

Every command takes an explicit path instead (`depp provision path/to/depp.toml`).

SSH host keys are verified strictly by default. Before the first depp
connection, connect with `ssh` and verify the host key through your normal
trusted channel, or use `--host-key-policy accept-new` for explicit
trust-on-first-use. The latter records the first key and rejects later changes.
`--host-key-policy insecure` disables verification and should be reserved for
disposable test systems.

### 5. Deploy your application

```bash
depp doctor
depp deploy
depp deploy --check
depp deploy -v
```

Repeat step 5 for updates.

Deployments require a valid Git HEAD and a clean working tree. Clean releases
use the full commit SHA as their immutable image tag. `--yes` skips confirmation
only; it does not bypass this provenance check. For deliberate local testing,
`--allow-dirty` permits uncommitted source and assigns a unique
`<commit>-dirty-<id>` tag that cannot be confused with the clean commit.

The initial public scope is locally built project images. Direct deployment of
stock third-party images without a local build project is not supported yet.

## Architecture

```txt
Internet → Apache (host, TLS) → 127.0.0.1:<loopback_port> → pod :<listen_port> (App, or Caddy → App)
```

- **Apache**: Handles external traffic, TLS termination, and any per-project
  access rules (on host). Project directives come from `deploy/vhost.conf`.
- **Pod**: Serves plain HTTP on `listen_port`. An app that serves its own
  static files (a Go binary, Django with WhiteNoise) needs no sidecar; an app
  that wants a static-file server or several backends puts a Caddy container
  in front, as the bundled example does.
- Deployments use dedicated non-privileged users; provisioning uses `become: true`.

Multiple apps per host via distinct users/ports.

## Project Layout

depp reads one directory: the one that holds `kube.yaml`, the optional `.env`
and the optional `vhost.conf`. Where that directory is follows from where
`depp.toml` lives — the file names themselves are fixed:

| `depp.toml` location | `project_root` default | deploy files |
| --- | --- | --- |
| `<project>/deploy/depp.toml` (canonical) | `..` | next to `depp.toml` |
| `<project>/depp.toml` (0.0.1 layout) | `.` | `<project>/deploy/` |
| elsewhere, e.g. a config repo, with `project_root` set | — | `<project_root>/deploy/` |

The rule is keyed on the directory *name* `deploy`, not on the toml being
inside the project, so a configuration repository does not suddenly have the
files looked up next to its toml. Local backups always land in `backups/` next
to `depp.toml` (`deploy/backups/` in the canonical layout; gitignore it, or
`deploy/` as a whole, since it sits beside `.env`).

`project_root` is the container build context. Keep `deploy/` out of the image
with a `.containerignore` (a `COPY . .` would otherwise bake `.env` into a
layer):

```txt
.git
/deploy
```

### `deploy/kube.yaml` (dev + production)

A standard Kubernetes Pod manifest. The same file is used locally (with
`podman kube play --publish`) and in production (managed by the generated
service unit). Example:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: myapp
spec:
  restartPolicy: Always
  containers:
    - name: caddy
      image: docker.io/library/caddy:2-alpine
      volumeMounts:
        - name: private
          mountPath: /app/data/private
        - name: media
          mountPath: /app/data/media
    - name: app
      image: localhost/myapp:latest
      envFrom:
        # Injects every key from your .env file. The ConfigMap is named
        # "<image_name>-config" (image_name defaults to the app name) and is
        # Generated from deploy/.env; reference it to receive the values.
        - configMapRef:
            name: myapp-config
      volumeMounts:
        - name: private
          mountPath: /app/data/private
        - name: media
          mountPath: /app/data/media
  volumes:
    - name: private
      persistentVolumeClaim:
        claimName: myapp-private
    - name: media
      persistentVolumeClaim:
        claimName: myapp-media
```

**Volume setup and naming are entirely up to you, the app author** — declare
whatever volumes your app needs in `kube.yaml`. `kube.yaml` is depp's single
source of truth for volumes: it pre-creates every `persistentVolumeClaim` you
declare (reading the `claimName`s from the manifest) on deploy, and backs up the
same set (see [Backups](#depp-backup)). There is no separate volume list to keep
in sync. The `.env` file is rendered into the `myapp-config` ConfigMap and
injected into the containers that reference it with `envFrom` (see above).

depp intentionally supports a small, predictable dotenv subset: one
`KEY=value` assignment per line, blank lines, `#` comment lines, and one
optional layer of matching single or double quotes around a value. Values may
contain additional `=` characters. Variable names must contain only letters,
digits, `.`, `_`, or `-`. Malformed lines, duplicate names, unmatched quotes,
empty files, and non-UTF-8 input abort deployment with the offending line
number instead of being silently ignored. Because this file normally contains
secrets, keep it out of version control and restrict it with `chmod 600`.

### Deployment preflight

Before invoking Ansible, depp validates the local deployment inputs:

- `containerfile` must be a relative path to an existing regular file inside
  `project_root`; it defaults to `Containerfile`.
- `kube.yaml` must be valid UTF-8 YAML containing exactly one Pod.
- The Pod needs a valid name and at least one regular container. Container,
  volume, PVC, and ConfigMap names must use lowercase Kubernetes name syntax.
- Container and volume names must be unique, and every `volumeMount` must refer
  to a declared volume.
- No container may declare a `hostPort`: depp publishes the pod on
  `127.0.0.1:<loopback_port>` so that Apache stays the only entry point, and a
  `hostPort` would bind on all interfaces and bypass it (and TLS).
- A regular or init container must reference
  `localhost/<image_name>:latest`, the image built by depp.
- When `deploy/.env` exists, a regular or init container must reference the
  generated `<image_name>-config` ConfigMap. Referencing that generated
  ConfigMap without an env file is also rejected.

The Pod must serve HTTP on `[app] listen_port` (default 80): the generated
unit publishes `127.0.0.1:<loopback_port>:<listen_port>`. Kubernetes
`containerPort` declarations are optional metadata and do not prove which port
a process actually listens on, so depp documents but cannot preflight that
runtime requirement. Set `listen_port` to whatever the app binds — an
unprivileged port lets a non-root container serve without a sysctl or an
added capability.

### HTTP probe requirement

When `deploy/kube.yaml` defines a Kubernetes `httpGet` readiness or liveness
probe, Podman translates that probe into a `curl` command executed inside the
probed container. The container image must therefore include `curl`; otherwise
every probe fails with `curl: not found`, and a liveness probe can repeatedly
restart an otherwise healthy container.

This does not apply to depp's post-deploy health gate described below. That
check runs from the host through the loopback port and does not require
`curl` in the application image.

## What depp does

### `depp deploy`

1. Reads the full Git commit SHA and rejects uncommitted source unless
  `--allow-dirty` is explicit
2. Builds the container image locally with `podman build`, using the configured
  `containerfile` or the default `Containerfile`
3. Saves it as an OCI archive (`.tar`)
4. Transfers it to the remote host (rsync)
5. Ensures the named volumes declared in `kube.yaml` exist (`podman volume`)
6. Copies `kube.yaml` → `~/config/{name}/kube.yaml`
7. Generates `{name}.service` from bundled template → `~/.config/systemd/user/`
8. Renders `.env`, when present, into
  `~/config/{name}/configmap.yaml` and injects it with `--configmap`
9. Loads the image, tags it as `:latest`
10. Reloads systemd and restarts the pod
11. Polls the app over HTTP until it serves, then reports success (see
    [Deployment downtime](#deployment-downtime))

### Deployment downtime

depp runs **one pod per app**, so a deploy is a stop-then-start: `podman kube
down` tears down the old pod, then `podman kube play` starts the new one. There
is a brief window — typically around a second — where the pod is not serving.
This is **not** zero-downtime in the Kamal sense (which keeps the old version
running alongside the new during cutover); a single pod that bundles a database
cannot safely run two copies at once. depp instead keeps that window as small as
possible and makes it graceful:

- **Image is preloaded.** The image is built, transferred, loaded, and tagged
  `:latest` *before* the pod is restarted, so no pull or transfer happens inside
  the window.
- **A clean "deployment in progress" page** is served during the gap. Host Apache
  (which stays up across deploys) returns a branded page with `Retry-After: 5`
  via `ErrorDocument 503` instead of a raw proxy error, so browsers and uptime
  monitors see an intentional, retryable state. The page is provisioned to
  `/var/www/depp/maintenance.html`.
- **The deploy is health-gated.** After the restart, depp polls
  `http://127.0.0.1:<loopback_port><health_path>` until the app actually
  answers, and **fails the deploy** if it never comes up — so a crash-looping
  build is caught instead of being reported as a green deploy.

depp does not automatically roll back a failed release. It returns a non-zero
status and leaves the attempted deployment in place for diagnosis.

Tune the health gate with an optional `[deploy]` section in `depp.toml` (both
fields are optional and shown here with their defaults):

```toml
[deploy]
health_path = "/"     # URL path polled on the host loopback port
health_timeout = 30   # seconds to keep polling before failing the deploy
```

**Keep the cutover short: do per-deploy work at build time, not pod start.** The
biggest avoidable cost is work that runs every time the pod starts. For example,
collect static assets into the image in your `Containerfile` so an initContainer
only has to copy the pre-built files into a shared volume, instead of running a
full `collectstatic` on every restart — see
[`examples/wagtail-postgres`](examples/wagtail-postgres). Likewise, run database
migrations as an explicit step (`depp exec … -- python manage.py migrate`) rather
than blocking pod startup on them.

### `depp provision`

1. Installs podman on the remote host
2. Creates the deployment user (`[host] user`, default: the fqdn). The
   generated vhost records it (`Define DEPP_USER`), and a later run naming a
   different user is refused
3. Enables systemd lingering
4. Installs Apache, ensures it is running, and writes the vhost for `fqdn`
5. Installs the project's `deploy/vhost.conf`, when present, to
   `/etc/apache2/depp/<fqdn>.vhost.conf` (and removes it when the project no
   longer ships one)

Apache is only reloaded after `apachectl configtest` passes, so a broken
vhost or snippet fails the provisioning run with Apache's own error and leaves
the running configuration untouched.

#### Customizing the Apache vhost (`deploy/vhost.conf`)

depp's vhost is deliberately generic: TLS via mod_md, an HTTPS redirect, a
maintenance page during deploys, and a catch-all `ProxyPass` to the pod. What
a project needs beyond that goes into `deploy/vhost.conf`, a plain Apache
fragment that `depp provision` installs verbatim and includes from inside the
`<VirtualHost *:443>` block:

```apache
# deploy/vhost.conf — spliced into <VirtualHost *:443> by depp

# Only trusted networks may reach the admin area.
<LocationMatch "^/manage(/|$)">
    Require ip 192.0.2.0/24 2001:db8::/32
</LocationMatch>

# Pin HTTPS in browsers (depp's own MDRequireHttps is "temporary").
Header always set Strict-Transport-Security "max-age=31536000"

# WebSockets: upgrade one path (Apache ≥ 2.4.47), leave the rest as HTTP.
ProxyPass /ws ${DEPP_BACKEND}/ws upgrade=websocket

# Restate any of depp's defaults to override them: last directive wins.
Protocols http/1.1
LogLevel warn
```

The fragment sits **after** depp's own directives and **before** the catch-all
`ProxyPass /`. Two consequences follow: a directive you restate (`Protocols`,
`LogLevel`, a `RequestHeader`) replaces depp's, and a `ProxyPass` you add is
matched first, because Apache matches `ProxyPass` prefixes in order. depp
defines five variables the fragment may use:

| Variable | Value |
| --- | --- |
| `${DEPP_FQDN}` | `[host] fqdn` |
| `${DEPP_APP}` | `[app] name` |
| `${DEPP_USER}` | the deployment user (`[host] user`, default: the fqdn) |
| `${DEPP_LOOPBACK_PORT}` | `[host] loopback_port` |
| `${DEPP_BACKEND}` | `http://127.0.0.1:<loopback_port>` |

Things to know:

- **It is root-parsed Apache configuration.** An `Include`, a `CustomLog "|…"`
  pipe or a `ScriptAlias` in it runs with Apache's privileges. Treat the file
  with the trust you give the `sudo` step that installs it. It is applied only
  by `depp provision` (elevated, confirmed), never by `depp deploy`, so a
  change to it cannot ride along the unprivileged deploy path.
- **Only the `:443` vhost.** The `:80` vhost exists to redirect to HTTPS and to
  answer ACME challenges; there is no hook for it on purpose.
- **depp validates only what Apache would accept but cannot mean here:** the
  file must not open its own `<VirtualHost>` or `<MDomainSet>`. Everything
  else is checked by `apachectl configtest` on the host, before the reload.
  `--check` cannot run that test.
- **`Require ip` sees the address Apache sees.** For a `server_alias` that an
  upstream proxy forwards here, that is the proxy's address, not the
  visitor's. depp does not configure `mod_remoteip`; add
  `RemoteIPHeader`/`RemoteIPTrustedProxy` to the fragment yourself if you
  need client addresses behind another proxy.
- The installed copy is at `/etc/apache2/depp/<fqdn>.vhost.conf` (root, 0640)
  for inspection; `apachectl -S` shows the vhost that includes it.

#### Changing the deployment user

Everything the pod owns — the podman storage and named volumes, the systemd
units, `~/config/<app>` — lives in the deployment user's home. Changing
`[host] user` on a host that is already provisioned is therefore a migration,
not a setting, and `depp provision` refuses it. To migrate by hand, as root,
with the service stopped (`systemctl --user stop <app>` as the old user):
`usermod -l NEW -d /home/NEW -m OLD`, `loginctl disable-linger OLD`,
`loginctl enable-linger NEW`, change the `Define DEPP_USER` line in
`/etc/apache2/sites-available/<fqdn>.conf` to NEW (or delete that file), then
run `depp provision` and `depp deploy`. Hosts provisioned by 0.0.1 have no
such line and are recognised by their fqdn-named user instead; they are
refused the same way.

### `depp doctor`

Runs non-mutating deployment-readiness checks:

- validates `depp.toml`, the Containerfile, `deploy/kube.yaml`, and `.env`;
- checks Git HEAD and working-tree state;
- checks required control-node executables, Ansible 14 or newer, and every collection
  module used by the bundled playbooks;
- connects through the configured connection and host-key policy;
- verifies target rsync, rootless Podman, and the user systemd manager.

Run doctor after provisioning and before the first deployment:

```bash
depp doctor
```

A dirty working tree makes doctor return status 1 because a normal deployment
would be rejected. Use `--allow-dirty` when the dirty source is intentional.
Doctor does not install dependencies, alter the project, or change target
state.

> **Note:** Earlier depp versions restricted SSH logins on the host to an
> `ssh_allowed_depp` group via an sshd `AllowGroups` drop-in. This is no
> longer done. To lift the restriction on a host provisioned back then, run
> (as root): `rm /etc/ssh/sshd_config.d/allow_groups_depp.conf && systemctl
> reload ssh` — remove the drop-in *first*; deleting the group while
> `AllowGroups` is active would deny all SSH logins. Afterwards the unused
> group can be removed with `groupdel ssh_allowed_depp`.

#### Operator configuration (`~/.config/depp.toml`)

Secrets like the ACME External Account Binding (EAB kid + hmac) belong to the
**operator**, not to the target project — they authenticate *you* to the CA
and are only used by `depp provision`. Keep them out of the project repo and
put them in a per-user operator config instead:

```toml
# ~/.config/depp.toml   (chmod 600 — it holds secrets)

[acme]
external_account_binding = "kid hmac"
certificate_authority = "https://acme-v02.api.letsencrypt.org/directory"
```

The `[acme]` keys are merged over the project `depp.toml`'s `[acme]` section
(the operator value wins) and validated like the project's own: unknown keys
are rejected. The file is loaded automatically when it exists
(`$XDG_CONFIG_HOME` respected); use `--secrets FILE` on `provision` to point
elsewhere.

### `depp backup`

Fetches the contents of **every `persistentVolumeClaim` declared in your
`kube.yaml`** to a timestamped local directory — no configuration needed.
Ephemeral volumes (`emptyDir`, `configMap`) are not PVCs, so they are excluded
automatically. The pod must be running. Volume data is accessed via the host-side
mountpoint — no temporary files or `podman cp` required.

```bash
depp backup [-v]
```

Backups are saved next to `depp.toml` (so under `deploy/` in the canonical
layout):

```txt
backups/
  myapp.example.com/
    2026-03-14T103045Z/     ← UTC timestamp
      myapp-private/        ← one subdir per PVC in kube.yaml
      myapp-media/
```

> **Database caveat:** backup copies the live volume mountpoint with `rsync`.
> For a *running* database, the on-disk data directory is not guaranteed to be
> crash-consistent. For databases prefer a logical dump into a backed-up volume,
> e.g. `depp exec -- pg_dump … > /app/data/media/dump.sql`.

### `depp restore`

Pushes local files back into the named podman volumes on the remote host.
Always takes a safety backup first — the restore is aborted if the backup fails.
The pod is stopped during the transfer and restarted afterwards.

```bash
depp restore [DEPLOY_TOML] RESTORE_PATH [-v] [--yes]
```

`RESTORE_PATH` is a local directory whose **subdirectory names must match the
PVC names** declared in `kube.yaml`. This is the same structure that
`depp backup` produces:

```txt
RESTORE_PATH/
  myapp-private/    ← subdirectory name == claimName from kube.yaml
    somefile.db
  myapp-media/      ← subdirectory name == claimName from kube.yaml
    logo.png
```

Restores are **additive by default** — existing files not present in the source
are left in place. Only the volumes whose subdirectory exists under `RESTORE_PATH`
are restored; missing volume directories produce a warning and are skipped, not
an error. This makes it straightforward to restore a single file or a single
volume:

```txt
my-partial-restore/
  myapp-private/          ← only this volume is restored; myapp-media is skipped
    path/to/single-file
```

File permissions, ownership, and timestamps are preserved (`rsync --archive`).

### `depp exec`

Runs a command inside the running application container on the remote host
via SSH. If no command is given, opens an interactive shell (`/bin/sh` in
the container, or a login shell with `--host`). Uses `podman exec` under
the hood for container access.

The target container is resolved from `kube.yaml`: the pod's only container,
or the one named `app` when there are several. Pick a different one with
`--container NAME` (the name as written in `kube.yaml`). Put `--` before the
command; when `DEPLOY_TOML` is omitted that is what tells depp where the
command starts.

```bash
depp exec                                    # interactive shell in container
depp exec -- ls -la /app                     # run command in container
depp exec --container worker -- ps ax
depp exec --host                             # login shell on remote host
depp exec --host -- systemctl --user status  # run command on host
cat local-dump.sql | depp exec -- psql -U app appdb
depp exec path/to/depp.toml -- ls            # explicit configuration file
```

A TTY is allocated whenever *both* stdin and stdout are terminals, on the SSH
hop and in `podman exec` alike. So prompting commands work as they do locally,
while pipes and redirects still get clean, un-mangled byte streams (no pty
echo, no CRLF translation). Creating a Django superuser in the running app
container is just:

```bash
depp exec -- python manage.py createsuperuser
```

Override the detection with `-t`/`--tty` (force allocation) or `-T`/`--no-tty`
(never allocate). In CI and other non-TTY contexts, use the command's own
non-interactive mode:

```bash
depp exec -- env DJANGO_SUPERUSER_PASSWORD=changeme \
  python manage.py createsuperuser --noinput --username admin --email admin@example.com
```

## CLI Reference

```bash
depp provision [DEPLOY_TOML] [options]
depp deploy [DEPLOY_TOML] [options]
depp backup [DEPLOY_TOML] [options]
depp restore [DEPLOY_TOML] RESTORE_PATH [options]
depp reset [DEPLOY_TOML] [options]
depp doctor [DEPLOY_TOML] [options]
depp exec [DEPLOY_TOML] [options] [-- COMMAND ...]
```

`DEPLOY_TOML` defaults to `./deploy/depp.toml`, then `./depp.toml`; without
either, depp exits with status 1. Run `depp COMMAND --help` for
command-specific options. `--host-key-policy` accepts `strict` (default),
`accept-new`, or `insecure`.

### Connection modes

Commands use `--connection ssh` by default. For testing against the current
machine, every command also accepts `--connection local`:

```bash
depp deploy --connection local --yes
depp exec --connection local -- python manage.py check
```

Local mode keeps the configured FQDN as the Ansible inventory and application
identity, but executes as the current local user. It does not use SSH, the depp
SSH key, or host-key policy. Image transfer and loading are skipped because the
locally built image is already available to local Podman. `depp exec` invokes
Podman directly; `depp exec --host` invokes the requested command directly.

Use local provisioning carefully: `depp provision --connection local` runs the
provisioning playbook against the current machine and can make system-wide
changes through privilege escalation. Local mode is intended for disposable
or dedicated Debian 13 test hosts, not for pretending a workstation is a remote
production target.

### Automation and exit status

`provision`, `deploy`, `restore`, and `reset` ask for confirmation when run
interactively, except for check mode. In CI or any environment without an
interactive stdin, pass `--yes`; otherwise depp refuses before starting command
work. `--yes` only answers the confirmation prompt. It does not disable SSH
verification or permit a dirty Git working tree. Use the separate explicit
options for those choices.

depp uses these process exit statuses:

| Status | Meaning |
| --- | --- |
| `0` | Operation succeeded, check completed, or the user explicitly cancelled |
| `1` | Configuration, validation, connection, or operation failed |
| `2` | Command-line usage error reported by argparse |
| `130` | Interrupted with Ctrl-C |

## Upgrading from 0.0.1

An app deployed with 0.0.1 keeps deploying with its `depp.toml`, `kube.yaml`
and `.env` untouched. [UPGRADING.md](UPGRADING.md) lists the few things that
do need action: the one breaking change, the deprecated key, and what a
re-provision changes on the host.

## System Requirements

### Local (Control Node)

- Python 3.12+
- Ansible (installed automatically as a Python dependency of depp)
- Podman
- Git
- rsync

### Target Host

- Debian 13 Trixie
- Rootless podman 5.4+
- Apache 2
- Systemd (user scope)
- SSH access when using `--connection ssh`

## Remote Directory Structure

After deployment, files are placed as:

```txt
~/.config/systemd/user/          # Generated service unit
  myapp.service
~/config/myapp/                  # App config
  kube.yaml                      # Copied from deploy/kube.yaml
  configmap.yaml                 # Rendered from deploy/.env, injected via --configmap
podman volume (myapp-private)/   # A named volume declared in kube.yaml
podman volume (myapp-media)/     # Another named volume declared in kube.yaml
```

Provisioning (as root) additionally writes:

```txt
/etc/apache2/sites-available/<fqdn>.conf   # Generated vhost (enabled via sites-enabled)
/etc/apache2/depp/<fqdn>.vhost.conf        # Copy of deploy/vhost.conf, when present
/var/www/depp/maintenance.html             # Page served while the pod restarts
```

## Design Principles

- **`kube.yaml` authored by developer**: Pod definition lives in the project repo
- **Service unit generated by depp**: Single source of truth for the
  `podman kube play` invocation
- **No registry**: Direct image transfer via rsync
- **Rootless**: Everything runs under a non-privileged user
- **Standard formats**: Kubernetes Pod manifest, ConfigMap, and `.env`
- **Ansible-backed**: Uses standard modules from the Ansible community package,
  not custom deployment scripts

## Development

```bash
git clone https://github.com/tombreit/depp.git
cd depp
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                 # test suite; no network, no host required
.venv/bin/ruff check .           # lint
.venv/bin/ansible-lint depp/     # playbooks, production profile
```

`tests/expected/` holds the rendered systemd unit and Apache vhost for a
project that uses no newer option. A template change that alters what an
existing host gets shows up there as a reviewable diff; update the expected
file only when that change is intended.

The playbooks can be checked without a target host:

```bash
for playbook in depp/ansible_*/*.yml; do
  ANSIBLE_CONFIG=depp/ansible_common/ansible.cfg \
    .venv/bin/ansible-playbook --syntax-check "$playbook"
done
```

CI (`.github/workflows/ci.yml`) runs these same checks plus
`ruff format --check .` and `ansible-lint depp/` on every push and pull
request — against a single Python version, the newest stable release
(`python-version: "3.x"`). This is deliberate: depp is an operator tool run
from one control node rather than a library embedded in other people's
projects, so a matrix over older interpreters would cost CI time without
protecting anyone. The `requires-python = ">=3.12"` floor in `pyproject.toml`
is the `ansible` dependency's floor and stays installable, but it is not
exercised in CI.

## Security

depp builds every subprocess invocation as an argument list — there is no
`shell=True`, `os.system`, or `eval` anywhere — and passes `.env`-derived
secrets to Ansible through a `0600` inventory file rather than on the command
line, so they never appear in `ps` output or shell history. SSH host key
checking is strict by default, and `become` is used only during provisioning.

If you find a security issue, please report it privately to <mail@thms.de>
rather than opening a public issue.

## Licence

Copyright (c) 2026 Thomas Breitner

Licensed under the EUPL-1.2-or-later. See [LICENSE](LICENSE) for the full text.
