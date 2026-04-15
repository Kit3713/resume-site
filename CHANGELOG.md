# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.3.0

### Added — Phase 19.2 (admin surface): Webhook Management UI + REST API
- `/admin/webhooks` — operator-facing list / create / edit / delete / test page. Each row shows name, URL, subscribed events (as tags), enabled/disabled badge, consecutive failure count, and last-delivery timestamp. The cross-webhook "Recent Deliveries" panel lists the last 20 attempts with status-coded chips. Inline `<details>` editor lets operators rotate the secret, change the event subscription, toggle enabled, and reset the failure counter without leaving the list view.
- `/admin/webhooks/<id>/deliveries` — per-webhook attempt log (last 100, newest first) with event, status code, latency, error message, and timestamp.
- Admin sidebar link added under the existing "API Tokens" entry so the new surface is discoverable.
- Synchronous `Test` button on every row fires a `webhook.test` event with a small JSON payload through `deliver_now`, records the attempt in `webhook_deliveries`, and bumps `failure_count` (or resets it on 2xx) under the same auto-disable contract as the bus dispatcher. The result surfaces as an inline flash so the operator sees the HTTP status and latency without polling the deliveries log.
- `/api/v1/admin/webhooks` (Bearer `admin` scope) — full CRUD over the `webhooks` table:
  - `GET /admin/webhooks` — list every subscription. Secrets are intentionally OMITTED from the response payload.
  - `POST /admin/webhooks` — create a new subscription. Body accepts `name`, `url`, optional `secret` (auto-generated 32-byte URL-safe value when omitted), `events` (list or comma-separated string; defaults to `["*"]`), and `enabled` (default true). The response payload is the standard webhook record PLUS the `secret` field echoed exactly once — the only endpoint that ever returns it.
  - `GET /admin/webhooks/<id>` — fetch one row (no secret).
  - `PUT /admin/webhooks/<id>` — partial update. All fields optional; `reset_failures: true` zeros the consecutive-failure counter for manual recovery after fixing a downstream. The rotated secret, if supplied, is NOT echoed back.
  - `DELETE /admin/webhooks/<id>` — hard delete (cascades the delivery log).
  - `POST /admin/webhooks/<id>/test` — fires a synchronous test delivery; returns `{ok, status_code, response_time_ms, error}` so a CLI can trigger a verifier from a deploy script.
  - `GET /admin/webhooks/<id>/deliveries?limit=N` — per-webhook delivery log, newest first (limit clamped to [1, 500], default 50).
- OpenAPI 3.0 spec extended (`docs/openapi.yaml`) with five new `/admin/webhooks*` operations and four schemas: `Webhook`, `WebhookCreate`, `WebhookUpdate`, `WebhookCreateResult` (the create-only secret echo), `WebhookTestResult`, `WebhookDelivery`. The Phase 16.5 drift guard catches any future divergence between the spec and the live URL map.
- 42 new tests in `tests/test_webhooks_admin.py` (779 → 821 total): admin auth + IP gates (7 routes × auth + IP), CRUD round-trip and validation (URL scheme rejection, missing name, default `["*"]` events), partial update (secret-keep-when-blank, rotate, reset_failures, 404 paths), the synchronous `/test` button success + `URLError` failure paths (with `failure_count` increment), per-webhook delivery log rendering, REST API auth gating (admin scope required, write scope rejected), one-time secret echo contract (verified by asserting the secret bytes never appear in any GET response), CSV/list `events` coercion, partial PUT semantics, ETag/If-None-Match contract on the list endpoint, and admin activity log instrumentation.

### Added — Phase 19.2 (foundation): Webhook Dispatch Subsystem
- `migrations/009_webhooks.sql` — `webhooks` (id, name, url, secret, events JSON, enabled, failure_count, created_at, last_triggered_at) and `webhook_deliveries` (per-attempt log; cascades on webhook delete) tables, with indexes on `webhooks(enabled)` and `webhook_deliveries(webhook_id, created_at DESC)`.
- `app/services/webhooks.py` — full dispatch subsystem (~600 lines). Stdlib only (`urllib.request` + `hmac` + `hashlib` + `threading` + `sqlite3`); zero new runtime deps.
  - `Webhook` / `DeliveryResult` namedtuples.
  - CRUD: `create_webhook`, `get_webhook`, `list_webhooks`, `list_enabled_subscribers`, `update_webhook`, `delete_webhook`.
  - Delivery log: `record_delivery`, `list_recent_deliveries`, `purge_old_deliveries(keep_days=30)`.
  - Auto-disable: `increment_failures` flips `enabled=0` once consecutive failures cross the configured threshold; `reset_failures` zeros the counter on the next 2xx. `threshold=0` opts out entirely.
  - Signing: `sign_payload(secret, body)` — HMAC-SHA256 hex digest, accepts str or bytes.
  - Sync delivery: `deliver_now(webhook, event_name, payload, *, timeout=5)` — POSTs a `{event, timestamp, data}` envelope (sorted JSON for stable signatures), captures HTTP errors / network errors / timeouts in the returned `DeliveryResult` rather than raising.
  - Async fan-out: `dispatch_event_async(db_path, event_name, payload, ...)` spawns one daemon `threading.Thread` per matching enabled subscriber. Each worker opens a fresh sqlite3 connection (Flask's request-scoped one lives on the wrong thread).
  - Bus integration: `register_bus_handlers(db_path)` registers one closure per `Events.*` constant. Idempotent — re-registering the same db_path drops previous closures first so the test suite stays clean across the autouse `clear()` fixture.
- `webhooks_enabled` (default `false`), `webhook_timeout_seconds` (default `5`, clamped to [1, 60]), and `webhook_failure_threshold` (default `10`, `0` disables auto-disable) added to `SETTINGS_REGISTRY` in a new "Webhooks" category. Master toggle is read at dispatch time so admin edits propagate within the 30 s settings cache TTL.
- App factory wires `register_bus_handlers(app.config['DATABASE_PATH'])` at startup so every existing emission (Phase 19.1) automatically fans out to enabled webhooks once the master toggle is on.
- 36 new tests in `tests/test_webhooks.py` (743 → 779 total): HMAC signing (4), CRUD + normalisation (10), delivery log truncation + purge (3), auto-disable thresholds (3), `deliver_now` happy + HTTPError + URLError + Timeout + sorted-envelope (5), async fan-out + worker daemon contract + cross-thread DB writes (6), bus integration short-circuit + dispatch + every-event coverage + bad-settings fallback (4), and the lookup-failure fail-open contract (1).

### Added — Phase 19.1 (completion): Event Bus Emissions From HTML / Admin Routes
- `contact.submitted` now fires from the public HTML form (`app/routes/contact.py`) with `source='public_form'`. Mirrors the API-side emission so a webhook subscriber sees the same shape regardless of submission origin. Honeypot-flagged submissions still fire (with `is_spam: true`) so abuse dashboards stay accurate.
- `review.submitted` now fires from the token URL (`app/routes/review.py`) with `source='public_token'` and the inherited review type / rating-presence flag.
- `review.approved` now fires from the admin UI (`app/routes/admin.py:reviews_update`) on the approve action only — reject / update_tier remain admin housekeeping.
- `blog.published` / `blog.updated` now fire from `app/routes/blog_admin.py` on every new/edit/delete path (publish → published, save / unpublish / archive / delete → updated). Payloads built via a new `_blog_event_payload` helper so all five paths emit the same shape. `blog.updated` carries `status='deleted'` when the row is removed (mirrors `api.blog_delete`).
- `photo.uploaded` now fires from `app/routes/admin.py:photos_upload` after the row commits, mirroring `api.portfolio_create`.
- `settings.changed` now fires from `app/routes/admin.py:settings` with `keys` = the sorted submitted form keys (csrf_token excluded so subscribers see no noise).
- `security.rate_limited` now fires from a new `errorhandler(429)` in `app/__init__.py`. Observability-only — re-raises so Flask's default 429 response (and Flask-Limiter's `Retry-After` header) is unchanged. Payload carries `request_id`, `ip_hash`, `method`, `endpoint` (the URL rule template, not the rendered path, so cardinality stays bounded), and the `limit` description from the exception.
- 12 new integration tests in `tests/test_events.py` (21 → 33) covering every new emission path: legitimate + honeypot contact submissions, review submission with token inheritance, admin approve-vs-reject distinction, blog publish-vs-save-vs-delete, photo upload (real PNG via Pillow), settings save with csrf_token exclusion, and 429 emission with body / status untouched.

### Added — Phase 17.2: Scheduled Backups (Container-Native)
- `resume-site-backup.service` + `resume-site-backup.timer` — systemd units that wrap `podman exec resume-site python manage.py backup --prune --keep 7` on a daily schedule (02:00 with 30-min jitter, `Persistent=true` so missed windows still run on next boot). `RESUME_SITE_KEEP` overridable via `systemctl edit` without forking the unit files.
- `resume-site-backups` named volume in `compose.yaml` and the Quadlet (`resume-site.container`), mounted at `/app/backups`. The container env carries `RESUME_SITE_BACKUP_DIR=/app/backups` so the CLI writes archives onto the volume by default.
- Admin dashboard "Last Backup" card showing the most recent `backup_last_success` timestamp (rendered via the new `time_ago` Jinja filter), archive count, and total size. A "Recent Backups" table lists the five newest archives with size + relative mtime.
- `app/services/time_helpers.py` — stdlib-only `time_ago(value, *, now=None)` accepts ISO-8601 strings, `datetime` objects, and Unix epoch numbers; renders "5 minutes ago" / "yesterday" / "in 2 hours" / "never". Registered as the `time_ago` Jinja filter at app startup.
- README "Backup" section rewritten: covers the CLI, the systemd timer install (rootless + system-wide), an `OnCalendar`/retention override recipe, compose-only cron alternative, restore procedure, offsite mirroring example (rclone), and per-archive gpg encryption via an `ExecStartPost=` drop-in.
- 27 new tests: `tests/test_time_helpers.py` (25 — bucket boundaries, future intervals, every input shape, Jinja registration) and 2 dashboard widget tests in `tests/test_admin.py` (404-state "never" rendering and populated-state archive listing).

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
