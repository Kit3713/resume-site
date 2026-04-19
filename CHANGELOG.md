# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.3.0

### Added — Phase 18.14: Observability Runbook
- `docs/OBSERVABILITY_RUNBOOK.md` — new development-facing runbook (the incident-facing runbook is `alerting-rules.md`, which this cross-links extensively). Covers: a quick-index table pairing every observability tool with the question it answers, a decision tree of "when to reach for each tool" (structured logs vs `/metrics` vs alerting vs synthetic vs manual inspection), the full development workflow from pre-feature baseline capture through post-deploy monitoring, step-by-step setup for Prometheus + Grafana + Alertmanager (compose snippet, `rule_files` wiring, Alertmanager receiver routing by severity + component that preserves `runbook_url` annotations), and three tiers of synthetic-monitoring setup (Uptime Kuma, `healthcheck.sh` via cron-with-flock or systemd timer, `monitor.py` via Playwright on the monitoring host).
- Matching troubleshooting cheatsheet maps ~8 common symptoms to the first tool to reach for, so the on-call engineer at 3am has a tree to follow instead of a "try everything" flowchart.
- Every external tool reference (`query-audit`, `profile`, `mutation-report`, `benchmark_routes.py`, `locust`) is documented with its exact CLI invocation — no "run the thing" vague advice.

### Added — Phase 18.12: Synthetic Monitoring Scripts
- `tests/synthetic/healthcheck.sh` — POSIX-adjacent bash + curl script probing the five most important public routes (`/`, `/portfolio`, `/blog`, `/contact`, `/readyz`). Each route check asserts HTTP 200, response time under `RESUME_MAX_RT_MS` (default 2000 ms), and a per-route case-insensitive body regex so a blank-body regression fails the probe. Every route is tried on each run (no short-circuit on first failure) so the operator sees the whole picture in one output. Optional `RESUME_WEBHOOK_URL` alert posts a JSON envelope built without jq — no runtime deps beyond curl. Designed for cron-with-flock or a systemd timer; fail/success maps cleanly to exit 1/0 so monitoring-as-exit-code works.
- `tests/synthetic/monitor.py` — Playwright (chromium, headless) script running a full user journey: landing → portfolio (verifies images loaded via `naturalWidth > 0`) → blog index → first published post → contact form (fills the honeypot so the submission is flagged spam and never reaches the admin inbox) → admin login page (200 or 404 both accepted since the blueprint is IP-gated). Each step's duration is recorded; any failure captures a full-page screenshot under `RESUME_MONITOR_SCREENSHOTS` and posts the same JSON failure envelope as the curl script. Dev-dependency only (playwright not in the runtime image).
- Neither script is imported by the pytest test suite — `pytest tests/synthetic/ --co` collects nothing by design. They're operator templates shipped in-repo so operators can clone and parameterise via environment variables.
- Scripts pass ruff, bandit `-ll`, and bash `-n` syntax checks. The two `# noqa: S310  # nosec B310` suppressions on the webhook POST path are justified inline: `RESUME_WEBHOOK_URL` is an operator-chosen value, not user input, and treating its scheme as a security boundary would break legitimate http://localhost dev setups.

### Added — Phase 18.11: Grafana Dashboard Template
- `docs/grafana-dashboard.json` — 11-panel dashboard importable via Grafana 10+ UI (or the provisioning API). Panels: request rate by status (status-class colour coding), p50/p95/p99 latency (with 1s threshold marker matching `ResumeHighLatency`), error rate by category (stacked area), login attempts by outcome, process uptime stat, blog posts by status, backup age (48h threshold matching `ResumeBackupStale`), photo upload rate, disk usage gauge (1GB matching `ResumeDiskUsageHigh`), contact submissions split by spam flag, and a top-10 endpoints table over a 1h window. DB query performance + API scope + CSP-violation panels deferred — those metrics aren't declared yet (Phase 18.2 deferred batch + Phase 13 CSP reporting not wired).
- `$DS_PROMETHEUS` templating variable (`type=datasource, query=prometheus`) lets operators pick their data source at import time. Every panel target references `${DS_PROMETHEUS}` rather than a hardcoded uid. `__inputs` section declares the expected Prometheus plugin so the Grafana import UI surfaces the datasource picker.
- 6 new drift-guard tests in `tests/test_alerting_rules.py` (25 total, was 19). The new tests walk every panel's targets, assert each `resume_site_*` metric reference matches a registered metric (accounting for histogram suffixes), verify each panel targets the datasource variable, confirm the `__inputs` declaration is present, and assert panel ids and gridPos fields are structurally sound. A rename in `app/services/metrics.py` now fails CI instead of producing a silently-broken Grafana import.

### Added — Phase 18.10 (completion): Brute-Force Login Alert
- `resume_site_login_attempts_total{outcome}` counter in `app/services/metrics.py` — three label values (`success`, `invalid`, `locked`) keyed to the outcome of each admin-login attempt. `app/services/login_throttle.py` increments the counter from `record_failed_login` / `record_successful_login` / `check_lockout` (the last only when it actually refuses an attempt), so the metric reflects real decisions rather than every probe. Metric emission is wrapped in `contextlib.suppress(Exception)` so a misbehaving registry can never break authentication.
- `ResumeBruteForce` rule in `docs/alerting-rules.yaml` (warning, component=security) fires when `rate(resume_site_login_attempts_total{outcome="invalid"}[5m]) > 0.1` (~6/min) averaged over 5 minutes. Runbook section in `docs/alerting-rules.md` documents how to triage the new alert alongside `ResumeAuthErrorSpike` (which watches every 401/403 across the app) — both firing together is the sign of a real password-guessing campaign.
- Rationale for not touching `errors_total`: adding an `endpoint` label there would have required changes at the `_log_request` call site in `app/__init__.py`, which owns request-lifecycle concerns this agent could not modify safely. A dedicated `login_attempts_total` metric is also the more Prometheus-idiomatic choice — specific label vocabulary (outcomes), low cardinality, and it survives renaming the login route without churning the alert expression.
- Tests: +4 in `tests/test_metrics.py` covering the new counter's declaration, the three outcome increments (including the locked-but-below-threshold no-emit case).

### Fixed — Container image actually builds and runs cleanly on first boot
- `Containerfile` was COPYing `babel.cfg` which `.containerignore` excluded — every build failed at step 9 ("no items matching glob"). `babel.cfg` is a build-time artifact used by `manage.py translations extract`; the runtime image ships the compiled `.mo` catalogs under `translations/` instead. Dropped from the COPY list with an inline comment explaining why.
- Fresh volumes + fresh container now boot to a working site without the operator remembering to run `manage.py init-db`. New `docker-entrypoint.sh` runs `init-db` (migrate + seeds — both idempotent) before handing off to Gunicorn. Upgrades to a new image tag are a pure `pull` + `restart` cycle; pending migrations apply automatically. The Phase 18.7 corruption check (`PRAGMA integrity_check` + 100-byte header guard) still gates the start, so a damaged DB file aborts rather than silently getting a fresh schema on top.
- Entrypoint sets `umask 027` before creating any files so SQLite writes the DB at `0640` rather than the driver default `0644`. Silences the startup security audit's "Database file is world-readable" warning under normal deployments, and `chmod`s any pre-existing files in `/app/data` to match (for operators restoring from permissive backups).
- `README.md` "Quick Start" updated — Option A and Option B no longer mention the separate `init-db` step.

### Added — Phase 21.4: Production Deployment Guide
- `docs/PRODUCTION.md` — operator-facing deployment walk-through covering choice of deployment shape (Compose / Quadlet / k8s), server prerequisites, first-deploy checklist (secret key + password hash generation, config.yaml layout, verification), reverse-proxy setup for Caddy / Nginx / Traefik, firewall + TLS + admin IP gating, resource sizing for 1k / 10k / 100k monthly visitors with the Postgres-migration trigger point, logging (journald / json-file / forwarding), monitoring (uptime-only free tier vs Prometheus + Grafana), backups (references the existing README section with the production-minimum recipe), upgrades (with `cosign verify` command), day-2 operations (rotating secret keys + API tokens, approving pending reviews), known limitations (single-writer SQLite, no object storage, no public login yet), and a getting-help section that indexes every other doc.
- Checks off Phase 21.4's "Production deployment guide" bullet in the roadmap.

### Added — Phase 18.7: Failure Mode and Resilience Testing
- `tests/test_resilience.py` expanded from 6 to 14 tests covering every roadmap failure scenario that doesn't require browser automation. Each test exercises a real failure boundary (SMTP, DB lock contention, disk full, corrupt upload, tampered cookie, malformed DB file) and asserts the app degrades gracefully — no traceback leak, no partial files, no silent data loss.
- `manage.py` — new `_check_db_not_corrupt(db_path)` helper invoked before every migration run. Rejects database files that exist but are < 100 bytes (SQLite header size; smaller means aborted backup restore or truncation) with a clear error. Runs `PRAGMA integrity_check` on larger files; any non-`ok` result aborts with a non-zero exit code. Fresh-install path (no DB file yet) still works.
- `app/services/photos.py` — `process_upload` now **rejects** corrupt / truncated uploads instead of silently accepting them. The `except (OSError, ValueError, Image.DecompressionBombError)` branch returns the user-facing error `"Image file is corrupt or truncated."` and the `finally` block deletes the quarantine file. Previously these uploads landed in storage with `width=height=None` and broke the responsive-variant pipeline downstream.
- `PERFORMANCE.md` — Failure Modes table expanded with expected-behaviour descriptions and linked test functions (one row per scenario). "Not tested (deferred)" list for full-disk DB recovery and CDN unavailability (the latter needs Playwright from Phase 18.4).
- Test suite: +8 resilience tests (14 → 22 in `test_resilience.py`).

### Added — Phase 16.1 (deferred bullet): API Accept-Language Support
- `_resolve_request_locale()` helper in `app/routes/api.py` uses Werkzeug's `accept_languages.best_match(available_locales)` against the site's `available_locales` setting and falls back to `default_locale` when the client's preferred locale isn't configured. `_locale_headers(locale)` returns the `Content-Language` + `Vary: Accept-Language` pair that every translatable response now carries.
- Every public read endpoint that serves translatable content threads the resolved locale through the translation overlay from Phase 15.4: `GET /api/v1/content/:slug`, `/services`, `/stats`, `/certifications`, `/projects`, `/projects/:slug`, `/blog`, `/blog/:slug`. `rendered_html` on `/blog/:slug` reflects the overlaid content so a Spanish API consumer gets Spanish HTML, not a re-rendered English body. Portfolio / testimonials / case studies remain locale-neutral (no translatable columns).
- `_paginated_response` gains an `extra_headers` parameter so `/blog` can emit `Content-Language` alongside the pagination envelope.
- The ETag already varies with the serialized body, so 304 round-trips stay correct across locales automatically. A regression test (`test_services_etag_differs_per_locale`) locks that contract in.
- OpenAPI spec (`docs/openapi.yaml`) — new reusable `components.parameters.AcceptLanguage` header parameter referenced from every translatable GET endpoint. Top-level `info.description` now includes a "Content negotiation" section documenting the header contract, the `Content-Language` / `Vary` response headers, and the ETag-per-locale guarantee.
- 17 tests in `tests/test_api_locale.py`: missing-header default, exact-match, q-value preference (`es;q=0.9,en;q=0.5`), unconfigured-locale fallback, missing-translation-row fallback, per-locale ETag divergence, 404 responses DON'T emit `Content-Language`, content-block / stats / certifications / projects / blog-list / blog-detail overlays, non-translatable endpoints (`/portfolio`, `/testimonials`) don't emit locale headers, and an OpenAPI-spec drift guard asserting the `AcceptLanguage` parameter is referenced on exactly the expected set of paths.

### Added — Phase 15.4: Public Translation Rendering + SEO
- `app/services/translations.py` — locale-aware wrappers around the public model queries: `get_visible_services_for_locale`, `get_visible_stats_for_locale`, `get_visible_projects_for_locale`, `get_visible_certifications_for_locale`, `get_content_block_for_locale`, `overlay_post_translation`, `overlay_posts_translations`, `get_available_post_locales`. Each wrapper short-circuits to the original query when the active locale matches the default, so single-locale deployments pay no JOIN cost. Multi-locale requests fall through to `get_all_translated` / `get_translated` with a graceful fallback to the default-locale row when no translation exists.
- `og_locale(code)` helper — maps ISO 639-1 short codes (`en`, `es`, `fr`, …) to the BCP 47 form (`en_US`, `es_ES`, `fr_FR`) that Facebook, LinkedIn, and other Open Graph consumers require. Built-in map covers 18 common locales; unknown codes fall back to `xx_XX`; already-region-qualified inputs (`pt-BR`) are normalised to underscore case.
- Landing page, `/services`, `/projects`, `/projects/<slug>`, `/certifications`, and the blog (list, tag, single-post) routes all run through the translation overlay. Untranslated fields fall through to the default-locale row.
- `base.html` — new `og_locale` and `og_locale_alternates` blocks emit `<meta property="og:locale" content="xx_XX">` for the active locale plus `<meta property="og:locale:alternate">` tags for every other configured locale. Blog-post pages override the alternate block so only locales with translation rows for that specific post are listed.
- `/blog/feed.xml?lang=<code>` — locale-specific RSS feed. The `<language>` channel element and the self-referential `<atom:link>` both reflect the resolved locale. Unknown `?lang` values silently fall back to the default locale so bad links don't 500.
- `/sitemap.xml` — now emits `xmlns:xhtml="http://www.w3.org/1999/xhtml"` plus per-url `<xhtml:link rel="alternate" hreflang="...">` entries and an `x-default` pointer when more than one locale is configured. Single-locale deployments stay clean (no xmlns declaration, no alternates) so the sitemap doesn't grow cruft for operators who don't need it.
- Migration 011 column alignment — `certification_translations` column renamed from `title` to `name` (matches the parent `certifications` table so `get_all_translated`'s `COALESCE(t.X, s.X)` pairs line up); `project_translations` gains a `summary` column so the project card grid's blurb is translatable. Both changes land as in-place edits to the unreleased migration — no one has production data in these tables yet.
- `_TRANSLATION_TABLES['certifications']['fields']` updated to `('name', 'description')`; `_TRANSLATION_TABLES['projects']['fields']` updated to `('title', 'summary', 'description')` to match the schema changes.
- 24 new tests in `tests/test_translations_public.py`: overlay unit tests (short-circuit path, translation application, missing-translation fallback, content-block by-slug, stats, OG locale mapping including region-qualified and unknown codes), route-level integration (landing page, services, projects, project detail, certifications), sitemap hreflang emission (multi-locale + single-locale), OG locale meta tags (default, Spanish session, single-locale), and blog RSS behaviour (default channel language, `?lang=es` overlay + channel tag, unknown `?lang` fallback, per-post OG alternates).

### Added — Phase 21.3: CVE Scanning + Image Signing
- New `container-scan` CI job between `container-build` and `publish`. Uses Trivy (`aquasecurity/trivy-action@0.28.0`) to scan the freshly-built image for OS and Python package CVEs plus leaked secrets. Flags: `--severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed --scanners vuln,secret`. Vuln database cached between runs for fast scans. SARIF report uploaded as the `trivy-results` artifact for triage.
- `publish` and `publish-main` jobs gain `needs: [test, container-build, container-scan]` — no image is pushed to GHCR if Trivy finds an actionable HIGH or CRITICAL CVE.
- Cosign keyless OIDC signing on every published image. Both publish jobs install `sigstore/cosign-installer@v3`, request `id-token: write` permission, and run `cosign sign --yes ghcr.io/.../resume-site@<digest>`. Signature + certificate land in the public Sigstore transparency log; no key material to manage. Operators verify with `cosign verify --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp 'https://github.com/Kit3713/resume-site/.+' ...`.
- `CONTRIBUTING.md` — new "Container Image Changes" section documenting local Trivy scan + cosign verify invocations for contributors who touch `Containerfile` or `requirements.txt`.

### Changed — Phase 21.1: Containerfile + `.containerignore` Tidy
- `Containerfile` — runtime stage now takes an `IMAGE_VERSION` build-arg (default `dev`) sourcing the OCI version label, replacing the hardcoded `0.2.0`. CI's `container-build` job sets it to `ci-<short-sha>`; the publish workflow sets it to the git tag (Phase 21.5). Volume-mount and health-check sections in the header comment block updated to reference the Phase 21.2 `/readyz` endpoint and the Phase 17.2 backups volume.
- `.containerignore` — explicit exclusions for `tests/`, `docs/`, every `*.md` (ROADMAP/CHANGELOG/README/CONTRIBUTING/SECURITY/PERFORMANCE), `pyproject.toml`, `requirements-dev*`, `babel.cfg`, `.pre-commit-config.yaml`, `.secrets.baseline`, dev-tooling dirs (`.venv`, `.pytest_cache`, `.coverage`), and operator-side files (`compose.yaml`, `resume-site.container`, the systemd backup units). Dropped the no-op `!requirements.txt` exception.
- `.github/workflows/ci.yml` — `container-build` job now passes `--build-arg IMAGE_VERSION="ci-${GITHUB_SHA::7}"` and additionally smoke-tests both `/healthz` and `/readyz` (was: `/` only). Catches probe regressions before any image is published.

### Added — Phase 21.2: Readiness Probe (`/readyz`)
- `GET /readyz` — Kubernetes-style readiness probe that runs four checks in order, short-circuiting on the first failure: `db_connect` (fresh sqlite3 connection + `SELECT 1`), `migrations_current` (every `migrations/*.sql` recorded in `schema_version`), `photos_writable` (configured `PHOTO_STORAGE` exists and is writable), and `disk_space` (database's host filesystem has at least `RESUME_SITE_READYZ_MIN_FREE_MB` free; default 100MB). Returns 200 with `{"ready": true, "checks": {…}}` on success, 503 with `{"ready": false, "failed": "<check>", "detail": "…", "checks": {…}}` on the first failure. The route catches every exception so it can never 500.
- Liveness vs readiness contract documented inline: `/healthz` (Phase 17 era) stays the lightweight liveness probe used by Podman/Docker HEALTHCHECK; `/readyz` is the deeper "can it serve right now?" check intended for orchestrator readiness probes (k8s, Nomad). `compose.yaml` and the Quadlet unit keep `/healthz` as their healthcheck and ship a commented-out k8s readiness probe block referencing `/readyz`.
- `app/services/migrations.py` (NEW) — extracted `list_migration_files()`, `get_applied_versions()`, `ensure_schema_version_table()`, `get_pending_migrations()`, and `get_migrations_dir()` from `manage.py` so the route layer can import them without dragging argparse in. `manage.py` re-exports the underscore-prefixed names for backward compatibility — every existing internal call site still works.
- `tests/test_readyz.py` — 14 tests covering the success path, every failure mode (db connect, pending migrations, missing/unwritable photos dir, low disk, env-override clamping), the analytics-exclusion regression guard (`/readyz` traffic must not pollute `page_views`), and the service-module helpers in isolation.
- `tests/conftest.py` — `_init_test_db()` now populates `schema_version` after applying each migration, mirroring what `manage.py migrate` does in production. Without this, the readiness probe correctly reports every shipped migration as pending in the test environment.

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
