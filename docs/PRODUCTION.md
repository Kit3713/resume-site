# Production Deployment Guide

End-to-end guide for deploying resume-site to a production server. If
you're experimenting locally, the README's "Quick Start" is shorter and
gets you to a running site in about five minutes. This document
assumes you're putting the site in front of real visitors and want a
hardened, monitored, backed-up deployment.

> **TL;DR — GHCR is the canonical artifact.** Production deploys pull
> a signed image from `ghcr.io/kit3713/resume-site`. A source checkout
> is for development only. Every release is Trivy-clean,
> cosign-signed, and multi-arch verified through the
> [release-publication gate](#release-publication-gate). Verify the
> signature before each upgrade — see §[3.0 Pull and verify the
> signed image](#30-pull-and-verify-the-signed-image).

---

## 1. Choose your deployment shape

Three supported shapes, ordered from simplest to richest. All three
consume the same GHCR image — they only differ in how the host
supervises the container:

| Shape | Best for | Defined by |
|---|---|---|
| **Podman Compose** | Single host, "just get it running" | `compose.yaml` |
| **Podman Quadlet (systemd)** | Fedora/RHEL servers, auto-start, journald logs | `resume-site.container`, `resume-site-backup.{service,timer}` |
| **Kubernetes / Nomad** | Multi-host, orchestrated, horizontal scaling | See [§13 Kubernetes / Nomad manifests](#13-kubernetes--nomad-manifests) below |

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

### 3.0 Pull and verify the signed image

Pin to an exact `vX.Y.Z` tag for production. The aliases (`v0.3`,
`v0`, `latest`) move between releases — convenient for "give me the
newest patch" pulls, dangerous if your manifest is supposed to be
reproducible.

```bash
podman pull ghcr.io/kit3713/resume-site:v0.3.1
```

Verify the cosign signature **before** running anything from the new
image. Every release is signed by the publish CI job using GitHub's
OIDC identity (no keypair to manage; the signature + certificate land
in the public Sigstore transparency log):

```bash
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+' \
  ghcr.io/kit3713/resume-site:v0.3.1
```

A non-zero exit means the image was not built by the resume-site CI
workflow (or has been tampered with on the registry between push and
your pull). **Do not deploy.** That's a stop-ship — see
§[12 Release publication gate](#release-publication-gate).

For maximum reproducibility, capture the digest after the verify
passes and pin to that in your manifests:

```bash
podman image inspect ghcr.io/kit3713/resume-site:v0.3.1 \
    --format '{{ index .RepoDigests 0 }}'
# → ghcr.io/kit3713/resume-site@sha256:<64-hex>
```

A digest pin is the only way to guarantee the bits you tested are the
bits that boot in production — the `vX.Y.Z` tag is conventionally
immutable but a force-pushed registry tag could in theory swap under
you. Digests cannot.

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
    # Strip-and-overwrite: $remote_addr (NOT $proxy_add_x_forwarded_for)
    # so client-supplied X-Forwarded-For cannot slip through. See
    # §3.5.1 for the trust-model rationale.
    proxy_set_header X-Forwarded-For  $remote_addr;
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

#### 3.5.1 Reverse-proxy binding and the X-Forwarded-For trust model ⚠ {#reverse-proxy-xff}

> **tl;dr** — Keep the container bound to `127.0.0.1:8080` (the
> v0.3.1 default) and put a reverse proxy in front. Exposing port
> 8080 directly to the public internet is an admin-bypass waiting to
> happen until `get_client_ip()` extraction lands in v0.3.2 Phase
> 23.2.

As of v0.3.1, `compose.yaml` and `resume-site.container` both publish
the container on `127.0.0.1:8080` only (Phase 22.5, issue #66). The
app is reachable exclusively through a reverse proxy running on the
host — Caddy, nginx, Cloudflare Tunnel, Tailscale Funnel, or
equivalent. **Do not change those bindings to `0.0.0.0` /
`"8080:8080"` unless you understand the trust model below.**

##### Why the app cannot be safely exposed on `0.0.0.0` today

The admin panel uses an IP allowlist (`admin.allowed_networks` in
`config.yaml`) to gate `/admin/*`. Phase 22.6 (issue #16) fixed the
admin allowlist to consult `X-Forwarded-For` only when the inbound
TCP peer (`request.remote_addr`) is inside `trusted_proxies`. But
four other callsites — contact-form rate limit, API rate limit,
analytics IP hashing, `/metrics` access gate, login throttle — still
trust `X-Forwarded-For` unconditionally (issue #34). The full
extraction into a shared `get_client_ip()` helper lands in **v0.3.2
Phase 23.2**.

Until that ships:

- **Binding to a public interface lets a remote attacker forge
  `X-Forwarded-For: 127.0.0.1`** to sidestep the analytics hash, the
  rate limiters, and the `/metrics` IP gate.
- A misconfigured `trusted_proxies` can even re-open the admin gate
  to the forged header.

##### What "safe" looks like on v0.3.1

```yaml
# config.yaml
trusted_proxies:
  - "127.0.0.0/8"    # Caddy / nginx on the same host
# - "10.0.0.0/24"    # or the docker-compose bridge network if the proxy is a peer container
```

```yaml
# compose.yaml (shipped default)
ports:
  - "127.0.0.1:8080:8080"
```

…and a reverse proxy that:

1. Terminates TLS.
2. Sets `X-Forwarded-For: <real client IP>`.
3. Does NOT pass through any client-supplied `X-Forwarded-For`
   (strip-and-overwrite, not append).

Caddy does this out of the box; nginx requires
`proxy_set_header X-Forwarded-For $remote_addr;` (**not**
`$proxy_add_x_forwarded_for`, which _appends_ the client-supplied
header).

##### If you really need to expose 8080 directly (not recommended)

You are opting out of the Phase 22.6 guarantee. Wait for v0.3.2
Phase 23.2, or:

1. Set `trusted_proxies: []` in `config.yaml` so the admin gate
   ignores `X-Forwarded-For` outright.
2. Accept that contact-form, API, and login rate-limiting will be
   spoofable (the other four callsites still trust XFF — see
   issue #34).
3. Restrict `admin.allowed_networks` to the **public** IP range you
   actually administer from, not `127.0.0.0/8`.

Track v0.3.2 Phase 23.2 before making this permanent.

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

### 7.3 Observability runbook cross-reference {#observability-runbook}

<!-- ANCHOR: agent-c-observability-cross-ref (Phase 36.6).
     Agent C: drop the one-line pointer to docs/OBSERVABILITY_RUNBOOK.md
     here. Five-minute edit; section exists so the link has a stable
     home (and so the v0.3.0 carry-over is visibly closed in this
     file rather than left as an implicit "see §7"). -->

_Reserved for Agent C's observability runbook pointer (Phase 36.6)._
Until Agent C lands, the runbook lives at
[`docs/OBSERVABILITY_RUNBOOK.md`](OBSERVABILITY_RUNBOOK.md) — the
"when to reach for which tool" decision tree, the Prometheus +
Grafana + Alertmanager wiring, and the synthetic-monitoring tiers.

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

[`docs/UPGRADE.md`](UPGRADE.md) covers data-survival across upgrades
(migration reversibility, `pre-restore-*` sidecars, rolling-upgrade
replay). For the orthogonal API-consumer contract — what stays stable
across `/api/v1/*` and what triggers a `/api/v2/` prefix bump — see
[`docs/API_COMPATIBILITY.md`](API_COMPATIBILITY.md).

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

## 12. Release publication gate {#release-publication-gate}

Every published release passes through a fixed CI pipeline. The
operator-visible side of that pipeline is what's described here —
what gets published, under which tags, and the rules under which a
release is **not** published.

### 12.1 Tag matrix

Every stable release advances four tags pointing at the same digest.
The exact `vX.Y.Z` tag is pushed by the multi-arch build itself; the
`vX.Y`, `vX`, and `latest` aliases are layered on via
`docker buildx imagetools create` (no image data is re-uploaded — the
alias is a pure registry-side pointer to the same multi-arch
manifest):

| Tag | Stability | When it moves |
|---|---|---|
| `vX.Y.Z` | **Immutable.** Pin this in production manifests. | Once per release. Never re-pushed. |
| `vX.Y` | Latest patch on this minor line. | Every patch release. |
| `vX` | Latest minor on this major line. | Every minor (and patch) release. |
| `latest` | Most recent stable release. | Every release. **Gated on `release-verify` smoke test passing on both amd64 and arm64.** |

The `latest` alias is advanced **after** the `release-verify` job
pulls the just-pushed digest on a clean runner and confirms `/healthz`
+ `/readyz` answer green on both `linux/amd64` and `linux/arm64`. If
either arch fails the smoke test, `latest` does not move; the
`vX.Y.Z` tag remains pulled but operators tracking `latest` are not
silently moved to a broken build.

`:main` continues to track trunk on every push to `main` and is
explicitly **not a release tag**:

- It is built without the `release-verify` smoke test.
- It does **not** participate in the tag matrix.
- It is signed with cosign (same as a release tag) so its provenance
  is verifiable, but it is not promoted from RC → stable.
- Use it for local poking / nightly builds. Never deploy it.

### 12.2 Stop-ship gate {#stop-ship-gate}

Each rule below is a **full stop**, not a ratchet. Failing any one of
them aborts the release; nothing is published, no tags move, no
operator gets paged about a "soft failure."

| Gate | What fails the release |
|---|---|
| `quality` | Any ruff lint, ruff format, bandit, or SQL grep finding. |
| `test (3.11, 3.12)` | Any failing test, or coverage below 60 %. |
| `container-build` | Image fails to build, or the smoke-test container fails `/`, `/healthz`, or `/readyz`. |
| `container-scan` | **Trivy reports any HIGH or CRITICAL CVE with an available fix.** Unfixed CVEs are advisory only — operators can't action what upstream hasn't patched. |
| `publish` | Multi-arch build/push to GHCR fails, or `cosign sign` fails. |
| `release-verify` | Pulling the just-pushed digest fails on either `linux/amd64` or `linux/arm64`, or `/healthz` / `/readyz` fail on the pulled image. |
| **Cosign verify on a clean machine** | A release-checklist step pulls the published image to a clean machine and runs `cosign verify` against it. A failure here means the signature didn't replicate to the registry correctly — release is rolled back. |
| **Release notes** | The release does not honour [`.github/RELEASE_TEMPLATE.md`](../.github/RELEASE_TEMPLATE.md) — missing pull command, digest line, cosign verify line, "Breaking changes" section, or "Migration notes" section. |

The whole point of the gate is that there is no "we'll fix it
forward" path. A release that can't satisfy the gate is not a
release. Either the gate is wrong (rare; document the exception
explicitly and update the gate) or the release is not ready (common;
fix the underlying issue and try again).

### 12.3 Dry-run on `vX.Y.Z-rc.1`

Before any stable tag, cut `vX.Y.Z-rc.1` against the same gate.
Everything the stable release has to do, the RC has to do — same
Trivy scan, same cosign signature, same `release-verify` smoke test,
same release-notes template (with "release candidate" called out).
The RC is the dress rehearsal; if anything in the gate behaves
unexpectedly on the RC, fix it there before promoting.

The RC's tag matrix is narrower: only `vX.Y.Z-rc.1` and (optionally)
`vX.Y.Z-rc` are pushed. RCs **do not** advance `vX.Y`, `vX`, or
`latest`.

---

## 13. Kubernetes / Nomad manifests {#k8s-manifests}

<!-- ANCHOR: agent-c-k8s-manifests (Phase 36.8).
     Agent C: drop the commented-out k8s Deployment + Service + Ingress
     manifests here. Include the readinessProbe / livenessProbe block
     with `initialDelaySeconds: 5, failureThreshold: 3` (Phase 21.2
     contract — already documented in compose.yaml). The probe pair is
     what makes the image work in orchestrated environments; this
     section is the full manifest form operators have asked for.
     Not officially supported, but the image is designed to support it. -->

_Reserved for Agent C's k8s / Nomad commented-example manifests
(Phase 36.8)._ Until that lands, the `compose.yaml` health-check block
documents the readiness contract every orchestrator needs:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/healthz"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 10s
```

For a real Kubernetes deployment, mirror that into a `livenessProbe`
on `/healthz` and a `readinessProbe` on `/readyz` with
`initialDelaySeconds: 5, failureThreshold: 3` (matching the Phase
21.2 contract).

---

## 14. Getting help

- GitHub Issues for bugs and feature requests.
- [`docs/LOGGING.md`](LOGGING.md) for log schema and forwarding recipes.
- [`docs/PENTEST_CHECKLIST.md`](PENTEST_CHECKLIST.md) for security self-audit.
- [`docs/alerting-rules.md`](alerting-rules.md) for per-alert runbooks.
- [`docs/UPGRADE.md`](UPGRADE.md) for the upgrade-survivability story
  (rolling-upgrade replay, `pre-restore-*` sidecars, rollback).
- [`docs/openapi.yaml`](openapi.yaml) for the REST API surface (interactive at
  `/api/v1/docs` when you set `api_docs_enabled = true` in admin
  settings).
- [`.github/RELEASE_TEMPLATE.md`](../.github/RELEASE_TEMPLATE.md) for the
  required release-notes skeleton (pull command, digest, cosign verify,
  Breaking changes, Migration notes — see §12 above).
