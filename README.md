# resume-site

> **Status: Feature-complete (Phases 1–4). Ready for content population and deployment.**

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
- **CI pipeline** — GitHub Actions running pytest + flake8 across Python 3.11/3.12 with container build verification.

## Tech Stack

- **Backend:** Python, Flask, Gunicorn
- **Database:** SQLite
- **Frontend:** Jinja2, CSS custom properties, GSAP
- **Image Processing:** Pillow
- **Container:** Podman / Docker (OCI-compliant)
- **Reverse Proxy:** Caddy (not bundled)
- **CI:** GitHub Actions

## Project Structure

```
resume-site/
├── app.py                      # Flask application entry point
├── config.example.yaml         # Template for infrastructure config
├── requirements.txt            # Python dependencies
├── Containerfile               # Container build instructions
├── schema.sql                  # Database schema
├── manage.py                   # CLI tools (init-db, hash-password, tokens, analytics)
├── .github/
│   ├── workflows/ci.yml        # GitHub Actions CI pipeline
│   ├── ISSUE_TEMPLATE/         # Bug report + feature request templates
│   └── PULL_REQUEST_TEMPLATE.md
├── app/
│   ├── __init__.py             # App factory + blueprint registration
│   ├── models.py               # Database models and queries
│   ├── routes/
│   │   ├── public.py           # All public page routes + sitemap/robots
│   │   ├── admin.py            # Full admin panel (content, photos, reviews,
│   │   │                       #   tokens, settings, services, stats)
│   │   ├── contact.py          # Contact form with honeypot + SMTP
│   │   └── review.py           # Token-based review submission
│   ├── services/
│   │   ├── config.py           # YAML config loader
│   │   ├── photos.py           # Photo upload, Pillow processing, deletion
│   │   ├── mail.py             # SMTP relay for contact form
│   │   ├── analytics.py        # Page view tracking middleware
│   │   └── tokens.py           # Review token validation
│   ├── templates/
│   │   ├── base.html           # Public layout, nav, footer, OG tags, dark/light
│   │   ├── public/
│   │   │   ├── index.html      # Landing page (hero, about, stats, featured, CTA)
│   │   │   ├── portfolio.html  # Photo gallery (masonry grid, category filter)
│   │   │   ├── case_study.html # Case study detail (problem/solution/result)
│   │   │   ├── services.html   # Services + expandable skill cards
│   │   │   ├── projects.html   # Technical projects list
│   │   │   ├── project_detail.html
│   │   │   ├── testimonials.html
│   │   │   ├── certifications.html
│   │   │   ├── contact.html
│   │   │   └── review.html     # Token-based review submission form
│   │   └── admin/
│   │       ├── base_admin.html # Admin layout with sidebar navigation
│   │       ├── dashboard.html  # Analytics overview
│   │       ├── login.html
│   │       ├── content.html    # Content block list
│   │       ├── content_edit.html # Rich text content editor
│   │       ├── photos.html     # Photo manager (upload, tiers, metadata)
│   │       ├── reviews.html    # Review moderation
│   │       ├── tokens.html     # Token generator
│   │       ├── services.html   # Service card manager
│   │       ├── stats.html      # Stats bar manager
│   │       └── settings.html   # All site toggles and config
│   └── static/
│       ├── css/
│       │   └── style.css       # Full dark/light theming, admin styles, responsive
│       └── js/
│           └── main.js         # GSAP animations, dark/light toggle, interactions
└── tests/
    ├── conftest.py             # Pytest fixtures (temp DB, test client)
    └── test_app.py             # Route, auth, IP restriction, and page tests
```

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/Kit3713/resume-site.git
cd resume-site
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your SMTP credentials and admin password. This file is gitignored.

### 2. Run locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py init-db
flask run --debug
```

### 3. Deploy with Podman

```bash
podman build -t resume-site .
podman run -d \
  --name resume-site \
  -p 8080:8080 \
  -v ./config.yaml:/app/config.yaml:ro,Z \
  -v ./photos:/app/photos:Z \
  -v ./data:/app/data:Z \
  resume-site
```

### 4. Configure Caddy

```
portfolio.yourdomain.com {
    reverse_proxy localhost:8080
}
```

### 5. Access admin

Navigate to your site from a local/Tailscale IP and log in at `/admin`.

## Configuration

### config.yaml (infrastructure)

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

## Private Deployment Fork

For personal use, fork this repo as a **private fork** on GitHub. Your private fork holds:

- `config.yaml` — your personal configuration
- `photos/` — your portfolio images
- `data/` — your database (optional, can be gitignored)
- Any personal customizations

Pull upstream updates from the public repo to get new features without losing your config.

## License

MIT License — see [LICENSE](LICENSE) for details.
