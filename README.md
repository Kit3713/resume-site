# resume-site

> **Current release: v0.2.0** — Hardened, extensible portfolio and blog platform with i18n, admin customization, and container-native deployment.
>
> See [ROADMAP_v0.2.0.md](ROADMAP_v0.2.0.md) for the full development history.

A self-hosted, containerized resume and portfolio website engine built with Flask. Apple-inspired design, and admin panel for content management.

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
- **CI pipeline** -- GitHub Actions running pytest + flake8 across Python 3.11/3.12 with container build verification and GHCR publishing.

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

### Option A: Pull from container registry (recommended)

```bash
podman pull ghcr.io/kit3713/resume-site:latest
```

Create your config file:

```bash
curl -O https://raw.githubusercontent.com/Kit3713/resume-site/main/config.example.yaml
cp config.example.yaml config.yaml
# Edit config.yaml with your SMTP credentials and admin password
```

Run:

```bash
podman run -d \
  --name resume-site \
  -p 8080:8080 \
  -v ./config.yaml:/app/config.yaml:ro,Z \
  -v resume-site-data:/app/data:Z \
  -v resume-site-photos:/app/photos:Z \
  ghcr.io/kit3713/resume-site:latest
```

Initialize the database:

```bash
podman exec resume-site python manage.py init-db
```

### Option B: Podman Compose

```bash
git clone https://github.com/Kit3713/resume-site.git
cd resume-site
cp config.example.yaml config.yaml
# Edit config.yaml
podman compose up -d
podman compose exec resume-site python manage.py init-db
```

### Option C: Podman Quadlet (systemd)

For systemd-managed deployments on Fedora, RHEL, or AlmaLinux:

```bash
mkdir -p ~/resume-site
cp config.example.yaml ~/resume-site/config.yaml
# Edit ~/resume-site/config.yaml

cp resume-site.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start resume-site
systemctl --user enable resume-site
```

### Option D: Local development

```bash
git clone https://github.com/Kit3713/resume-site.git
cd resume-site
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml
python manage.py init-db
flask run --debug
```

### Configure your reverse proxy

```
portfolio.yourdomain.com {
    reverse_proxy localhost:8080
}
```

### Access admin

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

The container image is published to GitHub Container Registry on every tagged release:

```bash
# Latest stable release
podman pull ghcr.io/kit3713/resume-site:latest

# Specific version
podman pull ghcr.io/kit3713/resume-site:0.2.0

# Rolling development (main branch HEAD)
podman pull ghcr.io/kit3713/resume-site:main
```

Multi-arch support: `linux/amd64` and `linux/arm64`.

### Container security

- Multi-stage build — build tools not present in runtime image
- Non-root user (`appuser`, UID 1000)
- Health check endpoint
- Minimal system dependencies (curl only)
- OCI labels for provenance

### Volumes

| Host | Container | Purpose |
|------|-----------|---------|
| `config.yaml` | `/app/config.yaml` | Infrastructure config (mount read-only) |
| Named volume or `./data/` | `/app/data` | SQLite database |
| Named volume or `./photos/` | `/app/photos` | Uploaded images |

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
podman pull ghcr.io/kit3713/resume-site:latest
podman stop resume-site
podman rm resume-site
# Re-run your original podman run command (or podman compose up -d)
```

Your data and photos persist in volumes. Database migrations (v0.2.0+) will run automatically or via `manage.py migrate`.

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

See [ROADMAP_v0.2.0.md](ROADMAP_v0.2.0.md) for the full development plan. The original v0.1.0 build phases are documented in [ROADMAP.md](ROADMAP.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## Security

See [SECURITY.md](SECURITY.md) for the security model and vulnerability reporting.

## License

MIT License — see [LICENSE](LICENSE) for details.
