# resume-site v0.3.0 Roadmap

> **Codename:** Forge  
> **Status:** Planning  
> **Baseline:** v0.2.0 (Phases 5–11 complete — hardened, extensible portfolio and blog platform)  
> **Target:** Production-grade, observable, plugin-extensible portfolio engine with API-first architecture

---

## Release Philosophy

v0.2.0 transformed the codebase from a prototype into a foundation. v0.3.0 forges that foundation into production steel. The release makes four commitments:

1. **Every line of code is audited, profiled, and optimized.** This is not a "fix what's broken" pass — it is an exhaustive review of every module, every query, every template render path, every static asset, and every container layer. The goal is a codebase where nothing is left unexamined.

2. **Security posture moves from "hardened" to "defense-in-depth."** v0.2.0 added CSRF, rate limiting, input sanitization, and security headers. v0.3.0 adds WAF-style request filtering, CSP enforcement (not just report-only), secret rotation, token-scoped API authentication, automated dependency vulnerability scanning on every commit, and a formal threat model document.

3. **The platform becomes API-first and extensible.** A full REST API (public reads + authenticated admin writes), a plugin architecture with both internal hooks and external module loading, a webhook/notification dispatch system, and a visual theme editor with live preview. Every new subsystem is designed so that v0.4.0+ features (multi-user, RBAC, SaaS mode) snap in without architectural rework.

4. **Observability-driven development becomes the methodology, not an afterthought.** Every optimization is measured before and after. Every failure mode is tested. Every deployment is monitored. This means: structured JSON logging with request correlation, a Prometheus-compatible `/metrics` endpoint, per-request performance profiling, SQLite query analysis, load testing with CI regression gates, failure mode and resilience testing, fuzz testing on every input surface, mutation testing to validate test quality, static analysis in pre-commit and CI, Grafana dashboard templates, alerting rule definitions, container health probes, automated backup tooling, and synthetic monitoring documentation. The standard is not "it works" — the standard is "we can prove it works, prove it's fast, prove it's secure, and prove it stays that way after every commit."

All new features ship behind feature flags. All changes are backward-compatible with v0.2.x data. Every phase ships with tests.

---

## Scope Summary

### Completing v0.2.0 Deferrals

These items were explicitly marked "deferred to v0.3.0" in the v0.2.0 roadmap:

| Deferred Item | v0.2.0 Source | v0.3.0 Phase |
|---|---|---|
| Homepage layout selector (section ordering) | Phase 9.1 | 14 |
| Nav item ordering (drag-and-drop) | Phase 9.2 | 14 |
| Custom nav links (external URLs in navbar) | Phase 9.2 | 14 |
| Bulk operations (multi-select delete/status) | Phase 9.3 | 14 |
| Drag-and-drop reordering (services, stats, photos, projects) | Phase 9.3 | 14 |
| Image preview in editors | Phase 9.3 | 14 |
| Admin search (cross-content) | Phase 9.3 | 14 |
| Browser-based dark/light mode test | Phase 7.5 | 18 |

### New v0.3.0 Features

| Feature | Phase |
|---|---|
| Full REST API (public + authenticated admin) | 16 |
| Automated scheduled backups | 17 |
| Multilingual user-generated content (translation junction tables) | 15 |
| Visual theme editor with live preview | 14 |
| Webhook/notification dispatch system | 19 |
| Plugin architecture (hooks + external loading) | 20 |

### Cross-Cutting Initiatives (Primary Focus)

| Initiative | Phase |
|---|---|
| Exhaustive code optimization | 12 |
| Static analysis and code quality enforcement | 12 |
| Security hardening and vulnerability patching | 13 |
| Fuzz testing and automated security scanning (DAST) | 13 |
| Structured logging, metrics, and request profiling | 18 |
| Load testing, failure mode testing, CI regression gates | 18 |
| Mutation testing for test suite quality validation | 18 |
| Alerting rules, Grafana dashboards, synthetic monitoring | 18 |
| Container hardening and deployment maturity | 21 |
| **Ship every release as a published GHCR container image** | 21 |

### Deferred to v0.4.0+

- Multiple admin / viewer accounts
- Public-facing login
- Role-based access control (RBAC)
- SaaS / multi-tenant mode
- OAuth2 / OIDC provider integration

The v0.3.0 architecture (API token auth, plugin hooks, activity log with `admin_user` field, settings registry) is designed so these land cleanly in v0.4.0.

---

## Phase 12 — Exhaustive Code Optimization

*The single largest phase. Every module is audited for performance, correctness, readability, and maintainability. This is the "leave no stone unturned" pass.*

### 12.1 — SQLite Query Optimization

**Problem:** Queries work but have never been profiled under load. No indexes beyond primary keys and unique constraints. The `page_views` table will grow unbounded on active sites. The `settings` table is read on every single request via the context processor.

- [ ] **Query audit:** Enumerate every `db.execute()` call across all models and services. Catalog query patterns, identify N+1 queries (e.g., `get_skill_domains_with_skills` runs N+1 queries), and tag hot paths (executed on every request vs. admin-only)
- [ ] **Index pass:** Add indexes based on actual query WHERE/ORDER BY patterns:
  - `page_views(path)`, `page_views(created_at)`, `page_views(ip_address)` — analytics is the heaviest table
  - `blog_posts(status, published_at)` — public listing query
  - `blog_post_tags(post_id)`, `blog_post_tags(tag_id)` — junction table joins
  - `reviews(status, display_tier)` — public testimonials query
  - `photos(display_tier, sort_order)` — portfolio gallery
  - `admin_activity_log(created_at)` — dashboard feed
  - `contact_submissions(ip_address, created_at)` — rate limit check
- [ ] **Settings cache:** The `inject_settings()` context processor runs `SELECT * FROM settings` on every request. Implement an in-process cache with a configurable TTL (default 30s) and cache-bust on admin settings save. Use a module-level dict with a timestamp — no external cache dependency
- [ ] **Batch N+1 elimination:** Rewrite `get_skill_domains_with_skills()` to use a single JOIN query instead of N+1. Audit for similar patterns in blog tag loading
- [ ] **Connection pooling evaluation:** Current per-request `sqlite3.connect()` is fine for SQLite's threading model, but document why and add a `PRAGMA` audit (verify WAL, busy_timeout, foreign_keys are set consistently)
- [ ] **EXPLAIN QUERY PLAN:** Add a `manage.py query-audit` command that runs EXPLAIN QUERY PLAN on every cataloged query and reports any full table scans on tables expected to be large (page_views, blog_posts, contact_submissions)
- [ ] **Write a migration** (`005_indexes.sql`) for all new indexes

### 12.2 — Python Code Optimization

**Problem:** The codebase is functional and well-documented but has never had a performance-focused review. Some patterns are repeated across services. Error handling is inconsistent.

- [ ] **Import audit:** Map all imports across the project. Eliminate redundant imports, consolidate common stdlib imports, ensure no circular import risk from the expanding module graph
- [ ] **Hot path profiling:** Identify the 5 most-hit routes (landing page, portfolio, blog listing, blog post, contact). For each, measure: DB query count, DB query time, template render time, total response time. Establish baseline numbers in a `PERFORMANCE.md` document
- [ ] **Template rendering:** Audit Jinja2 templates for redundant database calls (any `{{ }}` expression that triggers a query), unnecessary loops, and missing `{% cache %}` opportunities
- [ ] **String handling:** Replace any f-string SQL construction (should be none after v0.2.0 audit, but verify exhaustively). Ensure all string concatenation in hot paths uses join() or format() efficiently
- [ ] **Pillow pipeline:** Profile photo upload processing. Evaluate lazy loading, progressive JPEG output, and EXIF stripping. Ensure Pillow operations release memory promptly (explicit `image.close()` or context managers)
- [ ] **Service layer DRY pass:** Extract common patterns across services into shared utilities:
  - CRUD boilerplate (get_all, get_by_id, create, update, delete) → `app/services/base.py` mixin or helper functions
  - Slug generation (duplicated in blog.py and potentially needed by projects, case studies) → `app/utils/slugify.py`
  - Pagination logic → `app/utils/pagination.py` (reusable for blog, API, admin lists)
  - Sort-order management → shared utility for any table with `sort_order` column
- [ ] **Error handling standardization:** Define a consistent error handling pattern:
  - Services raise domain-specific exceptions (e.g., `NotFoundError`, `ValidationError`, `DuplicateSlugError`)
  - Routes catch and translate to HTTP responses (404, 400, 409)
  - Create `app/exceptions.py` with the hierarchy
  - Ensure no bare `except Exception: pass` outside of analytics tracking
- [ ] **Type hints:** Add type hints to all public function signatures across services and models. Not enforced by mypy in CI yet (that's a v0.4.0 concern), but documented for IDE support and contributor clarity
- [ ] **Docstring audit:** Verify every public function has a docstring. Standardize format (already Google-style, ensure consistency). Remove any stale docs that don't match post-v0.2.0 implementations

### 12.3 — Frontend Optimization

**Problem:** CSS is a single 2514-line file. JavaScript is two files with no minification. No asset fingerprinting for cache busting. GSAP loaded from CDN on every page.

- [ ] **CSS audit:** Profile `style.css` for unused rules, redundant declarations, and specificity conflicts. Split into logical partitions (variables/reset, layout, components, pages, dark-mode overrides, admin) using CSS `@import` or a build step. Audit CSS custom property usage — ensure no hardcoded colors bypass the theming system
- [ ] **CSS minification:** Add a build step (or Gunicorn middleware) that serves minified CSS in production. Preserve source CSS for development
- [ ] **JavaScript audit:** Profile `main.js` for unused functions, redundant event listeners, and GSAP animations that fire on hidden/off-screen elements. Audit `admin.js` for the same
- [ ] **JavaScript minification:** Same as CSS — minified in production, source in development
- [ ] **Asset fingerprinting:** Append a content hash to static asset URLs (`style.abc123.css`) so `Cache-Control: immutable` works correctly across deployments. Implement via Flask's `url_for('static', ...)` override or a manifest file
- [ ] **GSAP optimization:** Audit every ScrollTrigger registration. Ensure `kill()` is called on elements that leave the DOM (SPA-style navigation isn't used, but verify no memory leaks on long sessions). Evaluate whether GSAP can be loaded only on pages that use animations (not admin pages)
- [ ] **Critical CSS:** Extract above-the-fold CSS for the landing page and inline it in `<head>` to eliminate the render-blocking stylesheet on first paint
- [ ] **Image optimization pipeline:** Current Pillow processing resizes to 2000px max. Add: WebP generation as a secondary format (serve via `<picture>` with JPEG fallback), responsive `srcset` generation (640w, 1024w, 2000w), lazy loading (`loading="lazy"`) on all below-fold images, and LQIP (Low Quality Image Placeholder) blur-up thumbnails
- [ ] **Font loading:** Audit Google Fonts loading. Implement `font-display: swap`, preconnect hints (`<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>`), and consider self-hosting the 5 font pairings to eliminate the external dependency

### 12.4 — Template Optimization

- [ ] **Template inheritance audit:** Map the full inheritance chain (base.html → page templates). Ensure no block is overridden redundantly. Verify that admin templates do not inherit public-facing CSS/JS they don't use
- [ ] **Macro extraction:** Identify repeated HTML patterns across templates (card components, pagination controls, empty states, flash message rendering) and extract into Jinja2 macros in `app/templates/components/`
- [ ] **Conditional rendering:** Audit `{% if %}` blocks that check settings — ensure they short-circuit cleanly (no rendering hidden content then wrapping in `display:none`)
- [ ] **SEO template audit:** Verify meta tags, Open Graph, structured data (JSON-LD for Person, BlogPosting), canonical URLs, and hreflang are correct on every public page

### 12.5 — Static Analysis and Code Quality Enforcement

**Problem:** The codebase has no automated code quality gates. Code review catches style issues but misses patterns that tooling detects instantly — unused variables, unreachable code, overly complex functions, security anti-patterns, and inconsistent formatting. Professional codebases enforce quality mechanically, not manually.

- [ ] **Linter (ruff):** Add `ruff` as the primary Python linter. Configure in `pyproject.toml` with rule sets: `E` (pycodestyle errors), `F` (pyflakes), `W` (pycodestyle warnings), `I` (isort — import sorting), `B` (flake8-bugbear — common pitfalls), `S` (flake8-bandit — security), `C4` (flake8-comprehensions), `SIM` (flake8-simplify), `UP` (pyupgrade — Python 3.12+ idioms). Fix all existing violations before enabling in CI
- [ ] **Formatter (ruff format):** Enable `ruff format` as the code formatter. Configure line length (88, Black-compatible). Run across the entire codebase as a one-time commit, then enforce in CI
- [ ] **Security scanner (bandit):** Add `bandit` as a dedicated security static analysis tool. Configure to scan all Python files, exclude tests. Target: zero findings of MEDIUM severity or higher. Document any accepted suppressions in-line with `# nosec` and a justification comment
- [ ] **Pre-commit hooks:** Create `.pre-commit-config.yaml`:
  - `ruff` (lint + format)
  - `bandit` (security scan)
  - `pip-audit` (dependency vulnerabilities)
  - `check-yaml` / `check-json` / `check-toml` (syntax validation on config files)
  - `detect-secrets` (prevent accidental credential commits)
  - `trailing-whitespace` / `end-of-file-fixer` / `mixed-line-ending`
  - Document pre-commit setup in `CONTRIBUTING.md`
- [ ] **CI quality gate:** Add a CI job that runs `ruff check`, `ruff format --check`, `bandit`, and `pip-audit` on every push. Failures block merge. This is non-negotiable — no code lands without passing static analysis
- [ ] **Complexity tracking:** Configure `ruff` to enforce maximum cyclomatic complexity per function (threshold: 15). Functions exceeding this are flagged for refactoring. ~~Add a `manage.py complexity-report` command that prints the top 20 most complex functions in the codebase~~ (`complexity-report` shipped; ruff C901 gate still pending)
- [ ] **Dead code detection:** Run `vulture` across the codebase to find unused functions, variables, and imports. Fix or document (some apparent dead code is used by Jinja2 templates or Flask's import machinery). Add `vulture` to CI as a warning (not blocking), ratchet to blocking in v0.4.0

---

## Phase 13 — Security Hardening (Defense-in-Depth)

*v0.2.0 established the security baseline. v0.3.0 elevates it to defense-in-depth with proactive threat modeling, enforcement-mode CSP, and automated vulnerability scanning.*

### 13.1 — Threat Model Document

- [ ] Produce `THREAT_MODEL.md` documenting:
  - Attack surface enumeration (public routes, admin routes, API routes, file upload, SMTP relay, SQLite, container boundary)
  - Threat actors (anonymous internet user, authenticated API consumer, compromised reverse proxy, supply chain)
  - Mitigations in place (by phase and layer)
  - Residual risks and accepted trade-offs
  - Incident response outline (what to do if the SQLite DB is compromised, if the container is breached, if an API token leaks)
- [ ] Review against OWASP Top 10 (2021) and map each item to resume-site's controls

### 13.2 — CSP Enforcement

**Problem:** v0.2.0 ships CSP in `Content-Security-Policy-Report-Only`. This detects violations but doesn't block them.

- [ ] Migrate from `Content-Security-Policy-Report-Only` to enforced `Content-Security-Policy`
- [ ] Eliminate `'unsafe-inline'` from `style-src`:
  - Custom CSS injection (admin textarea → `<style>` block) must move to a nonce-based approach: generate a per-request nonce, set `style-src 'nonce-<value>'`, apply the nonce to the injected `<style>` tag
  - Quill.js inline styles: audit whether Quill can be configured to use classes instead of inline styles. If not, apply the nonce to Quill's style injections
  - Accent color and font pairing dynamic styles: same nonce approach
- [ ] Add `report-uri` or `report-to` directive pointing to an internal endpoint (`/csp-report`) that logs violations to the activity log. Admin dashboard displays CSP violation count
- [ ] Test exhaustively: every public page, every admin page, every GSAP animation, every font load, every CDN script

### 13.3 — Request Filtering (WAF-Lite)

- [ ] Add a `before_request` handler that inspects incoming requests for common attack patterns:
  - Path traversal attempts (`../`, `..%2f`, `%00`)
  - SQL injection fingerprints in query parameters (common patterns: `' OR 1=1`, `UNION SELECT`, `; DROP`)
  - Oversized request bodies (enforce `MAX_CONTENT_LENGTH` globally, not just on upload routes)
  - Malformed `Content-Type` headers on POST requests
  - Suspicious `User-Agent` strings (empty, single character, known scanner signatures)
- [ ] Blocked requests return 400 (not 403 — don't reveal the filter exists) and are logged with full request details
- [ ] The filter is configurable: `request_filter_enabled` setting with an admin toggle, and a `request_filter_log_only` mode for tuning
- [ ] Do NOT implement a full WAF — this is a lightweight first-pass filter. Document what it catches and what it doesn't in the threat model

### 13.4 — API Authentication (Token-Based)

*This phase establishes the auth model that Phase 16 (REST API) builds on.*

- [ ] **Token model:** `api_tokens` table with columns: `id`, `token_hash` (SHA-256 of the raw token), `name` (human label), `scope` (comma-separated: `read`, `write`, `admin`), `created_at`, `expires_at` (nullable — null = no expiry), `last_used_at`, `revoked` (boolean), `created_by` (admin username for future multi-user)
- [ ] Migration: `006_api_tokens.sql`
- [ ] **Token generation:** `manage.py generate-api-token --name "My Integration" --scope read,write --expires 90d` — prints the raw token once (never stored), stores the hash
- [ ] **Admin UI:** Token management page — list active tokens (name, scope, last used, created), revoke, generate new. Token value shown once on creation, then hidden forever
- [ ] **Auth middleware:** Decorator `@require_api_token(scope='read')` that checks `Authorization: Bearer <token>` header, validates hash against DB, checks scope, checks expiry, updates `last_used_at`. Returns 401 on missing/invalid, 403 on insufficient scope
- [ ] **Rate limiting:** API routes get separate rate limits from browser routes (configurable, default 60/min for read, 30/min for write, 10/min for admin)
- [ ] **Token rotation:** `manage.py rotate-api-token --name "My Integration"` — generates a new token, revokes the old one, prints the new value

### 13.5 — Secret Rotation and Audit

- [ ] **Secret key rotation:** `manage.py rotate-secret-key` — generates a new secret key, writes to config.yaml (or prints for manual insertion), warns that all active sessions will be invalidated
- [ ] **Startup security audit:** Expand the existing startup warnings into a formal audit log entry:
  - Secret key strength (length, entropy estimate)
  - Password hash algorithm and iteration count
  - SMTP credentials present (warn if missing — contact form won't work)
  - Admin `allowed_networks` configured (warn if empty — admin open to all IPs)
  - HTTPS indicators (session cookie secure flag, HSTS header config)
  - Database file permissions (warn if world-readable)
  - Container user (warn if running as root)
- [ ] **Dependency scanning:** Add `pip-audit` as a pre-commit hook (not just CI). Add `safety` as a secondary scanner. Document the process for responding to CVEs in `SECURITY.md`

### 13.6 — Session and Cookie Hardening

- [ ] **Session storage review:** Flask's default cookie-based sessions store all session data client-side (signed but not encrypted). Evaluate whether to move to server-side sessions (SQLite-backed via Flask-Session) now that the session will carry API context and locale data. If not moving to server-side, document the trade-off explicitly
- [ ] **Cookie audit:** Enumerate every cookie the app sets. Verify each has appropriate `Secure`, `HttpOnly`, `SameSite`, `Path`, and `Max-Age`/`Expires` attributes
- [ ] **Login hardening:** Add account lockout after N failed attempts (configurable, default 10 within 15 minutes) with a time-based unlock. Currently only rate-limited at 5/min via Flask-Limiter — add an application-level lockout that persists across rate limit windows

### 13.7 — File Upload Hardening

- [ ] **Antivirus integration hook:** Add a configurable `upload_scan_command` setting. When set, uploaded files are passed to the command (e.g., `clamdscan --fdpass`) before processing. If the scan fails or returns non-zero, the upload is rejected. Document ClamAV setup in deployment docs
- [ ] **Upload quarantine:** Files land in a temporary directory first, are validated (magic bytes, size, dimensions, scan), and only moved to the photo storage directory on success. Failed uploads are logged and cleaned up
- [ ] **EXIF stripping:** Strip all EXIF metadata from uploaded images by default (GPS coordinates, camera info, timestamps). Add an `upload_preserve_exif` setting for users who want to keep it (off by default)

### 13.8 — Fuzz Testing

**Problem:** Unit tests verify expected inputs. Fuzz testing verifies the application doesn't crash, leak data, or behave dangerously when given unexpected, malformed, or adversarial input. This is the difference between "it works" and "it's resilient." Professional security audits always include fuzzing.

- [ ] **Property-based testing with Hypothesis:** Add `hypothesis` to dev dependencies. Write property-based tests for every function that accepts user input:
  - `_slugify()` — fuzz with arbitrary Unicode strings, verify output is always URL-safe, never empty on non-empty input, never contains consecutive hyphens
  - `_calculate_reading_time()` — fuzz with arbitrary HTML strings, verify output is always a positive integer, never raises on malformed HTML
  - `_ensure_unique_slug()` — fuzz with concurrent slug generation, verify uniqueness holds
  - `sanitize_html()` — fuzz with arbitrary strings including embedded `<script>`, event handlers, CSS expressions. Verify output never contains executable content
  - `_validate_magic_bytes()` — fuzz with random byte sequences, verify never returns True for non-image data
  - Contact form fields — fuzz name, email, message with boundary-length strings, null bytes, Unicode edge cases (RTL, combining characters, zero-width joiners)
  - Settings values — fuzz all setting types (str, int, bool, color, select) with out-of-bound values, verify no crash and no SQL injection
  - Review submission fields — same treatment as contact form
  - Blog post content — fuzz Markdown input through mistune → sanitize pipeline, verify no XSS survives
  - API request bodies — fuzz JSON payloads with missing fields, extra fields, wrong types, deeply nested objects, extremely large arrays
- [ ] **Crash oracle:** Every fuzz test asserts that the function either returns a valid result or raises a specific, expected exception type. Any `500 Internal Server Error`, `sqlite3.OperationalError`, or unhandled exception is a test failure
- [ ] **CI integration:** Hypothesis tests run in CI with a time budget (30 seconds per test in CI, unlimited locally). Failures produce a minimal reproducing example that gets added to the regular test suite as a regression test
- [ ] **Fuzz the HTTP layer:** Use Hypothesis with the Flask test client to generate random HTTP requests (random paths, methods, headers, query parameters, body content) and verify the app never returns 500, never leaks stack traces, and never returns data from other users' sessions

### 13.9 — Dynamic Application Security Testing (DAST)

**Problem:** Static analysis (bandit) catches code patterns. DAST catches vulnerabilities in the running application — things like actual XSS that survives rendering, actual SQL injection through the full request pipeline, misconfigured headers on specific routes, and authentication bypass paths.

- [ ] **OWASP ZAP baseline scan:** Add `zap-baseline.py` to the CI pipeline. Runs a passive scan against the test app (started in a CI container). Scans all public routes and the admin login page. Fails the build on MEDIUM+ findings
- [ ] **ZAP configuration file:** `zap-config.yaml` — customize scan rules, exclude false positives, set authentication credentials for scanning admin routes (use the test admin account)
- [ ] **DAST in CI workflow:** New CI job `security-scan` that:
  1. Builds the container image
  2. Starts it with a test config
  3. Seeds the database with test content
  4. Runs ZAP baseline scan against all routes
  5. Uploads the HTML report as a CI artifact
  6. Fails on findings above threshold
- [ ] **Manual pen test checklist:** `docs/PENTEST_CHECKLIST.md` — a step-by-step manual penetration testing guide covering: authentication bypass attempts, privilege escalation (non-admin accessing admin routes), file upload abuse, CSRF validation, session fixation, clickjacking, CORS misconfiguration, information disclosure (error messages, headers, debug info), and rate limit bypass. Not automated — designed for periodic manual security review

---

## Phase 14 — Admin Panel Completion (v0.2.0 Deferrals + Visual Theme Editor)

*Completes every deferred admin feature from v0.2.0 Phase 9, plus adds the visual theme editor with live preview.*

### 14.1 — Drag-and-Drop Reordering

**Covers:** Nav item ordering, services, stats, photos, projects, homepage sections.

- [ ] Add a lightweight drag-and-drop JS library (Sortable.js via CDN — 8KB gzipped, no dependencies, touch-friendly)
- [ ] **Reorder API:** Generic `/admin/reorder` POST endpoint accepting `table`, `id_order` (JSON array of IDs in new order). Validates table name against an allowlist. Updates `sort_order` column in a single transaction
- [ ] **Services page:** Replace static list with sortable cards. Drag handle on left. Save button persists order
- [ ] **Stats page:** Same sortable pattern
- [ ] **Photos page:** Sortable grid with thumbnail previews
- [ ] **Projects page:** Sortable list
- [ ] **Nav ordering:** New admin section "Navigation" — lists all nav items with drag handles. Order saved to `nav_order` setting (JSON array of nav keys). The base template reads `nav_order` and renders links in that sequence
- [ ] **Homepage layout selector:** New admin section "Homepage Layout" — lists all homepage sections (hero, about, stats, services, portfolio, testimonials, CTA, footer) with drag handles and visibility toggles. Order saved to `homepage_layout` setting (JSON array of `{section, visible}` objects). The `index.html` template reads this and renders sections in the configured order, skipping hidden sections

### 14.2 — Custom Nav Links

- [ ] `custom_nav_links` setting storing a JSON array of `{label, url, position, new_tab}` objects
- [ ] Admin "Navigation" section includes an "Add Custom Link" form: label, URL, position (before/after which built-in link), open in new tab toggle
- [ ] Custom links rendered in the navbar at the configured position with `rel="noopener noreferrer"` on external links
- [ ] Drag-and-drop reordering includes custom links in the same sortable list as built-in nav items
- [ ] Maximum 10 custom links (prevent navbar overflow)

### 14.3 — Bulk Operations

- [ ] **Multi-select UI:** Checkbox column on admin list pages (photos, reviews, blog posts, contact submissions). "Select All" checkbox in header. Selection count badge. Bulk action dropdown at top of list
- [ ] **Bulk actions by content type:**
  - Photos: bulk delete (with file cleanup), bulk change display tier, bulk change category
  - Reviews: bulk approve, bulk reject, bulk change display tier, bulk delete
  - Blog posts: bulk publish, bulk unpublish, bulk archive, bulk delete
  - Contact submissions: bulk delete, bulk mark as spam
- [ ] **Confirmation modal:** Bulk destructive actions (delete) require a confirmation dialog showing the count and action
- [ ] **Activity logging:** Each bulk action logged as a single activity entry with count (e.g., "Deleted 12 photos")

### 14.4 — Image Preview in Editors

- [ ] **Photo upload:** Show a thumbnail preview of the selected file before upload (client-side `FileReader` + `URL.createObjectURL`)
- [ ] **Blog cover image:** Same preview pattern on the blog editor page
- [ ] **Existing photo editing:** Show the current image alongside the metadata form when editing a photo's title/description/category
- [ ] **Drag-and-drop upload zone:** On the photo manager page, add a drop zone that accepts dragged files with a visual indicator

### 14.5 — Admin Search

- [ ] **Global admin search bar** in the admin nav bar — single text input that searches across all content types
- [ ] **Search targets:** Content blocks (title, content plain text), blog posts (title, summary, content), reviews (reviewer name, message), contact submissions (name, email, message), photos (title, description, category), services (title, description), projects (title, description)
- [ ] **Implementation:** SQLite FTS5 virtual table for full-text search across content types. Migration `007_fts5.sql` creates the FTS table and triggers to keep it in sync
- [ ] **Results page:** Grouped by content type with direct links to the edit page for each result. Result count per type. Highlight matching terms
- [ ] **Incremental indexing:** Triggers on INSERT/UPDATE/DELETE keep the FTS index current. `manage.py rebuild-search-index` for manual reindexing

### 14.6 — Visual Theme Editor with Live Preview

**Covers v0.2.0 deferred "Visual theme editor with live preview" — builds on the existing custom CSS, accent color, and color preset infrastructure.**

- [ ] **Theme editor admin page** (`/admin/theme`) — dedicated full-width page (not crammed into the settings page):
  - Left panel: theme controls (color pickers, font selectors, spacing sliders, CSS textarea)
  - Right panel: live preview iframe showing the public landing page with changes applied in real-time
- [ ] **Live preview mechanism:** The iframe loads the landing page with a `?preview=1` query parameter. A JavaScript `postMessage` bridge sends CSS variable overrides from the editor to the iframe. Changes are applied instantly via `document.documentElement.style.setProperty()` — no page reload
- [ ] **Theme controls:**
  - Primary accent color (with color picker and hex input)
  - Secondary accent color (new)
  - Background color overrides (dark mode, light mode)
  - Text color overrides
  - Font pairing selector (existing, but now with live preview)
  - Border radius scale (0 = sharp corners, 1 = current, 2 = pill-shaped)
  - Spacing scale (compact, default, spacious)
  - Custom CSS textarea (existing, but now previews live)
- [ ] **Theme presets:** Expand from 6 to 12 presets. Each preset sets all the above variables as a bundle. Presets are a starting point — the user can customize individual values after selecting a preset
- [ ] **Theme export/import:** "Export Theme" button downloads a JSON file with all theme values. "Import Theme" button loads from JSON. Enables sharing themes and backup before experimentation
- [ ] **Theme save:** "Save Theme" persists all values to the settings table. "Reset to Default" button restores the v0.1.0 defaults
- [ ] **Preview safety:** The preview iframe is sandboxed (`sandbox="allow-same-origin allow-scripts"`). CSS from the custom textarea is sanitized server-side on save (strip `@import`, `url()` with non-https schemes, `expression()`, `-moz-binding`, JavaScript in CSS)

---

## Phase 15 — Multilingual User-Generated Content

*v0.2.0 shipped the i18n framework (Flask-Babel, string extraction, locale routing) for UI strings only. v0.3.0 extends this to user-generated content: content blocks, blog posts, services, stats, project descriptions, and certification descriptions.*

### 15.1 — Translation Junction Tables

**Architecture:** Each translatable content type gets a companion `_translations` table with a locale column. The original table retains its content columns as the default-locale version. Queries fall back to the default locale when no translation exists for the requested locale.

- [ ] Migration `008_content_translations.sql`:
  ```
  content_block_translations (block_id FK, locale, title, content, plain_text)
  blog_post_translations     (post_id FK, locale, title, summary, content)
  service_translations       (service_id FK, locale, title, description)
  stat_translations          (stat_id FK, locale, label, suffix)
  project_translations       (project_id FK, locale, title, description)
  certification_translations (cert_id FK, locale, title, description)
  ```
  Each table has a UNIQUE constraint on `(parent_id, locale)` and a foreign key to the parent table with `ON DELETE CASCADE`.

### 15.2 — Translation-Aware Query Layer

- [ ] Create `app/services/translations.py`:
  - `get_translated(db, table, id, locale, fallback_locale='en')` — returns the translation row for the given locale, falling back to the default locale, then to the parent table's values
  - `get_all_translated(db, table, locale, **filters)` — bulk translation resolution for list pages (single JOIN query, not N+1)
  - `save_translation(db, table, parent_id, locale, **fields)` — INSERT OR REPLACE into the translations table
  - `delete_translation(db, table, parent_id, locale)` — remove a single locale's translation
  - `get_available_translations(db, table, parent_id)` — list which locales have translations for a given item
- [ ] Update every public model query function to accept an optional `locale` parameter. When provided, JOIN with the translations table and COALESCE translated fields over default values
- [ ] The public route context (set by locale middleware) automatically passes the current locale to model queries

### 15.3 — Admin Translation UI

- [ ] **Translation tab on edit pages:** When editing a content block, blog post, service, stat, project, or certification, a "Translations" tab appears below the main editor. Tabs for each configured locale (from `available_locales` setting). Each tab shows the translatable fields pre-filled with the default-locale content (as a reference, not editable), and empty fields for the translated values
- [ ] **Translation status indicators:** On admin list pages, a locale badge shows which translations exist for each item (e.g., "en ✓ es ✓ fr ✗")
- [ ] **Bulk translation export/import:** `manage.py translations export-content --locale es --format po` exports all translatable user content as a .po file for external translation tools. `manage.py translations import-content --locale es content-es.po` imports translations back
- [ ] **Translation completeness dashboard:** Widget on the admin dashboard showing per-locale translation coverage (e.g., "Spanish: 45/60 items translated — 75%")

### 15.4 — Public Translation Rendering

- [ ] All public templates updated to use the translation-aware query results. No changes to template logic — the query layer handles locale resolution transparently
- [ ] Blog RSS feed: include `<language>` tag, and optionally generate per-locale feeds
- [ ] Sitemap: include `hreflang` alternate links for pages with translations
- [ ] Open Graph: `og:locale` tag set to current locale, `og:locale:alternate` for available translations

---

## Phase 16 — REST API

*Full REST API with public read endpoints and token-authenticated admin write endpoints. Built on the service layer from v0.2.0 Phase 5.4 and the token auth from Phase 13.4.*

### 16.1 — API Blueprint and Middleware

- [ ] Create `app/routes/api.py` blueprint mounted at `/api/v1/`
- [ ] CSRF exemption on all API routes (token auth replaces CSRF for non-browser clients)
- [ ] JSON request/response only — `Content-Type: application/json` enforced on POST/PUT/PATCH
- [ ] Versioned URL prefix (`/api/v1/`) so future breaking changes can coexist
- [ ] Standard error response format: `{"error": "message", "code": "ERROR_CODE", "details": {...}}`
- [ ] Standard pagination format: `{"data": [...], "pagination": {"page": 1, "per_page": 20, "total": 142, "pages": 8}}`
- [ ] `Accept-Language` header respected for multilingual content responses
- [ ] `ETag` and `If-None-Match` on read endpoints for conditional requests (304 Not Modified)

### 16.2 — Public Read Endpoints (No Auth Required)

- [ ] `GET /api/v1/site` — site metadata (title, tagline, availability status, available locales)
- [ ] `GET /api/v1/content/:slug` — single content block
- [ ] `GET /api/v1/services` — visible services list
- [ ] `GET /api/v1/stats` — visible stats
- [ ] `GET /api/v1/portfolio` — visible photos with pagination, optional `?category=` filter
- [ ] `GET /api/v1/portfolio/:id` — single photo with metadata
- [ ] `GET /api/v1/case-studies/:slug` — single case study
- [ ] `GET /api/v1/projects` — visible projects
- [ ] `GET /api/v1/projects/:slug` — single project detail
- [ ] `GET /api/v1/testimonials` — approved reviews with pagination, optional `?tier=featured` filter
- [ ] `GET /api/v1/certifications` — visible certifications
- [ ] `GET /api/v1/blog` — published posts with pagination, optional `?tag=` filter
- [ ] `GET /api/v1/blog/:slug` — single blog post with rendered content
- [ ] `GET /api/v1/blog/tags` — all tags with post counts

### 16.3 — Authenticated Write Endpoints (Token Required — `write` scope)

- [ ] `POST /api/v1/blog` — create blog post (draft by default)
- [ ] `PUT /api/v1/blog/:slug` — update blog post
- [ ] `DELETE /api/v1/blog/:slug` — delete blog post
- [ ] `POST /api/v1/blog/:slug/publish` — publish a draft
- [ ] `POST /api/v1/blog/:slug/unpublish` — unpublish
- [ ] `POST /api/v1/portfolio` — upload photo (multipart/form-data)
- [ ] `PUT /api/v1/portfolio/:id` — update photo metadata
- [ ] `DELETE /api/v1/portfolio/:id` — delete photo (with file cleanup)
- [ ] `POST /api/v1/contact` — submit a contact form entry (rate limited, honeypot enforced)

### 16.4 — Admin Endpoints (Token Required — `admin` scope)

- [ ] `GET /api/v1/admin/settings` — all settings (grouped by category)
- [ ] `PUT /api/v1/admin/settings` — bulk update settings
- [ ] `GET /api/v1/admin/analytics` — page view summary (total, per-page, time series)
- [ ] `GET /api/v1/admin/activity` — recent activity log
- [ ] `GET /api/v1/admin/reviews` — all reviews with status filter
- [ ] `PUT /api/v1/admin/reviews/:id` — update review (approve, reject, change tier)
- [ ] `POST /api/v1/admin/tokens` — generate review invite token
- [ ] `DELETE /api/v1/admin/tokens/:id` — revoke review token
- [ ] `GET /api/v1/admin/contacts` — contact submissions with pagination
- [ ] `POST /api/v1/admin/backup` — trigger an on-demand backup (returns backup file path)

### 16.5 — API Documentation

- [ ] Generate OpenAPI 3.0 specification (`openapi.yaml`) — hand-written, not auto-generated (keeps it clean and intentional)
- [ ] Serve interactive docs at `/api/v1/docs` using Swagger UI (loaded from CDN, behind a `api_docs_enabled` feature flag, default off)
- [ ] Include authentication examples, error code catalog, and pagination guide
- [ ] API changelog section in `CHANGELOG.md` for tracking breaking changes

### 16.6 — API Tests

- [ ] Test every endpoint: correct status codes, response format, pagination boundaries
- [ ] Auth tests: missing token → 401, invalid token → 401, expired token → 401, wrong scope → 403, revoked token → 401
- [ ] Rate limiting tests: exceed threshold → 429 with `Retry-After` header
- [ ] Content negotiation: request without `Accept: application/json` → still works (JSON is default)
- [ ] Locale: `Accept-Language: es` → translated content returned (when available)
- [ ] ETag: second identical request with `If-None-Match` → 304

---

## Phase 17 — Automated Backups

*Built-in backup command + container-native orchestration via systemd timers.*

### 17.1 — Backup Command *(shipped)*

- [x] `manage.py backup` — creates a timestamped `resume-site-backup-YYYYMMDD-HHMMSS.tar.gz` archive containing the SQLite DB (online backup API), `photos/`, and `config.yaml`. Output dir resolves via `--output-dir` > `RESUME_SITE_BACKUP_DIR` > `<repo>/backups`. Atomic write (`.tar.gz.tmp` → `os.replace`).
- [x] `manage.py backup --db-only` — database-only archive.
- [x] `manage.py backup --list` — newest-first table with name, size (MB), mtime. Ignores in-flight `.tmp` files and `pre-restore-*` sidecars.
- [x] `manage.py backup --prune --keep N` — retention (N ≥ 1 enforced by argparse).
- [x] `manage.py restore --from FILE [--force]` — round-trip DB + photos; always writes a pre-restore sidecar; `--force` suppresses the interactive prompt; non-TTY without `--force` exits with a clear error. Path-traversal, symlinks, absolute-path members, and corrupted tarballs are rejected by `_safe_extract` (see `app/services/backups.py`).

### 17.2 — Scheduled Backups (Container-Native)

- [ ] **Systemd timer unit:** `resume-site-backup.timer` and `resume-site-backup.service` — runs `podman exec resume-site python manage.py backup --prune --keep 7` on a configurable schedule (daily at 2 AM by default)
- [ ] **Quadlet integration:** Update `resume-site.container` Quadlet file to reference the backup volume mount
- [ ] **Backup volume:** Add a `resume-site-backups` volume to `compose.yaml`. Document mount point and recommended host path
- [ ] **Compose-based schedule:** Document using `podman compose exec` in a cron job or systemd timer for users not using Quadlets
- [ ] **Backup health:** ~~Add a `backup_last_success` setting that `manage.py backup` updates on completion.~~ *(setting write shipped with 17.1 — the settings-table row is maintained by `create_backup` on every successful run, including `--db-only`. The admin-dashboard "Last backup: X ago" widget is still pending.)*
- [ ] **Documentation:** Dedicated "Backups" section in README covering: automatic setup (Quadlet/timer), manual invocation, restore procedure, offsite backup strategies (rsync, rclone, S3-compatible), and backup encryption (gpg wrapper example)

---

## Phase 18 — Observability: Structured Logging, Metrics, and Profiling

*The "know what your app is doing at all times" phase. Transforms the current print-to-stdout approach into structured, queryable, actionable telemetry.*

### 18.1 — Structured Logging

**Problem:** Current logging is implicit (Gunicorn access logs + Python's default logger). No structured fields, no request correlation, no log levels used consistently.

- [x] **Logging configuration:** `app/services/logging.py` configures Python's `logging` module with a JSON formatter (default) and a human-readable formatter (dev). Mode + level via env vars `RESUME_SITE_LOG_FORMAT` and `RESUME_SITE_LOG_LEVEL`. Status → level mapping: 2xx → INFO, 4xx → WARNING, 5xx → ERROR. Per-request log entry via `_log_request` after-request hook in `app/__init__.py`, includes `timestamp`, `level`, `logger`, `message`, `module`, `request_id`, `client_ip_hash`, `method`, `path`, `status_code`, `duration_ms`, `user_agent` (first 200 chars). **Remaining:** migrating `config.py` stderr prints through the logger (separate commit — requires factory reshuffling).
- [x] **Request ID propagation:** Generate a UUID4 per request, store in `g.request_id`, echo as `X-Request-ID` response header. Allowlist-validated inbound header propagated verbatim for reverse-proxy correlation. Included in every structured log entry (via `_RequestContextFilter` on the root logger).
- [x] **Sensitive data scrubbing (PII posture):** Metadata-only request logging — we log method/path/status/duration/request_id/user_agent and a per-deployment **SHA-256 hash** of the client IP. Never logged: query strings, POST bodies, full IPs, passwords, tokens. IP hash uses `secret_key` as salt so log files alone can't correlate visitors across deployments.
- [ ] **Log rotation:** Document Gunicorn's `--access-logfile` and `--error-logfile` integration with container log drivers. For file-based logging (non-container), add `RotatingFileHandler` configuration

### 18.2 — Prometheus-Compatible Metrics Endpoint

- [ ] `GET /metrics` — returns Prometheus exposition format text
- [ ] **Metrics collected:**
  - `resume_site_requests_total{method, path_template, status}` — counter (path_template, not raw path, to avoid label explosion: `/blog/:slug` not `/blog/my-first-post`)
  - `resume_site_request_duration_seconds{method, path_template}` — histogram (buckets: 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
  - `resume_site_db_query_duration_seconds{query_name}` — histogram of named query durations
  - `resume_site_db_query_total{query_name}` — counter of queries executed
  - `resume_site_active_sessions` — gauge
  - `resume_site_photo_uploads_total` — counter
  - `resume_site_contact_submissions_total{is_spam}` — counter
  - `resume_site_blog_posts_total{status}` — gauge (published, draft, archived)
  - `resume_site_api_requests_total{method, endpoint, status, scope}` — counter
  - `resume_site_backup_last_success_timestamp` — gauge (epoch seconds)
  - `resume_site_uptime_seconds` — gauge
- [ ] **Implementation:** Lightweight custom implementation using a module-level metrics registry and `after_request` hooks — no Prometheus client library dependency. The `/metrics` endpoint renders the registry in exposition format
- [ ] **Feature flag:** `metrics_enabled` setting (default `false`). When disabled, `/metrics` returns 404
- [ ] **Access control:** `/metrics` is restricted to allowed_networks (same as admin) by default. Overridable via `metrics_allowed_networks` setting for separate Prometheus scraper access

### 18.3 — Request Profiling

- [ ] **Per-request timing breakdown** (when `profiling_enabled` setting is `true`, default `false`):
  - Total request duration
  - Database query count and total query time
  - Template rendering time
  - Photo processing time (upload routes only)
  - Breakdown logged as structured JSON at INFO level
- [ ] **Slow request logging:** Requests exceeding a configurable threshold (`slow_request_threshold_ms`, default 500) are logged at WARNING with full timing breakdown regardless of the profiling flag
- [ ] **SQLite query counter:** Wrap `get_db()` to return a connection proxy that counts queries and measures execution time per request. Store counts in `g.db_query_count` and `g.db_query_time_ms` for use in logging and metrics
- [ ] **Profile export:** `manage.py profile --requests 100 --output profile.json` — runs the app with profiling enabled, processes N simulated requests (using the test client), and outputs a JSON report with per-route timing statistics, sorted by total time. Provides a baseline for optimization work

### 18.4 — Browser-Based Testing (v0.2.0 Deferral)

- [ ] Add Playwright to dev dependencies
- [ ] Test: dark/light mode toggle sets `localStorage` value and applies correct CSS class
- [ ] Test: GSAP animations fire on scroll (verify element visibility states)
- [ ] Test: Quill.js editor in admin — type text, save, verify content persists
- [ ] Test: Photo upload drag-and-drop zone works
- [ ] Test: Theme editor live preview updates iframe in real-time
- [ ] Test: Drag-and-drop reordering persists order after page reload

### 18.5 — Performance Baseline Document

- [ ] `PERFORMANCE.md` — established and maintained alongside the codebase:
  - Baseline metrics for the top 10 routes (response time p50/p95/p99, DB queries per request, response size)
  - SQLite `EXPLAIN QUERY PLAN` output for all indexed queries
  - Container startup time
  - Memory usage at idle and under load (50 concurrent users simulated with `locust`)
  - Static asset sizes (before and after optimization)
  - Lighthouse scores for the landing page (Performance, Accessibility, Best Practices, SEO)
  - Updated with every release — this document is the living proof that optimization work produces measurable results

### 18.6 — Load Testing and CI Performance Regression Gates

**Problem:** Without load testing, you don't know how the application behaves under realistic traffic, and without CI regression gates, you don't know when a code change makes it slower. Professional applications fail the build when performance degrades, the same way they fail the build when tests fail.

- [ ] **Load testing with locust:** Add `locust` to dev dependencies. Create `tests/loadtests/locustfile.py`:
  - `PublicUserBehavior`: simulates a visitor browsing the landing page, portfolio, blog listing, individual blog post, testimonials, and contact page. Weighted by realistic traffic distribution (landing page = 40%, portfolio = 20%, blog = 20%, rest = 20%)
  - `APIConsumerBehavior`: simulates an API consumer making read requests to all public endpoints with realistic pagination patterns
  - `AdminBehavior`: simulates an admin session — login, dashboard, edit content, upload photo, publish blog post, save settings
  - Configurable user count and spawn rate via CLI or `tests/loadtests/config.yaml`
- [ ] **Baseline load test:** Run locust with 50 concurrent users for 5 minutes. Record: requests/second, p50/p95/p99 response times per endpoint, error rate, DB connection pool usage. Store results in `PERFORMANCE.md`
- [ ] **CI performance regression gate:** New CI job `perf-regression` that:
  1. Starts the app with a seeded database (consistent test data for reproducible results)
  2. Runs locust with 20 concurrent users for 60 seconds (fast enough for CI, long enough to stabilize)
  3. Compares p95 response times against baseline thresholds stored in `tests/loadtests/thresholds.json`
  4. Fails the build if any endpoint's p95 exceeds its threshold by more than 20%
  5. Outputs a summary table showing endpoint-by-endpoint comparison
  - Thresholds are updated manually after intentional performance changes (e.g., "we added translations, the blog listing is now 10ms slower, update the threshold")
- [ ] **Memory leak detection:** The load test monitors process RSS memory at start and end. If memory grows more than 50% over the test duration, flag a potential leak. Not blocking in CI initially — warning only, ratchet to blocking after establishing stable baselines
- [ ] **Concurrency stress test:** Run locust with 200 concurrent users for 30 seconds. The app should not crash, should not return 500 errors, and should not corrupt the SQLite database. This is not about response time — it's about proving the app degrades gracefully under overload rather than failing catastrophically. Document the degradation behavior in `PERFORMANCE.md`

### 18.7 — Failure Mode and Resilience Testing

**Problem:** Unit tests verify the happy path and some error paths. Resilience tests verify the application behaves correctly when infrastructure fails — disk full, database locked, SMTP unreachable, DNS timeout, upstream CDN down. Professional systems are tested against failure, not just against inputs.

- [ ] **SMTP failure:** Test that when SMTP is unreachable (mock raises `ConnectionRefusedError`), the contact form:
  - Still saves the submission to the database (data is not lost)
  - Shows a user-friendly error message (not a stack trace)
  - Logs the SMTP failure at ERROR level with request ID
  - Does NOT return 500
- [ ] **Database locked:** Test that when a SQLite write lock is held by another connection (simulate with a long-running transaction in a separate thread), the application:
  - Retries within the busy_timeout window (5 seconds)
  - Returns a graceful error if the timeout is exceeded
  - Does NOT corrupt the database
  - Logs the contention event at WARNING level
- [ ] **Disk full on photo upload:** Test that when `os.makedirs` or `image.save()` raises `OSError(errno.ENOSPC)`:
  - The upload is rejected with a clear error message
  - No partial files are left on disk (cleanup on failure)
  - The database transaction is rolled back (no orphaned photo record)
  - Logs at ERROR level
- [ ] **Disk full on database write:** Test that when SQLite raises `sqlite3.OperationalError: database or disk is full`:
  - The request returns a 503 Service Unavailable (not 500)
  - The error is logged with full context
  - Subsequent requests still work once disk space is freed
- [ ] **Corrupted upload (truncated file):** Test that when an uploaded file is truncated mid-stream (simulate with a file object that raises IOError after N bytes):
  - No partial file is saved
  - The database is not modified
  - The user gets a retry-friendly error
- [ ] **Template rendering failure:** Test that if a Jinja2 template references a variable that's somehow missing from the context (e.g., a settings key was deleted from the database), the page either renders with a safe default or returns a 500 with proper logging — never exposes a raw traceback to the user
- [ ] **Malformed database:** Test that if the SQLite database is corrupted (truncate the file to 0 bytes), `manage.py migrate` detects the corruption and refuses to proceed with a clear error message, rather than silently creating a new empty database
- [ ] **CDN unavailability:** Test (via Playwright) that if the GSAP CDN (`cdnjs.cloudflare.com`) is unreachable, the page still renders and is fully functional (just without animations). Verify no JavaScript errors block page interaction
- [ ] **Session store exhaustion:** Test that if the Flask session cookie is malformed, oversized, or tampered with, the server rejects it cleanly (new session) rather than crashing
- [ ] **Document failure behaviors:** Add a "Failure Modes" section to `PERFORMANCE.md` documenting what happens under each failure condition and the expected behavior. This becomes part of the operations runbook

### 18.8 — Mutation Testing (Test Quality Validation)

**Problem:** Code coverage tells you which lines are executed by tests. It does NOT tell you whether the tests would catch a bug on those lines. A test that runs a function but never asserts on the result gives 100% coverage and 0% bug detection. Mutation testing answers the real question: "if I introduce a bug, do my tests catch it?"

- [ ] **Add `mutmut` to dev dependencies.** Configure in `pyproject.toml`:
  - Target modules: `app/services/`, `app/models.py`, `app/db.py`, `app/routes/` (Python code that has business logic)
  - Exclude: templates, static files, tests themselves, migrations
  - Timeout: 30 seconds per mutation (kill slow-running mutants)
- [ ] **Baseline mutation score:** Run `mutmut run` across the full target set. Calculate the mutation score (killed mutants / total mutants). Record in `PERFORMANCE.md`. Target: ≥ 70% mutation score by v0.3.0 release
- [ ] **Priority mutation targets:** Focus on the modules where mutations surviving would indicate real risk:
  - `app/services/blog.py` — slug generation, reading time calculation, publish/unpublish logic
  - `app/services/photos.py` — magic byte validation, file size enforcement, EXIF stripping
  - `app/services/reviews.py` — approval workflow, tier management
  - `app/services/settings_svc.py` — type validation, boolean handling
  - `app/routes/admin.py` — IP restriction logic (a surviving mutation here is a security bug)
  - `app/routes/contact.py` — rate limiting, honeypot detection
  - Authentication logic in admin routes (password verification, session management)
- [ ] **Surviving mutant review:** For each surviving mutant (mutation that tests don't catch):
  - Determine if it represents a real missing assertion (add the test)
  - Determine if it's an equivalent mutation (code change that doesn't affect behavior — mark as accepted)
  - Document the decision
- [ ] **CI integration (warning only):** Add `mutmut` to CI as an informational job. Report the mutation score but don't fail the build. Ratchet to blocking once the baseline is stable. Display the score in the CI summary so it's visible on every PR
- [ ] **Mutation testing report:** `manage.py mutation-report` — runs mutmut, generates a human-readable report showing: surviving mutants by module, killed/survived/timeout counts, and the overall mutation score. Outputs to `mutation-report.html` for review

### 18.9 — Error Categorization and Structured Error Tracking

**Problem:** The current app uses bare `except Exception: pass` in analytics and generic 500 responses elsewhere. There's no way to answer "how many errors happened today, what types, and which endpoints?" without reading raw logs. Professional applications categorize errors, track error rates, and alert on anomalies.

- [ ] **Error taxonomy:** Create `app/errors.py` defining error categories:
  - `ClientError` — 4xx: bad input, missing fields, invalid tokens (expected, non-alarming)
  - `AuthError` — 401/403: failed login, invalid API token, IP restriction (security-relevant, monitor rate)
  - `ExternalError` — SMTP failure, CDN timeout, DNS resolution (infrastructure, may need operator action)
  - `DataError` — database corruption, migration failure, constraint violation (critical, needs investigation)
  - `InternalError` — unhandled exceptions, assertion failures (bugs, must be fixed)
- [ ] **Error counter metric:** `resume_site_errors_total{category, endpoint, status_code}` — counter in the metrics endpoint. Each error category is tracked separately so you can alert on "InternalError rate > 0" (which means a bug) independently of "ClientError rate spike" (which might mean a bot)
- [ ] **Error response standardization:** Every error response includes:
  - A consistent JSON body: `{"error": "human message", "code": "MACHINE_CODE", "request_id": "..."}`
  - The `X-Request-ID` header for correlation
  - A structured log entry at the appropriate level (WARNING for client errors, ERROR for internal errors)
  - No stack traces, no internal paths, no database schema hints in the response body (even in debug mode — stack traces go to logs only)
- [ ] **Unhandled exception handler:** Register a Flask `errorhandler(500)` that:
  - Logs the full traceback at ERROR level with request context (method, path, query params, request_id, client_ip)
  - Returns the standardized error JSON to the client
  - Increments the `InternalError` metric counter
  - Emits a `security.internal_error` event (for webhook notification)
- [ ] **Error rate dashboard widget:** Admin dashboard shows: error count by category for the last 24 hours, last 7 days, and a trend indicator (up/down/flat). InternalError count > 0 shows a red warning badge

### 18.10 — Alerting Rules and Thresholds

**Problem:** Metrics without alerting are just numbers. Alerting converts observability data into operator actions. This phase defines what conditions should trigger alerts and provides ready-to-use rule definitions.

- [ ] **Alerting rules document:** `docs/alerting-rules.yaml` — Prometheus alerting rules in standard format, ready to load into Alertmanager or any compatible system:
  - `ResumeHighErrorRate`: `rate(resume_site_errors_total{category="InternalError"}[5m]) > 0` — any internal error is a bug and should alert immediately
  - `ResumeHighLatency`: `histogram_quantile(0.95, rate(resume_site_request_duration_seconds_bucket[5m])) > 1.0` — p95 over 1 second
  - `ResumeHighRequestRate`: `rate(resume_site_requests_total[1m]) > 100` — possible DDoS or bot activity
  - `ResumeBruteForce`: `rate(resume_site_errors_total{category="AuthError", endpoint="/admin/login"}[5m]) > 5` — login brute force
  - `ResumeBackupStale`: `time() - resume_site_backup_last_success_timestamp > 172800` — no successful backup in 48 hours
  - `ResumeContainerUnhealthy`: health check failure (external probe)
  - `ResumeDiskSpace`: `resume_site_disk_usage_bytes{path="/app/data"} / resume_site_disk_total_bytes{path="/app/data"} > 0.9` — disk 90% full
  - `ResumeAPITokenExpiring`: API tokens expiring within 7 days (logged at INFO, not a Prometheus metric — CLI command or admin dashboard warning)
- [ ] **Disk usage metric:** Add `resume_site_disk_usage_bytes{path}` gauge to `/metrics` — reports usage for `/app/data` (database) and `/app/photos` (uploads). Enables alerting before disk-full failures occur
- [ ] **Alert documentation:** Each rule in `alerting-rules.yaml` includes: a `description` (what it means), a `runbook_url` pointing to the relevant section of `docs/PRODUCTION.md` (what to do about it), and `severity` (critical/warning/info)
- [ ] **In-app alerting (admin dashboard):** The admin dashboard renders a "System Health" panel showing: active warnings (stale backup, disk usage > 80%, recent internal errors), performance summary (avg response time, requests/hour), and a link to `/metrics` for detailed data. This gives operators basic situational awareness without requiring an external monitoring stack

### 18.11 — Grafana Dashboard Template

**Problem:** Telling operators "scrape `/metrics` with Prometheus" is like giving someone a database and telling them to write SQL. A pre-built dashboard is the difference between "monitoring is set up" and "monitoring is actually used."

- [ ] **Dashboard JSON:** `docs/grafana-dashboard.json` — a complete Grafana dashboard importable via the Grafana UI or provisioning API. Panels:
  - **Request Rate:** time series of `resume_site_requests_total` rate, broken down by status code (2xx green, 4xx yellow, 5xx red)
  - **Response Time:** p50, p95, p99 overlaid on a single time series, with the CI regression threshold as a horizontal annotation line
  - **Error Rate:** time series of `resume_site_errors_total` by category, stacked area chart
  - **Database Performance:** query duration histogram heatmap, query count rate
  - **Active Endpoints:** table of endpoints sorted by request count, with avg/p95 latency columns
  - **Blog & Content:** gauge panels for published posts, pending reviews, approved reviews
  - **API Usage:** request rate by scope (read/write/admin), top consumers by token name
  - **System:** disk usage, memory estimate (from container metrics), uptime, backup age
  - **Security:** login failure rate, rate limit trigger rate, CSP violation count, WAF-lite block count
- [ ] **Dashboard variables:** Configurable time range, endpoint filter, status code filter. Uses Prometheus as the data source (configurable name)
- [ ] **Setup documentation:** Section in `docs/PRODUCTION.md` covering: install Prometheus + Grafana (compose snippet), configure Prometheus to scrape `/metrics`, import the dashboard JSON, configure alerting rules. Estimated setup time: 15 minutes for someone with a running Prometheus/Grafana stack

### 18.12 — Synthetic Monitoring Documentation

**Problem:** Internal metrics tell you the app is healthy from the inside. Synthetic monitoring tells you it's healthy from the outside — can a real user actually reach the site, does the page actually load, does the SSL certificate work?

- [ ] **Synthetic monitoring guide:** Section in `docs/PRODUCTION.md` covering three levels of synthetic monitoring:
  - **Level 1 (free, 5 minutes):** Set up Uptime Kuma (self-hosted) or UptimeRobot (free tier) to ping `/healthz` every 60 seconds. Alert on failure. This catches "the site is down" and nothing else
  - **Level 2 (moderate, 30 minutes):** Set up a cron job (or systemd timer) that runs `curl` against 5 key pages (landing, portfolio, blog, contact, API health) and checks: HTTP 200, response time < 2 seconds, response body contains expected strings (site title, etc.). Alert on failure via webhook to your notification channel
  - **Level 3 (comprehensive, 1 hour):** Set up a Playwright script (`tests/synthetic/monitor.py`) that runs a full user journey: load landing page, click portfolio, verify images load, navigate to blog, verify post renders, submit contact form with test data, check admin login page loads. Run every 15 minutes via cron. Alert on any step failure with a screenshot
- [ ] **Example scripts:** Ship `tests/synthetic/healthcheck.sh` (Level 2 curl script) and `tests/synthetic/monitor.py` (Level 3 Playwright script) as ready-to-use templates. Users configure their domain and notification webhook
- [ ] **Status page suggestion:** Document how to expose synthetic monitoring results as a simple status page (e.g., using Uptime Kuma's built-in status page feature or a custom `/status` endpoint)

### 18.13 — Edge Case Test Exhaustiveness Methodology

**Problem:** The v0.2.0 test suite verifies features work. It doesn't exhaustively verify edge cases — what happens at boundaries, with empty inputs, with maximum-length inputs, with Unicode, with concurrent access. This is the "3 assertions vs. 15" gap. This sub-phase establishes a methodology and applies it retroactively to all existing tests and all v0.3.0 additions.

- [ ] **Edge case checklist:** Create `tests/TESTING_STANDARDS.md` documenting the minimum edge cases that every test function must cover. For any function that accepts input:
  - **Empty/null:** empty string, None, zero, empty list/dict
  - **Boundary:** minimum valid, maximum valid, one below minimum, one above maximum
  - **Type mismatch:** string where int expected, int where string expected, boolean edge cases (`"true"` vs `True` vs `1` vs `"1"`)
  - **Unicode:** ASCII, multi-byte UTF-8, emoji, RTL text (Arabic/Hebrew), combining characters, zero-width joiners, null bytes
  - **Length:** single character, exactly at the database column limit, one character over the limit, 10x the limit
  - **Concurrency:** two requests hitting the same resource simultaneously (where applicable — slug uniqueness, token usage, sort order updates)
  - **Injection:** SQL metacharacters (`'; --`), HTML/JS (`<script>`), path traversal (`../`), template injection (`{{ }}`), CRLF injection (`\r\n`)
- [ ] **Retroactive edge case pass:** Apply the checklist to all existing test files. For each test function, add missing edge case assertions. This is tedious but essential — it's where real bugs hide. Track progress as a checklist per test file in `tests/TESTING_STANDARDS.md`
- [ ] **New code requirement:** Every PR that adds a new function accepting user input must include edge case tests per the checklist. Code review checks for this. No exceptions

### 18.14 — Observability-Driven Development Runbook

**Problem:** All the tooling in the world is useless if there's no process for using it. This runbook defines when and how to use each observability tool during development, not just in production.

- [ ] **`docs/OBSERVABILITY_RUNBOOK.md`** — the operational playbook for the project:

  **Before writing any new feature:**
  1. Run `manage.py profile` to establish a baseline for the routes the feature will affect
  2. Record the baseline in the PR description

  **During development:**
  3. Run `ruff check` and `bandit` continuously (pre-commit handles this, but also run manually on save)
  4. Write tests using the edge case checklist from `tests/TESTING_STANDARDS.md`
  5. Run `pytest --cov` and check that coverage doesn't drop below the ratchet threshold

  **Before submitting a PR:**
  6. Run `manage.py profile` again and compare to baseline. Document any regression and justify it (or fix it)
  7. Run `mutmut run --paths-to-mutate=<changed files>` on the modified modules. Surviving mutants need new tests or justification
  8. Run the locust load test locally: `locust -f tests/loadtests/locustfile.py --headless -u 20 -r 5 -t 30s`. Check for 500 errors and latency spikes
  9. If the feature involves user input, run the Hypothesis fuzz tests: `pytest tests/ -k fuzz --hypothesis-seed=random`
  10. Run `manage.py query-audit` to verify no new full-table scans were introduced

  **CI will automatically:**
  11. Run all unit, integration, security, fuzz, and edge case tests
  12. Run static analysis (ruff, bandit, pip-audit, detect-secrets)
  13. Run the performance regression check (locust against thresholds)
  14. Run the DAST scan (ZAP baseline)
  15. Report mutation score (informational)
  16. Report test coverage (blocking if below threshold)

  **After deploying to production:**
  17. Monitor `/metrics` for error rate spikes in the 30 minutes after deployment
  18. Check the admin dashboard "System Health" panel for new warnings
  19. Verify synthetic monitoring checks pass (if configured)
  20. If any anomaly is detected, use the `X-Request-ID` to trace the issue through structured logs

---

## Phase 19 — Webhook / Notification Dispatch

*Event-driven system that fires HTTP callbacks when things happen in the application. Prepares for Phase 20 (plugins) by establishing the internal event bus.*

### 19.1 — Event System

- [ ] Create `app/events.py` — a simple synchronous event bus:
  - `register(event_name, callback)` — register a handler
  - `emit(event_name, **payload)` — fire all registered handlers for an event
  - Handlers are called synchronously in registration order
  - If a handler raises, log the error and continue to the next handler (fail-open)
- [ ] **Built-in events:**
  - `contact.submitted` — new contact form entry
  - `review.submitted` — new review awaiting approval
  - `review.approved` — review approved by admin
  - `blog.published` — blog post published
  - `blog.updated` — blog post updated
  - `settings.changed` — settings saved (includes changed keys)
  - `photo.uploaded` — new photo uploaded
  - `backup.completed` — backup finished
  - `api.token_created` — new API token generated
  - `security.login_failed` — failed admin login attempt
  - `security.rate_limited` — rate limit triggered
- [ ] Register analytics, activity log, and metrics as event handlers (decouple them from route code)

### 19.2 — Webhook Delivery

- [ ] **Webhook table:** `webhooks` table: `id`, `name`, `url`, `secret` (for HMAC signing), `events` (JSON array of event names, or `["*"]` for all), `enabled`, `created_at`, `last_triggered_at`, `failure_count`
- [ ] Migration: `009_webhooks.sql`
- [ ] **Delivery mechanism:** On event emit, for each matching webhook:
  - Build JSON payload: `{"event": "blog.published", "timestamp": "...", "data": {...}}`
  - Sign with HMAC-SHA256 using the webhook's secret → `X-Webhook-Signature` header
  - POST to the webhook URL with a 5-second timeout
  - Log success/failure in `webhook_deliveries` table (event, url, status_code, response_time_ms, error_message)
  - On failure: increment `failure_count`. After 10 consecutive failures, auto-disable the webhook and log a warning
- [ ] **Delivery is asynchronous:** Use a background thread (not blocking the request). For single-process deployments, use `threading.Thread(daemon=True)`. Document that high-volume webhook delivery should use an external queue (RabbitMQ, Redis) — not in scope for v0.3.0
- [ ] **Admin UI:** Webhook management page — list webhooks, create, edit, delete, test (fires a `webhook.test` event), view delivery log (last 50 deliveries per webhook with status codes and timing)
- [ ] **API endpoints:** CRUD for webhooks via the REST API (`/api/v1/admin/webhooks`)

---

## Phase 20 — Plugin Architecture

*Enables extending resume-site without modifying core code. Two mechanisms: internal Python hooks (for tightly-coupled extensions) and external plugin modules (for distributable add-ons).*

### 20.1 — Internal Hook System

*Built on Phase 19's event system, extended with filter hooks (modify data) in addition to action hooks (side effects).*

- [ ] Extend `app/events.py` with filter hooks:
  - `apply_filters(hook_name, value, **context)` — passes `value` through all registered filters in priority order. Each filter receives the current value and returns the modified value
  - Filters have a `priority` parameter (default 10, lower = earlier)
- [ ] **Built-in filter hooks:**
  - `template.head_extra` — inject additional `<head>` content (CSS, meta tags)
  - `template.body_end_extra` — inject content before `</body>` (scripts, widgets)
  - `template.nav_items` — modify the navigation item list
  - `template.footer_extra` — inject content into the footer
  - `content.before_save` — transform content before database write
  - `content.after_render` — transform rendered HTML before template output
  - `api.response` — transform API response data before JSON serialization
  - `admin.dashboard_widgets` — add custom widgets to the admin dashboard
  - `admin.settings_categories` — add custom settings categories
- [ ] **Hook documentation:** `PLUGINS.md` documenting every action and filter hook, their signatures, when they fire, and example usage

### 20.2 — External Plugin Loading

- [ ] **Plugin directory:** `/app/plugins/` (mapped to a container volume for persistence)
- [ ] **Plugin structure:**
  ```
  plugins/
    my-plugin/
      plugin.yaml      # Metadata: name, version, author, description, hooks
      __init__.py       # Entry point: register(app) function
      templates/        # Optional Jinja2 template overrides
      static/           # Optional static assets
      migrations/       # Optional database migrations (numbered, same format as core)
  ```
- [ ] **Plugin lifecycle:**
  - Discovery: On app startup, scan `plugins/` for directories containing `plugin.yaml`
  - Validation: Check `plugin.yaml` schema (name, version, `resume_site_version_min` compatibility field)
  - Registration: Call `plugin.register(app)` which receives the Flask app and can register event handlers, filter hooks, blueprints, template directories, and static asset directories
  - Migration: Plugin migrations run after core migrations (prefixed with plugin name to avoid collisions)
- [ ] **Plugin settings:** Plugins can register settings via the settings registry (Phase 9.4 from v0.2.0). Plugin settings appear in their own category in the admin settings page
- [ ] **Plugin isolation:** Plugins run in the same process (no sandboxing in v0.3.0). Document the trust model: only install plugins you trust, they have full access to the database and Flask app. Sandboxing is a v0.5.0+ concern
- [ ] **Plugin enable/disable:** Admin UI toggle per plugin. Disabled plugins are not loaded on startup. Disabling preserves the plugin's data (migrations are not reversed)
- [ ] `manage.py plugins list` — show installed plugins, versions, enabled status
- [ ] `manage.py plugins enable <name>` / `manage.py plugins disable <name>`
- [ ] `manage.py plugins validate <name>` — check plugin.yaml, run plugin's self-test if defined

### 20.3 — Example Plugins

*Ship two example plugins to validate the architecture and serve as templates for plugin developers:*

- [ ] **`example-analytics-export`** — registers a hook on `admin.dashboard_widgets` to add a "Download Analytics CSV" button. Registers an API endpoint `/api/v1/plugins/analytics-export` that returns page_views as CSV. Demonstrates: widget injection, custom API endpoint, no database migration needed
- [ ] **`example-social-cards`** — registers a filter on `template.head_extra` to inject auto-generated Open Graph image tags (using a simple SVG-to-PNG pipeline for blog posts without cover images). Demonstrates: filter hooks, template injection, Pillow integration

---

## Phase 21 — Container and Deployment Maturity

*The final hardening pass on the deployment story. Everything from Phase 11 (v0.2.0) is already in place — this phase refines, optimizes, and documents for production confidence.*

### 21.1 — Container Image Optimization

- [ ] **Layer audit:** Review every `COPY` and `RUN` instruction. Combine layers where possible. Ensure the `.containerignore` excludes all non-essential files (tests, docs, example plugins, translations source files, `.git`)
- [ ] **Image size profiling:** Measure layer sizes with `podman image tree`. Target: < 150MB total image size. Current baseline TBD — measure and document
- [ ] **Distroless evaluation:** Evaluate switching the runtime stage from `python:3.12-slim` to a distroless Python image (Google's `distroless/python3` or Chainguard's Python image). Trade-off: smaller attack surface and image size vs. no shell for debugging. If adopted, add a separate `debug` stage with a shell for troubleshooting
- [ ] **Build caching:** Optimize the Containerfile for Docker/Podman layer caching — `requirements.txt` copied and installed before application code so dependency layer is cached across code changes
- [ ] **Multi-platform build verification:** CI builds and tests on both `linux/amd64` and `linux/arm64`. Verify Pillow, nh3, and argon2-cffi compile correctly on both architectures
- [ ] **Startup optimization:** Measure cold start time (container start → first successful `/healthz` response). Target: < 5 seconds. Profile and optimize if slower

### 21.2 — Health and Readiness

- [ ] **Separate health endpoints:**
  - `/healthz` — liveness probe (existing). Returns 200 if the process is alive. No DB check (avoids false negatives from transient DB locks)
  - `/readyz` — readiness probe (new). Returns 200 if the app can serve requests: DB connectable, migrations current, photo directory writable. Returns 503 with diagnostic JSON if not ready
- [ ] **Startup probe:** `/readyz` with relaxed timing for initial DB migration and FTS index build
- [ ] **Health in compose.yaml:** Update the health check to use `/healthz` for liveness and add a commented-out Kubernetes-style readiness probe section

### 21.3 — Container Security Scanning

- [ ] Add Trivy container scan to CI pipeline (scan the built image for OS and Python package CVEs)
- [ ] Fail the pipeline on CRITICAL and HIGH vulnerabilities
- [ ] Document the remediation process for base image CVEs (rebuild with `--pull` and `--no-cache`)
- [ ] Add `cosign` image signing to the publish workflow (signs the GHCR image with a keyless signature for supply chain verification)

### 21.4 — Deployment Documentation

- [ ] **Production deployment guide:** New `docs/PRODUCTION.md` covering:
  - Recommended reverse proxy configuration (Caddy, Nginx, Traefik) with security headers, TLS, and rate limiting at the proxy layer
  - Firewall rules (only expose 443, restrict admin to VPN/Tailscale at the network level)
  - Resource sizing (CPU, RAM, disk for 100/1K/10K monthly visitors)
  - SQLite concurrency limits and when to consider PostgreSQL migration (not in v0.3.0 scope, but documented as a future path)
  - Log aggregation setup (journalctl, Loki, CloudWatch)
  - Monitoring setup (Prometheus + Grafana dashboard template, or Uptime Kuma for simple checks)
  - Backup automation (cross-reference Phase 17)
  - Upgrade procedure (pull new image → backup → migrate → restart → verify)
- [ ] **Kubernetes / Nomad deployment examples:** Commented-out example manifests (not officially supported, but the image is designed to work in orchestrated environments)

### 21.5 — Release Publication (Container is the Shipping Artifact)

*The container image — not source-tree installs — is the canonical v0.3.0 release. Every
release of this GitHub project ships at minimum as a published OCI image. Source installs
remain supported for development, but the container is the artifact the docs, the
deployment guide, and the support matrix point at.*

- [ ] **GHCR is the release surface:** Every tagged release publishes to
      `ghcr.io/<owner>/resume-site` via the existing CI workflow (`.github/workflows/ci.yml`,
      `publish` job). No tag is considered released until the image is pushed and pullable.
- [ ] **Multi-arch image:** Each release ships `linux/amd64` and `linux/arm64` manifests
      under the same tag (already wired via `docker/build-push-action` + QEMU). Verify both
      architectures pull and start cleanly before promoting any `vN.N.N` tag to `latest`.
- [ ] **Tag matrix per release:** Push three immutable tags plus one moving tag for every
      stable release: `vMAJOR.MINOR.PATCH`, `vMAJOR.MINOR`, `vMAJOR`, and `latest`. The
      moving `:main` tag (already produced by `publish-main`) is for tracking trunk only —
      never recommended in production docs.
- [ ] **Release notes link to the image:** Every GitHub Release entry must include the
      exact pull command (`podman pull ghcr.io/<owner>/resume-site:vX.Y.Z`), the image
      digest (`sha256:...`), and the `cosign verify` command for the signed image (Phase
      21.3). Don't ship a release without these three lines.
- [ ] **Smoke-test the published image, not the source:** The release checklist runs
      `podman run` against the freshly-published GHCR tag (with a minimal `config.yaml`)
      and verifies `/healthz` and `/readyz` before announcing the release. This catches
      registry-side regressions (auth, manifest, multi-arch missing variants) that source
      tests miss.
- [ ] **Document the container as the recommended install path:** Update `README.md` and
      `docs/PRODUCTION.md` so the first install instruction is `podman pull` /
      `docker pull` from GHCR. Source-tree install drops to a "Development" sub-section.
      Quadlet / compose examples reference the GHCR image by digest-pinned tag.
- [ ] **Stop-ship gate:** A failed publish (CI red on `publish` job, image not pullable,
      cosign verification failure, or smoke test failure on the published image) is a
      release blocker. Re-tag and re-run rather than back-fill a broken release.

---

## Phase Sequencing

```
Phase 12  (Code Optimization + Static Analysis) ──┐
Phase 13  (Security + Fuzz/DAST)                 ──┤── Run in parallel. These are the foundation.
Phase 18  (Observability — full scope)            ──┘   Profiling (18) informs optimization (12).
                                                        Fuzz testing (13) feeds edge cases (18).
                                                        Static analysis (12) feeds CI gates (18).

Phase 14  (Admin Completion)       ──── After 12+13 stabilize the core.
Phase 15  (Multilingual Content)   ──── After 14 (admin UI for translations).

Phase 16  (REST API)               ──── After 13.4 (token auth) + 12 (optimized services).
Phase 17  (Backups)                ──── After 16 (API backup trigger endpoint).

Phase 19  (Webhooks)               ──── After 16 (API event sources) + 12 (service decoupling).
Phase 20  (Plugins)                ──── Last feature phase. Builds on 19 (event bus).

Phase 21  (Container Maturity)     ──── Final phase. Image is built after all features land.
```

### Parallel Work Streams

```
Stream A (Core Quality + Observability):  12 → 13 → 18 ──────────────────── → 21
Stream B (Admin + Content):               ─────────────── → 14 → 15 ──────── → 21
Stream C (API + Events):                  ─────────────────────── → 16 → 17 → 19 → 20 → 21
```

Streams A and B can run concurrently after Phase 12's query optimization and Phase 13's CSP work stabilize. Stream C starts once token auth (13.4) and the optimized service layer (12.2) are complete.

**Phase 18 is not a single sprint.** It spans the entire release as a continuous practice:
- 18.1–18.3 (logging, metrics, profiling) start with Phase 12 — you need measurement before optimization
- 18.4 (Playwright tests) runs alongside Phase 14 (admin features to test)
- 18.5–18.6 (baselines, load tests) run after Phase 12 optimization to capture post-optimization numbers
- 18.7 (failure mode tests) run alongside Phase 13 (security scenarios)
- 18.8 (mutation testing) runs after all test suites are written
- 18.9–18.12 (error tracking, alerting, dashboards, synthetic monitoring) finalize alongside Phase 21
- 18.13–18.14 (edge case methodology, runbook) are living documents updated throughout

---

## New Dependencies (v0.3.0)

| Package | Purpose | Phase | Runtime/Dev |
|---------|---------|-------|-------------|
| `sortablejs` (CDN) | Drag-and-drop reordering in admin | 14 | Runtime (frontend) |
| `ruff` (dev only) | Python linter + formatter | 12 | Dev |
| `bandit` (dev only) | Security static analysis | 12 | Dev |
| `vulture` (dev only) | Dead code detection | 12 | Dev |
| `pre-commit` (dev only) | Git hook framework | 12 | Dev |
| `detect-secrets` (dev only) | Credential leak prevention | 12 | Dev |
| `hypothesis` (dev only) | Property-based / fuzz testing | 13 | Dev |
| `playwright` (dev only) | Browser-based testing | 18 | Dev |
| `locust` (dev only) | Load testing and performance regression | 18 | Dev |
| `mutmut` (dev only) | Mutation testing for test quality validation | 18 | Dev |
| None (custom) | Metrics endpoint (no prometheus_client — custom lightweight implementation) | 18 | Runtime |
| None (custom) | Event bus and webhook delivery (stdlib `threading`) | 19 | Runtime |
| None (custom) | Plugin loader (stdlib `importlib`) | 20 | Runtime |

**Dependency philosophy for v0.3.0:** Add no new runtime Python dependencies. The event bus, metrics endpoint, webhook delivery, plugin loader, and API framework are all built with Flask and the stdlib. All new Python packages are dev-only (linting, testing, profiling) and never ship in the container image. Sortable.js is the only new frontend dependency (CDN-loaded). This keeps the supply chain narrow, the container image small, and the attack surface minimal.

---

## New Database Migrations (v0.3.0)

| Migration | Tables/Changes | Phase |
|-----------|---------------|-------|
| `005_indexes.sql` | Add indexes on page_views, blog_posts, reviews, photos, contacts, activity_log | 12 |
| `006_api_tokens.sql` | `api_tokens` table | 13 |
| `007_fts5.sql` | FTS5 virtual table for admin search | 14 |
| `008_content_translations.sql` | Translation junction tables for all content types | 15 |
| `009_webhooks.sql` | `webhooks` and `webhook_deliveries` tables | 19 |
| `010_plugins.sql` | `plugins` table (name, version, enabled, installed_at) | 20 |

---

## New CLI Commands (v0.3.0)

| Command | Purpose | Phase |
|---------|---------|-------|
| `manage.py query-audit` | EXPLAIN QUERY PLAN on all cataloged queries | 12 |
| `manage.py complexity-report` | Top 20 most complex functions (cyclomatic) | 12 |
| `manage.py generate-api-token` | Create API token with scoped access | 13 |
| `manage.py rotate-api-token` | Rotate an existing API token | 13 |
| `manage.py rotate-secret-key` | Generate new Flask secret key | 13 |
| `manage.py backup` | Create timestamped backup archive | 17 |
| `manage.py restore` | Restore from backup archive | 17 |
| `manage.py profile` | Run performance profiling against test client | 18 |
| `manage.py mutation-report` | Run mutmut and generate HTML report | 18 |
| `manage.py rebuild-search-index` | Rebuild FTS5 index | 14 |
| `manage.py translations export-content` | Export translatable content as .po | 15 |
| `manage.py translations import-content` | Import translated content from .po | 15 |
| `manage.py plugins list/enable/disable/validate` | Plugin management | 20 |

---

## New Documentation (v0.3.0)

| Document | Purpose | Phase |
|----------|---------|-------|
| `THREAT_MODEL.md` | Formal threat model and security controls | 13 |
| `PERFORMANCE.md` | Performance baselines, failure modes, optimization log | 18 |
| `tests/TESTING_STANDARDS.md` | Edge case checklist and test methodology | 18 |
| `docs/OBSERVABILITY_RUNBOOK.md` | When and how to use each observability tool | 18 |
| `docs/alerting-rules.yaml` | Prometheus alerting rules with runbook links | 18 |
| `docs/grafana-dashboard.json` | Pre-built Grafana dashboard (importable) | 18 |
| `docs/PENTEST_CHECKLIST.md` | Manual penetration testing guide | 13 |
| `PLUGINS.md` | Plugin development guide, hook reference | 20 |
| `docs/PRODUCTION.md` | Production deployment, monitoring, synthetic checks | 21 |
| `docs/API.md` | API quickstart (complements OpenAPI spec) | 16 |
| `openapi.yaml` | OpenAPI 3.0 specification | 16 |
| `.pre-commit-config.yaml` | Pre-commit hook definitions | 12 |
| `pyproject.toml` | ruff, bandit, mutmut, vulture configuration | 12 |
| `tests/loadtests/locustfile.py` | Load test scenarios | 18 |
| `tests/loadtests/thresholds.json` | CI performance regression thresholds | 18 |
| `tests/synthetic/healthcheck.sh` | Level 2 synthetic monitoring script | 18 |
| `tests/synthetic/monitor.py` | Level 3 Playwright synthetic monitoring | 18 |

---

## Test Coverage Targets

| Phase | New Test Files | Target Coverage |
|-------|---------------|----------------|
| 12 | `test_performance.py` (benchmark assertions), static analysis in CI | Baseline established |
| 13 | `test_api_auth.py`, expand `test_security.py`, `test_fuzz.py` (Hypothesis property-based tests), DAST scan in CI | 75% overall |
| 14 | `test_admin_advanced.py` (drag-drop, bulk ops, search, theme editor) | 78% |
| 15 | expand `test_i18n.py` (content translations) | 80% |
| 16 | `test_api.py` (all endpoints, auth, pagination, errors) | 83% |
| 17 | `test_backup.py` (backup/restore cycle) | 84% |
| 18 | `test_observability.py` (metrics format, log structure), `test_resilience.py` (failure modes), `test_edge_cases.py` (retroactive edge case pass), Playwright tests, load tests in CI, mutation testing (informational) | 90% |
| 19 | `test_webhooks.py` (delivery, signing, retry, auto-disable) | 91% |
| 20 | `test_plugins.py` (discovery, loading, hooks, lifecycle) | 92% |
| 21 | Container smoke tests in CI | 92%+ |

**Testing quality metrics (in addition to line coverage):**

| Metric | Target | Tool |
|--------|--------|------|
| Line coverage | ≥ 92% | pytest-cov |
| Branch coverage | ≥ 85% | pytest-cov |
| Mutation score | ≥ 70% | mutmut |
| Fuzz test coverage | Every user-input function | hypothesis |
| Failure mode coverage | All documented failure modes pass | test_resilience.py |
| Edge case coverage | Checklist complete per TESTING_STANDARDS.md | Manual review |
| DAST findings | Zero MEDIUM+ in ZAP baseline | OWASP ZAP |
| Static analysis | Zero findings in ruff + bandit | ruff, bandit |
| Performance regression | Zero regressions beyond 20% threshold | locust |

Ratchet: CI `--cov-fail-under` increments with each phase. Mutation score ratchets annually.

---

## Out of Scope (v0.4.0+)

These are explicitly deferred. The v0.3.0 architecture is designed to make them possible:

- Multiple admin / viewer accounts (API token auth, activity log `admin_user` field, plugin settings per-user prepare for this)
- Public-facing login (CSRF, session hardening, and API auth prepare for this)
- Role-based access control (API scope model generalizes to role-based permissions)
- SaaS / multi-tenant mode (plugin architecture and settings registry per-namespace prepare for this)
- OAuth2 / OIDC provider integration (token auth pattern extends to delegated auth)
- PostgreSQL backend option (service layer abstracts DB access; migration system supports multiple backends with driver switch)
- Real-time features (WebSocket support for live admin collaboration)
- Plugin sandboxing (currently plugins have full access; v0.5.0+ could add process isolation)

---

## Version Tagging

- `v0.2.0` — baseline (current main branch)
- `v0.3.0-alpha.N` — tagged as phase groups complete for testing
- `v0.3.0-beta.1` — all features complete, optimization and polish pass
- `v0.3.0-rc.1` — feature freeze, testing and documentation only
- `v0.3.0` — stable release, published to GHCR

---

## Success Criteria

v0.3.0 is ready to ship when:

**Code Quality:**
1. Every query in the codebase has been run through `EXPLAIN QUERY PLAN` and optimized
2. `ruff check` and `ruff format --check` pass with zero findings
3. `bandit` scan passes with zero MEDIUM+ findings
4. `vulture` dead code report reviewed and all items resolved or documented
5. Every public function has type hints and a docstring
6. No function exceeds cyclomatic complexity of 15

**Security:**
7. CSP is in enforcement mode with zero violations on all pages
8. OWASP ZAP baseline scan passes with zero MEDIUM+ findings
9. All Hypothesis fuzz tests pass with no crashes or unexpected exceptions
10. `THREAT_MODEL.md` is complete and reviewed against OWASP Top 10
11. `docs/PENTEST_CHECKLIST.md` has been executed manually at least once

**Features:**
12. All v0.2.0 deferred admin features are functional and tested
13. The REST API passes a full integration test suite with auth, pagination, and error handling
14. Backup and restore complete a round-trip without data loss
15. The plugin system loads, enables, and disables example plugins without error
16. Webhook delivery succeeds with HMAC verification on a test endpoint

**Observability:**
17. `/metrics` endpoint produces valid Prometheus exposition format with all defined metrics
18. Structured JSON logs are emitted for every request with timing data and request ID
19. `X-Request-ID` header present on every response and correlated in logs
20. Error categorization is implemented — every error is classified and tracked
21. Admin dashboard "System Health" panel shows live status

**Testing:**
22. Test line coverage is ≥ 92% with zero skipped security tests
23. Test branch coverage is ≥ 85%
24. Mutation score is ≥ 70% (mutmut) with all surviving mutants reviewed
25. All failure mode tests pass (SMTP down, disk full, DB locked, corrupted uploads)
26. Edge case checklist is complete for every function accepting user input
27. Load test with 50 concurrent users shows zero 500 errors and p95 < 500ms

**Performance:**
28. CI performance regression gate passes (no endpoint regresses beyond 20% threshold)
29. Container image is < 150MB, starts in < 5 seconds, passes Trivy scan with zero CRITICAL/HIGH CVEs
30. Lighthouse score ≥ 95 on Performance for the landing page
31. `PERFORMANCE.md` documents baselines for all top routes with before/after optimization data

**Documentation:**
32. `PERFORMANCE.md`, `THREAT_MODEL.md`, `PLUGINS.md`, `docs/PRODUCTION.md`, `docs/OBSERVABILITY_RUNBOOK.md`, `tests/TESTING_STANDARDS.md`, `docs/alerting-rules.yaml`, and `docs/grafana-dashboard.json` are complete
33. `docs/PENTEST_CHECKLIST.md` exists and has been used at least once
34. Synthetic monitoring scripts (`tests/synthetic/`) are functional and documented

**Release / Distribution:**
35. The `v0.3.0` tag has published a multi-arch (`linux/amd64` + `linux/arm64`) container image to `ghcr.io/<owner>/resume-site` and the image is publicly pullable
36. The published image carries the `vMAJOR.MINOR.PATCH`, `vMAJOR.MINOR`, `vMAJOR`, and `latest` tags, all pointing at the same digest
37. The image is signed with `cosign` (keyless / OIDC) and `cosign verify` succeeds against a clean machine
38. The GitHub Release notes for `v0.3.0` include the exact `podman pull` command, the image digest, and the `cosign verify` command
39. A clean-machine smoke test (`podman run` against the published GHCR tag with a minimal `config.yaml`) reaches `/healthz` and `/readyz` successfully before the release is announced
