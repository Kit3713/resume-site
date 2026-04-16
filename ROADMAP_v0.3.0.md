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

- [x] **Query audit:** Catalogued in the `_AUDIT_QUERIES` table in `manage.py` (see the `query_audit` command below) — ten hot-path queries covering every model + service `db.execute()` that fires on public routes, with `expected_scan` flags for queries where a full scan is correct (e.g. the 30-row `settings` table).
- [x] **Index pass:** `migrations/005_indexes.sql` — every index from the roadmap bullet, each with an inline rationale comment. `page_views`, `blog_posts(status, published_at)`, `blog_post_tags` junction indexes, `reviews(status, display_tier)`, `photos(display_tier, sort_order)`, `admin_activity_log(created_at)`, `contact_submissions(ip_address, created_at)` all present. All use `IF NOT EXISTS` for safe re-runs.
- [x] **Settings cache:** `app/services/settings_svc.py:_settings_cache` — module-level dict + `threading.Lock`, `DEFAULT_SETTINGS_TTL = 30.0` seconds, `invalidate_cache()` called from every admin settings-save path. Tests: `tests/test_settings_cache.py` (8 tests including the cache-bust contract).
- [x] **Batch N+1 elimination:** `get_skill_domains_with_skills()` in `app/models.py:96-128` — two queries total (domains, then a single `WHERE domain_id IN (...)` for all skills). `get_tags_for_posts()` in `app/services/blog.py` gets the same batch treatment. Regression test suite `tests/test_n_plus_1.py` (7 tests) instruments `sqlite3.set_trace_callback` to assert the exact query counts — locks the contract against future regressions.
- [x] **Connection pooling evaluation:** Per-request `sqlite3.connect()` is retained; the PRAGMA audit lives in `app/db.py:_PER_CONNECTION_PRAGMAS` (foreign_keys, busy_timeout, synchronous, temp_store, cache_size, mmap_size). `tests/test_db_pragmas.py` (5 tests) locks in every pragma value so a silent removal would fail CI.
- [x] **EXPLAIN QUERY PLAN:** `manage.py query-audit` — runs `EXPLAIN QUERY PLAN` on every cataloged query, marks each `✓ INDEX` / `~ OK-SCAN` / `✗ SCAN`, exits non-zero on unexpected full scans so CI can pick it up.
- [x] **Write a migration** — shipped as `migrations/005_indexes.sql` (see the Index pass bullet above).

### 12.2 — Python Code Optimization

**Problem:** The codebase is functional and well-documented but has never had a performance-focused review. Some patterns are repeated across services. Error handling is inconsistent.

- [x] **Import audit:** Done as part of the Phase 12.2 sweep (commit c785890). Multiple services now carry `from __future__ import annotations`; no circular-import issues surfaced by ruff `F401` / `I001` in CI.
- [x] **Hot path profiling:** `PERFORMANCE.md` (118 lines) — baseline p50/p95/query-count/response-size per top-5 route, plus a "regression threshold" section marked as CI-blocking. Measurement harness is `scripts/benchmark_routes.py` (216 lines) — spins up a fresh app against a seeded SQLite, runs the test client, reports the numbers.
- [x] **Template rendering:** Audit complete — no Jinja template triggers a DB call. The only template-level callables are `csrf_token()`, `str.strip()`, `str.split(',')`, and the `time_ago` filter (pure Python). `site_settings` / `site_config` come from the single context processor in `app/__init__.py:506-532`, which reads through the 30 s settings cache (Phase 12.1). Nested loops in `services.html` (domains → skills → tools) and `blog_index.html` (posts → tags) iterate data pre-batched by `get_skill_domains_with_skills()` / `get_tags_for_posts()` — the N+1 contract is locked in by `tests/test_n_plus_1.py`. Per-route query counts (6–10) in `PERFORMANCE.md` confirm there are no hidden template queries. No `{% cache %}` opportunities identified — every page is either cached upstream by `Cache-Control` headers (`app/__init__.py:491-495`) or too dynamic (admin pages, per-locale public pages) to benefit from Jinja fragment caching at this scale.
- [x] **String handling:** No f-string SQL construction anywhere in `app/`. Enforced at CI time by the "Check for unsafe SQL patterns" step in `.github/workflows/ci.yml` (greps for `execute(f"`, `.format` near execute/select/insert/update/delete — fails the build on any hit).
- [x] **Pillow pipeline:** `app/services/photos.py` uses context managers on every `Image.open()`, saves JPEG with `progressive=True`, strips EXIF on save, closes intermediates explicitly. `tests/test_photo_processing.py` (5 tests) covers the upload path. WebP secondary format is generated at `photos.py:188-189`.
- [x] **Service layer DRY pass:**
  - Slug generation → `app/services/text.py` (`slugify()`, 43 lines, reused across blog and tags — `tests/test_text_utils.py` has 79 lines covering it).
  - Pagination → `app/services/pagination.py` (93 lines, reused by blog + admin lists; `tests/test_pagination.py` has 105 lines covering the boundaries).
  - (Deferred: CRUD base mixin and sort-order utility — the current services are small enough that a premature base class would obscure the SQL; revisit when REST-API write handlers are added in Phase 16.)
- [x] **Error handling standardization:** `app/exceptions.py` (63 lines) defines `DomainError` plus `ValidationError` / `NotFoundError` / `DuplicateError`, all multi-inheriting from the matching stdlib types (`ValueError` / `LookupError`) so pre-existing catches keep working during transition. `tests/test_exceptions.py` (107 lines) covers the contract. Orthogonal operational categorisation lives in `app/errors.py` (Phase 18.9). Bare `except Exception: pass` patterns replaced with `contextlib.suppress(OSError)` across `app/services/*` and the 500 handler; analytics `except` is the one documented exemption.
- [x] **Type hints:** Public function signatures across `app/models.py` and every `app/services/*.py` module now carry PEP 604 annotations (`X | None`) with `from __future__ import annotations` at the top of each file. Canonical types: `sqlite3.Connection` for DB handles, `sqlite3.Row` for result rows, `collections.abc.Iterable` / `Mapping` for argument collections (never `typing.Iterable`, ruff UP035 enforces this). 835-test suite green after the sweep; not yet enforced by mypy in CI (v0.4.0 concern).
- [x] **Docstring audit:** AST-walk across `app/` confirms every module, class, and public function/method (including non-trivial `__init__`s) carries a docstring. Google-style formatting is consistent; dunder / underscore-prefixed helpers are exempt. The audit script (`ast.get_docstring` over `ast.FunctionDef` / `ClassDef` / `Module`) reports zero gaps.

### 12.3 — Frontend Optimization

**Problem:** CSS is a single 2514-line file. JavaScript is two files with no minification. No asset fingerprinting for cache busting. GSAP loaded from CDN on every page.

- [x] **CSS audit:** Audited `style.css` (2528 lines, 30 named sections). No unused BEM block components found — all major sections are referenced by templates. Custom property audit identified 28 hardcoded color values bypassing the theming system. Extracted 13 semantic custom properties (`--color-success`, `--color-warning`, `--color-danger`, `--color-muted`, plus `-bg` variants, `--color-text-inverse`, `--color-overlay`, `--color-overlay-light`) into both dark and light theme blocks, then replaced all 28 hardcoded references. File already well-organized with clear section headers; no splitting needed at this scale.
- [x] **Asset fingerprinting:** `app/assets.py` provides `static_hashed()` template function that appends `?v=<sha256[:8]>` to static asset URLs. Content hash computed once per process lifetime (thread-safe cache). In debug mode, no hash appended. Templates updated to use `static_hashed()` for `style.css`, `main.js`, and `swagger-init.js`. Static assets already served with `Cache-Control: public, max-age=2592000, immutable`.
- [ ] **CSS minification:** Add a build step (or Gunicorn middleware) that serves minified CSS in production. Preserve source CSS for development
- [ ] **JavaScript audit:** Profile `main.js` for unused functions, redundant event listeners, and GSAP animations that fire on hidden/off-screen elements. Audit `admin.js` for the same
- [ ] **JavaScript minification:** Same as CSS — minified in production, source in development
- [ ] **Asset fingerprinting:** Append a content hash to static asset URLs (`style.abc123.css`) so `Cache-Control: immutable` works correctly across deployments. Implement via Flask's `url_for('static', ...)` override or a manifest file
- [x] **GSAP optimization:** Audited — GSAP is already excluded from admin templates (admin uses `base_admin.html` which doesn't load GSAP CDN). All GSAP usage in `main.js` is guarded by `typeof gsap !== 'undefined'` so pages degrade gracefully. No SPA navigation so no `kill()` needed. All ScrollTrigger registrations are on persistent elements.
- [x] **Critical CSS / preload:** Added `<link rel="preload" as="style">` hint for the main stylesheet in `base.html` to eliminate render-blocking delay. Full critical CSS inlining deferred — at 58KB the stylesheet is small enough that the preload hint + fingerprinted caching is sufficient.
- [x] **Image optimization pipeline:** Responsive variants (640w, 1024w) generated during upload via `_generate_responsive_variants()` in `app/services/photos.py`. WebP secondary format generated for all non-GIF uploads. New `<picture>` macro in `app/templates/components/picture.html` with WebP `<source>` and `srcset` (640w/1024w/2000w) with `sizes` attribute. Applied to portfolio grid, featured portfolio, and landing page. `delete_photo_file()` updated to clean up all variants. LQIP blur-up deferred.
- [x] **Font loading:** `app/templates/base.html:45-46` declares `<link rel="preconnect" href="https://fonts.googleapis.com">` + `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>`; line 55 appends `&display=swap` to the CSS URL. Admin base template has the same pair. Self-hosting the five font pairings stays deferred — operators preferring a zero-CDN stance can still set `custom_css` to override.

### 12.4 — Template Optimization

- [x] **Template inheritance audit:** All 15 admin templates extend `base_admin.html` (separate from public CSS/JS). Admin login intentionally extends `base.html` for navbar access. No redundant block overrides found. All 12 public templates extend `base.html`.
- [x] **Macro extraction:** Created `components/empty_state.html` macro (repeated in 10+ templates). `components/picture.html` responsive image macro (Phase 12.3). Pagination has only one occurrence — not extracted.
- [x] **SEO template audit:** Added `{% block canonical %}`, `{% block og_description %}`, `{% block og_type %}`, `{% block og_extra %}`, and `{% block jsonld %}` to `base.html`. Landing page: Person JSON-LD schema. Blog posts: BlogPosting JSON-LD schema, canonical URL, `og:description` from summary, `article:author`, `article:tag` for each tag. hreflang already present. Conditional rendering clean — no hidden-content anti-patterns found.

### 12.5 — Static Analysis and Code Quality Enforcement

**Problem:** The codebase has no automated code quality gates. Code review catches style issues but misses patterns that tooling detects instantly — unused variables, unreachable code, overly complex functions, security anti-patterns, and inconsistent formatting. Professional codebases enforce quality mechanically, not manually.

- [x] **Linter (ruff):** `pyproject.toml:[tool.ruff.lint]` selects `E/W/F/I/B/S/C4/SIM/UP` — exactly the roadmap set. Per-file ignores for `tests/**` (assert/hardcoded-password) and `manage.py` (print/subprocess) are documented inline with justifications. Line length 100. Active in pre-commit (`.pre-commit-config.yaml`) and in CI (`.github/workflows/ci.yml`).
- [x] **Formatter (ruff format):** `pyproject.toml:[tool.ruff.format]` — single-quote style, LF line endings. `ruff format --check` is the second step in the CI "quality" job; it also runs via pre-commit. Entire tree already formatted (verified with `ruff format --check .` → all pass).
- [x] **Security scanner (bandit):** `pyproject.toml:[tool.bandit]` — excludes tests/ (test fixtures legitimately carry credentials). Runs at severity `-ll` (low+) in both pre-commit and CI. Zero MEDIUM+ findings on the current tree. `# nosec` suppressions carry an explanatory comment alongside the rule ID (e.g. the `B202` on `tarfile.extractall` in `app/services/backups.py` points at the `_safe_extract` contract).
- [x] **Pre-commit hooks:** `.pre-commit-config.yaml` ships ruff (lint + format), bandit, detect-secrets, trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-added-large-files, check-merge-conflict, mixed-line-ending, check-case-conflict. `CONTRIBUTING.md` documents the install + `pre-commit run --all-files` flow. *(Minor gaps vs the roadmap wish-list: `pip-audit` runs in CI but not yet as a pre-commit hook; `check-json` omitted because there are no committed `.json` config files.)*
- [x] **CI quality gate:** `.github/workflows/ci.yml` "quality" job runs `ruff check .`, `ruff format --check .`, `bandit -r app/ -c pyproject.toml -ll`, and the SQL-interpolation grep guard — all four block the build. Test job `needs: quality`, so failures halt the whole pipeline. `pip-audit` runs in the same workflow but with `continue-on-error: true` (advisory); promoting it to blocking is a ratchet-up deferred until the existing advisory findings are triaged.
- [x] **Complexity tracking:** `C90` added to ruff `select` in `pyproject.toml`; `[tool.ruff.lint.mccabe] max-complexity = 15`. Two functions exceed the threshold — `create_app` (app factory, 28 — sequential setup) and `config_validate` (CLI validator, 28 — sequential field checks) — both suppressed with `# noqa: C901` and inline justification. `manage.py complexity-report` command (shipped earlier) prints the top N functions by cyclomatic complexity.
- [x] **Dead code detection:** `vulture app/ manage.py vulture_allowlist.py --min-confidence 80` runs clean. One false positive (`close_db(exception=None)` — Flask teardown signature) is allow-listed in `vulture_allowlist.py`. `[tool.vulture]` config in `pyproject.toml` covers `ignore_decorators` (route/handler/fixture decorators) and `ignore_names` (factory/lifecycle methods). CI quality job runs vulture as an advisory step (`continue-on-error: true`); ratchet to blocking in v0.4.0.

---

## Phase 13 — Security Hardening (Defense-in-Depth)

*v0.2.0 established the security baseline. v0.3.0 elevates it to defense-in-depth with proactive threat modeling, enforcement-mode CSP, and automated vulnerability scanning.*

### 13.1 — Threat Model Document

- [x] `THREAT_MODEL.md` produced with all sections: attack surface (7 categories: public routes, admin routes, API routes, file upload, SMTP relay, SQLite, container boundary), threat actors (4: anonymous, API consumer, compromised proxy, supply chain), mitigations table (20+ controls mapped to phases), OWASP Top 10 (2021) mapping (all 10 items), residual risks with acceptance rationale (8 documented), incident response outlines for DB compromise, container breach, API token leak, and spam/abuse flood

### 13.2 — CSP Enforcement

**Problem:** v0.2.0 ships CSP in `Content-Security-Policy-Report-Only`. This detects violations but doesn't block them.

- [x] **Nonce infrastructure (Phase 13.2 part 1):** Per-request nonce generated via `secrets.token_urlsafe(16)` in `_assign_csp_nonce()` before-request handler, stored in `g.csp_nonce`, injected into template context. All inline `<script>` and `<style>` tags across `base.html`, `base_admin.html`, `content_edit.html`, `blog_edit.html`, `api_tokens_reveal.html`, and `settings.html` carry `nonce="{{ csp_nonce }}"`. CSP header updated to `'nonce-<value>'` in both `script-src` and `style-src`, replacing `'unsafe-inline'`. Still in `Report-Only` mode.
- [x] **CSP enforcement (Phase 13.2 part 2):** Header switched from `Content-Security-Policy-Report-Only` to enforced `Content-Security-Policy`. `report-uri /csp-report` directive added. `/csp-report` POST endpoint (`app/routes/public.py`) logs violation details (directive, blocked URI, document URI) at WARNING level on the `app.security` logger. Endpoint is CSRF-exempt (browsers send reports without tokens). Excluded from analytics tracking. Security test updated to assert enforced header + nonce presence + no Report-Only fallback.
- [ ] Test exhaustively: every public page, every admin page, every GSAP animation, every font load, every CDN script (manual test — deferred to Playwright in Phase 18.4)

### 13.3 — Request Filtering (WAF-Lite)

- [x] **Request filter:** `app/services/request_filter.py` — `before_request` handler inspecting: path traversal (`../`, `..%2f`, `%00`, null bytes in both decoded and raw paths), SQL injection probes (`' OR`, `UNION SELECT`, `; DROP` in query strings), oversized request bodies (>10 MB), and missing Content-Type on non-empty POST/PUT/PATCH bodies. Returns 400 (not 403). Logged at WARNING with method, path, IP, and truncated user-agent.
- [x] **Filter settings:** `request_filter_enabled` (default `true`) and `request_filter_log_only` (default `false`) in the Security category of SETTINGS_REGISTRY. Log-only mode passes requests through but still logs violations for tuning.
- [x] **Tests:** 9 tests in `tests/test_request_filter.py` covering path traversal, encoded traversal, null bytes, SQL injection, UNION SELECT, normal requests, disabled filter, and log-only mode.

### 13.4 — API Authentication (Token-Based)

*This phase establishes the auth model that Phase 16 (REST API) builds on.*

- [x] **Token model:** `api_tokens` table in `migrations/007_api_tokens.sql` — `id`, `token_hash` (SHA-256 UNIQUE), `name`, `scope` (comma-separated), `created_at`, `expires_at` (nullable), `last_used_at`, `revoked`, `created_by`. Three indexes: `idx_api_tokens_hash` (auth hot path), `idx_api_tokens_name` (rotate-by-name), `idx_api_tokens_created_at` (admin list). Note: `006` was already taken by `login_attempts`, so this is `007`.
- [x] **Service layer:** `app/services/api_tokens.py` — `generate_token`, `verify_token`, `rotate_token`, `revoke_token`, `list_tokens`, `get_token`, `purge_expired`, `parse_expires`. Stdlib-only (secrets + hashlib). Scope semantics are EXPLICIT — `write` does NOT imply `read`; a `@require_api_token('read')` route rejects a write-only token. Constant-time hash comparison via `secrets.compare_digest` after the index equality lookup.
- [x] **Token generation CLI:** `manage.py generate-api-token --name ... --scope ... [--expires 90d|7d|24h|never|ISO-date]`. Prints a loud one-time reveal banner; only the hash hits disk. Emits `Events.API_TOKEN_CREATED` with a redacted payload (no raw, no hash).
- [x] **Token rotation CLI:** `manage.py rotate-api-token --name ...` — generates a fresh token inheriting scope + expires_at from the newest active match, marks the old row revoked, prints the new raw value once.
- [x] **Revoke / list CLI:** `manage.py revoke-api-token --id N` and `manage.py list-api-tokens` (table view with name / scope / status / expires / last used).
- [x] **Admin UI:** `/admin/api-tokens` lists every token (active + revoked). `POST /admin/api-tokens/generate` + `GET /admin/api-tokens/reveal` implement the one-time reveal via session-held-and-popped raw value — refresh / back-button cannot re-show. `POST /admin/api-tokens/<id>/revoke` flips the soft-delete bit. All actions land in the activity log under category `api_tokens`. Nav link added; the existing review-tokens active-state collision (`'tokens' in request.endpoint`) was fixed to `startswith('admin.tokens')` so both pages highlight correctly.
- [x] **Auth middleware:** `@require_api_token(scope='read')` decorator in `app/services/api_tokens.py`. Returns 401 (`missing` / `malformed` / `invalid` / `revoked` / `expired`) or 403 (`insufficient_scope`), with `WWW-Authenticate: Bearer` on 401s. On success, populates `flask.g.api_token` with a `VerifiedToken` namedtuple and updates `last_used_at`. Refuses tokens presented as query strings to avoid leakage through access logs.
- [x] **Rate limiting primitives:** `rate_limit_read` / `rate_limit_write` / `rate_limit_admin` callables exported from `app/services/api_tokens.py` read through the 30 s settings cache and return Flask-Limiter-compatible strings. Settings registry entries `api_rate_limit_read` (default 60), `api_rate_limit_write` (default 30), `api_rate_limit_admin` (default 10) land in the `Security` category. Phase 16 API routes will wire these in alongside `@require_api_token`.
- [x] **Tests:** 44 service unit tests (`tests/test_api_tokens.py`), 14 CLI tests (`tests/test_api_tokens_cli.py`), 14 admin UI tests (`tests/test_api_tokens_admin.py`). Covers generation / verification / rotation / revocation / expiry parsing / purge / decorator status codes + `WWW-Authenticate` header / session-pop one-time reveal / event payload redaction / activity-log writes.

### 13.5 — Secret Rotation and Audit

- [x] **Secret key rotation:** `manage.py rotate-secret-key` — generates a new 64-byte URL-safe key, writes directly to config.yaml via PyYAML, prints truncated old/new keys, warns that all sessions are invalidated.
- [x] **Startup security audit:** `_startup_security_audit()` in `app/services/config.py` checks: SMTP configuration (warn if missing), admin `allowed_networks` (warn if empty), `session_cookie_secure` (warn if false), database file permissions (warn if world-readable), running as root (warn). Secret key strength and password hash algorithm checks were already in `_validate_secret_key()` and the existing password_hash check.
- [x] **Dependency scanning:** `pip-audit` added to `.pre-commit-config.yaml` as a hook (v2.7.3, with `--require-hashes`). CVE response process documented in `SECURITY.md` (triage, patch, container rebuild, release, disclose).

### 13.6 — Session and Cookie Hardening

- [x] **Session storage review:** Decision documented in `SECURITY.md` "Session and Cookie Audit (Phase 13.6)": keep client-side sessions. Rationale: payload is small (<500 bytes, no secrets beyond CSRF token), server-side sessions add a dependency + table + cleanup job that don't pay for themselves at single-admin scale. Revisit in v0.4.0 if multi-user auth lands.
- [x] **Cookie audit:** Single cookie (`resume_session`) confirmed. All attributes explicitly set in `create_app()`: `SESSION_COOKIE_NAME='resume_session'`, `HTTPONLY=True`, `SAMESITE='Lax'`, `SECURE=True` (configurable for local dev). No `set_cookie()` calls anywhere in the codebase. No remember-me cookie (Flask-Login `remember=True` not used). Full audit table in `SECURITY.md`.
- [x] **Login hardening:** Sliding-window IP lockout persisted in the new `login_attempts` table (migration `006_login_attempts.sql`). `app/services/login_throttle.py` exposes `record_failed_login` / `record_successful_login` / `check_lockout` / `purge_old_attempts`. Three admin-configurable settings in the new `Security` category: `login_lockout_threshold` (default 10), `login_lockout_window_minutes` (default 15), `login_lockout_duration_minutes` (default 15). IPs are stored as the SHA-256 hash from Phase 18.1 (raw addresses never hit disk). The timer resets to the *most recent* failure in the window so an attacker can't space attempts to reset the clock. Setting the threshold to 0 disables the feature without deleting the table — fail-safe against misconfiguration. Admin login POST now runs the check before credential verification, so a correct password is refused while locked (lockout wins). Failures and lockout-rejections both emit the `security.login_failed` event (with `reason='invalid_credentials'` or `'locked'`) that Phase 19.1 wired up. `Retry-After` header on the 429 response carries the seconds-remaining for polite clients. Flask-Limiter's 5/min burst cap stays in place as a second layer.

### 13.7 — File Upload Hardening

- [x] **Upload quarantine:** Files now save to a `tempfile.mkstemp()` quarantine file in the photo directory, go through validation + optional AV scan + Pillow optimization, and only `os.replace()` to the final UUID-named path on success. The `finally` block deletes the quarantine file if any step fails — no partial files left on disk.
- [x] **Antivirus integration hook:** `upload_scan_command` setting (Security category, default empty). When set, the quarantined file is passed as the first argument to the configured command (e.g., `clamdscan`). Non-zero exit rejects the upload. Timeout of 30 seconds. Scan failures are logged and treated as rejections (fail-closed).
- [x] **EXIF stripping:** Already the default — Pillow's `save()` drops EXIF metadata unless `exif=` is passed. New `upload_preserve_exif` setting (Security category, default `false`) opts in to keeping EXIF by passing the original `img.info['exif']` through to `save()`. Privacy-by-default.

### 13.8 — Fuzz Testing

**Problem:** Unit tests verify expected inputs. Fuzz testing verifies the application doesn't crash, leak data, or behave dangerously when given unexpected, malformed, or adversarial input. This is the difference between "it works" and "it's resilient." Professional security audits always include fuzzing.

- [x] **Property-based testing with Hypothesis:** 12 tests in `tests/test_fuzz.py` (856 total). Coverage: `slugify()` (3 tests: never crashes, URL-safe output, no consecutive hyphens), `_calculate_reading_time()` (2 tests: never crashes for arbitrary text and HTML), `sanitize_html()` (3 tests: never crashes, no script/event handler output survives, explicit XSS payload suite), `_validate_magic_bytes()` (2 tests: never crashes on random bytes, rejects non-image data). HTTP layer: random paths never 500, random methods on known routes never 500. All tests use `@settings(max_examples=50-200)` for CI budget. Contact form / settings / review / API body fuzzing deferred to Phase 18.13 edge case pass.

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

- [x] **Sortable.js:** Added via CDN (`cdn.jsdelivr.net/npm/sortablejs@1.15.6`) to `base_admin.html`.
- [x] **Reorder API:** Generic `POST /admin/reorder` endpoint accepting `{"table": "<name>", "id_order": [1,3,2]}`. Table validated against `_REORDER_ALLOWLIST` (services, stats, photos, projects). Updates `sort_order` in a loop. Activity log records reorder events.
- [x] **Services page:** Sortable list with drag handles. `onEnd` callback POSTs the new order to `/admin/reorder`.
- [x] **Stats page:** Same sortable pattern on the `<tbody>` with drag handle column.
- [x] **Photos page:** Sortable grid with drag handles. Sortable.js `onEnd` callback auto-saves to `/admin/reorder`.
- [x] **Nav ordering:** `nav_order` setting (JSON array of nav keys, Navigation category). `base.html` reads the pre-parsed list from the context processor and renders nav items in the configured order. Default order preserved when setting is empty. Data-driven nav item map with per-item visibility checks.
- [x] **Homepage layout visibility:** `homepage_layout` setting (JSON array of `{section, visible}` objects, Appearance category). `index.html` builds a `hidden_sections` set and wraps each of the 8 homepage sections (hero, about, stats, services, portfolio, blog, testimonials, contact) in `{% if 'key' not in hidden_sections.items %}` conditionals. Section reordering deferred — visibility toggles are the most useful part for a single-page scroll layout.

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

- [x] **Blueprint:** `app/routes/api.py` mounted at `/api/v1/`. Registered and CSRF-exempted in `app/__init__.py` (CSRF is a browser-form mitigation; the API uses Bearer tokens on writes and public reads).
- [x] **CSRF exemption:** `csrf.exempt(api_bp)` in the factory. Covered by a regression test (`test_csrf_does_not_apply_to_api`) that POSTs to a read endpoint on a CSRF-enabled fixture and expects 405 rather than 400.
- [x] **Versioned URL prefix:** `url_prefix='/api/v1'`. Future `/api/v2/` can coexist without route-name collisions.
- [x] **Uniform error envelope:** `{"error": "<human message>", "code": "<STABLE_TAG>"}` with optional `details` dict. App-level `errorhandler(404)` and `errorhandler(405)` match on `request.path.startswith('/api/')` and return the JSON envelope (the blueprint's own errorhandlers don't fire for unmatched paths, which is why the handler lives at app level).
- [x] **Uniform pagination envelope:** `{"data": [...], "pagination": {"page", "per_page", "total", "pages"}}`. Built on `app.services.pagination.paginate`. `per_page` is clamped to `[1, 100]` via `_parse_per_page`; malformed inputs fall back to the endpoint default.
- [x] **ETag + If-None-Match:** `_conditional_response` computes a strong ETag (`"<sha256[:32]>"`) from the canonicalised JSON body (`sort_keys=True`, `separators=(',', ':')`), returns 304 on a matching `If-None-Match`, echoes the ETag and sets `Cache-Control: no-cache` on both 200 and 304 responses.
- [ ] **JSON Content-Type enforcement on POST/PUT/PATCH:** deferred to Phase 16.3 (write endpoints land then; no POST body on any current route, so the check would be no-op).
- [ ] **`Accept-Language` respected for multilingual content:** deferred to Phase 15 (user-content translation junction tables) — the query layer there will accept a `locale` argument which the API will pull from `Accept-Language`.

### 16.2 — Public Read Endpoints (No Auth Required)

- [x] `GET /api/v1/site` — site metadata (title, tagline, availability_status, hero strings, feature toggles for blog / case studies / contact form, available_locales, api_version, server_time).
- [x] `GET /api/v1/content/:slug` — single content block (id, slug, title, content, plain_text, updated_at). 404 with `NOT_FOUND` code when absent.
- [x] `GET /api/v1/services` — list via `get_visible_services`.
- [x] `GET /api/v1/stats` — list via `get_visible_stats`.
- [x] `GET /api/v1/portfolio` — paginated photos (hidden tier excluded), optional `?category=` exact-match filter. Pagination envelope on every response.
- [x] `GET /api/v1/portfolio/:id` — single visible photo. Hidden photos return 404 (not 403) so the endpoint doesn't reveal their existence.
- [x] `GET /api/v1/portfolio/categories` — distinct category names across visible photos (convenience for UI filter bars; not in the original roadmap bullet but falls out naturally).
- [x] `GET /api/v1/testimonials` — paginated approved reviews, optional `?tier=featured|standard` filter. Defaults to the featured-first ordering from `get_all_approved_reviews`.
- [x] `GET /api/v1/certifications` — list via `get_visible_certifications`.
- [x] `GET /api/v1/case-studies/:slug` — single published case study. Two gates: `case_studies_enabled` setting must be `true` AND `published = 1`. Both miss paths return 404 (not 403) so existence isn't leaked. No list endpoint — consumers discover case-study slugs via the `case_study_slug` column on `/portfolio/<id>`.
- [x] `GET /api/v1/projects` and `GET /api/v1/projects/:slug` — list uses `get_visible_projects`; detail uses `get_project_by_slug`, which requires `visible = 1 AND has_detail_page = 1`. Hidden or link-only (GitHub-URL-only) projects 404 on the detail route but still show up on the list.
- [x] `GET /api/v1/blog`, `GET /api/v1/blog/:slug`, `GET /api/v1/blog/tags` — every blog route checks `blog_enabled` first and 404s when off. List is paginated (default 10 per page, max 100) with optional `?tag=<slug>` filter via `get_posts_by_tag`. Detail includes both raw `content` and `rendered_html` (markdown rendered + sanitized via the existing `render_post_content`) plus a `tags` array. Tags endpoint counts only published posts, including tags with zero published posts (count = 0). Flask's dispatcher is verified to prefer `/blog/tags` over `/blog/<slug>` by a regression test that seeds a post with slug `tags`.

**Tests:** 46 tests in `tests/test_api.py` (29 from the initial commit + 17 for the new endpoints). The new tests cover: case-study feature-toggle gating, unpublished-case-study 404, projects visible-only filtering, project detail 404 for link-only projects, blog endpoint 404-when-disabled on all three routes, blog list default + tag-filtered, embedded tags on list items, draft 404, `render_post_content` pass-through for HTML, tag counts excluding drafts, and the `/blog/tags` vs `/blog/<slug>` dispatcher precedence.

### 16.3 — Authenticated Write Endpoints (Token Required — `write` scope)

**Infrastructure shipped with this phase (closes the 16.1 deferrals):**

- [x] **JSON Content-Type enforcement on POST/PUT/PATCH.** `before_request` middleware on the API blueprint rejects non-JSON bodies with 415 + `UNSUPPORTED_MEDIA_TYPE` code (details dict echoes the received Content-Type). Multipart routes are allow-listed via `_MULTIPART_ENDPOINTS` (reserved for 16.3b photo upload).
- [x] **Rate limiting via `rate_limit_write` callable** (60/30/10-per-minute read/write/admin, configurable through the Security-category settings shipped in Phase 13.4). Every blog write route wears `@limiter.limit(rate_limit_write, methods=[...])`.

**Endpoints shipped:**

- [x] `POST /api/v1/blog` — create blog post. Body `{title, summary?, content?, content_format?, cover_image?, author?, tags?, meta_description?, featured?, publish?}`. Draft by default; `"publish": true` publishes immediately (mirrors the admin "Publish" action). Returns 201 with full detail including server-generated slug + tags. Emits `BLOG_PUBLISHED` (when publish=true) or `BLOG_UPDATED` (draft save).
- [x] `PUT /api/v1/blog/<slug>` — update a post. Every field optional; omitted fields keep current value. Supports slug renames via `{"slug": "new-slug"}`. Rejects empty/whitespace-only title. Emits `BLOG_UPDATED`.
- [x] `DELETE /api/v1/blog/<slug>` — deletes the post + tag associations (via `delete_post` which cascades `blog_post_tags`). Returns 204 No Content on success. Emits `BLOG_UPDATED` with `status='deleted'` so subscribers can distinguish.
- [x] `POST /api/v1/blog/<slug>/publish` — publishes a draft (preserves original `published_at` if previously published). Emits `BLOG_PUBLISHED`.
- [x] `POST /api/v1/blog/<slug>/unpublish` — reverts to draft. Emits `BLOG_UPDATED`.
- [x] `POST /api/v1/contact` — public (no token required) submission endpoint. Body `{name, email, message, website?}` where `website` is the honeypot. Honors `contact_form_enabled` toggle (404 if off), validates required fields + email format, per-IP hourly cap of 5 non-spam submissions (spam bypasses the cap so bots can't probe 429s), and fire-and-forget SMTP relay (mirrors the HTML form). Emits `CONTACT_SUBMITTED` with `{submission_id, is_spam, source}`. Flask-Limiter applies a 10/min burst cap on top.
- [x] `POST /api/v1/portfolio` — multipart upload. Path name `api.portfolio_create` is allow-listed in `_MULTIPART_ENDPOINTS` so the JSON Content-Type middleware skips it. Reuses the existing `app.services.photos.process_upload` pipeline (magic-byte validation, size check, EXIF stripping, 2000-px downscale). Metadata fields come from the form body: title, description, category, tech_used, display_tier (default `grid`, validated against `{featured, grid, hidden}`). A bad `display_tier` cleans up the uploaded file before returning 400 so rejection isn't silent data retention. Emits `PHOTO_UPLOADED` with `{photo_id, title, category, display_tier, storage_name, file_size, source}`.
- [x] `PUT /api/v1/portfolio/<id>` — JSON metadata update (title / description / category / tech_used / display_tier / sort_order). All fields optional; omitted fields keep current value. Validates `display_tier` and coerces `sort_order` to int with 400 on failure.
- [x] `DELETE /api/v1/portfolio/<id>` — deletes the row, then the file on disk. File-cleanup OSError is logged but doesn't fail the request — the DB row is gone either way so the site never serves a broken reference. Returns 204 on success, 404 if id unknown.

**Portfolio write tests:** 17 additional tests in `tests/test_api.py` (18 in the file for this phase total, counting the scope-gate tests that apply to all three). Coverage: happy-path multipart upload with Pillow-generated PNG bytes, missing file part, invalid extension, magic-byte mismatch (png extension + non-PNG bytes), rejected `display_tier` value with file cleanup verified, auth gate (401 without token, 403 with read-only scope) on all three routes, metadata partial update preserves unchanged fields, invalid tier / non-int sort_order on PUT, end-to-end upload → delete → row gone → file gone verified.

**Auth + scope semantics:** every blog write route sits behind `@require_api_token('write')`. A `read`-only token returns 403 `insufficient_scope`; a revoked token returns 401 `revoked`; a missing / malformed header returns 401 with `WWW-Authenticate: Bearer`. Verified in tests.

**Events wired in this phase (Phase 19.1 progress):**
- `blog.published`, `blog.updated`, `contact.submitted` now fire from API routes. Equivalent admin-UI emissions are still TODO — every API-side subscriber will already see the payload when the admin routes catch up.

**Tests:** 28 new tests in `tests/test_api.py` (total 74). Coverage includes: JSON Content-Type 415 on form-encoded / no-content-type / GETs-are-fine; 401/403/401 for missing/wrong-scope/revoked tokens on the blog create path; create flow (draft default, publish flag, slug generation, tags sync, title validation, whitespace-only title); update flow (partial updates preserve fields, 404 on unknown slug, empty-title rejection); delete flow (204 + row actually gone, 404 on missing); publish/unpublish status transitions + event emission; contact flow (valid submission, honeypot flagging, required-field validation, malformed email, disabled-form 404, per-IP 429 after 5 prior submissions, event emission). A `no_rate_limits` fixture isolates write tests from Flask-Limiter's shared in-memory bucket.

### 16.4 — Admin Endpoints (Token Required — `admin` scope)

All 10 routes sit behind `@require_api_token('admin')` + the slower `rate_limit_admin` bucket (default 10/min). Every mutation writes to the admin activity log with category-tagged detail so the API and the HTML admin UI share a single audit trail.

- [x] `GET /api/v1/admin/settings` — returns `{data: {categories: [{name, settings: [...]}], flat: {key: value}}}`. Each setting carries its registry metadata (type, default, label, options) so a headless admin panel can render the form without hard-coding the schema.
- [x] `PUT /api/v1/admin/settings` — bulk update from a flat JSON object. Unknown keys silently dropped (matches HTML form contract), booleans normalised to `'true'`/`'false'`, `None` coerced to empty string. Emits `Events.SETTINGS_CHANGED` with the sorted list of updated keys. Does NOT flip unset booleans to false (that's an HTML-form quirk; API clients may send partial updates).
- [x] `GET /api/v1/admin/analytics` — total views / recent window / popular paths / daily time series. `?days=N` (1–90, default 7) and `?popular_limit=N` (1–50, default 10). All SQL parameters are bound; the `-N days` modifier is built from the clamped int so adversarial query strings can't reach the driver.
- [x] `GET /api/v1/admin/activity` — recent activity log entries. `?limit=N` (1–200, default 20).
- [x] `GET /api/v1/admin/reviews` — all reviews or filter by `?status=pending|approved|rejected`. Invalid status → 400 `VALIDATION_ERROR`. No status = pending/approved/rejected concatenated in that order (matches admin UI).
- [x] `PUT /api/v1/admin/reviews/<id>` — body `{action: "approve"|"reject"|"set_tier", display_tier: "..."}`. Unknown action → 400; unknown id → 404. Emits `Events.REVIEW_APPROVED` on approve; every action writes to activity log.
- [x] `POST /api/v1/admin/tokens` — generate a review invite token (single-use, shared verbatim with a contact — different from API tokens which are hashed). Body `{name, type: "recommendation"|"client_review"}`. Returns 201 with the raw token string.
- [x] `DELETE /api/v1/admin/tokens/<id>` — hard-delete a review token. 204 on success, 404 if missing.
- [x] `GET /api/v1/admin/contacts` — paginated submissions. `?per_page=N` (1–100, default 20), `?page=N`, `?include_spam=true` (default false). The dynamic WHERE clause uses only two hardcoded literals (`''` and `'WHERE is_spam = 0'`) — never user input — documented with inline `# noqa: S608 # nosec B608` on the f-string.
- [x] `POST /api/v1/admin/backup` — on-demand backup via `app.services.backups.create_backup`. Body `{db_only: bool}` (optional, default false). Returns 201 with `{archive_path, archive_name, size_bytes, db_only}`. `create_backup` emits `Events.BACKUP_COMPLETED` itself so the route doesn't double-emit. Output directory resolves via `RESUME_SITE_BACKUP_DIR` env var > `<repo>/backups`.

**Tests:** 27 new tests in `tests/test_api.py` (total 118 API, 678 project-wide). Coverage includes: auth gating (write scope → 403 on admin routes; missing token → 401), settings list envelope + registry metadata, settings update filtering unknown keys + event emission, analytics total/popular/series/clamping, activity log round-trip, reviews filter-by-status + invalid-status 400, review approve/reject/set_tier transitions + 404 + event, review-token create with type validation + delete 204 + 404, contacts pagination with include_spam toggle, backup create via tmp_path + env override + event verification.

**Ruff + bandit clean.** Two S608/B608 false positives (dynamic WHERE in /admin/contacts with two hardcoded literal alternatives) are suppressed inline with rationale.

### 16.5 — API Documentation

- [x] **OpenAPI 3.0 spec:** `docs/openapi.yaml` — hand-authored, 34 operations across 27 paths. Bearer security scheme, reusable schemas (`Error`, `Pagination`, every resource shape), reusable responses (`NotFound`, `Unauthorized`, `Forbidden`, `RateLimited`, `ValidationError`, `UnsupportedMediaType`), tags `Public` / `Write` / `Admin`. Drift-guarded by `tests/test_openapi_spec.py` (18 tests) which asserts the spec ↔ Flask URL-map sets are byte-identical.
- [x] **Swagger UI:** `GET /api/v1/docs` renders a standalone template wired to `swagger-ui-dist@5.17.14` (pinned, CDN). Init lives in `app/static/js/swagger-init.js` so the page never relies on CSP `'unsafe-inline'` for scripts. `GET /api/v1/openapi.yaml` and `/openapi.json` serve the spec with ETag + 304 round-tripping. All three routes sit behind the `api_docs_enabled` setting (default `false`, Security category) and 404 when disabled — matches the `/metrics` and disabled-blog "don't leak existence" pattern.
- [x] **Documentation completeness:** auth examples (Bearer header + scope semantics), error-code catalog (enum on the `Error` schema, cross-checked against `app/routes/api.py` literals by `test_error_code_catalog_covers_source_codes`), and a pagination guide (envelope + `page`/`per_page` clamping rules) all live in `info.description` so Swagger UI renders them at the top.
- [x] **CHANGELOG:** "Added — Phase 16.5: OpenAPI 3.0 Documentation" entry under Unreleased records the spec file, three routes, the new setting, and the test additions.

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

- [x] **Systemd timer unit:** `resume-site-backup.timer` + `resume-site-backup.service` ship at the repo root. The service runs `podman exec resume-site python manage.py backup --prune --keep ${RESUME_SITE_KEEP}` (default 7) and depends on `resume-site.service` so the timer can't fire while the container is starting. The timer fires daily at 02:00 local time with `RandomizedDelaySec=30min` to avoid storage stampedes across a fleet, and `Persistent=true` so a host that was off at 02:00 catches up on next boot. Both retention count and schedule are overridable via `systemctl --user edit` without forking the unit files.
- [x] **Quadlet integration:** `resume-site.container` now mounts `resume-site-backups:/app/backups:Z` and sets `Environment=RESUME_SITE_BACKUP_DIR=/app/backups` so the CLI writes archives to the persistent volume by default.
- [x] **Backup volume:** `resume-site-backups` declared in `compose.yaml`'s `volumes:` block and mounted at `/app/backups` on the service. README documents the host-side path discovery via `podman volume inspect`.
- [x] **Compose-based schedule:** README ships an `OnCalendar`-equivalent cron snippet (`0 2 * * * podman compose ... exec -T ... manage.py backup --prune --keep 7`) for operators who don't use systemd / Quadlets.
- [x] **Backup health:** Admin dashboard now carries a "Last Backup" card (rendered via the new `time_ago` Jinja filter at `app/services/time_helpers.py`) showing the most recent `backup_last_success` timestamp, the total archive count, and the total on-disk size. A "Recent Backups" table lists the five newest archives with size + relative mtime. Tested in `tests/test_admin.py::test_dashboard_backup_card_*` (no-backups → "never" / populated → counts and listing).
- [x] **Documentation:** README "Backup" section rewritten: manual invocation (CLI + REST API), systemd timer install (rootless + system-wide) with `systemctl edit` recipes, compose-cron alternative, restore procedure, offsite mirroring example (rclone), per-archive gpg encryption via `ExecStartPost=` drop-in, and the original `podman volume export` left as a belt-and-suspenders option.

---

## Phase 18 — Observability: Structured Logging, Metrics, and Profiling

*The "know what your app is doing at all times" phase. Transforms the current print-to-stdout approach into structured, queryable, actionable telemetry.*

### 18.1 — Structured Logging

**Problem:** Current logging is implicit (Gunicorn access logs + Python's default logger). No structured fields, no request correlation, no log levels used consistently.

- [x] **Logging configuration:** `app/services/logging.py` configures Python's `logging` module with a JSON formatter (default) and a human-readable formatter (dev). Mode + level via env vars `RESUME_SITE_LOG_FORMAT` and `RESUME_SITE_LOG_LEVEL`. Status → level mapping: 2xx → INFO, 4xx → WARNING, 5xx → ERROR. Per-request log entry via `_log_request` after-request hook in `app/__init__.py`, includes `timestamp`, `level`, `logger`, `message`, `module`, `request_id`, `client_ip_hash`, `method`, `path`, `status_code`, `duration_ms`, `user_agent` (first 200 chars). **Remaining:** migrating `config.py` stderr prints through the logger (separate commit — requires factory reshuffling).
- [x] **Request ID propagation:** Generate a UUID4 per request, store in `g.request_id`, echo as `X-Request-ID` response header. Allowlist-validated inbound header propagated verbatim for reverse-proxy correlation. Included in every structured log entry (via `_RequestContextFilter` on the root logger).
- [x] **Sensitive data scrubbing (PII posture):** Metadata-only request logging — we log method/path/status/duration/request_id/user_agent and a per-deployment **SHA-256 hash** of the client IP. Never logged: query strings, POST bodies, full IPs, passwords, tokens. IP hash uses `secret_key` as salt so log files alone can't correlate visitors across deployments.
- [x] **Log rotation:** `docs/LOGGING.md` covers: Podman/journald rotation (`SystemMaxUse`, `MaxRetentionSec`), Docker `json-file` driver (`max-size`/`max-file` per-container and daemon-wide), forwarding to Loki/CloudWatch/Fluentd via log driver config, bare-metal Gunicorn access/error log rotation via `logrotate` (with `copytruncate`), and a `RotatingFileHandler` snippet for `gunicorn.conf.py` (`post_fork` hook, 50 MB / 5 backups). Also documents env vars, JSON schema, PII posture, and `X-Request-ID` correlation

### 18.2 — Prometheus-Compatible Metrics Endpoint

- [x] `GET /metrics` — returns Prometheus exposition format text (`text/plain; version=0.0.4`)
- [x] **Metrics collected (core shipped):**
  - `resume_site_requests_total{method, path, status}` — counter. Uses `url_rule.rule` as path (`/blog/<slug>` not `/blog/my-first-post`); unmatched requests normalised to `<unmatched>` sentinel to cap cardinality.
  - `resume_site_request_duration_seconds{method, path}` — histogram with the documented bucket ladder.
  - `resume_site_uptime_seconds` — gauge, refreshed at scrape time.
- [x] **Metrics collected (domain-specific batch):**
  - `resume_site_photo_uploads_total` — counter incremented via event bus handler on `photo.uploaded`.
  - `resume_site_contact_submissions_total{is_spam}` — counter incremented via event bus handler on `contact.submitted`.
  - `resume_site_blog_posts_total{status}` — gauge refreshed at scrape time from `GROUP BY status` query.
  - `resume_site_backup_last_success_timestamp` — gauge refreshed at scrape time from the `backup_last_success` setting (ISO-8601 → Unix epoch).
- [ ] **Metrics collected (deferred to later commits):**
  - `resume_site_db_query_duration_seconds{query_name}` — needs an instrumented cursor wrapper (Phase 18.3).
  - `resume_site_db_query_total{query_name}` — same.
  - `resume_site_active_sessions` — needs session tracking.
  - `resume_site_api_requests_total{method, endpoint, status, scope}` — lands with the REST API scope tracking.
- [x] **Implementation:** Stdlib-only `app/services/metrics.py` with `MetricsRegistry` singleton, `Counter`/`Gauge`/`Histogram` primitives, and a text-exposition renderer. `/metrics` self-excludes from the request counters so a high scrape rate doesn't drown out real traffic.
- [x] **Feature flag:** `metrics_enabled` setting (default `false`). When off, `/metrics` returns 404 — not 403, so the endpoint doesn't reveal itself.
- [x] **Access control:** `/metrics` honours the comma-separated `metrics_allowed_networks` setting; empty falls back to admin `allowed_networks` in `config.yaml`. Disallowed clients also get 404 (same "does this exist?" ambiguity).

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

- [x] **Error taxonomy:** `app/errors.py` exposes the five categories (`ClientError`, `AuthError`, `ExternalError`, `DataError`, `InternalError`) as string constants on `ErrorCategory`, plus explicit `ExternalError` / `DataError` exception classes for service code to raise. Classification via `categorize_status(status)` (HTTP code → category) and `categorize_exception(exc, status_code=None)` (explicit classes → sqlite3 → network errors → DomainError → fallback to status → InternalError).
- [x] **Error counter metric:** `resume_site_errors_total{category, status}` — increments from the `_log_request` after-request hook for every 4xx/5xx. The `endpoint` label is deliberately omitted in this first pass to avoid cardinality blow-up; it can be added later guarded by the same `url_rule.rule` path template used elsewhere.
- [x] **Error response standardization:** `errorhandler(Exception)` in `app/__init__.py` returns a minimal safe body (Request ID only — never a traceback, exception message, path, or schema hint). Content negotiation: `Accept: application/json` returns `{"error": ..., "code": <category>, "request_id": ...}`; otherwise a short `text/plain` body. Every response carries `X-Request-ID` (from the earlier Phase 18.1 work) so operators can correlate client-side complaints with server-side logs.
- [x] **Unhandled exception handler:** Registered on `Exception` but explicitly passes `HTTPException` subclasses through to Flask's defaults (404s and 403s shouldn't render as 500s). Logs the full traceback at ERROR level on the `app.request` logger via `request_logger.error(..., exc_info=exc, extra={...})`, including `error_category` and `exception_type`. The `security.internal_error` webhook emission is deferred to Phase 19 (event system).
- [x] **Error rate dashboard widget:** "Errors (since restart)" card on the admin dashboard shows total error count + per-category breakdown (ClientError, AuthError, etc.). Reads from the in-memory `errors_total` counter in `app/services/metrics.py` — resets on process restart, which matches the gauge's semantics. Full error history is in structured logs.

### 18.10 — Alerting Rules and Thresholds

**Problem:** Metrics without alerting are just numbers. Alerting converts observability data into operator actions. This phase defines what conditions should trigger alerts and provides ready-to-use rule definitions.

- [x] **Alerting rules document:** `docs/alerting-rules.yaml` — seven rules in two groups (`resume-site-application`, `resume-site-availability`):
  - `ResumeInternalErrorRate` (critical) — any InternalError is a bug
  - `ResumeAuthErrorSpike` (warning) — 401/403 flow > 6/min sustained
  - `ResumeHighLatency` (warning) — p95 request duration > 1s
  - `ResumeHighRequestRate` (info) — > 100 requests/minute
  - `ResumeNoTraffic` (warning) — /metrics reachable but requests_total flat for 30 min
  - `ResumeProcessRestarted` (info) — uptime_seconds < 120s
  - `ResumeScrapeDown` (critical) — Prometheus `up{job="resume-site"} == 0`
  Each rule carries `severity` + `component` labels and `summary` + `description` + `runbook_url` annotations.
- [x] **Alert documentation:** `docs/alerting-rules.md` — per-alert runbook section (what it means, what to check, mitigations in order of reversibility) plus setup instructions and a severity taxonomy.
- [x] **Metric-name drift guard:** `tests/test_alerting_rules.py` (18 tests) parses the YAML, validates the Prometheus schema, confirms every rule has the required fields, and cross-references every `resume_site_*` metric in a rule `expr` against the live registry in `app/services/metrics.py`. Every `runbook_url` anchor is verified against the actual headings in `alerting-rules.md`. A canary test fires if a shipped metric is never alerted on.
- [x] **Disk usage metric:** `resume_site_disk_usage_bytes{path}` gauge with `database` and `photos` labels. Refreshed at scrape time by walking the photo directory and stat-ing the DB file. `ResumeDiskUsageHigh` alerting rule fires when either path exceeds 1 GB. Runbook section in `alerting-rules.md`.
- [x] **Stale backup alert:** `ResumeBackupStale` fires when `resume_site_backup_last_success_timestamp` (gauge, from settings row) is >48 hours old. Runbook section in `alerting-rules.md`.
- [ ] **Brute-force "endpoint" label:** deferred — `errors_total` currently has `{category, status}` only; adding `endpoint` was kept out of Phase 18.9 to avoid cardinality. A finer `ResumeBruteForce` rule can land once the label is added.
- [ ] **In-app alerting (admin dashboard):** deferred — admin-dashboard template work.

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

- [x] `app/events.py` — synchronous, thread-safe, dependency-free event bus. `register` / `unregister` / `emit` / `clear` / `handler_count`. Handlers run in registration order; a handler that raises is logged at WARNING on the `app.events` logger and swallowed (fail-open per the design contract — a broken webhook must never break a contact-form submission). Snapshot-before-dispatch so a handler that mutates the registry mid-emit doesn't corrupt the current fan-out. Twelve canonical event names exposed as string constants on `Events`; bespoke names are also dispatchable.
- [x] **Built-in events:** twelve names registered as constants including the original list plus `security.internal_error` (promised from the Phase 18.9 commit's deferral note).
- [x] **Initial emissions wired:**
  - `security.internal_error` fires from the `errorhandler(Exception)` in `app/__init__.py`. Payload: `request_id`, `method`, `path`, `exception_type`, `category`. Payloads carry no traceback / exception message so third-party subscribers can't leak internals. Never fires for `HTTPException` subclasses (404/403 are not bugs).
  - `backup.completed` fires from `app/services/backups.create_backup` after a successful archive. Payload: `archive_path`, `db_only`, `size_bytes`. Event failures are swallowed so a misbehaving subscriber never breaks a backup.
- [x] **Remaining emissions wired:** all ten remaining canonical events now fire from their natural call sites:
  - `contact.submitted` — `app/routes/contact.py` (HTML form) + `app/routes/api.py` (REST). Mirrors API shape with `source='public_form'`. Honeypot path still fires with `is_spam=true`.
  - `review.submitted` — `app/routes/review.py` (token URL). Carries `review_type` (inherited from token) and `has_rating`.
  - `review.approved` — `app/routes/admin.py:reviews_update` (admin UI) + `app/routes/api.py` (REST). Approve only; reject / update_tier remain admin housekeeping.
  - `blog.published` / `blog.updated` — `app/routes/blog_admin.py` for every new/edit/delete path via the centralised `_blog_event_payload` helper. Delete fires `blog.updated` with `status='deleted'` (matches `api.blog_delete`).
  - `photo.uploaded` — `app/routes/admin.py:photos_upload` (admin UI) + `app/routes/api.py` (REST).
  - `settings.changed` — `app/routes/admin.py:settings` (admin UI, csrf_token excluded from keys list) + `app/routes/api.py` (REST).
  - `api.token_created` — `app/routes/admin.py:api_tokens_generate` (admin UI) + `manage.py generate-api-token` / `rotate-api-token` (CLI).
  - `security.login_failed` — `app/routes/admin.py:login` for both invalid-credentials and IP-locked branches.
  - `security.rate_limited` — new `errorhandler(429)` in `app/__init__.py`. Observability-only: re-raises so Flask's default 429 response (and Flask-Limiter's `Retry-After`) reaches the client unchanged. Endpoint label uses the URL rule template, not the rendered path, to bound cardinality.
- [ ] **Register analytics / activity log / metrics as event handlers** — deferred. Current code calls those subsystems directly from route handlers, which still works. The bus is now available whenever a specific migration becomes valuable (e.g. moving photo upload to emit + subscriber in one commit).

### 19.2 — Webhook Delivery

- [x] **Webhook table:** `webhooks` (id, name, url, secret, events JSON, enabled, failure_count, created_at, last_triggered_at) shipped in `migrations/009_webhooks.sql`. `["*"]` in the events column means "every event"; otherwise an exact-match list of `Events.*` strings.
- [x] **Migration:** `migrations/009_webhooks.sql` ships both `webhooks` and `webhook_deliveries` (per-attempt log; cascades on webhook delete) with hot-path indexes on `webhooks(enabled)` and `webhook_deliveries(webhook_id, created_at DESC)`.
- [x] **Delivery mechanism:** `app/services/webhooks.py:deliver_now` builds the canonical `{event, timestamp, data}` envelope (sorted JSON for stable signatures), HMAC-SHA256-signs it with the row's secret into `X-Webhook-Signature`, sets `X-Webhook-Event` + `Content-Type: application/json` + `User-Agent: resume-site-webhooks/1.0`, and POSTs with the configured timeout (default 5s, clamped to [1, 60]). HTTP errors / network errors / timeouts are captured in the returned `DeliveryResult` (status_code 0 for non-HTTP failures) rather than raised. `record_delivery` writes the result to `webhook_deliveries` and bumps `webhooks.last_triggered_at` in the same connection. `increment_failures` flips `enabled=0` once consecutive failures cross the configured threshold (default 10; 0 disables auto-disable); `reset_failures` zeros the counter on the next 2xx. WARNING-level log on auto-disable.
- [x] **Asynchronous delivery:** `dispatch_event_async` spawns one daemon `threading.Thread` per matching enabled subscriber. Each worker opens a fresh `sqlite3.connect(db_path)` because Flask's request-scoped connection lives on the wrong thread. `register_bus_handlers(db_path)` is wired into `app/__init__.create_app` so every Phase 19.1 emission automatically fans out once the `webhooks_enabled` master toggle (Security/Webhooks category) is on. Idempotent — re-registering against the same db_path drops previous closures first. README for high-volume / external-queue deployments still pending (Phase 19.2 admin-UI commit will cover docs).
- [x] **Admin UI:** `/admin/webhooks` operator page — list, create (auto-generated 32-byte secret pre-filled in the form), inline `<details>` editor (rotate secret, change events, toggle enabled, reset failure counter), delete, synchronous Test button (uses `deliver_now` so the operator sees the HTTP status / latency inline as a flash), per-webhook delivery log at `/admin/webhooks/<id>/deliveries` (last 100 attempts). Sidebar link added under "API Tokens" so the surface is discoverable. All routes inherit the admin blueprint's IP gate + `@login_required`.
- [x] **API endpoints:** `GET / POST /api/v1/admin/webhooks`, `GET / PUT / DELETE /api/v1/admin/webhooks/<id>`, `POST /api/v1/admin/webhooks/<id>/test`, `GET /api/v1/admin/webhooks/<id>/deliveries`. All require Bearer `admin` scope and use the slower `rate_limit_admin` bucket. The HMAC `secret` is echoed exactly once in the create response and otherwise OMITTED from every payload (verified by tests asserting the secret bytes never appear in any GET body). PUT supports `reset_failures: true` for manual recovery after fixing a downstream. OpenAPI 3.0 spec extended with five new operations + six schemas (`Webhook`, `WebhookCreate`, `WebhookUpdate`, `WebhookCreateResult`, `WebhookTestResult`, `WebhookDelivery`); the Phase 16.5 drift guard catches any future spec divergence. Coverage: 42 tests in `tests/test_webhooks_admin.py`.

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

- [x] **Layer audit:** `Containerfile` reviewed; multi-stage layout already minimal (builder stage discarded; runtime stage has 4 `COPY` and 3 `RUN` invocations, each justified inline). `.containerignore` rewritten to exclude `tests/`, `docs/`, every `*.md` (with explicit entries for ROADMAP/CHANGELOG/README/CONTRIBUTING/SECURITY), `pyproject.toml`, `requirements-dev*`, `babel.cfg`, `.pre-commit-config.yaml`, `.secrets.baseline`, dev-tool dirs (`.venv`, `.pytest_cache`, `.coverage`), and operator-side files (`compose.yaml`, `resume-site.container`, `resume-site-backup.{service,timer}`). Dropped the no-op `!requirements.txt` exception (the file is COPY'd inside the builder stage before `.containerignore` filtering applies).
- [x] **Build caching:** Already optimised — `requirements.txt` is COPY'd and `pip install`'d in the builder stage before the application code COPY, so a code-only change reuses the dependency layer cache. Verified by inspection. Documented as the build-cache contract in PRODUCTION.md (Phase 21.4).
- [x] **Multi-platform build verification:** CI builds amd64+arm64 in the `publish` job (`docker/build-push-action@v6` with QEMU + Buildx). The `container-build` smoke test runs on amd64 only — arm64 is exercised at publish time. The image size baseline measurement at v0.3.0 release will record both architectures.
- [ ] **Image size profiling:** Documented baseline pending — captured in PRODUCTION.md (Phase 21.4) once the v0.3.0-rc1 image is built. No automated regression gate this release; revisit in v0.4.0 if size becomes a bottleneck.
- [ ] **Distroless evaluation:** Evaluated and **kept `python:3.12-slim`**. Rationale captured in PRODUCTION.md (Phase 21.4): retains shell access for production debugging, `curl` for the HEALTHCHECK, and team familiarity. Distroless trade-off (smaller attack surface, no debug shell) doesn't pencil out at this scale; revisit in v0.4.0.
- [ ] **Startup optimization:** Cold-start measurement deferred to PRODUCTION.md baseline. The `HEALTHCHECK --start-period=10s` already accommodates the slowest observed cold start under the test-image config; tighten if v0.3.0-rc1 measurements show consistent sub-5s startup.

Build-arg `IMAGE_VERSION` (default `dev`) added to the runtime stage so CI labels each image with the git tag (`v0.3.0`) rather than the previously-hardcoded `0.2.0`. The `container-build` CI job exercises the new build-arg flow plus calls into `/healthz` and `/readyz` as a smoke test of both probes.

### 21.2 — Health and Readiness

- [x] **Separate health endpoints:** `/healthz` (existing) stays as the lightweight liveness probe — no I/O, no DB. `/readyz` (NEW, `app/routes/public.py`) runs four short-circuiting checks: `db_connect` (fresh sqlite3 connection + `SELECT 1` with 1s busy timeout), `migrations_current` (every `migrations/*.sql` recorded in `schema_version`; reuses the new `app/services/migrations.py` helpers), `photos_writable` (configured `PHOTO_STORAGE` exists and is writable), and `disk_space` (database's host filesystem has at least `RESUME_SITE_READYZ_MIN_FREE_MB` free; default 100MB). Returns 200 with `{"ready": true, "checks": {…}}` on success; 503 with `{"ready": false, "failed": "<name>", "detail": "…", "checks": {…}}` on first failure. The route catches every exception so it can never 500. Excluded from analytics in `app/services/analytics.py` so probe traffic doesn't pollute `page_views`. 14 tests in `tests/test_readyz.py`.
- [x] **Startup probe:** Same `/readyz` endpoint serves this role — k8s readiness probes already accept `initialDelaySeconds` for relaxed startup timing. Documented in the commented k8s probe block in `compose.yaml` (`initialDelaySeconds: 5`, `failureThreshold: 3`).
- [x] **Health in compose.yaml:** `/healthz` remains the active healthcheck (Podman/Docker only support one healthcheck per container). Added a commented-out Kubernetes-style probe block to both `compose.yaml` and `resume-site.container` showing the readiness/liveness pair operators should mirror in their k8s manifests.

### 21.3 — Container Security Scanning

- [x] **Trivy CVE scan in CI:** New `container-scan` job in `.github/workflows/ci.yml` between `container-build` and `publish`. Uses `aquasecurity/trivy-action@0.28.0` against a freshly-built image with `--severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed --scanners vuln,secret`. SARIF results uploaded as a build artifact (`trivy-results`) so triagers can review. Vuln database cached between runs to keep scan time bounded.
- [x] **Pipeline gate:** Both `publish` and `publish-main` jobs gain `needs: [test, container-build, container-scan]` — no image is pushed to GHCR if Trivy finds an actionable HIGH or CRITICAL CVE.
- [x] **Cosign keyless signing:** `publish` and `publish-main` install `sigstore/cosign-installer@v3` and sign the published image with the GitHub Actions OIDC identity (`COSIGN_EXPERIMENTAL=1` for the keyless flow). Signature + certificate land in the public Sigstore transparency log; no key material to manage. Both jobs gain `permissions: { id-token: write, contents: read, packages: write }` so the OIDC token is available.
- [x] **Remediation docs:** Operators verify a pulled image with `cosign verify --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+' ...`. The exact invocation lives in `CONTRIBUTING.md` (developer-facing) and will land in `docs/PRODUCTION.md` (operator-facing) in Phase 21.4. Base-image CVE remediation: `docker build --pull --no-cache` then re-tag and re-push — same flow as before, just gated by Trivy now.

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
| `006_login_attempts.sql` | `login_attempts` table for Phase 13.6 admin login lockout | 13 |
| `007_api_tokens.sql` | `api_tokens` table | 13 |
| `008_fts5.sql` | FTS5 virtual table for admin search | 14 |
| `009_content_translations.sql` | Translation junction tables for all content types | 15 |
| `010_webhooks.sql` | `webhooks` and `webhook_deliveries` tables | 19 |
| `011_plugins.sql` | `plugins` table (name, version, enabled, installed_at) | 20 |

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
