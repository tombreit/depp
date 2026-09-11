# Wagtail + Postgres — depp demo

A minimal, self-contained example you can deploy with [`depp`](../../README.md).
It shows the full contract a project gives depp:

- a **`Containerfile`** (the app image),
- a **`deploy/kube.yaml`** pod manifest,
- a **`deploy/depp.toml`**, and
- a **`deploy/.env`** file for secrets.

## What's in the pod

```
Internet → Apache (host, TLS) → 127.0.0.1:8200 → Caddy :80 ─┬─ /static/* , /media/*  (served by Caddy)
                                                            └─ everything else → gunicorn :8000 (Wagtail)
                                                               Wagtail → Postgres (127.0.0.1:5432)
```

- **Static is collected at build time.** `collectstatic` runs in the
  `Containerfile`, so the `copy-static` **initContainer** only copies the
  pre-built files into a shared volume — a fast file copy, not a full
  `collectstatic` on every pod start — keeping the deploy cutover short.
- **Caddy** serves `/static` and `/media` and reverse-proxies the rest to gunicorn.
- **app** is Wagtail on gunicorn `:8000`.
- **db** is `postgres:18-alpine`; its data lives in the `wagtail-demo-pgdata` volume.

`deploy/.env` is rendered by depp into the `wagtail-demo-config` ConfigMap and
injected into the app and db containers via `envFrom` — no `.env` file is copied
into a volume, and no `kubectl` is required.

## Deploy

```bash
cd examples/wagtail-postgres

# 1. Secrets — copy the template and edit. Keep DATABASE_URL's password in sync
#    with POSTGRES_PASSWORD.
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env

# 2. depp reads the project's git HEAD for the deployed version, so commit first.
#    (If this example already lives inside the depp git repo, you can skip this.)
git init && git add -A && git commit -m "wagtail demo"

# 3. Point deploy/depp.toml at a host you control (fqdn, contact_email), then:
depp provision                  # one-time host setup (podman, user, Apache)
depp deploy                     # build, transfer, run

# 4. First-run database setup (run once after the first deploy).
depp exec -- python manage.py migrate
depp exec -- python manage.py createsuperuser
```

Then browse to `https://wagtail-demo.example.com/` and log in at `/admin/`.

### Why `migrate` is a manual step

Keeping the app a single gunicorn process (no entrypoint script) makes the demo
easy to read. `restartPolicy: Always` means the app retries while Postgres
finishes its first-boot initialization, so the pod converges even though the app
may briefly start before the database is ready. Run `migrate` once the pod is up;
re-run it after any deploy that adds migrations.

## Adapting this for your own app

- Rename `wagtail-demo` everywhere (`deploy/depp.toml` `name`, the pod/volume/claim
  names in `deploy/kube.yaml`, the `wagtail-demo-config` references).
- Not using Python? Swap the `Containerfile` (add `containerfile = "Dockerfile"`
  to `[app]` if your build file isn't named `Containerfile`) and the app/db
  containers. Everything else — Caddy sidecar, ConfigMap injection, volumes — is
  framework-agnostic.
