# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.2.0

### Added — Phase 5: Architecture Hardening
- `app/db.py` — single source of truth for database connection management (consolidated from `__init__.py` and `models.py`)
- Database migration system: `migrations/` directory with numbered SQL files, `schema_version` tracking table, auto-detection of existing v0.1.0 databases
- `manage.py migrate` command with `--status` and `--dry-run` flags
- `manage.py config` command — validates config.yaml structure, warns on typos and misplaced settings
- `config.schema.json` — formal JSON Schema specification for config.yaml
- Environment variable overrides for all config values (`RESUME_SITE_SECRET_KEY`, `RESUME_SITE_DATABASE_PATH`, `RESUME_SITE_SMTP_*`, etc.) — 12-factor app support
- Service layer: `app/services/content.py`, `reviews.py`, `stats.py`, `service_items.py`, `settings_svc.py` — admin routes are now thin controllers
- Settings registry with type validation and key whitelisting

### Added — Phase 6: Security Hardening
- CSRF protection on all POST forms via Flask-WTF (`CSRFProtect`)
- CSRF tokens auto-injected into admin templates and manually added to public forms
- Security response headers on every response: `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`
- `Cache-Control: no-store` on all admin pages
- HTML sanitization via `nh3` on all content block writes (allowlisted tags only)
- File upload hardening: magic byte validation, configurable size limits (`max_upload_size`), null byte filename rejection
- Admin session timeout with configurable inactivity period (`session_timeout_minutes`, default 60 min)
- Startup validation: warns on weak/placeholder secret keys and keys shorter than 32 characters
- `manage.py generate-secret` command for cryptographically secure key generation
- `smtp.password_file` support for Docker/Podman secrets integration

### Added — Phase 7: Expanded Test Suite
- `tests/test_admin.py` — 47 tests covering all admin CRUD operations, auth, and IP restriction
- `tests/test_security.py` — 21 tests covering CSRF enforcement, security headers, HTML sanitization
- `tests/test_migrations.py` — 13 tests covering migration system (fresh DB, v0.1.0 detection, bad SQL, status output)
- `tests/test_integration.py` — 9 end-to-end tests (full review flow, contact flow, settings reflection, sitemap, file upload validation, session timeout)
- Test fixtures: `auth_client` (pre-authenticated admin), `populated_db` (sample content), `csrf_app` (CSRF-enabled)
- Total: 121 tests, all passing

### Infrastructure
- Multi-stage Containerfile with non-root user, health check, and OCI labels
- `compose.yaml` for Podman/Docker Compose deployment
- `resume-site.container` Podman Quadlet unit file for systemd integration
- `.containerignore` to minimize container image size
- GitHub Actions CI pipeline with GHCR publishing (multi-arch amd64+arm64)
- `ROADMAP_v0.2.0.md` development plan
- Updated `SECURITY.md` with v0.2.0 hardening commitments

### Planned
- Blog / articles engine with tags, RSS, markdown support
- Admin panel customization (theme editor, nav ordering, custom CSS, settings registry)
- i18n framework (Flask-Babel, string extraction, locale routing)
- Container registry publishing via `podman pull ghcr.io/kit3713/resume-site`

---

## [0.1.0] — 2026-04-11

Initial release. Feature-complete portfolio engine with admin panel.

### Added — Phase 4: Polish
- Open Graph meta tags (`og:title`, `og:description`, `og:type`, `og:site_name`) in base template
- Auto-generated `sitemap.xml` route built from active pages
- `robots.txt` route
- CLI command: `generate-token` for creating review invite tokens
- CLI command: `list-reviews` for viewing pending/approved/all reviews
- CLI command: `purge-analytics` for deleting page views older than N days

### Added — Phase 3: Admin Panel
- Full admin panel with sidebar navigation (`base_admin.html`)
- Admin dashboard with analytics overview (page views, popular pages, recent submissions)
- Content block manager: list all content blocks, rich text editor for each
- Photo manager: upload with Pillow auto-optimization (2000px max, quality optimization for JPEG/PNG/WebP), assign categories/metadata/display tiers, delete with file cleanup
- Review manager: view all submissions, approve/reject, set display tier (featured/standard/hidden)
- Token manager: generate invite tokens, view active/used tokens, delete tokens
- Settings panel: site title, tagline, availability status, contact visibility toggles, resume visibility, dark/light mode default, testimonial display mode, stats and case study toggles
- Services manager: add/edit/delete service cards with descriptions and icons
- Stats manager: add/edit/delete stat counters with labels and values

### Added — Phase 2: Public Pages & Services
- Public page routes: portfolio, case study detail, services, testimonials, projects, project detail, certifications, resume PDF download, contact, and review submission
- Contact form blueprint with honeypot spam protection, rate limiting, and SMTP relay
- Token-based review submission system (validate token, submit review, mark token used)
- SMTP mail service for contact form relay
- Analytics middleware logging page views to SQLite (path, referrer, user agent, IP)
- Photo file serving from configurable storage directory
- Token validation service (checks expiry, used status)
- Database models and queries for all content types: settings, content blocks, stats, services, skill domains, photos, reviews, projects, certifications, contact submissions, review tokens
- GSAP scroll animations: hero entrance, section fade/slide reveals, staggered card animations, animated stat counters, page header reveals
- Full public templates: landing page (hero, about, stats, services preview, featured portfolio, featured testimonials, contact CTA), portfolio gallery with category filtering, case study detail, services with expandable skill cards, projects list and detail, testimonials with featured/standard tiers, certifications with badge images, contact form, review submission form
- Expanded test suite covering all public page routes, contact form, review token flow, analytics

### Added — GitHub Community Files
- MIT LICENSE file
- GitHub Actions CI workflow (pytest + flake8 on Python 3.11/3.12 with container build)
- Issue templates (bug report, feature request)
- Pull request template with checklist
- CONTRIBUTING.md
- SECURITY.md
- .editorconfig

### Added — Phase 1: Foundation
- Flask app factory with Gunicorn entrypoint
- YAML-based infrastructure configuration (`config.example.yaml`)
- SQLite database schema with tables for content, reviews, analytics, photos, settings, and tokens
- Base HTML template with fixed navbar, footer, and dark/light mode toggle
- CSS custom properties for full dark and light theming
- Landing page with hero section (split layout)
- Containerfile for OCI-compliant image builds
- Admin route IP restriction middleware (private networks + Tailscale)
- Admin login/logout with Flask-Login (single user, hashed password)
- Admin dashboard shell
- CLI tools: `init-db`, `hash-password` (via `manage.py`)
- Basic test suite covering routes, auth, IP restriction, static assets
- `.gitignore` for config, database, photos, and Python artifacts
