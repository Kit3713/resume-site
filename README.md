# resume-site

> **Current pre-release: v0.3.3-beta-1** — _Proof_ beta cycle. Every beta iteration passes the full release-publication gate (cosign-signed, Trivy-clean, rolling-upgrade replay verified); the stable `v0.3.3` tag cuts once the beta cycle closes. See [ROADMAP_v0.3.3.md](ROADMAP_v0.3.3.md) (current — Performance + CI hygiene + redundancy closeout shipped; DAST/Playwright/load-test/mutation/edge-case carry-overs still open), [ROADMAP_v0.3.2.md](ROADMAP_v0.3.2.md) (Shield — closed at beta-11 except the deferred items), [ROADMAP_v0.3.1.md](ROADMAP_v0.3.1.md) (Keystone — closed except the RC dry-run release-time action), the [v0.2.0 history](ROADMAP_v0.2.0.md), and [CHANGELOG.md](CHANGELOG.md) for the full story.

A self-hosted, containerized resume and portfolio website engine built with Flask. Apple-inspired design, and admin panel for content management. **Distributed as a signed, multi-arch container image on GHCR** — a source checkout is only needed for development.

## Overview

resume-site is a configurable portfolio website designed around the idea that **you are the product**. It ships as a container image you deploy behind your reverse proxy, with all personal content managed through a local-access-only admin panel or a private configuration file.

### Features

- **Admin panel** -- Manage all content, photos, reviews, services, stats, blog posts, and settings from a browser. Local/VPN access only.
- **Blog engine** -- Full blogging system with rich text editor, tags, RSS 2.0 feed, reading time, pagination, cover images, and publish/draft/archive workflow.
- **Dynamic photo gallery** -- Upload photos via admin with automatic Pillow optimization (resize to 2000px max, JPEG/PNG/WebP quality optimization). Magic byte validation and size limits.
- **Invite-only testimonials** -- Generate tokens via admin or CLI for trusted contacts to submit reviews. Approve, feature, or hide from the review manager.
- **Configurable everything** -- Toggle contact methods, pages, stats, blog, availability status, dark/light mode, and more from the settings panel. No code changes needed.
- **Security hardened** -- CSRF protection, HTML sanitization (nh3), security response headers, admin session timeout, file upload validation, IP-restricted admin access.
- **12-factor configuration** -- Environment variable overrides for all config values (`RESUME_SITE_*`), Docker/Podman secrets support for SMTP passwords.
- **Database migrations** -- Numbered SQL migration system with auto-detection of existing databases, dry-run mode, and status reporting.
- **GSAP animations** -- Scroll-triggered section reveals, staggered card animations, animated stat counters, hero entrance, page header reveals.
- **Dark/light mode** -- Visitor toggle with admin-configurable default.
- **Built-in analytics** -- Page view tracking stored in SQLite with dashboard overview. No cookies, no third parties. Auto-purge via CLI.
- **Contact form** -- Honeypot spam protection, rate limiting, SMTP relay to your personal email. Visitors never see your address.
- **Internationalization (i18n)** -- Flask-Babel integration with 220+ translatable strings. Session-based locale persistence, Accept-Language negotiation, hreflang SEO tags, language switcher. Ship with English, add languages via standard `.po` files.
- **SEO ready** -- Open Graph meta tags, auto-generated sitemap.xml, robots.txt, hreflang tags.
- **Mobile-first responsive** -- Equal priority desktop and mobile experience.
- **Zero personal data in repo** -- All private info lives in your config file and database, never committed.
- **CI pipeline** -- GitHub Actions gate: ruff/bandit/vulture quality, pytest across Python 3.11/3.12 with coverage floor, container build + Trivy CVE scan (HIGH/CRITICAL), rolling-upgrade replay against the previous `:latest`, cosign-signed GHCR publish. Every gate is a full stop — a failure anywhere blocks the image from reaching the registry.

## Tech Stack

- **Backend:** Python 3.12, Flask, Gunicorn
- **Database:** SQLite (WAL mode)
- **Frontend:** Jinja2, CSS custom properties, GSAP
- **Image Processing:** Pillow
- **Container:** Podman / Docker (OCI-compliant, multi-arch)
- **Registry:** GitHub Container Registry (GHCR)
- **Reverse Proxy:** Caddy (not bundled)
- **CI/CD:** GitHub Actions

## Quick Start

The container image on GHCR is the canonical artifact. **Pull it; do not build from source for a deployment.** Source-tree workflows are documented under [Development](#development).

### 1. Pull and verify the signed image

```bash
podman pull ghcr.io/kit3713/resume-site:v0.3.3-beta-1
```

Verify the cosign signature before you run anything (keyless OIDC,
recorded in the public Sigstore transparency log):

```bash
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+' \
  ghcr.io/kit3713/resume-site:v0.3.3-beta-1
```

A non-zero exit means do not deploy — see the
[Stop-ship gate](docs/PRODUCTION.md#stop-ship-gate) for what to do
next.

> **Tag matrix.** Every stable release advances four tags atomically
> (all four point at the same digest):
>
> | Tag | Use |
> |---|---|
> | `v0.3.1` | This exact release (immutable). **Pin this in production.** |
> | `v0.3` | Latest patch on the v0.3 line — moves on every patch. |
> | `v0` | Latest minor on the v0 line — moves on every minor. |
> | `latest` | Most recent stable release — moves on every release. |
>
> **During a beta cycle**, iterative pre-releases ship as
> `vX.Y.Z-beta-N` (e.g. `v0.3.1-beta-1`, `v0.3.1-beta-2`, …) — each
> immutable, each through the same publication gate, but they don't
> advance the stable aliases above. The stable `vX.Y.Z` tag only cuts
> when the beta cycle closes.
>
> `:main` tracks trunk and is **not** a release tag. Treat it as a
> nightly build for local poking — never deploy it.

### 2. Configure

```bash
curl -O https://raw.githubusercontent.com/Kit3713/resume-site/main/config.example.yaml
cp config.example.yaml config.yaml
# Edit config.yaml with your SMTP credentials and admin password.
# Generate a secret_key + admin password hash with:
#   podman run --rm ghcr.io/kit3713/resume-site:v0.3.3-beta-1 python manage.py generate-secret
#   podman run --rm -it ghcr.io/kit3713/resume-site:v0.3.3-beta-1 python manage.py hash-password
```

### 3. Run

Pick one shape — Compose for one-host simplicity, Quadlet for a
systemd-native deployment, or `podman run` for ad-hoc.

#### Podman / Docker (one-shot)

```bash
podman run -d \
  --name resume-site \
  -p 127.0.0.1:8080:8080 \
  -v ./config.yaml:/app/config.yaml:ro,Z \
  -v resume-site-data:/app/data:Z \
  -v resume-site-photos:/app/photos:Z \
  -v resume-site-backups:/app/backups:Z \
  ghcr.io/kit3713/resume-site:v0.3.3-beta-1
```

Bind to `127.0.0.1` so the reverse proxy is the only public ingress —
exposing port 8080 directly to the internet defeats the
`X-Forwarded-For` trust model the app ships with.

#### Podman Compose

```bash
curl -O https://raw.githubusercontent.com/Kit3713/resume-site/main/compose.yaml
podman compose up -d
```

The shipped `compose.yaml` already references the GHCR image; edit
the `image:` line to pin the digest for production.

#### Podman Quadlet (systemd, Fedora / RHEL / AlmaLinux)

```bash
mkdir -p ~/resume-site
cp config.yaml ~/resume-site/config.yaml

curl -O https://raw.githubusercontent.com/Kit3713/resume-site/main/resume-site.container
cp resume-site.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user enable --now resume-site
```

The container entrypoint applies pending migrations and seeds default
content automatically on every start — no separate `init-db` step
needed. Upgrades are the same `pull` + `restart` cycle; new migrations
apply on the next boot.

### 4. Configure your reverse proxy

```
portfolio.yourdomain.com {
    reverse_proxy localhost:8080
}
```

[`docs/PRODUCTION.md`](docs/PRODUCTION.md) has the Caddy / Nginx /
Traefik configurations, TLS guidance, and the full XFF /
admin-allowlist trust-model walkthrough.

### 5. Access admin

Navigate to your site from a local or Tailscale IP and log in at `/admin`.

## Project Structure

```
resume-site/
├── app.py                        # Flask application entry point
├── manage.py                     # CLI tools (migrate, hash-password, etc.)
├── schema.sql                    # Database schema (baseline)
├── config.example.yaml           # Template for infrastructure config
├── config.schema.json            # JSON Schema for config validation
├── requirements.txt              # Python dependencies
├── Containerfile                 # Multi-stage OCI container build
├── compose.yaml                  # Podman/Docker Compose deployment
├── resume-site.container         # Podman Quadlet unit file
├── babel.cfg                     # Babel extraction config for i18n
├── migrations/                   # Numbered SQL migration files
│   ├── 001_baseline.sql
│   ├── 002_blog_tables.sql
│   ├── 003_admin_customization.sql
│   └── 004_i18n.sql
├── translations/                 # i18n message catalogs
│   ├── messages.pot              # Extracted translatable strings
│   └── en/LC_MESSAGES/           # English reference catalog
├── .github/
│   └── workflows/ci.yml          # CI + GHCR publishing pipeline
├── app/
│   ├── __init__.py               # App factory + blueprint registration
│   ├── db.py                     # Database connection lifecycle
│   ├── models.py                 # Read queries and data access layer
│   ├── routes/
│   │   ├── public.py             # Public pages + sitemap/robots
│   │   ├── admin.py              # Admin panel (content, photos, reviews,
│   │   │                         #   tokens, settings, services, stats)
│   │   ├── blog.py               # Public blog routes + RSS feed
│   │   ├── blog_admin.py         # Blog admin CRUD
│   │   ├── contact.py            # Contact form with honeypot + SMTP
│   │   ├── review.py             # Token-based review submission
│   │   └── locale.py             # Language switching endpoint
│   ├── services/
│   │   ├── config.py             # YAML config loader + env var overrides
│   │   ├── blog.py               # Blog CRUD, slug generation, tags
│   │   ├── content.py            # Content blocks + HTML sanitization
│   │   ├── reviews.py            # Review lifecycle (approve/reject/tier)
│   │   ├── settings_svc.py       # Settings registry + validation
│   │   ├── service_items.py      # Service cards CRUD
│   │   ├── stats.py              # Stat counters CRUD
│   │   ├── photos.py             # Photo upload, Pillow processing
│   │   ├── mail.py               # SMTP relay for contact form
│   │   ├── analytics.py          # Page view tracking middleware
│   │   ├── tokens.py             # Review token validation
│   │   └── activity_log.py       # Admin activity audit log
│   ├── templates/
│   │   ├── base.html             # Public layout, nav, footer, OG tags
│   │   ├── public/               # All public page templates
│   │   └── admin/                # Admin panel templates
│   └── static/
│       ├── css/style.css         # Full dark/light theming, responsive
│       └── js/main.js            # GSAP animations, dark/light toggle
└── tests/
    ├── conftest.py               # Pytest fixtures (app, client, auth_client)
    ├── test_app.py               # Foundation + public page tests
    ├── test_admin.py             # Admin CRUD tests
    ├── test_blog.py              # Blog engine tests
    ├── test_security.py          # CSRF, headers, sanitization tests
    ├── test_migrations.py        # Migration system tests
    ├── test_integration.py       # End-to-end flow tests
    ├── test_customization.py     # Theme, colors, fonts, nav, activity log
    └── test_i18n.py              # Locale switching, translations, hreflang
```

## Configuration

### config.yaml (infrastructure only)

```yaml
secret_key: "generate-a-random-key"
database_path: "/app/data/site.db"
photo_storage: "/app/photos"

smtp:
  host: "smtp.gmail.com"
  port: 587
  user: "your@email.com"
  password: "app-password"
  recipient: "your@email.com"

admin:
  username: "admin"
  password_hash: "pbkdf2:sha256:..."
  allowed_networks:
    - "10.0.0.0/8"
    - "192.168.0.0/16"
    - "100.64.0.0/10"
```

### Environment variable overrides

All config.yaml values can be overridden with `RESUME_SITE_*` environment variables (12-factor app support):

```bash
RESUME_SITE_SECRET_KEY="your-secret"
RESUME_SITE_DATABASE_PATH="/app/data/site.db"
RESUME_SITE_SMTP_HOST="smtp.gmail.com"
RESUME_SITE_SMTP_PASSWORD_FILE="/run/secrets/smtp_password"  # Docker/Podman secrets
```

Precedence: environment variables > config.yaml > built-in defaults.

### Admin panel (everything else)

All content, display settings, contact visibility, page toggles, photo management, review moderation, blog management, and site appearance are managed through the admin panel. No container rebuild needed for content changes.

## CLI Tools

```bash
# Initialize the database (runs all pending migrations)
python manage.py init-db

# Apply pending database migrations
python manage.py migrate
python manage.py migrate --status     # Show applied/pending
python manage.py migrate --dry-run    # Preview without executing

# Validate config.yaml structure
python manage.py config

# Generate a cryptographically secure secret key
python manage.py generate-secret

# Generate an admin password hash
python manage.py hash-password

# Generate a review invite token
python manage.py generate-token --name "Contact Name" --type recommendation

# List pending reviews
python manage.py list-reviews --status pending

# Purge analytics older than N days
python manage.py purge-analytics --days 90

# Translation management (i18n)
python manage.py translations extract        # Scan code, generate .pot file
python manage.py translations init --locale es  # Create new locale
python manage.py translations compile        # Compile .po to .mo
python manage.py translations update         # Update .po with new strings
```

All CLI commands work inside a running container:

```bash
podman exec resume-site python manage.py <command>
```

## Container Image

Published to GitHub Container Registry on every tagged release through
the v0.3.1 [release-publication gate](docs/PRODUCTION.md#release-publication-gate)
— Trivy-clean (no HIGH/CRITICAL CVEs with an available fix), cosign
keyless OIDC-signed, multi-arch verified.

The [Quick Start](#quick-start) above covers the canonical pull +
verify + run flow. Tag matrix and stop-ship rules live in
[`docs/PRODUCTION.md`](docs/PRODUCTION.md). Multi-arch support:
`linux/amd64` and `linux/arm64`.

### Container security

- Multi-stage build — build tools not present in runtime image
- Non-root user (`appuser`, UID 1000)
- Health check endpoint
- Minimal system dependencies (curl only)
- OCI labels for provenance
- Cosign-signed (keyless OIDC, Sigstore transparency log)
- Trivy-scanned at publish time — release blocked on any HIGH/CRITICAL
  with an available fix

### Volumes

| Host | Container | Purpose |
|------|-----------|---------|
| `config.yaml` | `/app/config.yaml` | Infrastructure config (mount read-only) |
| Named volume or `./data/` | `/app/data` | SQLite database |
| Named volume or `./photos/` | `/app/photos` | Uploaded images |
| Named volume or `./backups/` | `/app/backups` | Backup archives (see [Backup](#backup)) |

### Backup

resume-site ships a built-in backup CLI that produces a single
timestamped `.tar.gz` containing the SQLite DB (online-backup API,
safe to take while the site is serving traffic), the photo storage
directory, and `config.yaml`. The Phase 17.2 systemd timer drives
this on a daily schedule.

#### Manual / on-demand

```bash
# Inside the container:
podman exec resume-site python manage.py backup
podman exec resume-site python manage.py backup --list
podman exec resume-site python manage.py backup --prune --keep 7
podman exec resume-site python manage.py backup --db-only      # smaller, faster

# Via the REST API (Phase 16.4) — token must carry `admin` scope:
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"db_only": false}' \
     https://your-site/api/v1/admin/backup
```

Archives land in `RESUME_SITE_BACKUP_DIR` (defaults to `/app/backups`,
mapped to the `resume-site-backups` named volume in both
`compose.yaml` and the Quadlet unit). Inspect the host-side path with
`podman volume inspect resume-site-backups`.

For the formal compatibility / deprecation contract covering every
`/api/v1/*` endpoint and the webhook envelope, see
[`docs/API_COMPATIBILITY.md`](docs/API_COMPATIBILITY.md).

#### Scheduled (systemd timer)

For Quadlet / systemd deployments, the repository ships
`resume-site-backup.service` and `resume-site-backup.timer`. They
shell out to `podman exec resume-site python manage.py backup
--prune --keep 7` once per day with a 30-minute jitter:

```bash
# Per-user (rootless):
cp resume-site-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now resume-site-backup.timer
systemctl --user list-timers resume-site-backup.timer

# System-wide:
sudo cp resume-site-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now resume-site-backup.timer
```

Override the schedule or retention without forking the unit files:

```bash
systemctl --user edit resume-site-backup.timer    # change OnCalendar
systemctl --user edit resume-site-backup.service  # change RESUME_SITE_KEEP
```

The admin dashboard's "Last Backup" card shows the most recent
successful run (read from the `backup_last_success` settings row that
`create_backup` writes on every success), the archive count, and the
total on-disk size.

#### Compose-based scheduling (no systemd)

If you're not using Quadlets, schedule the same command from the host
crontab:

```cron
0 2 * * * podman compose -f /path/to/compose.yaml exec -T resume-site \
          python manage.py backup --prune --keep 7
```

#### Restore

```bash
# List candidate archives:
podman exec resume-site python manage.py backup --list

# Restore (will refuse without --force on a non-TTY):
podman exec -it resume-site python manage.py restore \
    --from /app/backups/resume-site-backup-20260415-020000.tar.gz
```

`restore` always writes a `pre-restore-*` sidecar before extraction so
a botched restore can be reversed. The extractor rejects path
traversal, absolute paths, and symlinks (see `_safe_extract` in
`app/services/backups.py`).

#### Offsite copies

The `resume-site-backups` volume is local to the host. For disaster
recovery, mirror it offsite. Example with rclone to an S3-compatible
bucket:

```bash
rclone sync /var/lib/containers/storage/volumes/resume-site-backups/_data \
            s3:my-bucket/resume-site/ --exclude '*.tmp'
```

Run from the same systemd timer (add a second `ExecStart=` line to
`resume-site-backup.service`, or chain a separate timer that runs 30
minutes after the local backup completes).

#### Encryption

For sensitive deployments, wrap each archive with `gpg` after it's
written. Example post-backup hook (drop-in override at
`~/.config/systemd/user/resume-site-backup.service.d/encrypt.conf`):

```ini
[Service]
ExecStartPost=/bin/sh -c 'for f in /var/lib/containers/storage/volumes/resume-site-backups/_data/*.tar.gz; do \
    [ -f "$f.gpg" ] || gpg --yes --batch --recipient backups@example.com --encrypt "$f"; \
done'
```

#### Volume export (legacy / belt-and-suspenders)

The original volume-export approach still works as a secondary
safety net:

```bash
podman volume export resume-site-data    > backup-data.tar
podman volume export resume-site-photos  > backup-photos.tar
podman volume export resume-site-backups > backup-archives.tar
```

This is coarser than `manage.py backup` (raw volume contents, no
online-backup safety on the SQLite file) but useful when you want a
single-shot capture of the entire deployment state.

## Upgrading

```bash
# Pin to a specific version for reproducibility (recommended).
# Re-run cosign verify on the new tag before restarting:
podman pull ghcr.io/kit3713/resume-site:v0.3.3-beta-1
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+' \
  ghcr.io/kit3713/resume-site:v0.3.3-beta-1

podman stop resume-site
podman rm resume-site
# Re-run your original podman run command (or podman compose up -d)
```

Your data and photos persist in volumes. Pending database migrations
apply automatically on container start (or via `manage.py migrate`
inside the running container). A release only reaches GHCR after the
CI's rolling-upgrade replay boots the previous `:latest` against seed
volumes, swaps to the freshly-built image on the same volumes, and
passes `/healthz` + `/readyz` + the landing page + the admin login
probe — a failure blocks publication. For what the `pre-restore-*`
sidecars are for and how to roll back, see
[`docs/UPGRADE.md`](docs/UPGRADE.md).

## Private Deployment Fork

For personal use, fork this repo as a **private fork** on GitHub. Your private fork holds:

- `config.yaml` — your personal configuration
- `photos/` — your portfolio images (if not using volumes)
- `data/` — your database (optional, can be gitignored)
- Any personal customizations

Pull upstream updates from the public repo to get new features without losing your config.

## Dependencies

All runtime dependencies are pinned with hashes in `requirements.txt` (generated via `pip-compile --generate-hashes`). The unpinned source specifications are in `requirements.in`.

| Package | Purpose |
|---------|---------|
| Flask | Web framework (routing, templates, sessions) |
| gunicorn | Production WSGI server |
| PyYAML | Configuration file parsing |
| Flask-Login | Admin session authentication |
| Pillow | Image upload processing (resize, optimize) |
| Flask-WTF | CSRF protection on all POST forms |
| Flask-Babel | Internationalization (i18n) framework |
| Flask-Limiter | Rate limiting on public POST endpoints |
| nh3 | HTML sanitization (Rust-based, safe allowlisting) |
| mistune | Markdown rendering for blog posts |

CI also runs `pip-audit` on every push to flag known vulnerabilities.

## Roadmap

The active roadmap is [ROADMAP_v0.3.1.md](ROADMAP_v0.3.1.md) (Keystone — release-publication gate). The successor releases are [v0.3.2 Shield](ROADMAP_v0.3.2.md) and [v0.3.3 Proof](ROADMAP_v0.3.3.md). Historical: [v0.3.0 Forge](ROADMAP_v0.3.0.md), [v0.2.0](ROADMAP_v0.2.0.md), and the original v0.1.0 build [ROADMAP.md](ROADMAP.md).

## Development

You only need a source checkout to **modify** resume-site. To deploy
or run it, use the GHCR image — see [Quick Start](#quick-start).

```bash
git clone https://github.com/Kit3713/resume-site.git
cd resume-site
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp config.example.yaml config.yaml
# Edit config.yaml
python manage.py init-db
flask run --debug    # or: python app.py --debug (with RESUME_SITE_DEV=1)
```

The dev-server entry point in `app.py` is gated behind
`RESUME_SITE_DEV=1` and an explicit `--debug` flag (Phase 22.1) — the
Werkzeug debug console is not reachable by accident in production.
For the full developer workflow (running the test suite, ruff, bandit,
container build, the upgrade-replay simulation), see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## Security

See [SECURITY.md](SECURITY.md) for the security model and vulnerability reporting.

## License

MIT License — see [LICENSE](LICENSE) for details.
