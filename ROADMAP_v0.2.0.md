# resume-site v0.2.0 Roadmap

> **Codename:** Platform  
> **Status:** Complete (All phases 5-11 done)  
> **Baseline:** v0.1.0 (Phases 1-4 complete -- single-user portfolio engine)  
> **Target:** A hardened, extensible, container-native portfolio and blog platform

---

## Release Goals

v0.1.0 proved the concept: a self-hosted portfolio engine with admin content management. v0.2.0 makes three commitments:

1. **The codebase becomes a foundation, not a prototype.** Architecture cleanup, migration system, security hardening, and a real test suite — so that every future feature lands cleanly instead of accumulating debt.
2. **The platform gets richer.** Blog engine, deeper admin customization, i18n framework.
3. **Deployment becomes turnkey.** `podman pull` from a container registry, run, done.

Every feature ships behind a toggle or is fully backward-compatible with v0.1.x data.

---

## Phase 5 — Architecture Hardening

*Do this first. Everything else builds on it.*

### 5.1 — Consolidate `get_db()`

**Problem:** `get_db()` is defined in both `app/__init__.py` and `app/models.py` with separate implementations. As services multiply this becomes a source of subtle bugs.

- [x] Create `app/db.py` as the single source of truth for database connection management
- [x] `get_db()`, `close_db()`, and `init_db()` all live here
- [x] `app/__init__.py` imports from `app/db.py` and registers teardown
- [x] `app/models.py` imports from `app/db.py` — remove its local `get_db()`
- [x] All route files and services import from `app/db.py`
- [x] Verify no circular imports

### 5.2 — Database Migration System

**Problem:** `schema.sql` is monolithic. Adding blog tables, user accounts, translation columns, or altering existing tables requires a way to upgrade live databases without data loss.

- [x] Create `migrations/` directory with numbered SQL files: `001_baseline.sql`, `002_blog_tables.sql`, etc.
- [x] Add `schema_version` table tracking applied migrations
- [x] `manage.py migrate` — applies all pending migrations in order, wraps each in a transaction
- [x] `manage.py migrate --status` — shows which migrations are applied and which are pending
- [x] `manage.py migrate --dry-run` — prints SQL without executing
- [x] Baseline migration (`001`) reproduces current `schema.sql` exactly so existing databases register as up-to-date
- [x] Separate seed data from schema: `seeds/defaults.sql` for the `INSERT OR IGNORE` settings block
- [x] `manage.py init-db` still works but now calls migrate internally
- [x] Document migration authoring in `CONTRIBUTING.md`

### 5.3 — Configuration Boundary

**Problem:** `config.yaml` and the `settings` table share conceptual space with no enforced contract. New features (default language, API keys, blog config, container registry URL) will make the overlap worse.

- [x] Enforce strict boundary: `config.yaml` = infrastructure and secrets only (secret_key, database_path, photo_storage, SMTP, admin credentials, allowed_networks). Nothing that the admin UI controls.
- [x] `settings` table = everything the admin panel manages. All display, content, toggle, and appearance settings.
- [x] Add `manage.py config validate` — checks config.yaml against a JSON schema, reports missing required fields and unknown keys
- [x] Environment variable overrides for all config.yaml values (12-factor): `RESUME_SITE_SECRET_KEY`, `RESUME_SITE_DATABASE_PATH`, `RESUME_SITE_SMTP_HOST`, etc.
- [x] Precedence order: env vars > config.yaml > defaults
- [x] Log a deprecation warning at startup if any settings-layer value appears in config.yaml
- [x] Create `config.schema.json` as the formal spec

### 5.4 — Service Layer Refactor

**Problem:** Admin routes contain raw SQL inline. As the admin panel grows (blog management, user management, theme editing), this becomes unmaintainable and untestable.

- [x] Create `app/services/content.py` — CRUD for content blocks
- [x] Create `app/services/reviews.py` — review lifecycle (approve, reject, update tier)
- [x] Create `app/services/stats.py` — stats CRUD
- [x] Create `app/services/service_items.py` — services CRUD (named to avoid package collision)
- [x] Create `app/services/settings_svc.py` — wraps get/set with validation and registry
- [x] Admin routes become thin controllers: validate input, call service, flash result, redirect
- [x] Models stay as query functions (reads); services handle writes with validation
- [x] Each service is independently testable without Flask request context

---

## Phase 6 — Security Hardening

*Run parallel with or immediately after Phase 5. Non-negotiable before any public-facing auth work.*

### 6.1 — CSRF Protection

**Problem:** No CSRF tokens on any POST form. The admin is IP-restricted so risk is low today, but the contact form and review form are public, and multi-user auth in v0.3.0 requires this.

- [x] Add `Flask-WTF` or implement manual CSRF tokens (hidden field + session validation)
- [x] Every `<form method="POST">` gets a CSRF token — admin, contact, review
- [x] CSRF validation middleware on all POST/PUT/DELETE routes
- [x] AJAX-friendly: support CSRF token in `X-CSRFToken` header for future API/JS work
- [x] Tests verify that POST without valid token returns 400

### 6.2 — Security Headers

- [x] Add `after_request` handler setting security headers on all responses:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `X-XSS-Protection: 0` (modern best practice — rely on CSP instead)
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains` (when behind HTTPS)
- [x] Content Security Policy header — report-only policy allowing GSAP CDN, Google Fonts, and Quill.js
- [x] `Cache-Control` on static assets (long cache with `max-age=2592000, immutable`)
- [x] `Cache-Control: no-store` on admin pages

### 6.3 — Input Validation & Sanitization

- [x] Sanitize all HTML content from Quill editor before storage (allowlist safe tags: `<p>`, `<strong>`, `<em>`, `<a>`, `<ul>`, `<ol>`, `<li>`, `<h1>`–`<h6>`, `<blockquote>`, `<code>`, `<pre>`, `<img>`) — use `bleach` or `nh3`
- [x] Validate file uploads: check magic bytes not just extension, enforce max file size (configurable, default 10MB), reject files with null bytes in name
- [x] Rate limiting on all public POST endpoints via Flask-Limiter (contact, review, admin login)
- [x] Admin session timeout (configurable, default 60 minutes of inactivity)
- [x] Parameterized queries audit — CI check for unsafe SQL string interpolation patterns

### 6.4 — Secrets Management

- [x] `manage.py generate-secret` — generates a cryptographically random secret key, writes to config.yaml or prints for manual insertion
- [x] Warn at startup if `secret_key` is the example value or shorter than 32 bytes
- [x] Admin password hash: startup validation warns on unrecognized or weak hash algorithms
- [x] Support reading SMTP password from a file path (`smtp.password_file`) for Docker/Podman secrets integration

### 6.5 — Dependency Security

- [x] Pin all dependencies with hashes in `requirements.txt` (via `pip-compile --generate-hashes`)
- [x] Add `pip-audit` check to CI pipeline
- [x] Minimal container image: `python:3.12-slim`, non-root user (UID 1000), curl-only runtime dep
- [x] Document the supply chain: dependency table in README with purpose for each package

---

## Phase 7 — Expanded Test Suite

*Build incrementally alongside Phases 5–6. Every new feature in Phases 8–10 ships with tests.*

### 7.1 — Test Infrastructure

- [x] Add `pytest-cov` for coverage reporting
- [x] Add coverage threshold to CI (`--cov-fail-under=60`, ratchet up over time)
- [x] Create test fixtures for authenticated admin sessions (login helper)
- [x] Create test fixtures for populated database (sample content blocks, photos, services, reviews)
- [x] Create test fixture for SMTP mock (verify emails sent without real relay)
- [x] Separate test files by domain: `test_app.py`, `test_admin.py`, `test_security.py`, `test_migrations.py`, `test_integration.py`

### 7.2 — Admin CRUD Tests

- [x] Content block create, read, update
- [x] Photo upload (valid file, invalid file, wrong extension)
- [x] Photo metadata edit, tier change, deletion (verify file cleanup)
- [x] Service add, edit, delete, visibility toggle
- [x] Stat add, edit, delete, visibility toggle
- [x] Review approve, reject, tier change
- [x] Token generate, delete
- [x] Settings save and verify persistence
- [x] All admin routes return 302 to login when unauthenticated
- [x] All admin routes return 403 from disallowed IP

### 7.3 — Security Tests

- [x] CSRF: POST without token → 400
- [x] CSRF: POST with valid token → succeeds
- [x] File upload: executable disguised as image → rejected
- [x] File upload: file exceeding size limit → rejected
- [x] HTML injection in content block → sanitized on save
- [x] XSS payload in contact form fields → escaped in admin dashboard display
- [x] Rate limiting: exceed threshold → 429
- [x] Session timeout: stale session → redirected to login
- [x] Security headers present on all responses
- [x] Admin login brute force: repeated failures → rate limited (5/min via Flask-Limiter)

### 7.4 — Migration Tests

- [x] Fresh database: all migrations apply cleanly
- [x] Existing v0.1.0 database: baseline detects as applied, subsequent migrations run
- [x] Migration with bad SQL: transaction rolls back, database unchanged
- [x] `--dry-run` produces output but no changes
- [x] `--status` accurately reports applied vs pending

### 7.5 — Integration Tests

- [x] Full review flow: generate token → visit link → submit review → admin approves → appears on testimonials page
- [x] Full contact flow: submit form → saved to DB → appears in admin dashboard
- [x] Photo upload → validated (magic bytes, null bytes, valid files accepted)
- [x] Settings changes reflect immediately in public templates
- [ ] Dark/light mode toggle persists via localStorage (requires browser-based testing framework)
- [x] Sitemap includes all active pages, excludes hidden content

---

## Phase 8 — Blog / Articles Engine

*The most self-contained new feature. Ships with full admin management and public display.*

### 8.1 — Data Model

- [x] Migration: `blog_posts` table (002_blog_tables.sql)
- [x] Migration: `blog_tags` and `blog_post_tags` junction table
- [x] Migration: `blog_settings` seed values (posts_per_page, show_reading_time, enable_rss, blog_title)
- [x] Service functions: `get_published_posts()`, `get_post_by_slug()`, `get_posts_by_tag()`, `get_all_tags()`, `get_recent_posts(n)`, `get_featured_posts(n)`

### 8.2 — Admin: Blog Manager

- [x] Blog post list view with status filter (all / draft / published / archived)
- [x] Blog post editor: title, slug (auto-generated from title, editable), summary, content (Quill.js with code block support), cover image, tags (comma-separated), meta description
- [x] Content format field (html/markdown) — stored per post
- [x] Reading time auto-calculated on save (words / 200, rounded up)
- [x] Publish/unpublish/archive with timestamp tracking
- [x] Blog settings in admin settings registry: posts per page, reading time display toggle, RSS toggle, blog page title
- [x] Sidebar nav entry: "Blog" in admin base template

### 8.3 — Public: Blog Pages

- [x] `/blog` — paginated list of published posts, newest first. Each card shows title, summary, cover image, date, reading time, tags
- [x] `/blog/<slug>` — full post view with formatted content, author, date, reading time, tags, prev/next navigation
- [x] `/blog/tag/<tag>` — filtered post list by tag
- [x] `/blog/feed.xml` — RSS 2.0 feed of published posts (togglable in settings)
- [x] Blog link in main navbar (togglable via settings — `blog_enabled`)
- [x] Featured blog posts section on landing page (shown when blog is enabled and posts are marked featured)
- [x] Add blog pages to sitemap.xml generation
- [x] Open Graph tags per blog post (title, summary, cover image)

### 8.4 — Blog Tests

- [x] CRUD: create draft, edit, publish, unpublish, archive, delete
- [x] Slug generation and uniqueness
- [x] Draft posts not visible on public routes
- [x] Published posts visible, ordered correctly
- [x] Tag filtering returns correct posts
- [x] Pagination works at boundary (exactly N posts, N+1 posts)
- [x] RSS feed valid XML, contains only published posts
- [x] Reading time calculation accuracy
- [x] Markdown rendering via mistune (when content_format='markdown')
- [x] Blog disabled in settings → `/blog` returns 404, nav link hidden

---

## Phase 9 — Admin Panel Customization

*Expand what the admin can control without code changes. Build the subsystems so future admin features snap in.*

### 9.1 — Theme Customization

- [x] **Custom CSS injection:** textarea in admin settings → contents injected as `<style>` block after `style.css`. Allows overriding any CSS variable or adding custom rules without rebuilding
- [x] **Accent color picker:** color input with live swatch preview and hex display in settings
- [x] **Font selection:** dropdown with 5 curated font pairings (Inter, Space Grotesk, Plus Jakarta Sans, DM Sans, Outfit). Generates the Google Fonts `<link>` tag dynamically
- [x] **Color scheme presets:** 6 presets (Blue, Ocean, Forest, Sunset, Minimal, Royal). Quick-select buttons set both the preset dropdown and accent color
- [ ] **Homepage layout selector:** choose which sections appear and in what order (deferred to v0.3.0)

### 9.2 — Navigation Customization

- [ ] **Nav item ordering:** drag-and-drop or up/down arrows in admin to reorder navbar links (deferred to v0.3.0)
- [x] **Nav item visibility:** toggle individual nav items on/off via `nav_hide_*` boolean settings (About, Services, Portfolio, Projects, Testimonials, Contact)
- [ ] **Custom nav links:** add external links to the navbar (deferred to v0.3.0)

### 9.3 — Admin UI Improvements

- [ ] **Bulk operations:** select multiple photos/reviews/posts → bulk delete, bulk status change (deferred to v0.3.0)
- [ ] **Drag-and-drop reordering:** for services, stats, photos, projects (deferred to v0.3.0)
- [ ] **Image preview in editors:** thumbnail preview when uploading photos or blog cover images (deferred to v0.3.0)
- [ ] **Admin search:** search across content blocks, blog posts, reviews, and contacts (deferred to v0.3.0)
- [x] **Activity log:** `admin_activity_log` table recording admin actions with timestamps. Displayed on dashboard. Actions logged: settings save, photo upload, review updates, blog CRUD

### 9.4 — Settings Architecture for Extensibility

*This is the subsystem that makes future admin features easy to add.*

- [x] **Settings registry:** define settings in code with metadata (key, type, default, category, label, description, options). Admin settings page renders from the registry instead of hardcoded HTML
- [x] **Setting types:** text, textarea, boolean, color, select, number — each type has a corresponding form widget
- [x] **Setting categories:** grouped into sections (Site Identity, Appearance, Navigation, Blog, Contact & Social)
- [x] **Setting validation:** type-checked on save with boolean checkbox handling
- [x] Adding a new setting to any future feature = one registry entry + migration for the default value. No template changes needed in the settings page

---

## Phase 10 — Internationalization (i18n)

*Architecture first, English only. Translation files are community contributions.*

### 10.1 — Framework Setup

- [x] Add `Flask-Babel` to dependencies
- [x] Configure Babel in app factory: default locale from settings, locale selector from session or browser `Accept-Language`
- [x] `babel.cfg` extraction config for Jinja2 templates and Python strings
- [x] `manage.py translations extract` — scans codebase, generates `.pot` file
- [x] `manage.py translations init <locale>` — creates locale directory with `.po` file
- [x] `manage.py translations compile` — compiles `.po` to `.mo`

### 10.2 — Mark All Strings

- [x] Wrap all user-facing strings in templates with `{{ _('...') }}`
- [x] Wrap all flash messages in routes with `_('...')`
- [x] Wrap form labels, button text, error messages, empty states
- [x] Admin panel strings marked separately (admin UI language could differ from public site language in future)
- [x] Do NOT translate user-generated content (content blocks, blog posts, reviews) — that's a v0.4.0 concern

### 10.3 — Locale Routing

- [x] Session-based locale persistence (simplified from URL prefix strategy — no need to duplicate all URL routes)
- [x] Default locale has no prefix (clean URLs for primary language)
- [x] Language switcher component in navbar (only shows if multiple locales configured)
- [x] Admin setting: `default_locale` and `available_locales` (comma-separated)
- [x] Locale stored in session so it persists across page loads
- [x] `hreflang` tags in `<head>` for SEO

### 10.4 — Ship English, Document for Contributors

- [x] Ship with complete `en` translation file as the reference (220 strings)
- [x] `CONTRIBUTING.md` section on adding a new language
- [x] Translation files are `.po` format — standard tooling (Poedit, Weblate compatible)

---

## Phase 11 — Container-Native Deployment

*Make `podman pull` the primary deployment path.*

### 11.1 — Container Image Hardening

- [x] Multi-stage build: builder stage installs deps, final stage copies only what's needed
- [x] Run as non-root user (`USER 1000:1000`)
- [x] Read-only filesystem where possible (`--read-only` compatible with writable volumes for data/photos)
- [x] No `sudo`, no package manager cache in final image
- [x] Health check: dedicated `/healthz` endpoint — lightweight JSON response, no DB or template rendering
- [x] Labels: `org.opencontainers.image.source`, `org.opencontainers.image.version`, `org.opencontainers.image.description`
- [x] `.containerignore`: exclude tests, docs, .git, config.yaml, data/, photos/

### 11.2 — GitHub Container Registry (GHCR) Publishing

- [x] CI workflow: on tag push (`v*`), build and push to `ghcr.io/kit3713/resume-site:<version>` and `ghcr.io/kit3713/resume-site:latest`
- [x] Multi-arch build (amd64 + arm64) for broader deployment targets
- [x] CI also pushes a `:main` tag on every merge to main (rolling latest)
- [x] README install instructions: `podman pull ghcr.io/kit3713/resume-site:latest`

### 11.3 — Compose / Quadlet Support

- [x] Ship a `compose.yaml` (Podman-compatible) with the full deployment stack: resume-site container + volume mounts + port mapping
- [x] Ship a `resume-site.container` Quadlet file for systemd-managed Podman on Fedora/RHEL
- [x] Document both paths in README (Options A–C in Quick Start)
- [x] Example Caddy integration in compose (commented-out sidecar with Caddyfile instructions)

### 11.4 — Deployment Documentation

- [x] Quick start: `podman pull` → `podman run` with volume mounts → configure Caddy → access admin
- [x] `manage.py` commands that work inside the running container: `podman exec resume-site python manage.py migrate`
- [x] Backup strategy: document which files/volumes to back up (data/site.db, photos/, config.yaml)
- [x] Upgrade path: pull new image → run migrations → restart

---

## Phase Sequencing

```
Phase 5  (Architecture)  ──┐
Phase 6  (Security)      ──┤──  can run in parallel, both are foundational
Phase 7  (Tests)         ──┘──  builds incrementally alongside 5+6

Phase 8  (Blog)          ──── after 5+6+7 foundation is solid
Phase 9  (Admin Custom)  ──── after 8 (more content types to configure)
Phase 10 (i18n)          ──── after 9 (touch all templates once)
Phase 11 (Containers)    ──── after all features land (image is final)
```

Phases 5, 6, and 7 overlap heavily and should be developed together as the "hardening sprint." Phase 11 container work can start early (Containerfile improvements, CI pipeline) but final publishing waits until feature-complete.

---

## New Dependencies (v0.2.0)

| Package | Purpose | Phase |
|---------|---------|-------|
| `Flask-WTF` | CSRF protection | 6 |
| `nh3` | HTML sanitization (Rust-based, fast, safe) | 6 |
| `Flask-Limiter` | Rate limiting | 6 |
| `pip-tools` | Dependency pinning with hashes | 6 |
| `Flask-Babel` | i18n framework | 10 |
| `pytest-cov` | Coverage reporting | 7 |
| `argon2-cffi` | Password hashing upgrade (optional, replaces pbkdf2) | 6 |
| `Markdown` or `mistune` | Blog markdown rendering | 8 |

---

## Out of Scope (v0.3.0+)

These are explicitly deferred. The v0.2.0 architecture is designed to make them easy to add:

- Multiple admin / viewer accounts (activity log and settings registry prepare for this)
- Public-facing login (CSRF and session hardening prepare for this)
- API endpoints for headless usage (service layer refactor prepares for this)
- Automated scheduled backups (container volume docs prepare for this)
- Multilingual user-generated content (i18n framework prepares for this)
- Visual theme editor with live preview (custom CSS and color presets prepare for this)
- Webhook/notification system
- Plugin architecture

---

## Version Tagging

- `v0.1.0` — tag the current main branch as-is before starting v0.2.0 work
- `v0.2.0-alpha.N` — tagged as phases complete for testing
- `v0.2.0-rc.1` — feature-complete, testing and polish
- `v0.2.0` — stable release, first image published to GHCR
