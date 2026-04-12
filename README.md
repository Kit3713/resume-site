# resume-site

> **Current release: v0.1.0** — Feature-complete portfolio engine. Content population and deployment ready.
>
> **In development: [v0.2.0](ROADMAP_v0.2.0.md)** — Blog engine, security hardening, admin customization, i18n, container registry publishing.

A self-hosted, containerized resume and portfolio website engine built with Flask. Apple-inspired design, admin panel for content management, and zero personal data in the repo.

## Overview

resume-site is a configurable portfolio website designed around the idea that **you are the product**. It ships as a container image you deploy behind your reverse proxy, with all personal content managed through a local-access-only admin panel or a private configuration file.

### Features

- **Admin panel** — Manage all content, photos, reviews, services, stats, and settings from a browser. Local/VPN access only. Dedicated admin base template with sidebar navigation.
- **Dynamic photo gallery** — Upload photos via admin with automatic Pillow optimization (resize to 2000px max, JPEG/PNG/WebP quality optimization). Assign categories, metadata, display tiers.
- **Invite-only testimonials** — Generate tokens via admin or CLI for trusted contacts to submit reviews. Approve, feature, or hide from the review manager.
- **Configurable everything** — Toggle contact methods, pages, stats, availability status, dark/light mode, and more from the settings panel. No code changes needed.
- **GSAP animations** — Scroll-triggered section reveals, staggered card animations, animated stat counters, hero entrance, page header reveals.
- **Dark/light mode** — Visitor toggle with admin-configurable default.
- **Built-in analytics** — Page view tracking stored in SQLite with dashboard overview. No cookies, no third parties. Auto-purge via CLI.
- **Contact form** — Honeypot spam protection, rate limiting, SMTP relay to your personal email. Visitors never see your address.
- **SEO ready** — Open Graph meta tags, auto-generated sitemap.xml, robots.txt.
- **Mobile-first responsive** — Equal priority desktop and mobile experience.
- **Zero personal data in repo** — All private info lives in your config file and database, never committed.
- **CI pipeline** — GitHub Actions running pytest + flake8 across Python 3.11/3.12 with container build verification and GHCR publishing.

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
├── config.example.yaml           # Template for infrastructure config
├── requirements.txt              # Python dependencies
├── Containerfile                 # Multi-stage OCI container build
├── compose.yaml                  # Podman/Docker Compose deployment
├── resume-site.container         # Podman Quadlet unit file
├── schema.sql                    # Database schema
├── manage.py                     # CLI tools
├── .github/
│   └── workflows/ci.yml          # CI + GHCR publishing pipeline
├── app/
│   ├── __init__.py               # App factory + blueprint registration
│   ├── models.py                 # Database models and queries
│   ├── routes/
│   │   ├── public.py             # Public page routes + sitemap/robots
│   │   ├── admin.py              # Admin panel (content, photos, reviews,
│   │   │                         #   tokens, settings, services, stats)
│   │   ├── contact.py            # Contact form with honeypot + SMTP
│   │   └── review.py             # Token-based review submission
│   ├── services/
│   │   ├── config.py             # YAML config loader
│   │   ├── photos.py             # Photo upload, Pillow processing, deletion
│   │   ├── mail.py               # SMTP relay for contact form
│   │   ├── analytics.py          # Page view tracking middleware
│   │   └── tokens.py             # Review token validation
│   ├── templates/
│   │   ├── base.html             # Public layout, nav, footer, OG tags
│   │   ├── public/               # All public page templates
│   │   └── admin/                # Admin panel templates
│   └── static/
│       ├── css/style.css         # Full dark/light theming, responsive
│       └── js/main.js            # GSAP animations, dark/light toggle
└── tests/
    ├── conftest.py               # Pytest fixtures
    └── test_app.py               # Route, auth, IP restriction tests
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

### Admin panel (everything else)

All content, display settings, contact visibility, page toggles, photo management, review moderation, and site appearance are managed through the admin panel. No container rebuild needed for content changes.

## CLI Tools

```bash
# Initialize the database
python manage.py init-db

# Generate an admin password hash
python manage.py hash-password

# Generate a review invite token
python manage.py generate-token --name "Contact Name" --type recommendation

# List pending reviews
python manage.py list-reviews --status pending

# Purge analytics older than N days
python manage.py purge-analytics --days 90
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
podman pull ghcr.io/kit3713/resume-site:0.1.0

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

Back up these paths to preserve your site:

1. `config.yaml` — your configuration
2. The data volume — `podman volume export resume-site-data > backup-data.tar`
3. The photos volume — `podman volume export resume-site-photos > backup-photos.tar`

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

## Roadmap

See [ROADMAP_v0.2.0.md](ROADMAP_v0.2.0.md) for the full development plan. The original v0.1.0 build phases are documented in [ROADMAP.md](ROADMAP.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## Security

See [SECURITY.md](SECURITY.md) for the security model and vulnerability reporting.

## License

MIT License — see [LICENSE](LICENSE) for details.
