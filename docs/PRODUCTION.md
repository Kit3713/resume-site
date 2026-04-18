# Production Deployment Guide

End-to-end guide for deploying resume-site to a production server. If
you're experimenting locally, the README's "Quick Start" is shorter and
gets you to a running site in about five minutes. This document
assumes you're putting the site in front of real visitors and want a
hardened, monitored, backed-up deployment.

---

## 1. Choose your deployment shape

Three supported shapes, ordered from simplest to richest:

| Shape | Best for | Defined by |
|---|---|---|
| **Podman Compose** | Single host, "just get it running" | `compose.yaml` |
| **Podman Quadlet (systemd)** | Fedora/RHEL servers, auto-start, journald logs | `resume-site.container`, `resume-site-backup.{service,timer}` |
| **Kubernetes / Nomad** | Multi-host, orchestrated, horizontal scaling | Adapt the `compose.yaml` probes + your own manifests |

Pick one and stick with it — don't mix Quadlet with compose or you'll
end up with two containers fighting over the same volumes.

---

## 2. Server prerequisites

- **OS**: Any Linux with Podman ≥ 4.4 or Docker ≥ 20.10. We test on
  Fedora Server 43+. Rootless Podman works.
- **Memory**: 256 MB minimum for the container (most of the RSS is
  Gunicorn + SQLite's page cache). 512 MB gives comfortable headroom.
- **CPU**: 1 vCPU handles the typical personal-portfolio load
  (< 100 monthly visitors). Two vCPUs if you enable webhooks or plan
  to run locust against it.
- **Disk**: 200 MB for the image + whatever you need for photos. The
  SQLite database itself stays small (a full year of analytics is
  around 10 MB on a modest-traffic site).
- **Network**: TCP 443 open inbound. Admin traffic arrives over
  Tailscale / WireGuard — admin routes are IP-gated to private
  ranges (see §5 below).
- **Reverse proxy**: Caddy, Nginx, or Traefik. Caddy's automatic
  Let's Encrypt flow is the fastest path to HTTPS — details in §4.

---

## 3. First-deploy checklist

### 3.1 Generate secrets

```bash
# One-shot random hex key for Flask's session signing:
python3 -c "import secrets; print(secrets.token_hex(32))"

# PBKDF2 hash for the admin password:
python manage.py hash-password
# (prompts twice, prints the hash to paste into config.yaml)
```

You can also run `manage.py hash-password` inside the already-pulled
container:

```bash
podman run --rm -it ghcr.io/kit3713/resume-site:latest \
    python manage.py hash-password
```

### 3.2 Write `config.yaml`

Start from `config.example.yaml`:

```yaml
secret_key: "<64-hex-chars from token_hex(32)>"
database_path: "/app/data/site.db"
photo_storage: "/app/photos"
session_cookie_secure: true        # HTTPS-only cookies; keep this true

smtp:
  host: "smtp.gmail.com"
  port: 587
  user: "you@yourdomain.com"
  password: "gmail-app-password"
  recipient: "you@yourdomain.com"

admin:
  username: "admin"
  password_hash: "<pbkdf2 hash from step 3.1>"
  allowed_networks:
    - "127.0.0.0/8"
    - "10.0.0.0/8"
    - "192.168.0.0/16"
    - "100.64.0.0/10"              # Tailscale CGNAT range
```

Keep `config.yaml` out of git — it ships outside the public repo, in a
private fork or a host directory.

### 3.3 Start the container

The container entrypoint (`/app/docker-entrypoint.sh`) automatically
runs `manage.py init-db` before handing off to Gunicorn. A fresh
volume comes up fully migrated and seeded — no separate step to
remember.

Compose:

```bash
podman compose up -d
```

Quadlet:

```bash
mkdir -p ~/resume-site
cp config.yaml ~/resume-site/       # the Quadlet mounts ~/resume-site/config.yaml
cp resume-site.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user enable --now resume-site
```

### 3.4 Verify the deployment

```bash
# Liveness: lightweight, no I/O, used by the Podman HEALTHCHECK.
curl -fsS http://localhost:8080/healthz
# → {"status":"ok"}

# Readiness: deeper check — DB connectivity, migrations current,
# photos dir writable, disk headroom. 503 tells an orchestrator to
# remove the pod from LB rotation.
curl -fsS http://localhost:8080/readyz
# → {"checks":{...},"ready":true}
```

Navigate to `http://localhost:8080/` from your local machine (or via
Tailscale for remote verification) and confirm you see the placeholder
landing page. Log in at `/admin/login` with the credentials from
step 3.1.

### 3.5 Behind the reverse proxy

Don't expose port 8080 to the public internet. Point your reverse
proxy at it.

Caddy is the lightest option. Minimal `Caddyfile`:

```caddy
portfolio.yourdomain.com {
    reverse_proxy localhost:8080
    encode gzip zstd
    # Resume-site sets its own security headers; don't duplicate them
    # here or you'll collide (Caddy's default removes X-Powered-By but
    # leaves the rest alone, which is what we want).
}
```

Nginx equivalent:

```nginx
server {
    listen 443 ssl http2;
    server_name portfolio.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/portfolio.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/portfolio.yourdomain.com/privkey.pem;

    client_max_body_size 15m;      # photo uploads are up to 10 MB by default
    proxy_set_header X-Real-IP        $remote_addr;
    proxy_set_header X-Forwarded-For  $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Host             $host;

    location / {
        proxy_pass http://localhost:8080;
    }
}
```

Traefik labels live on the container — see the Traefik docs for
v2.x router + middleware syntax. The backing service is a plain HTTP
service on port 8080.

---

## 4. TLS + firewall

Admin routes are IP-restricted at the application layer, but that's
defense-in-depth — the front door should also be locked.

**Firewall:**

```bash
# Public-facing host firewall — only HTTPS and SSH:
firewall-cmd --permanent --add-service=https
firewall-cmd --permanent --add-service=ssh
firewall-cmd --reload

# Verify nothing is exposing 8080 directly to the internet:
ss -tlnp | grep :8080
# → should only bind to 127.0.0.1 or an internal IP, NOT 0.0.0.0
```

If you're using Podman's default rootless networking, the container's
8080 port binds to `0.0.0.0:8080` on the host — use `-p 127.0.0.1:8080:8080`
in the run command to bind only to loopback so the reverse proxy can
reach it but external requests can't.

**TLS:** Caddy handles this automatically. For Nginx, use Certbot:

```bash
certbot --nginx -d portfolio.yourdomain.com
```

**Admin reachability:** Admin routes check the request's IP against
`admin.allowed_networks` in `config.yaml`. The production recipe is:

1. Tailscale (or another VPN) on the server.
2. `admin.allowed_networks` contains your VPN's CGNAT range
   (`100.64.0.0/10` for Tailscale).
3. Connect to Tailscale on your laptop and hit
   `https://portfolio.yourdomain.com/admin` — the reverse proxy sees
   a Tailscale IP, the app sees the same via `X-Forwarded-For`, the
   check passes.

If you don't use a VPN, limit admin to your home IP:

```yaml
admin:
  allowed_networks:
    - "203.0.113.0/24"   # your home IP range
```

---

## 5. Resource sizing

These are working numbers from the load-test scenarios in
`tests/loadtests/`. Revisit after you've run your own baseline.

| Monthly visitors | CPU | RAM | Disk | SQLite concerns |
|---|---|---|---|---|
| < 1,000 | 1 vCPU | 256 MB | 1 GB | None |
| 1,000 – 10,000 | 1 vCPU | 512 MB | 5 GB | Analytics purge on a timer |
| 10,000 – 100,000 | 2 vCPU | 1 GB | 20 GB | Enable WAL checkpointing; consider per-visitor caching at the proxy |
| > 100,000 | Revisit SQLite | — | — | Migration to Postgres (not supported in v0.3.0; see v0.4.0+ roadmap) |

SQLite's single-writer constraint is the real ceiling here. The site
is read-heavy (public pages) and write-light (contact form, analytics,
blog admin). At > 100k monthly visitors you'll want to move analytics
off SQLite before you hit contention issues. For typical personal
portfolios this is wildly beyond what you'll see.

---

## 6. Logging

The app emits structured JSON to stdout. The shape is documented in
[`docs/LOGGING.md`](LOGGING.md) — request correlation via `X-Request-ID`,
per-request timing, status→log-level mapping.

**Journald (Quadlet / systemd deployments):**

```bash
journalctl --user -u resume-site -f           # rootless
sudo journalctl -u resume-site -f             # system-wide
```

Limit retention in `/etc/systemd/journald.conf` (or
`~/.config/systemd/user/journald.conf`):

```ini
[Journal]
SystemMaxUse=500M
MaxRetentionSec=30day
```

**Compose (json-file driver):**

```yaml
services:
  resume-site:
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"
```

**Forwarding to Loki / CloudWatch / Fluentd**: see
`docs/LOGGING.md` — every log driver config lands there.

---

## 7. Monitoring

Two tiers. Pick based on what you actually need.

### 7.1 Uptime only (free, 5-minute setup)

Free tier of [UptimeRobot](https://uptimerobot.com) or
self-hosted [Uptime Kuma](https://uptime.kuma.pet). Point it at
`https://portfolio.yourdomain.com/healthz` with a 60-second interval
and email-on-failure. Catches "the site is down" and nothing else.

### 7.2 Full Prometheus + Grafana (30-minute setup)

Enable the metrics endpoint in the admin settings (Security →
`metrics_enabled = true`) and scrape it from Prometheus:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: resume-site
    metrics_path: /metrics
    static_configs:
      - targets: ["portfolio.yourdomain.com:443"]
    scheme: https
```

The alerting rules ship at
[`docs/alerting-rules.yaml`](alerting-rules.yaml) with runbook
sections in [`docs/alerting-rules.md`](alerting-rules.md). Drop them
into Prometheus's `rule_files:` list. A Grafana dashboard JSON is
planned for Phase 18.11.

**Lock down `/metrics`**: the endpoint honours the
`metrics_allowed_networks` setting (comma-separated CIDR list,
defaults to the admin allowed list). Never expose `/metrics` to the
public internet.

---

## 8. Backups

Shipped. Details in the README's "Backup" section — manual CLI, REST
API endpoint, systemd timer, compose-cron alternative, restore,
offsite mirroring, and per-archive gpg encryption. This section is
strictly additive operator guidance.

**Minimum production setup:**

```bash
# Enable the daily systemd timer (Quadlet deployments):
cp resume-site-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now resume-site-backup.timer

# Confirm it's scheduled:
systemctl --user list-timers resume-site-backup.timer
```

Archives land in the `resume-site-backups` named volume
(`/app/backups` inside the container). For disaster recovery, mirror
that volume offsite — rclone to an S3-compatible bucket is the
cheapest option, a second Podman host works too.

The admin dashboard's "Last Backup" card shows the most recent
successful run — check it after a freshly-configured timer fires to
confirm things are wired up. A stale `backup_last_success` timestamp
triggers `ResumeBackupStale` in the alerting rules if you're running
Prometheus.

---

## 9. Upgrades

```bash
# Pull the new image tag:
podman pull ghcr.io/kit3713/resume-site:latest
# (or a pinned vN.N.N tag for reproducibility)

# Re-pull the compose services, or restart the Quadlet unit:
podman compose pull && podman compose up -d
# systemctl --user restart resume-site     # Quadlet

# Verify:
curl -fsS https://portfolio.yourdomain.com/readyz
```

Pending migrations apply automatically on start via the entrypoint
script. Seeds use `INSERT OR IGNORE` so no default content is
clobbered. The corruption check in `manage.py migrate` aborts the
start if the DB file is truncated or fails `PRAGMA integrity_check` —
safer than silently applying schema changes over damaged data.

**Rollback:** pin back to the previous tag and restart. If a
migration already applied on the newer version you'd need to restore
from backup first (migrations are forward-only; we don't ship down
migrations).

**Signature verification (optional but recommended):**

```bash
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+' \
  ghcr.io/kit3713/resume-site:latest
```

Every published image is cosign-signed via GitHub's OIDC identity
(Phase 21.3). The command above confirms the image you just pulled
was built by the resume-site CI workflow and hasn't been tampered
with on the registry.

---

## 10. Day-2 operations

**Pending reviews / contact submissions:**

Both are surfaced on the admin dashboard's "Pending Work" card.
Approve/reject from `/admin/reviews`, triage contact spam from
`/admin/contacts`.

**Rotating the Flask `secret_key`:**

```bash
podman exec resume-site python manage.py rotate-secret-key
```

All admin sessions invalidate — log back in afterwards. Don't rotate
more often than your session timeout (60 minutes by default).

**Rotating an API token:**

```bash
podman exec resume-site python manage.py rotate-api-token --name "CI Bot"
```

The CLI prints the new raw token once. Update your consumer with the
new value, then revoke the old one:

```bash
podman exec resume-site python manage.py list-api-tokens
podman exec resume-site python manage.py revoke-api-token --id <old-id>
```

**Pen-test checklist:** run through
[`docs/PENTEST_CHECKLIST.md`](PENTEST_CHECKLIST.md) at least once per
release. It's designed for periodic human review, not automation.

---

## 11. Known limitations

- **Single-writer SQLite:** see §5. Horizontal scaling requires
  moving to Postgres — not planned until v0.4.0+.
- **No built-in object storage:** photos live on the host filesystem
  (via the `resume-site-photos` volume). If you want S3-backed
  storage, mount an s3fs / geesefs volume at `/app/photos`.
- **No CDN:** if you're serving large photo galleries to a global
  audience, put Cloudflare in front of the reverse proxy. The
  responsive image pipeline (Phase 12.3) already generates the small
  variants a CDN needs — just make sure your proxy caches by
  `Accept-Encoding` and `Accept-Language` (we set `Vary` correctly).
- **No public-facing login:** admin is the only account type.
  Multi-user / viewer accounts are deferred to v0.4.0.

---

## 12. Getting help

- GitHub Issues for bugs and feature requests.
- `docs/LOGGING.md` for log schema and forwarding recipes.
- `docs/PENTEST_CHECKLIST.md` for security self-audit.
- `docs/alerting-rules.md` for per-alert runbooks.
- `docs/openapi.yaml` for the REST API surface (interactive at
  `/api/v1/docs` when you set `api_docs_enabled = true` in admin
  settings).
