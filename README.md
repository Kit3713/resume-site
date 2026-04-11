# resume-site

> **Status: Early development (Phase 1 — Foundation)**

A self-hosted, containerized resume and portfolio website engine built with Flask. Apple-inspired design, admin panel for content management, and zero personal data in the repo.

## Overview

resume-site is a configurable portfolio website designed around the idea that **you are the product**. It ships as a container image you deploy behind your reverse proxy, with all personal content managed through a local-access-only admin panel or a private configuration file.

### Current State

The foundation is in place: Flask app factory, YAML config loading, SQLite schema, base template with dark/light mode, a hero-only landing page, Containerfile, admin IP restriction + login/logout, CLI tools (`init-db`, `hash-password`), and basic test scaffolding. See the [Roadmap](ROADMAP.md) for what's next.

### Planned Features

- **Admin panel** — Manage all content, photos, reviews, and settings from a browser. Local/VPN access only.
- **Dynamic photo gallery** — Upload photos via admin. Originals stored, optimized versions served automatically.
- **Invite-only testimonials** — Generate tokens for trusted contacts to submit reviews. Approve, feature, or hide from admin.
- **Configurable everything** — Toggle contact methods, pages, stats, availability status, dark/light mode, and more. No code changes needed.
- **GSAP animations** — Scroll-triggered reveals, parallax, animated counters, page transitions.
- **Dark/light mode** — Visitor toggle with admin-configurable default. *(toggle implemented)*
- **Built-in analytics** — Simple page view tracking stored in SQLite. No cookies, no third parties.
- **Contact form** — Honeypot spam protection, SMTP relay to your personal email. Visitors never see your address.
- **SEO ready** — Meta tags, Open Graph for rich link previews, auto-generated sitemap.
- **Mobile-first responsive** — Equal priority desktop and mobile experience.
- **Zero personal data in repo** — All private info lives in your config file and database, never committed.

## Tech Stack

- **Backend:** Python, Flask, Gunicorn
- **Database:** SQLite
- **Frontend:** Jinja2, CSS custom properties, GSAP, Quill.js (admin editor)
- **Image Processing:** Pillow
- **Container:** Podman / Docker (OCI-compliant)
- **Reverse Proxy:** Caddy (not bundled)

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
├── app/
│   ├── __init__.py             # App factory
│   ├── models.py               # Database models (AdminUser)
│   ├── routes/
│   │   ├── public.py           # Public-facing pages (index only)
│   │   ├── admin.py            # Admin panel routes (login, dashboard)
│   │   ├── review.py *         # Token-based review submission
│   │   └── contact.py *        # Contact form handling
│   ├── services/
│   │   ├── config.py           # YAML config loader
│   │   ├── photos.py *         # Image upload + Pillow processing
│   │   ├── mail.py *           # SMTP relay
│   │   ├── analytics.py *      # Page view tracking
│   │   └── tokens.py *         # Review token generation
│   ├── templates/
│   │   ├── base.html           # Layout, nav, footer, dark/light toggle
│   │   ├── public/
│   │   │   ├── index.html      # Landing page (hero section only)
│   │   │   ├── portfolio.html *
│   │   │   ├── case_study.html *
│   │   │   ├── services.html *
│   │   │   ├── skills.html *
│   │   │   ├── projects.html *
│   │   │   ├── project.html *
│   │   │   ├── testimonials.html *
│   │   │   ├── certifications.html *
│   │   │   ├── contact.html *
│   │   │   └── resume.html *
│   │   ├── admin/
│   │   │   ├── dashboard.html  # Basic admin dashboard shell
│   │   │   ├── login.html      # Admin login form
│   │   │   ├── content.html *
│   │   │   ├── photos.html *
│   │   │   ├── reviews.html *
│   │   │   ├── tokens.html *
│   │   │   └── settings.html *
│   │   └── review/
│   │       └── submit.html *
│   └── static/
│       ├── css/
│       │   └── style.css       # Custom properties, dark/light themes
│       └── js/
│           ├── main.js         # Dark/light toggle, basic interactions
│           └── admin.js *      # Admin panel interactions
└── tests/
    ├── conftest.py             # Pytest fixtures
    └── test_app.py             # Basic smoke tests
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
