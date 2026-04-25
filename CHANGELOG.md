# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.3.3 (Proof)

### Fixed — `save_translation` SELECT-then-INSERT race (#122)

- `app/services/translations.py:save_translation` previously ran SELECT-existing + INSERT/UPDATE without a transaction. Two concurrent saves to the same `(parent_id, locale)` could both observe "no existing row" and both attempt the INSERT; the loser tripped the `UNIQUE(parent_id, locale)` constraint and raised `IntegrityError` 500. The function now wraps the read+write in an explicit `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` transaction (matches Phase 27.2's atomicity pattern; `app.db._InstrumentedConnection` doesn't forward sqlite3's context-manager protocol so `with db:` isn't an option). On the rare race where the INSERT still loses, we retry once as an UPDATE — the racing caller's intent is real, we overlay our values onto their row rather than try to win the race. A non-UNIQUE `IntegrityError` (e.g. FK violation on `parent_id`) still propagates.
- Regression test `test_save_translation_concurrent_does_not_500_on_race` in `tests/test_translations_public.py` spawns two `threading.Thread` workers behind a `threading.Barrier(2)` to maximise the race window; after both return it asserts neither raised, exactly one row exists for `(service_id, 'es')`, and the surviving title is one of the two submitted values.
### Fixed — Phase 30: escape slug/locale in `/sitemap.xml` (#128)

- `app/routes/public.py:sitemap` previously emitted XML by f-string concatenation with no escaping of dynamic values. A legitimate blog slug like `q&a-with-jane` (`&` is a valid URL slug character) broke XML well-formedness because raw `&` is reserved — search engines reject malformed sitemaps, killing SEO. Every interpolated value (slug-derived `path`, `priority`, `locale`, `base_url`) now flows through `html.escape`, matching the convention `app/routes/blog.py` already uses for the RSS feed. `html.escape` covers `&`, `<`, `>`, `"`, `'` (the `quote=True` default) so attribute values are safe too. Stdlib only — no new dependency. A future cleanup could move to `xml.etree.ElementTree` if this function grows.
- Regression test `test_sitemap_escapes_special_characters` in `tests/test_integration.py` seeds two posts whose slugs carry `&` and `<`, asserts the raw response body contains `&amp;` / `&lt;` (not the literal characters in URL paths), parses the body with `defusedxml.ElementTree.fromstring` (must not raise), and asserts the `<loc>` text round-trips back to the unescaped slug.
### Fixed — `assets._cache` no longer pins `"missing"` forever (#133)

- `app/assets.py:hashed_static_url` previously cached the literal string `"missing"` for every static path it couldn't find on disk. A fresh container that hit `/static/css/style.css` before the volume mount finished propagating would pin the miss for the lifetime of the worker — every subsequent response served `?v=missing` URLs, defeating the whole point of content-hash cache busting (and producing the wrong `Cache-Control: immutable` semantics, since `?v=missing` is the same byte-string across deploys). The cache write on miss is removed; misses now fall through to a one-time `os.path.isfile` re-stat per request, so the next lookup after the volume settles computes the real SHA-256. Successful hashes are still cached forever (existing semantics — static files don't change in-place).
- New regression test `tests/test_app.py::test_hashed_static_url_recovers_when_missing_file_appears`: looks up a path that doesn't exist (asserts `?v=missing` URL and `_cache` untouched), then writes the file to the static dir and asserts the second lookup serves a real hash and caches the success.
### Fixed

- **#138 Pillow pin consistency** — `requirements.in` now pins Pillow with `==` like every other dependency. The previous `>=11.1.0` lower bound let `pip-compile -U` silently cross major-version boundaries; now an upgrade requires an explicit edit and a CHANGELOG entry, matching the rest of the dependency surface.

### Changed — Phase 26.6: benchmark harness sets its own log level (#64)

- `scripts/benchmark_routes.py` now `os.environ.setdefault('RESUME_SITE_LOG_LEVEL', 'WARNING')` before importing app code, so contributors following the docstring no longer silently measure stderr-sink overhead. The startup banner prints the effective `RESUME_SITE_LOG_LEVEL` so an operator override (`RESUME_SITE_LOG_LEVEL=DEBUG python scripts/benchmark_routes.py`) is visible at a glance. Docstring rewritten — the script handles the default, operators only set the variable to override.
### Closed — Phase 29.4: code-redundancy tracking issue closeout (#56)

- Issue #56 (the v0.3.3 audit's omnibus tracking issue for ~40 redundancy items across routes, services, models, templates, tests, and `manage.py`) carried a closeout comment listing which bullets landed in v0.3.3 (29.1 form-field helper, 29.2 CRUD `update_fields` triad, 29.3 test fixture consolidation) and which roll forward as standalone issues. The remaining bullets (A2-A13, B1-B15, C1-C4, D1-D2, E1-E5) are tracked individually so each one can be triaged on its own merits rather than as a half-life-decaying batch. Don't keep a tracking issue indefinitely — it stops tracking once the half-life exceeds the release cycle.
### Security — Phase 28.4: systemd hardening on Quadlet + backup service (#27)

- `resume-site.container` and `resume-site-backup.service` now ship the low-risk `systemd.exec` hardening set under `[Service]`: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `RestrictSUIDSGID`, `LockPersonality`, `RestrictNamespaces`, `SystemCallArchitectures=native`. Each directive carries an inline comment with its purpose and the per-line rollback (comment out + `daemon-reload`). `ReadWritePaths=` whitelists the dirs each unit actually needs writable under `ProtectSystem=strict` (`%h/.local/share/containers` + `%t/containers` for the container unit; `%t/containers` for the backup unit).
- `MemoryDenyWriteExecute=yes` ships **commented out** pending Pillow validation in staging — Pillow's libjpeg/libwebp DSOs sometimes use W^X-violating mappings on the image-processing path. Operators with the photo-upload code path should validate in a staging deployment before uncommenting; if Pillow segfaults on photo upload, leave it commented out.
- The `resume-site-purge.service` follow-up is unchanged from v0.3.2 — the unit was deferred and does not exist in-tree, so this phase ships hardening for the two units that do.
### Refactor — Phase 29.1: form-field extraction helper (#56)

- New `app/services/form.py:get_stripped(form, key, default='')` replaces the `request.form.get(...).strip()` and `(request.form.get(...) or '').strip()` idioms that had accreted across `app/routes/admin.py`, `app/routes/api.py`, `app/routes/blog_admin.py`, `app/routes/contact.py`, and `app/routes/review.py` (24 call sites total). Behaviour is byte-identical — same `str.strip()` semantics, same default-on-absent / default-on-empty, same whitespace-only → `''` collapse. No case folding, no normalisation; callers that needed `.lower()` or other downstream transforms keep them explicit.
- 19 regression tests in `tests/test_form_helper.py` pin the contract against both legacy idioms (default-arg form and `or '' ` form), including whitespace-character coverage (`\t`, `\n`, `\r`, mixed) and the `display_tier='grid'` non-empty-default case from `app/routes/api.py`.
### Refactor — Phase 29.3: consolidate admin-login test fixtures (#56)

- Three tests that hand-rolled their own admin-login setup (`tests/test_integration.py::test_session_timeout_redirects_to_login`, `tests/test_security.py::test_logout_revokes_cookie_on_another_client`, `tests/test_security.py::test_logout_revokes_cookie_on_blog_admin_routes`) now use the canonical `auth_client` fixture from `tests/conftest.py`. Removes duplicated `client.post('/admin/login', data={...})` boilerplate and inline `sess['_user_id'] = 'admin'` session manipulation. Behaviour is byte-identical — `auth_client` produces the same `_user_id` / `_fresh` / `_admin_epoch=0` session a real fresh-DB login produces, so the cookie-revocation and session-timeout assertions still trigger the exact same code paths.
### Refactor — Phase 29.2: shared `update_fields` CRUD helper (#56)

- New `app/services/crud.py:update_fields` extracts the partial-update + caller-supplied validation + activity-log-emission triad that was duplicated across the HTML admin services and the API services. Wraps the UPDATE and the optional `admin_activity_log` INSERT in a single explicit `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` transaction (matches Phase 27.2's atomicity pattern; `app.db._InstrumentedConnection` doesn't forward the context-manager protocol so the explicit form is required). Column names are spliced into the SQL string after a caller-supplied `column_allowlist` check; values bind through `?` placeholders.
- Migrated three services to the helper: `update_webhook` (was already partial-update; allowlist now lives next to its column constant), `update_service`, and `update_stat` (both converted from always-update to partial-update). The other `update_*` functions in `app/services/` (`update_post`, `update_review_tier`) carry one-off quirks — slug regeneration, derived `reading_time`, format-conditional sanitisation — so they're tracked for follow-up rather than force-fit.
- 12 helper tests in `tests/test_crud.py` cover single- and multi-column updates, allowlist rejection, validation rollback, activity-log emission/skip, concurrent-writer lock contention with a clean rollback on the loser, and the row-not-found return-zero path. The `test_services_edit` and `test_stats_edit` admin tests were strengthened to assert DB persistence (the `feedback_admin_route_coverage.md` pattern — render-only redirect checks let an ImportError ship in v0.3.1).

### Fixed — separate 502/503/504 from `InternalError`; categorise as `UpstreamError` (#134)

- `app.errors.categorize_status` previously lumped every 5xx into `ErrorCategory.INTERNAL`, so a rolling restart that produced a brief 502/503/504 burst from the reverse proxy would page on-call as if it were an unhandled crash. The function now splits the 5xx range: 500/501 (and 505+) stay `InternalError`; 502, 503, and 504 map to a new `ErrorCategory.UPSTREAM` ("UpstreamError"). `categorize_exception` gains a matching `OSError(errno=ECONNREFUSED)` branch (matching `ConnectionRefusedError` via the same errno path) so an upstream socket that isn't accepting connections classifies the same way. The docstring promise ("OSError with a network-looking errno → ExternalError") is now accurate for the refused-connection case, which has its own bucket.
- `docs/alerting-rules.yaml` adds a `ResumeUpstreamErrorRate` warning rule (`rate(... category="UpstreamError"[5m]) > 0.05`, `for: 5m`) sibling to the existing critical `ResumeInternalErrorRate`. `docs/alerting-rules.md` documents the runbook, and the Grafana dashboard description names the new category.
- Regression tests in `tests/test_errors.py` parametrize across `[(500, INTERNAL), (501, INTERNAL), (502, UPSTREAM), (503, UPSTREAM), (504, UPSTREAM), (404, CLIENT), (200, None)]` and assert both the bare `OSError(ECONNREFUSED, ...)` and the `ConnectionRefusedError(ECONNREFUSED, ...)` subclass classify as `UpstreamError` (the branch keys on errno, not class).

### Deprecated
### Fixed — contact-form failure flash + preserve input on validation (#80, #81)

- `app/routes/contact.py` now captures the return value of `send_contact_email` and flashes a sorry-couldn't-send error message when SMTP delivery fails (#80). Previously the success line was unconditional — a visitor whose message hit a transient SMTP failure was falsely told their message arrived. The submission row is still persisted to `contact_submissions` (Phase 27.3 already added a WARNING log), so admin visibility is preserved.
- The contact form template now repopulates `name`, `email`, and `message` from the submitted values when validation fails (empty fields, null bytes, malformed email, hourly cap), so visitors don't lose their typed input on the bounce-back (#81). Jinja autoescape keeps the values XSS-safe; a single `{% set fv = form_values or {} %}` at the top of the form hides the GET-vs-POST distinction from the field markup.
- Two regression tests in `tests/test_app.py`: `test_contact_smtp_failure_flashes_sorry` monkey-patches `send_contact_email` to return False and asserts the redirect carries a `Sorry`-prefixed error flash (no success line); `test_contact_validation_failure_preserves_input` POSTs a malformed email and asserts the rendered form contains `value="Jane Doe"`, `value="not-a-valid-email"`, and the typed message body.

### Fixed — `events.register` validates against canonical name set (#129)

- `app/events.py:register` now raises `ValueError` if `event_name` isn't one of the canonical strings on the `Events` namespace. Previously the docstring promised "the registry-keyed registration API enforces spelling for the canonical set" but the code accepted any string, so a typo (`register("photo.uploded", h)`) silently no-opped forever — emit-time matching against the wrong key never fired the handler. The error message includes `difflib.get_close_matches` suggestions so a one-letter typo points at the intended canonical name; bare-wrong names get the full sorted list. `emit` itself remains permissive — bespoke event names can still be dispatched ad-hoc.
- Four regression tests in `tests/test_events.py` cover the contract: unknown name raises, close-match typos suggest the canonical spelling, wildly-off names list valid ones, and every `Events.ALL` constant continues to register cleanly.

### CI — Phase 28.1: SQL grep guard accepts `# noqa: S608` too (#29)

- The "Check for unsafe SQL patterns" step in `.github/workflows/ci.yml` previously suppressed lines tagged `# nosec B608` (bandit) but not `# noqa: S608` (ruff/flake8-bandit). Every intentional interpolation in this codebase carries both annotations, so the bug had no false-positives in tree — but a future contributor who used only the ruff annotation would have been silently un-checked. The `grep -v` filter now matches either annotation via `grep -vE 'nosec B608|noqa: S608'`. The error message points to both styles.
- New `tests/test_ci_guards.py` with seven regression tests that shell out to `grep` against `tmp_path` fixtures and lock the suppression contract: bare interpolations fire, `nosec`-only suppresses, `noqa`-only suppresses (Phase 28.1 acceptance test), both together suppress, and the `.format()` half of the guard honours both annotations identically.
### CI — Phase 28.2: un-advisory `vulture` (#30)

- The `quality` job's vulture step is now blocking instead of advisory. `continue-on-error: true` removed; any new dead-code finding at `--min-confidence 80` fails the build. Current tree is already clean at that threshold, so the flip lands without code deletions or new allowlist entries.
- Matching pre-commit hook added (`https://github.com/jendrikseipp/vulture` v2.16) with the same paths and confidence as CI, so contributors catch findings before push. `CONTRIBUTING.md` documents the workflow: real dead code gets deleted, runtime-dispatched callables (Flask url_map handlers, reflection-invoked methods) get a single-line `vulture_allowlist.py` entry with an inline rationale.
### CI — Phase 28.3: retire `upgrade-simulation`; replace with `migrate --dry-run` probe (#31)

- Retired the long-advisory `upgrade-simulation` CI job. It tried to do a full `:latest` pull + volume replay against the freshly built image, but the bind-mount permissions kept tripping over SELinux on the GitHub runner and the job was stranded as `continue-on-error: true` for months — operators read it as green when it was effectively unmonitored. Replaced with a smaller `migrate-dryrun` job that retains the static `manage.py migrate --verify-reversible` walk, builds the image, then runs `manage.py migrate --dry-run` inside it against an empty DB. `publish` and `publish-main` gate on its clean exit. Same "migrations look right" guarantee the simulation tried to provide, without the SELinux/bind-mount maintenance burden. The in-process data-survival side of the Phase 21.5 contract still ships via `tests/test_upgrade.py`.
### Performance — Phase 26.4: `Image.draft()` for JPEG photo uploads (#61)

- `process_upload` now calls `img.draft('RGB', (2000, 2000))` immediately after `Image.open()` when the source is a JPEG. Pillow forwards this to libjpeg-turbo which emits a smaller image during DCT decoding — so the LANCZOS resize that follows works on a buffer already close to the 2000 px target rather than the full 6000 px source. Measured ~2.74× faster on a synthetic 24 MP gradient JPEG (median of 5 in-process iterations); libjpeg-turbo docs cite 4-8× on real DSLR JPEGs with 3-5 MB on-disk size and high-frequency detail. The "Photo upload CPU" perf cliff for 24 MP DSLR JPEGs drops from ≈ 5 s to ~1-2 s. Variant ladder (640w / 1024w / 2000 px) is unchanged; EXIF stripping still works.
- Three regression tests in `tests/test_photo_processing.py`: full variant ladder produced for a 24 MP fixture, post-`draft()` output stays within 1% byte tolerance of the pre-change pipeline at the same `quality=85` save (compared via monkeypatched draft no-op), and EXIF tags are still stripped on the 24 MP path.
### Performance — Phase 26.5: cache photo-directory size for `/metrics` (#36)

- The `resume_site_disk_usage_bytes{path="photos"}` gauge previously walked the entire photo directory on every Prometheus scrape — at 10 k photos that was multiple seconds per scrape, paid by every reader. The route now reads a cached `photos_disk_usage_bytes` setting in O(1). Photo upload and delete bump the value by the file-size delta (primary file plus whatever responsive variants landed); `manage.py purge-all` reconciles to a ground-truth directory walk so steady-state drift is bounded by the purge cadence (typically 24 h). Fresh installs with a missing or zero cache fall back to a one-time walk and cache the result for the next scrape.
- DB-size half left as-is — `os.stat()` on the SQLite file is already O(1) and always-fresh.
- Two new internal-only settings: `photos_disk_usage_bytes`, `photos_disk_usage_updated_at`. Not surfaced in the admin UI (category `Internal` is excluded from `SETTINGS_CATEGORIES`).
- Three regression tests in `tests/test_metrics.py`: gauge tracks an upload and delete in lockstep, scrapes never call the directory-walk function once the cache is populated, and a fresh install with no cache walks once and writes the result back.
- `PERFORMANCE.md` documents the cached-scrape contract and the staleness window.

### Performance — Phase 26.3: paginate `/admin/blog` (#54)

- The admin blog list previously rendered every row in one pass. Documented 8.3 ms at 150 posts, scaling linearly. The list route now wires up a new `get_all_posts_paginated` helper — default 25 posts/page, `?page=N` navigation, existing `?status=` filter preserved on paginator links so filter + page compose. Invalid `?page=` falls back to page 1.
- Two regression tests in `tests/test_blog.py`: 30 seeded posts split 25/5 across two pages with disjoint title sets; garbage `?page=not-a-number` returns 200, not 500.

### Performance — Phase 26.2: Gunicorn `--preload` and worker recycling (#28, #53)

- `docker-entrypoint.sh` now starts Gunicorn with `--preload --max-requests 2000 --max-requests-jitter 200`. `--preload` forks workers from a pre-loaded master (500-800 ms cold-start win, lower steady-state RSS via CoW). Worker recycling guards against Pillow / Jinja / SQLite statement-cache memory creep. The page_views drainer (25.2) and webhook thread pool (25.3) lazy-start on first use after fork, so `--preload` is safe.

### Performance — Phase 26.1: translations N+1 eliminated (#52)

- `overlay_posts_translations` rewritten from a per-post `get_translated` loop to a single batched `SELECT * FROM blog_post_translations WHERE post_id IN (...) AND locale IN (?, ?)` query, merged in Python. Before the rewrite every post in a listing paid two queries (parent re-fetch + per-post translation lookup); at a 20-post `/blog/feed.xml` that was 40 extra hot-path SELECTs. Fallback-locale chain preserved; fast-path when active locale equals fallback is unchanged (zero queries).
- Three regression tests in `tests/test_n_plus_1.py` assert: 1 query regardless of post count (3 vs 20); source row preserved when no translation matches; fast-path zero queries when active == fallback.

## [Unreleased] — v0.3.2 (Shield)

### Deprecated

### Security — Phase 27.4: `content_format` validation on HTML blog admin (#24)

- HTML blog-post create and edit paths now validate `content_format` against `{html, markdown}` before calling the service layer. Invalid values re-render the form with a flash. Brings the HTML path to parity with the API path's existing check; closes the last tier-2 form-validation gap.
- Regression test `test_blog_create_rejects_invalid_content_format` in `tests/test_blog.py` locks the new behaviour.

### Performance — Phase 25.2: `page_views` off the hot path (#49)

- `track_page_view` replaced its synchronous INSERT+COMMIT with an enqueue onto a bounded `queue.Queue` (10 000 cap). A single daemon drainer thread flushes in batches (500 events or 2 s, whichever first). Under burst load the SQLite write lock no longer contends with every other writer on the hot path. Queue-full drops silently + increments an exposed drop counter (`get_dropped_total()` for the future /metrics integration). Final flush on `atexit` so the last drain window isn't lost on process shutdown.
- Test-bypass (`app.config['TESTING']`) keeps the synchronous write path so `tests/` assertions that read `page_views` immediately after a `client.get(...)` continue to work without having to sleep for a drain interval.
- Four regression tests in `tests/test_page_views_batching.py` cover batch-flush correctness, atexit flush-remaining, queue-full drop counting, and 5-producer × 100-event concurrent enqueue without row loss.

### Fixed — Phase 27.2: review submission atomicity (#26)

- `create_review` + `mark_token_used` now run inside an explicit `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` transaction with a third in-transaction token re-validate. Before, two separate statements without a transaction meant two concurrent submissions of the same token could both succeed (race between "token valid" check and "mark used" update). Now exactly one wins; the losing caller rolls back cleanly. `app.db._InstrumentedConnection` doesn't forward the context-manager protocol so the explicit form is used instead of `with db:`.
- Regression test `test_review_token_concurrent_submission_rejected` in `tests/test_integration.py` asserts exactly one review row exists after two simultaneous POSTs of the same token.

### Added — Phase 37.2: `@deprecated` decorator + webhook envelope deprecation keys + metric

- New `app/services/deprecation.py` with `@deprecated(sunset_date, replacement=None, reason=None)` decorator. Stamps responses with `Deprecation: true` (RFC 9745 draft), `Sunset: <HTTP-date>` (RFC 8594 — ISO date converted via `email.utils.format_datetime` so the day/month names are locale-safe), and `Link: <url>; rel="successor-version"` when a replacement is named. Logs an INFO record on `app.api.deprecation` per call with request id, endpoint name, `User-Agent`, and optional `X-Client-ID` so operators can identify lingering consumers. Idempotent across decorator stacking — the inner wrapper stamps headers + counter + log, the outer wrapper sees `Deprecation` already set and bows out.
- New Prometheus counter `resume_site_deprecated_api_calls_total{endpoint}` increments once per call. Operators graph against the configured sunset date to confirm consumers have migrated; a still-non-zero rate close to the date pushes the sunset.
- Webhook envelope plumbing in `app.services.webhooks._build_envelope`: optional `deprecated=True` and `sunset='<iso>'` kwargs inject `"deprecated": true` / `"sunset": <iso>` keys into the inner `data` payload. Default-off; existing callers untouched. Mirrors the HTTP header pair so a webhook consumer can subscribe to the same warning lifecycle when an event schema is on its way out.
- Imported (`# noqa: F401`) into `app/routes/api.py` so the symbol is on the route-module's import surface; no existing route is decorated yet — the first usage waits for v0.4.0.
- Six tests in `tests/test_deprecation.py` cover the three headers, the `Link: rel="successor-version"` form, the INFO log line on `app.api.deprecation` (via `caplog`), the counter increment, decorator-stacking idempotency, and the webhook envelope plumbing.
### Added — Phase 37.3: OpenAPI deprecation drift guard + header regression test

- `tests/test_openapi_spec.py::test_openapi_deprecated_flag_matches_decorator` walks every operation in `docs/openapi.yaml`, and for each one flagged `deprecated: true` resolves the matching Flask view via `app.url_map`, asserts the `@deprecated` decorator is applied (detected via the `__deprecated_sunset__` marker), and that the spec's `x-sunset` extension matches the decorator's `sunset_date`. Walks no operations today (no endpoints are deprecated yet) but locks the contract for the first deprecation.
- `tests/test_api.py::test_deprecated_endpoint_emits_three_headers_and_logs` exercises the runtime contract: a `@deprecated`-wrapped handler emits `Deprecation: true`, `Sunset` (RFC 7231 HTTP-date), and `Link: <url>; rel="successor-version"` response headers plus an INFO log on `app.api.deprecation`. Closes ROADMAP_v0.3.2.md line 201.
- Includes a stub `@deprecated(sunset_date, replacement, reason)` decorator in `app/routes/api.py` that performs the header writes and logging — accepts the kwargs from Phase 37.2 so the regression test compiles and locks the contract before the full Phase 37.2 implementation lands.

### Added — Phase 37.1: API compatibility policy doc

- New `docs/API_COMPATIBILITY.md` — the stated contract between this codebase and any API / webhook consumer. Enumerates what MAY NOT change within a major version prefix (endpoints, field names, field types, error codes, event names, webhook envelope shape), what MAY change non-breakingly (new fields, new codes, new events, stricter validation), and the deprecation process every breaking change must go through (at minimum one release of `Deprecation`/`Sunset`/`Link` headers + CHANGELOG `Deprecated` entry + explicit removal release). Paired with the existing `docs/UPGRADE.md` which guarantees data survival; this doc closes the orthogonal consumer-contract gap.
- Cross-references to `docs/API_COMPATIBILITY.md` added from `README.md` (Backup / REST API section), `docs/PRODUCTION.md` §9 (Upgrades), and a new `docs/API.md` stub that contextualises the OpenAPI spec — so a reader landing in any of the three entry points can find the compat contract without grepping.
- Deferred to v0.3.3 or v0.4.0: the `@deprecated` decorator (37.2), OpenAPI spec drift guard (37.3), CHANGELOG-enforcement CI grep (37.4). The policy is the load-bearing piece; the plumbing lands as individual endpoints reach their first deprecation.
- Deferred to v0.3.3 or v0.4.0: the `@deprecated` decorator (37.2), OpenAPI spec drift guard (37.3). The policy is the load-bearing piece; the plumbing lands as individual endpoints reach their first deprecation.

### Added — Phase 37.4: CHANGELOG `Deprecated` section + CI grep-guard

- Permanent `### Deprecated` subsection template under every `[Unreleased]` heading in `CHANGELOG.md`. When an operator deprecates an endpoint, the one-line note lands here — the empty section is the slot, and the CI guard below ensures it gets filled.
- New `quality`-job step in `.github/workflows/ci.yml` (after the SQL-interpolation grep, before the `debug=True` guard) that fails the build when a PR adds `@deprecated(` to anything under `app/` without a matching addition under a `### Deprecated` heading in `CHANGELOG.md`. Push events to main are a no-op (no diff base). The error message points to `docs/API_COMPATIBILITY.md` for the deprecation contract.
- Deferred to v0.3.3 or v0.4.0: OpenAPI spec drift guard (37.3) and CHANGELOG-enforcement CI grep (37.4). 37.2 (`@deprecated` decorator + metric + envelope plumbing) lands in this release.

### Security — Phase 25.3: bounded webhook-dispatch thread pool (#47)

- `dispatch_event_async` rewritten from per-event daemon threads to a module-level `concurrent.futures.ThreadPoolExecutor` (16 workers, shared across all events). Before the rewrite a bulk admin action publishing 50 posts to 5 subscribers would spawn 250 threads; a runaway event loop could OOM the process.
- Queue bounded at 1000 pending tasks. When the work queue exceeds that threshold, new events are dropped (the submit call is skipped), `webhook_drops_total` increments, and a WARNING log line names the event + subscriber. Operators can see overruns in the logs immediately; Prometheus-counter integration lands with the follow-up in v0.3.3.
- Public API change: `dispatch_event_async` now returns `list[concurrent.futures.Future]` instead of `list[threading.Thread]`. The single test that read `.daemon` was updated to verify the Future contract.

### Added — Phase 25.1: `manage.py purge-all` (#42, #55, #62, #68)

- New CLI command runs every retention purge in one invocation: `page_views` (default 90d), `login_attempts` (30d), `webhook_deliveries` (30d), `admin_activity_log` (90d). Each retention window reads from a settings registry key (`page_views_retention_days`, etc.) so operators can tune via the admin UI without a config change. Individual purge failures never abort the others — exit code is non-zero if any errored. Each successful purge writes a `purge_last_success_<table>` setting so a future admin-dashboard "Retention" card can display freshness without having to parse logs.
- Integration test seeds one expired row per table, invokes the CLI as a subprocess, and asserts every table was purged plus the four freshness stamps landed.
- Deferred: systemd `resume-site-purge.timer` unit, compose.yaml cron snippet, admin dashboard widget — the CLI is the load-bearing piece, host-level timer plumbing is operator-specific.

### Changed — Phase 27.3: SMTP failures now surface in logs (#23)

- `send_contact_email` emits a WARNING log on any SMTP failure (`SMTP delivery failed: <ExceptionType> (host=... port=...)`) instead of silently returning False. The exception type only (not the message body) is logged so a server-side detail leak in the SMTP error text doesn't end up in log aggregators. Submissions are already persisted by the route; this adds operator visibility without changing the "no data lost on SMTP failure" contract.
- Deferred: new `mail_send_errors_total{reason}` Prometheus counter + `contact_submissions.smtp_status` column — needs migration 013 and is best paired with the admin dashboard widget.

### Security — Phase 27: correctness bugs (bulk actions, null bytes, open redirect, CSP report)

- **27.1 bulk actions (#20)** — `window.bulkAction` now sends the CSRF token as the `X-CSRFToken` header. Previously every bulk-action click from the admin UI returned 400, making the v0.3.0 Phase 14.3 feature unusable.
- **27.4 email validation (#39)** — HTML contact form now runs a real regex for the email shape instead of `'@' in email and '.' in email`. Rejects `@.`, `a@.`, `a@a`, `user@host..com`, etc.
- **27.5 null bytes in contact (#13)** — free-text fields containing `\x00` are rejected with a user-visible flash; no row lands in the DB. Previously the byte was stored verbatim.
- **27.6 open redirect on `/set-locale/<lang>` (#21, #40)** — the redirect target is now validated for same-origin before being echoed in the 302. External Referer headers and scheme-relative `//evil.example` paths fall back to `/`.
- **27.7 `/csp-report` rate limit + content-type gate (#32)** — non-JSON / non-CSP content types are silently 204'd, and the endpoint now carries a 60/minute per-IP rate limit to stop a bot from flooding `app.security`.

### Security — Phase 24.1: `/readyz` minimalism (#65)

- `/readyz` now returns `{"ready": true/false, "failed": "<name>"}` to untrusted callers. No filesystem paths, no exception class names, no migration filenames, no byte counts. Full detail lives in the `app.readyz` WARNING log line (request-id attached), so operators retain diagnosis fidelity; anonymous scrapers get zero actionable surface.
- Detailed body (with `checks: {...}`) gated on `metrics_allowed_networks` — same trust model as `/metrics`. Failure tests that used to assert on `body['detail']` opt into the detailed view via a one-line `_enable_readyz_detail` helper.

### Security — Phase 24.2: analytics and contact privacy (#45, #60)

- `page_views.ip_address` is now a 16-char salted SHA-256 of the effective client IP, not the raw address. `page_views.user_agent` is collapsed to one of ten tokens — `{firefox,chrome,safari,edge}-{desktop,mobile}`, `bot`, `other` — via new `classify_user_agent` in `app/services/logging.py`. A precise visitor UA can't be joined back from logs, which honours the "privacy-respecting" contract the docs already claimed.
- `contact_submissions.ip_address` and `.user_agent` get the same treatment on both the HTML form path (`app/routes/contact.py`) and the JSON API path (`app/routes/api.py`). Rate limiting still works: the hash is stable per-IP so `count_recent_submissions` sees the same 5-per-window for the same visitor.
- Deferred to a follow-up: migration to backfill historical rows and an admin "hashed-IP prefix" widget. Operators who want the legacy raw IPs gone can run `python manage.py purge-analytics --days 0`.

### Security — Phase 24.3: log-injection hygiene (#22)

- New `sanitize_log_field` helper in `app/services/logging.py`. Escapes `\r` / `\n` / `\t` to their visible backslash form, strips ANSI escape sequences (so a crafted payload can't rewrite a tailing operator's terminal), and truncates to 500 chars with an explicit `…` marker.
- `/csp-report` handler routes `violated-directive`, `blocked-uri`, `document-uri` through the helper before the `%s` formatter. Previously these logged verbatim, so a crafted POST could splice fake log lines below the legitimate one.

### Security — Phase 24.4: `Server` / `X-Powered-By` header stripped (#14)

- `after_request` handler now deletes the `Server` header (and any `X-Powered-By` a middleware might set) before the response goes out. Werkzeug / Gunicorn advertise their exact version by default, which hands an attacker the CVE list to try. The response shape is otherwise unchanged.
- Multi-route regression test `test_no_server_header_on_any_route` in `tests/test_security.py` sweeps `/`, `/healthz`, `/readyz`, `/admin/login`, `/blog/` so a future regression in error handlers, health-check shortcuts, or admin-form rendering can't re-leak the header on a surface the original single-route test doesn't touch.

### Security — Phase 23.2: one `get_client_ip()` helper (#34)

- Extracted `app/services/request_ip.py:get_client_ip(request)` that walks `X-Forwarded-For` right-to-left against the `trusted_proxies` set and returns the first untrusted IP (the real client). Previously the XFF-trust logic was copy-pasted across five files with subtly different shapes — four of them (`contact.py`, `api.py`, `analytics.py`, `metrics.py`, `__init__.py`'s login-throttle hash) trusted XFF blindly, which let any direct-exposure caller spoof their source IP for rate limiting, analytics bucketing, and login lockout. The v0.3.1 Phase 22.6 interim fix on `admin.py` used a "leftmost when peer is trusted" algorithm that was still spoofable from behind an honest proxy; the right-to-left walk is the correct boundary.
- Every inlined XFF split replaced with a call to the helper. The `test_no_inlined_xff_logic_remaining` grep-guard scans `app/**/*.py` and fails CI if a future route grows its own copy.
- Seven unit tests cover the algorithm: no-XFF, empty-trusted, peer-not-trusted, right-to-left walk with forged leftmost, all-entries-trusted fallback, IPv6, malformed middle entry.

### Security — Phase 23.3: constant-time admin credentials (#38, #46)

- Username compare switched to `hmac.compare_digest`. The old `==` compare leaked bytewise match progress through wall-clock timing — enough signal to brute-force the admin username a character at a time over ~200 requests per character.
- `check_password_hash` now runs **always**, even on a username miss. A module-level dummy hash (`generate_password_hash(secrets.token_urlsafe(32))`) absorbs the scrypt work when the username doesn't match, so "unknown username" and "valid username, bad password" take the same wall-clock time within the scrypt noise floor. Before this fix, a single request answered "does this username exist" in microseconds.
- Regression test asserts the real-vs-dummy scrypt cost is within 2x (both > 1 ms — i.e. both are paying real scrypt work, not a degraded cheap-path). Second test confirms both failure branches record a `login_attempts` row so neither short-circuits past the lockout counter.

### Security — Phase 23.4: weak `secret_key` is fatal at boot (#48) — BREAKING CHANGE

- `_validate_secret_key` now **rejects** (aborts app startup) on three conditions that were previously warn-and-continue: (a) length under 32 chars, (b) well-known placeholder values, (c) missing entirely (already fatal). The warn paths silently shipped a site signing cookies with a guessable key, trivially forgeable.
- The weak-key set picks up `CHANGE-ME-generate-a-random-key` (the `config.example.yaml` default, which was previously missed by the exact-match check) plus other common placeholders. The internal test marker `test-secret-key-for-testing-only` was removed from the set — it's 32 chars exactly and the conftest fixtures use it; not a realistic production placeholder.
- **Breaking change for operators running a placeholder / short key**: the container will fail to start with a readable error pointing to `python manage.py generate-secret`. Generate a real key, update `config.yaml` / the `RESUME_SITE_SECRET_KEY` secret, restart. Forget to, and every admin session cookie issued after the rotation is invalid (the session epoch bump from logout won't help — the signing key itself changed) — visitors keep browsing, admin just re-logs in once. No data migration needed.

### Security — Phase 23.5: header injection, body limits, tabnabbing, host-header spoof

- **#35 Email header injection on contact form:** added `_contains_header_injection` guard that rejects CR/LF/NUL in submitter name or email before constructing the `MIMEMultipart`. Reply-To switched from raw string assignment to `email.utils.formataddr` so display-name encoding handles accented characters correctly. Regression tests for both name-field and email-field injection attempts; one test asserts an accented `Amélie Dupont` legitimate name still sends.
- **#37 `MAX_CONTENT_LENGTH`:** set `app.config['MAX_CONTENT_LENGTH']` (default 16 MiB, overridable via new YAML key `max_request_size`). Werkzeug rejects oversized requests before any view code runs; the existing WAF-lite request filter may pre-empt with 400. Regression test asserts rejection regardless of which layer catches it.
- **#57 Host header injection:** new `canonical_host` YAML key + `app/services/urls.py:canonical_url_root()` helper. `/sitemap.xml`, `/robots.txt`, and `/blog/feed.xml` now build absolute URLs via the helper instead of `request.url_root`. When `canonical_host` is unset, fallback to `request.url_root` preserves pre-23.5 behaviour so existing deployments are unchanged until they opt in. Two tests: Host spoof against `canonical_host` is ignored; unset fallback still works.
- **#67 `target="_blank"` tabnabbing:** removed the `link_rel=None` override in `sanitize_html` so nh3 injects `rel="noopener noreferrer"` on every admin-authored `<a>`. Had to drop `rel` from `_ALLOWED_ATTRS['a']` (nh3 panics otherwise) — admin links can no longer carry custom rel values, but the default is now safe for every one of them.

### Security — Phase 23.6: settings and upload input validation

- **#18 `save_many` bool flip** — rewrote to iterate over submitted form keys, not `SETTINGS_REGISTRY`. Previously a save from one category silently reset every bool in every OTHER category to `false`. The admin form happened to submit every bool as a `<select>` so the HTML path was not observably affected, but API callers and any future partial-save flow were exposed. Two regression tests: `preserves_unrelated_bools` and `writes_submitted_bool_false`.
- **#25 JSON settings validators** — `nav_order` and `homepage_layout` are now validated at POST time by `_validate_json_list_of_strings` and `_validate_homepage_layout`. Malformed JSON or wrong type or missing required fields → 400 with a human-readable flash. `contextlib.suppress` still protects the display-side read so a legacy bad row doesn't crash the site.
- **#50 `display_tier` on photo upload** — HTML upload handler now validates `display_tier` against `{featured, grid, hidden}` before the INSERT, rejecting unknown values and cleaning up the quarantine file. The REST API path already had this check; the HTML path catches up.

### Security — Phase 23.1: session revocation (#33, #51)

- **#51 Cookie replay bypass on `blog_admin_bp` closed.** `check_session_epoch` was registered as a `before_request` hook on `admin_bp` only; the sibling `blog_admin_bp` (everything under `/admin/blog`) did not re-register it, so a captured pre-logout cookie kept authenticating against the blog admin routes for the cookie's full lifetime. Re-registered the guard on `blog_admin_bp` and added `test_admin_blueprint_middleware_parity` in `tests/test_security.py` that asserts the security-critical middleware set matches between the two blueprints — a future `*_admin_bp` forgetting the guard now fails CI.
- **#33 Cross-worker revocation race closed.** Logout bumps `_admin_session_epoch` in the settings table, but peer Gunicorn workers were reading it through the 30 s TTL'd `get_all_cached` — a captured cookie stayed valid on those workers for up to the TTL window. Added `settings_svc.get_uncached(db, key, default)` which issues a dedicated SELECT, bypasses the cache, and does **not** poison the cache with the fresh value. `check_session_epoch`, the login-side epoch stamp, and the logout-side epoch bump all switched to `get_uncached`. The per-request cost is one integer lookup on SQLite — negligible vs. the scrypt compare that already happens on login, and the request-time tradeoff is explicit: a 30 s window of accepted-but-revoked cookies would not survive an audit.
- Two regression tests lock in the revocation SLA: `test_logout_revokes_cookie_on_another_client` (two Flask test clients sharing a cookie; one logs out, the other's next `/admin/` is a 302-to-login in **< 250 ms**), and `test_logout_revokes_cookie_on_blog_admin_routes` (same pattern against `/admin/blog`).

---

## [Unreleased] — v0.3.1 (Keystone)

### Docs — Phase 22.5: PRODUCTION.md reverse-proxy callout closed (#66)
- The §3.5.1 "Reverse-proxy binding and the X-Forwarded-For trust model" callout was already drafted as part of the Phase 22 PRODUCTION.md rewrite; the roadmap checkbox is now ticked. The callout warns that exposing port 8080 directly to the public internet is unsafe until `get_client_ip()` extraction lands in v0.3.2 Phase 23.2 (issues #16 / #34 — admin allowlist hardened, four other XFF callsites still trust the header unconditionally).
### Documentation — Phase 36.6: observability runbook cross-reference (#18.11)

- `docs/PRODUCTION.md` §7.3 now links to `docs/OBSERVABILITY_RUNBOOK.md` as a single coherent paragraph. The runbook covers the "when to reach for which tool" decision tree, the Prometheus + Grafana + Alertmanager wiring, and the synthetic-monitoring tiers. Closes the v0.3.0 Phase 18.11 carry-over: the anchor stub left for Agent C is now a live cross-reference. The `{#observability-runbook}` anchor is preserved so any external bookmarks still resolve.
### Documentation — Phase 36.8: K8s / Nomad commented-example manifests (#21.4)

- New §13.1 in `docs/PRODUCTION.md` ships a complete Kubernetes Deployment + Service + Ingress YAML block: tag form (`ghcr.io/kit3713/resume-site:v0.3.1`) plus a digest-pin alternative (`@sha256:...`) commented inline; volume mounts mirror `compose.yaml` (`/app/data`, `/app/photos`, `/app/backups`); `livenessProbe` on `/healthz` and `readinessProbe` on `/readyz` with the Phase 21.2 contract values (`initialDelaySeconds: 5, failureThreshold: 3`); Service exposes 8080; Ingress shows TLS termination via cert-manager annotations (commented so operators terminating elsewhere can swap them out). New §13.2 mirrors the same probe contract into a Nomad job spec for operators on that stack. The §13 anchor stub left by Phase 35 is now closed.
### Verified — Phase 36.1: JavaScript audit (v0.3.0 Phase 12.3 carry-over)
- Profiled `app/static/js/main.js` (the only public-page bundle; no `admin.js` exists — admin pages load only the inline scripts inside their templates) for unused functions, redundant event listeners, and GSAP animations firing on elements absent from the current page. **No dead code found:** every declared function (`setTheme`, `openLightbox`, `closeLightbox`, `highlightStars`) has at least one in-file caller; no element has duplicate bindings to the same event; every GSAP `gsap.from` / `ScrollTrigger.create` call is already preceded by an `if (element)` or `length` check, so the bundle is no-op on `/admin/login`, `/contact`, and other pages that lack `.hero`, `.stats-bar__value`, or card grids. `swagger-init.js` is similarly minimal (one `load` listener, both presets used).
- No removals; the bundle is already audit-clean. Documented here so the v0.3.0 Phase 12.3 carry-over has a written outcome rather than a silent close. Manual browser verification recipe (boot via `RESUME_SITE_DEV=1 python app.py --debug`, console clean on `/` and `/admin/login`, scroll reveals still fire on `/`) lives in the PR description.
### Deprecated

### Added — Content block delete + duplicate-safe create
- New `POST /admin/content/delete/<slug>` route + Delete button on each row of `/admin/content`. The button lives in a tiny POST form with a `confirm()` prompt so an accidental click still needs a second confirmation. Previously the only way to remove a block was raw SQL against the SQLite file — an obvious gap given the admin UI lets you create and edit them.
- `POST /admin/content/new` now detects a duplicate slug (after normalising to lowercase + underscores). When a collision exists it flashes `A content block with slug "<slug>" already exists. Edit it instead.` and redirects to the edit form for that existing block, instead of silently no-op'ing via `INSERT OR IGNORE` and falsely flashing "Content block created."
- After a successful create, the operator is now redirected to the edit page for the new block (not the list). Eliminates the extra click to open the editor and populate content.
- The content editor (`admin/content_edit.html`) pre-fills the hidden `contentInput` with the existing block content and subscribes to Quill's `text-change` event (not only the form-submit event). If the CDN fails, Quill errors during init, or the submit handler is not attached in time, the save still carries the existing content instead of zeroing it out. Added a plain-`<textarea>` fallback branch for the case where the Quill script never loads.
- 4 regression tests in `tests/test_admin.py`: slug normalisation ("About" → "about"), duplicate-slug flash + redirect-to-edit, delete removes the row, delete of unknown slug is a 302 (not a 500).

### Fixed — Theme editor save crash
- `POST /admin/theme` raised `ImportError: cannot import name 'save_setting'` on every save, returning a 500 to the operator. Root cause: the route imported a symbol that was never exported by `app/services/settings_svc.py` (the module exports `set_one` / `save_many`). Swapped `save_setting` → `set_one` (which already commits + invalidates cache), so accent / preset / font / custom_css all persist through the form. Locked in with `test_theme_editor_save_persists_values` in `tests/test_customization.py` — a regression here now fails CI instead of reaching production.

### Added — Customizable "Services" label
- New `services_label` and `services_subtitle` settings under the Content category. Overrides the "Services" heading, nav link, page title, and CTA button text across the public site (homepage preview, /services page, nav bar). Empty = fall back to the default "Services". Typical use: rebrand the section to "Skills", "What I Do", "Expertise", etc. without editing code.
- `admin/settings.html` now surfaces each registered setting's `description` field for `str`-type inputs — previously only `textarea` inputs showed help text, so the new settings' descriptions (and any future str-type descriptions) render in the form.

### Added — SMTP sender decoupling (v0.3.1-beta-2)
- New `smtp.from_address` config field + `RESUME_SITE_SMTP_FROM_ADDRESS` env var. Populates the `From` header of outbound mail independently of the SMTP login identity — required for relay providers where the authenticated user is a fixed service account but the sender must be an operator-controlled verified-domain address. Known-good pairings: Resend (`user: "resend"` + `from_address: "contact@yourdomain.com"`), SendGrid (`user: "apikey"` + verified sender), Mailgun (`user: "postmaster@mg.yourdomain.com"` + verified sender). Falls back to `smtp.user` when unset — no behavior change or config migration required for existing Gmail / Outlook / self-hosted Postfix deployments.
- `config.schema.json` and `config.example.yaml` updated. Schema validator accepts the new key; example shows the three provider pairings inline so operators can copy-paste.
- 4 regression tests in `tests/test_mail.py` lock in: (a) new-provider path — `from_address` populates `From`, login credentials stay with `user`; (b) backward-compat path — unset `from_address` falls back to `user`; (c) empty-string override path — explicit blank still falls back; (d) `Reply-To` invariant — the sender-decoupling change does not bleed the operator's From address into Reply-To, so replies still route to the form submitter.

### Added — Phase 35: Release Publication Gate
- `.github/RELEASE_TEMPLATE.md` — required release-notes skeleton. Three commands no release omits: `podman pull ghcr.io/kit3713/resume-site:vX.Y.Z`, the `@sha256:` digest pin, and the `cosign verify` invocation with the OIDC issuer + identity-regexp baked in. Required headings for "Breaking changes" and "Migration notes" — operators rely on the section being present so they can grep for it across releases, even when the answer is "None." A new release that does not honour the template is a stop-ship.
- `release-verify` CI job — multi-arch smoke test that pulls the just-pushed `:vX.Y.Z` manifest for both `linux/amd64` and `linux/arm64` on a clean runner, boots each variant against a minimal `verify-config.yaml`, and asserts `/healthz` + `/readyz` answer green. arm64 runs through QEMU emulation registered via `setup-qemu-action` (allows a 90s start window vs amd64's 30s — Pillow / Cython init under emulation is ~3x slower). A failure on either arch leaves `:latest` un-promoted; operators tracking `:latest` are not silently moved to a broken multi-arch build.
- Tag matrix promotion in `publish` job — stable releases now advance `v<MAJOR>.<MINOR>` and `v<MAJOR>` lifecycle aliases via `docker buildx imagetools create` after the multi-arch build pushes the immutable `vX.Y.Z` digest. No image data re-upload — the alias is a pure registry-side pointer to the same multi-arch manifest. Pre-release tags (`vX.Y.Z-rc.1`, `-beta.N`) only push their exact tag and never move the lifecycle aliases or `:latest`.
- `:latest` is now gated. `:latest` advancement is the last step of `release-verify`, runs only after both arch smoke tests pass, and only for stable (non-pre-release) tags. Operators tracking `:latest` are by construction tracking "the most recent release that survived the gate."

### Changed — Phase 35: GHCR-first documentation
- `README.md` rewrite — `podman pull ghcr.io/kit3713/resume-site:v0.3.1` is now the headline install path, immediately followed by the `cosign verify` invocation. The full tag-matrix table (with `vX.Y.Z` / `vX.Y` / `vX` / `latest` semantics and the `:main` non-production caveat) sits inline under Quick Start so operators see it before they make a deployment decision. The legacy "Local development" install path was demoted out of Quick Start into a new `## Development` section near the bottom of the README — sourcing a clone is now explicitly framed as "you only need this to modify the project, not to deploy it."
- `docs/PRODUCTION.md` reorientation — new TL;DR callout above §1 establishes GHCR as the canonical artifact. New `§3.0 Pull and verify the signed image` runs first in the deploy checklist (before secret generation in §3.1) so the image is verified before any operator-facing step touches it. New `§12 Release publication gate` section documents the tag matrix + the full stop-ship rule set (every gate is a full stop, not a ratchet) + the `vX.Y.Z-rc.1` dry-run requirement. Anchor stubs left for Agent A's reverse-proxy XFF callout (§3.5.1, Phase 22.5), Agent C's observability cross-reference (§7.3, Phase 36.6), and Agent C's k8s/Nomad manifests (§13, Phase 36.8) — those drafts merge into stable headings rather than being added ad-hoc.
- `Container Image` section in README dedup'd — the per-tag `podman pull` examples lived in two places; the canonical pull command is now in Quick Start, and the Container Image section links back rather than duplicating.
- Cosign verify is now in the upgrade flow — both README's "Upgrading" section and PRODUCTION.md §9 show `cosign verify` as a required step before restart, not an optional aside.
- Shipped `compose.yaml` and `resume-site.container` now pin to `:v0.3.1` instead of `:latest`. Operators who `podman compose pull` or `podman auto-update` no longer silently roll forward to a new release. The Quadlet `AutoUpdate=registry` line is commented out (it is a no-op against an immutable tag); operators who prefer rolling auto-upgrades switch the tag back to `:latest` and re-enable the directive. Inline comments on both files walk through the digest-pin alternative and cross-reference `docs/PRODUCTION.md §3.0`.

---

## [Unreleased] — v0.3.0

### Deprecated

### Changed — Phase 18.5 / 18.14: PERFORMANCE.md baselines
- New "Phase 18.14 Baseline (v0.3.0-beta)" section with p50 / p95 / query-count / response-size for the five hot-path public routes, captured 2026-04-18 via `RESUME_SITE_LOG_LEVEL=WARNING python scripts/benchmark_routes.py 200`. Numbers regressed from 18ms to ~2ms p50 vs. the Phase 12.1 baseline — Python 3.14 + CPython JIT defaults on the hot request path. Query counts are now the strict-monotonic regression floor: landing / blog_index / blog_post each picked up +1 query (Phase 15.4 translation overlay + Phase 17.2 backup-timestamp settings read), which is the new floor.
- "Container Startup Time" table filled in with real measurements from `ghcr.io/kit3713/resume-site:0.3.2-beta`: cold start to first 200 on `/readyz` is 2.20 – 2.30 s (median 2.26 s across three runs with wiped volumes so the entrypoint's `init-db` runs fresh every time). Image size 217 MB uncompressed amd64. The 2.3 s cold start comfortably undersigns the `HEALTHCHECK --start-period=10s` budget in `Containerfile`.
- "Load Testing Baseline" table kept intentionally empty with a procedural note — captured against an in-process Flask test client would be misleading because locust is specifically for the real gunicorn + network + reverse-proxy path. The copy-pasteable `locust -u 50 -r 5 -t 5m` invocation is parked in the section so whoever captures the v0.3.0-rc numbers doesn't have to rebuild the knowledge.

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

### Deprecated

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
