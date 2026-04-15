# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.3.0

### Added — Phase 16.5: OpenAPI 3.0 Documentation
- `docs/openapi.yaml` — hand-authored OpenAPI 3.0 specification covering every `/api/v1/*` endpoint (34 operations across 27 paths). Includes a Bearer-auth security scheme, reusable schemas/responses/parameters, an in-spec error code catalog, and a pagination guide.
- `GET /api/v1/openapi.yaml` and `GET /api/v1/openapi.json` — serve the spec with strong ETag + `If-None-Match` 304 handling. Bytes and parsed dict are cached in module scope.
- `GET /api/v1/docs` — interactive Swagger UI (CDN-pinned `swagger-ui-dist@5.17.14`). Standalone template + external init script (`app/static/js/swagger-init.js`) so the page stays CSP-clean for the future enforce-mode promotion.
- `api_docs_enabled` setting (default `false`, Security category). When off, all three routes return `404 NOT_FOUND` to avoid revealing the feature exists — matches the `/metrics` and disabled-blog patterns.
- `tests/test_openapi_spec.py` — 18 tests including a **drift guard** that asserts the set of `(method, path)` pairs in the spec matches the live Flask URL map exactly, plus operationId hygiene, response coverage rules (401/403/404/415/429), `$ref` resolution, and an error-code catalog cross-check against the literals raised in `app/routes/api.py`.
- `tests/test_api.py` — 8 endpoint tests covering 404-when-disabled for all three routes, YAML/JSON serving, ETag round-trip, Swagger UI render contract, and a CSP forward-compat check that verifies the docs page has no inline script bodies.

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

### Added — Phase 5 (continued)
- `seeds/defaults.sql` — seed data separated from schema migrations (INSERT OR IGNORE for all default settings)
- `requirements.in` — unpinned dependency source file for pip-compile

### Added — Phase 6: Security Hardening
- CSRF protection on all POST forms via Flask-WTF (`CSRFProtect`)
- CSRF tokens auto-injected into admin templates and manually added to public forms
- Security response headers on every response: `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`
- `Content-Security-Policy-Report-Only` header allowing GSAP CDN, Google Fonts, Quill.js, and inline styles
- `Cache-Control: no-store` on all admin pages
- `Cache-Control: public, max-age=2592000, immutable` on static assets
- HTML sanitization via `nh3` on all content block writes (allowlisted tags only)
- File upload hardening: magic byte validation, configurable size limits (`max_upload_size`), null byte filename rejection
- Rate limiting via Flask-Limiter on all public POST endpoints: contact (10/min), review (5/min), admin login (5/min)
- Admin session timeout with configurable inactivity period (`session_timeout_minutes`, default 60 min)
- Startup validation: warns on weak/placeholder secret keys and keys shorter than 32 characters
- Startup validation: warns on unrecognized password hash algorithms
- `manage.py generate-secret` command for cryptographically secure key generation
- `smtp.password_file` support for Docker/Podman secrets integration
- CI check for unsafe SQL string interpolation patterns (parameterized queries audit)
- Dependencies pinned with hashes via `pip-compile --generate-hashes`
- `pip-audit` dependency vulnerability scanning in CI pipeline
- Supply chain documentation: dependency table in README

### Added — Phase 7: Expanded Test Suite
- `tests/test_admin.py` — 50 tests covering all admin CRUD operations, auth, IP restriction, and photo metadata/tier/delete
- `tests/test_security.py` — 26 tests covering CSRF enforcement, security headers, HTML sanitization, CSP, Cache-Control, file upload size limits, rate limiting (429)
- `tests/test_migrations.py` — 14 tests covering migration system (fresh DB, v0.1.0 detection, bad SQL, dry-run, status output)
- `tests/test_integration.py` — 9 end-to-end tests (full review flow, contact flow, settings reflection, sitemap, file upload validation, session timeout)
- Test fixtures: `auth_client` (pre-authenticated admin), `populated_db` (sample content), `csrf_app` (CSRF-enabled), `smtp_mock` (captures sent emails)
- Total: 199 tests, all passing

### Infrastructure
- Multi-stage Containerfile with non-root user, health check, and OCI labels
- `compose.yaml` for Podman/Docker Compose deployment
- `resume-site.container` Podman Quadlet unit file for systemd integration
- `.containerignore` to minimize container image size
- GitHub Actions CI pipeline with GHCR publishing (multi-arch amd64+arm64)
- `ROADMAP_v0.2.0.md` development plan
- Updated `SECURITY.md` with v0.2.0 hardening commitments

### Added — Phase 8: Blog Engine
- Full blog system with admin CRUD: create, edit, publish, unpublish, archive, delete posts
- Automatic slug generation from titles with numeric suffix for uniqueness
- Tag system with comma-separated input, junction table, and tag-based filtering (`/blog/tag/<slug>`)
- RSS 2.0 feed at `/blog/feed.xml` (respects `enable_rss` setting, excludes drafts)
- Reading time calculation (words/200, ceiling, HTML tags stripped)
- Paginated blog index and tag pages (configurable `posts_per_page`)
- Previous/next post navigation on individual post pages
- Quill.js rich text editor with code block support in admin
- Cover image, author, meta description fields per post
- Blog feature toggle: `blog_enabled` setting controls public routes and nav link visibility
- Admin status filter tabs (all/draft/published/archived)
- Blog settings in settings registry: `blog_title`, `posts_per_page`, `show_reading_time`, `enable_rss`
- Blog pages included in `sitemap.xml` when enabled
- Open Graph article meta tags on individual post pages
- `migrations/002_blog_tables.sql` — blog_posts, blog_tags, blog_post_tags tables with indexes
- Markdown rendering for blog posts via mistune (when content_format='markdown')
- Featured blog posts section on landing page (shown when blog is enabled and posts are marked featured)
- `app/services/blog.py` — service layer for all blog operations with HTML sanitization on write
- `tests/test_blog.py` — 31 tests covering admin CRUD, slug generation, public visibility, tag filtering, RSS feed, reading time, blog toggle, pagination boundaries, markdown rendering
- Total test suite: 149 tests, all passing

### Added — Phase 9: Admin Panel Customization
- Custom CSS injection: textarea in admin settings, contents rendered as `<style>` block on all public pages
- Accent color picker with live swatch preview and hex display in settings
- Font pairing selector: 5 curated pairings (Inter, Space Grotesk, Plus Jakarta Sans, DM Sans, Outfit) with dynamic Google Fonts loading
- Color scheme presets: 6 presets (Blue, Ocean, Forest, Sunset, Minimal, Royal) with quick-select buttons
- Nav item visibility toggles: individually hide/show About, Services, Portfolio, Projects, Testimonials, Contact from the navbar
- Activity log: `admin_activity_log` table recording admin actions with timestamps, displayed on dashboard
- Activity logging on settings save, photo upload, review updates, blog post create/publish/delete
- Settings registry enhanced with full metadata: type, default, label, category, options, description
- Settings page auto-rendered from registry with category grouping (Site Identity, Appearance, Navigation, Blog, Contact & Social)
- Setting widget types: text, textarea, boolean (select), color (picker), select (dropdown), number
- `migrations/003_admin_customization.sql` — activity log table and new setting seeds
- `app/services/activity_log.py` — log_action, get_recent_activity, purge_old_entries
- `tests/test_customization.py` — 25 tests covering settings registry, custom CSS, fonts, colors, nav visibility, activity log
- Total test suite: 171 tests (169 passing, 2 pre-existing RSS feed tests pending implementation)

### Added — Phase 10: Internationalization (i18n)
- Flask-Babel integration with session-based locale persistence and Accept-Language negotiation
- `babel.cfg` extraction configuration for Python and Jinja2 template string scanning
- `manage.py translations` CLI: `extract`, `init --locale <code>`, `compile`, `update` subcommands wrapping pybabel
- All public template strings marked with `{{ _('...') }}` (nav, headings, buttons, form labels, empty states, pagination)
- All admin template strings marked with `{{ _('...') }}` (sidebar, dashboard, CRUD forms, blog editor, settings)
- All route flash messages wrapped with `_()` for translation (admin, contact, review, blog)
- Language switcher in navbar (only visible when multiple locales are configured)
- `hreflang` SEO tags in `<head>` (only rendered when multiple locales are available)
- `/set-locale/<lang>` endpoint for language switching with session persistence
- Admin settings: `default_locale` and `available_locales` in settings registry (Internationalization category)
- `migrations/004_i18n.sql` — seeds default locale settings
- English translation catalog extracted (220 strings), compiled, and ready as reference for contributors
- `tests/test_i18n.py` — 16 tests covering locale switching, session persistence, Accept-Language negotiation, hreflang tags, language switcher visibility, settings registry, translation files
- Total test suite: 187 tests (185 passing, 2 pre-existing RSS feed tests pending implementation)

### Added — Phase 11: Container-Native Deployment
- Multi-stage Containerfile: builder stage for compilation, minimal runtime image with non-root user (UID 1000)
- Dedicated `/healthz` endpoint for container health checks (lightweight JSON, no DB/template rendering)
- OCI labels: source, version, description, license, authors
- `.containerignore` excluding tests, docs, .git, config, data, and photos from image
- GitHub Actions CI: tests on Python 3.11/3.12, container build verification with smoke test, GHCR multi-arch publishing (amd64+arm64)
- GHCR publishing: `:latest` and `:<version>` on tag push, rolling `:main` on merge to main
- `compose.yaml` with Caddy reverse proxy sidecar (commented-out, ready to enable)
- `resume-site.container` Podman Quadlet unit file for systemd integration with auto-update
- README: 4 deployment options (container pull, compose, Quadlet, local dev), backup/upgrade docs, CLI reference
- Translations directory included in container image for i18n support

### Fixed
- RSS feed tests: `_enable_blog` helper now preserves `enable_rss` setting (was being reset to false by settings save_many checkbox behavior)

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
