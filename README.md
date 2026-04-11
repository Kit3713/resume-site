# resume-site

> **Status: Phase 2 complete — Public pages and services implemented. Admin panel (Phase 3) is next.**

A self-hosted, containerized resume and portfolio website engine built with Flask. Apple-inspired design, admin panel for content management, and zero personal data in the repo.

## Overview

resume-site is a configurable portfolio website designed around the idea that **you are the product**. It ships as a container image you deploy behind your reverse proxy, with all personal content managed through a local-access-only admin panel or a private configuration file.

### What's Working

- All public-facing pages: landing (hero, about, stats, services, featured portfolio, featured testimonials, CTA), portfolio gallery with case study detail pages, services with expandable skill cards, projects with optional detail pages, testimonials, certifications, contact form, and resume PDF download
- Contact form with honeypot spam protection and SMTP relay
- Invite-only review submission via token-authenticated URLs
- GSAP scroll animations: hero entrance, section reveals, card staggers, animated stat counters, page header reveals
- Dark/light mode toggle with CSS custom properties
- Built-in analytics: page view tracking to SQLite (no cookies, no third parties)
- Photo serving from configurable storage directory
- Admin login with IP restriction (private/Tailscale networks only)
- Database models for all content types (settings, content blocks, stats, services, skills, photos, reviews, projects, certifications, contacts, tokens)
- CI pipeline via GitHub Actions (pytest + flake8 + container build)

### What's Next (Phase 3 — Admin Panel)

- Content editor (Quill.js rich text)
- Photo upload with Pillow processing (originals + optimized)
- Photo manager (categories, metadata, tiers)
- Review manager (approve/reject, set tiers and types)
- Token generator for review invites
- Settings panel (all toggles and configuration)

## Tech Stack

- **Backend:** Python, Flask, Gunicorn
- **Database:** SQLite
- **Frontend:** Jinja2, CSS custom properties, GSAP, Quill.js (admin editor — planned)
- **Image Processing:** Pillow (serving implemented, upload processing planned)
- **Container:** Podman / Docker (OCI-compliant)
- **Reverse Proxy:** Caddy (not bundled)
- **CI:** GitHub Actions (pytest, flake8, container build verification)

## Project Structure

Files marked with `*` are planned but not yet implemented.

```
resume-site/
├── app.py                      # Flask application entry point
├── config.example.yaml         # Template for infrastructure config
├── requirements.txt            # Python dependencies
├── Containerfile               # Container build instructions
├── schema.sql                  # Database schema
├── manage.py                   # CLI tools (init-db, hash-password)
├── .github/
│   ├── workflows/ci.yml        # GitHub Actions CI pipeline
│   ├── ISSUE_TEMPLATE/         # Bug report + feature request templates
│   └── PULL_REQUEST_TEMPLATE.md
├── app/
│   ├── __init__.py             # App factory + blueprint registration
│   ├── models.py               # Database models and queries
│   ├── routes/
│   │   ├── public.py           # All public page routes
│   │   ├── admin.py            # Admin login, dashboard, IP restriction
│   │   ├── contact.py          # Contact form with honeypot + SMTP
│   │   └── review.py           # Token-based review submission
│   ├── services/
│   │   ├── config.py           # YAML config loader
│   │   ├── photos.py           # Photo file serving
│   │   ├── mail.py             # SMTP relay for contact form
│   │   ├── analytics.py        # Page view tracking middleware
│   │   └── tokens.py           # Review token validation
│   ├── templates/
│   │   ├── base.html           # Layout, nav, footer, dark/light toggle
│   │   ├── public/
│   │   │   ├── index.html      # Landing page (hero, about, stats, featured, CTA)
│   │   │   ├── portfolio.html  # Photo gallery (masonry grid, category filter)
│   │   │   ├── case_study.html # Case study detail (problem/solution/result)
│   │   │   ├── services.html   # Services + expandable skill cards
│   │   │   ├── projects.html   # Technical projects list
│   │   │   ├── project_detail.html # Individual project detail page
│   │   │   ├── testimonials.html   # Reviews (featured + standard tiers)
│   │   │   ├── certifications.html # Cert badges with images and dates
│   │   │   ├── contact.html    # Contact form
│   │   │   └── review.html     # Token-based review submission form
│   │   └── admin/
│   │       ├── dashboard.html  # Admin dashboard shell
│   │       ├── login.html      # Admin login form
│   │       ├── content.html *  # Rich text content editor
│   │       ├── photos.html *   # Photo manager
│   │       ├── reviews.html *  # Review manager
│   │       ├── tokens.html *   # Token generator
│   │       └── settings.html * # All toggles and config
│   └── static/
│       ├── css/
│       │   └── style.css       # Full dark/light theming, responsive layout
│       └── js/
│           ├── main.js         # GSAP animations, dark/light toggle, interactions
│           └── admin.js *      # Admin panel interactions
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

All content, display settings, contact visibility, page toggles, photo management, review moderation, and site appearance will be managed through the admin panel (Phase 3). No container rebuild needed for content changes.

## CLI Tools

```bash
# Initialize the database
python manage.py init-db

# Generate an admin password hash
python manage.py hash-password
```

The following commands are planned but not yet implemented:

```bash
# Generate a review invite token
python manage.py generate-token --name "Contact Name" --type recommendation

# List pending reviews
python manage.py list-reviews --status pending
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
