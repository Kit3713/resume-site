# resume-site v0.3.2 Roadmap

> **Codename:** Shield
> **Status:** Planning
> **Baseline:** v0.3.1 (Keystone — first release through the publication gate)
> **Target:** Tier-2 security hardening, information-disclosure cleanup, operational hygiene for unbounded tables, the bulk of correctness bug fixes from the 2026-04-18/19 audit, and a new formal API compatibility / deprecation policy that closes the one real wire-compatibility gap.

---

## Why "Shield"

v0.3.1 plugged the exploitable holes. v0.3.2 closes the *second tier* — the "should have been right the first time" findings the deep audit shook loose: session revocation races, header injection, JSON settings validation, unbounded retention tables, information leaks via `/readyz`. None of these are a one-HTTP-request exploit on their own; each is a real ladder rung for someone who already has a local pivot. Shipping them together lets operators make one upgrade and be done with the audit backlog.

The new piece — **Phase 37, a formal API compatibility / deprecation policy** — is the only wire-compatibility gap we uncovered during the v0.3.5 split debate. The existing schema-versioning, reversibility checker, and rollback plumbing (`docs/UPGRADE.md`, `schema_version`, `manage.py migrate --verify-reversible`) already solve "data survives upgrade." Phase 37 closes the consumer-facing version of the same question: "my API / webhook consumer survives a field rename."

---

## Scope Summary

| Category | Source | Phase |
|---|---|---|
| Auth / session / header / input-validation hardening | Issues #18, #25, #33, #34, #35, #37, #38, #46, #48, #50, #51, #57, #67 | 23 |
| Information disclosure and privacy | Issues #14, #22, #45, #60, #65 | 24 |
| Operational hygiene (unbounded tables, purge timers, hot-path writes, thread limits) | Issues #42, #47, #49, #55, #62, #68 | 25 |
| Correctness bugs and reliability | Issues #13, #20, #21/#40, #23, #24, #26, #32, #39 | 27 |
| API compatibility and deprecation policy (new) | Identified during v0.3.5 split debate (2026-04-20) | 37 |

### Out of Scope (→ v0.3.3 or v0.4.0+)

- Performance wins → **v0.3.3 Phase 26**
- CI / packaging / tooling hygiene → **v0.3.3 Phase 28**
- Code redundancy audit closeout (#56) → **v0.3.3 Phase 29**
- DAST, Playwright, load-test gate, mutation baseline, edge-case methodology → **v0.3.3 Phases 30-34**
- Multi-user, public login, RBAC, OAuth/OIDC, PostgreSQL, plugin architecture → **v0.4.0+**

---

## Phase 23 — Auth, Session, Header, and Input-Validation Hardening

*Tier-2 security. Each item is real, but requires a local pivot / timing measurement / specific deployment shape to be exploited. These are the "should have been right the first time" fixes the v0.3.0 audit shook loose.*

### 23.1 — Session revocation: close both gaps (#33, #51)

- [x] **#51 Bypass on `blog_admin_bp`:** `check_session_epoch` is registered on `admin_bp` only. The separate `blog_admin_bp` (mounted under the same `/admin` prefix) does **not** re-register it, so a captured cookie survives a logout for the lifetime of the cookie. Register the full admin middleware bundle on `blog_admin_bp` in `app/routes/blog_admin.py:59-62` and add a regression test that iterates every registered blueprint and asserts the middleware set matches `admin_bp`.
- [x] **#33 Race across workers:** The current design bumps `_admin_session_epoch` in the settings table on logout. Other Gunicorn workers keep accepting the cookie for up to 30 s — the settings cache TTL. Pub-sub is overkill; the right fix is to bypass the cache specifically for this one key. Add a `settings_svc.get_uncached(key)` helper and have `check_session_epoch` call it. Accept the extra query per admin request.
- [x] Regression test: two simultaneous clients with the same cookie, one logs out, the other's next admin request is 401 within 250 ms.

### 23.2 — One `get_client_ip()` helper to rule them all (#34)

- [x] The `X-Forwarded-For`-trusting pattern is copy-pasted across five files: `admin.py`, `contact.py`, `api.py`, `analytics.py`, and `login_throttle.py`. Each is independently spoof-bypassable on non-proxied deployments. v0.3.1 Phase 22.6 landed the fix on `admin.py` only as an interim.
- [x] Extract `app/services/request_ip.py:get_client_ip(request)` that (a) consults the `trusted_proxies` config from 22.6, (b) walks the XFF chain right-to-left looking for the first untrusted IP, (c) falls back to `request.remote_addr` otherwise. Document the algorithm alongside the function.
- [x] Replace every call site; delete the inlined duplicates (including the interim copy in `admin.py`). Preserve the behaviour for the login throttle hash.
- [x] Unit tests: spoofed XFF with no trusted proxies, spoofed XFF with a trusted proxy, IPv6, chained XFF, empty XFF. Plus a grep-guard regression test that scans `app/**/*.py` for the inlined anti-pattern and fails CI if a new copy appears.

### 23.3 — Constant-time admin credentials (#38, #46)

- [x] **#38 Constant-time username comparison:** `app/routes/admin.py:280-284` uses `==`. Switch to `hmac.compare_digest(username.encode(), admin_username.encode())`.
- [x] **#46 Close the scrypt-skip side-channel:** Even with constant-time username compare, short-circuiting on `and` skips `check_password_hash` on username mismatch. Always run `check_password_hash` against a fixed dummy hash when the username doesn't match, so the wall-clock cost of a hit and a miss is indistinguishable at the rate-limit window. Dummy hash generated once at app boot from a random password.
- [x] Regression test: `test_login_scrypt_cost_paid_on_username_miss` asserts the real-vs-dummy scrypt cost is within 2x (both > 1 ms), and `test_login_username_miss_does_not_short_circuit` asserts the unknown-username and bad-password paths both hit `login_attempts` so neither short-circuits.

### 23.4 — `secret_key` strength: fatal, not advisory (#48)

- [x] `_validate_secret_key` currently warns on length < 32, warns on well-known placeholders, warns on weak keys. All three paths fall through to `return True`. Flip them all to `return False` (fatal at boot). Leave the "key is missing" path fatal as today.
- [x] `CHANGELOG.md` note: operators with a weak key will now fail to start after upgrade; `manage.py generate-secret` is the escape hatch. Called out in the v0.3.2 release notes as a **Breaking change** entry.

### 23.5 — Header injection and body-limit gaps

- [x] **#35 Email header injection on contact form:** `app/services/mail.py` assigns user input directly to `Subject` and `Reply-To`. Added `_contains_header_injection` guard that rejects CR/LF/NUL in name or email before constructing the `MIMEMultipart`, and switched Reply-To to `email.utils.formataddr`. Two regression tests assert CRLF in name and LF in email both cause `send_contact_email` to return False with no message composed.
- [x] **#37 `MAX_CONTENT_LENGTH`:** Added `app.config['MAX_CONTENT_LENGTH']` at app-factory time (default 16 MiB, configurable via new `max_request_size` YAML key). Werkzeug rejects with 413 before view code runs; the existing WAF-lite filter may pre-empt with 400 on some paths. Regression test asserts rejection regardless of which layer fires first.
- [x] **#57 Host header injection:** New `canonical_host` YAML key + `app/services/urls.py:canonical_url_root()` helper. `/sitemap.xml`, `/robots.txt`, and `/blog/feed.xml` now build absolute URLs via the helper. When `canonical_host` is unset, falls back to `request.url_root` — no behaviour change for existing deployments. Two regression tests: Host-spoof against `canonical_host` is ignored, and unset `canonical_host` preserves pre-23.5 behaviour.
- [x] **#67 `target="_blank"` tabnabbing:** Removed the `link_rel=None` override in `sanitize_html`; nh3 now injects `rel="noopener noreferrer"` on every admin-authored `<a>`. `rel` had to come out of the attribute allowlist — nh3 panics otherwise — so admin links can no longer carry custom rel values. Regression test asserts the attribute is present on a `target="_blank"` anchor. Gunicorn `--limit-request-*` flags deferred — CSP body limit is the primary control.

### 23.6 — Settings / upload input validation (#18, #25, #50)

- [x] **#18 `save_many` bool flip:** Rewrote `save_many` to iterate over the submitted form keys, not over `SETTINGS_REGISTRY`. Only bool keys actually present in the form get written; unrelated bools in other categories stay at their existing value. Regression test `test_save_many_preserves_unrelated_bools` locks the new behaviour and `test_save_many_writes_submitted_bool_false` confirms explicit `false` still saves.
- [x] **#25 JSON settings schema validation:** Added `_validate_json_list_of_strings` and `_validate_homepage_layout` validators in `admin.py`. `nav_order` and `homepage_layout` go through these before `save_many`; malformed JSON / wrong type / missing required fields → 400 with a human-readable error flash naming the offending field. `custom_nav_links` continues to use the existing `_validate_custom_nav_links`. Three regression tests.
- [x] **#50 `display_tier` on photo upload:** HTML admin upload now validates `display_tier` against `{featured, grid, hidden}` before the INSERT; rejection flashes an error and cleans up the quarantined file so disk doesn't leak. The REST API path already had this check — the fix brings the two write paths to parity.

---

## Phase 24 — Information Disclosure and Privacy

*Nothing here is exploitable as a privilege escalation, but each leak either reveals attack surface or violates the "privacy-respecting" contract the docs already claim.*

### 24.1 — `/readyz` minimalism (#65)

- [x] `/readyz` now returns a minimal `{"ready": true}` or `{"ready": false, "failed": "<check_name>"}` body to external callers. The full detail (absolute paths, exception types, migration filenames, byte counts) lives in the `app.readyz` WARNING log line with the request id attached, so operators retain the signal but anonymous scrapers get nothing actionable.
- [x] Detailed response gated on `metrics_allowed_networks` — same trust model as `/metrics`. Disallowed clients get the collapsed body. Existing failure-mode tests now call a `_enable_readyz_detail(app)` helper to opt into the detailed view.
- [x] New `test_readyz_detailed_body_for_trusted_client` covers the allowlisted path; the happy-path success test now asserts `'checks' not in body` and `'detail' not in body` on the untrusted default.

### 24.2 — Analytics and contact privacy (#45, #60)

- [x] **#45 `page_views` privacy:** the hot-path insert in `app/services/analytics.py` now hashes the client IP (salted with `secret_key`) and collapses the User-Agent via a new `classify_user_agent` helper to one of ten enum tokens (`{firefox,chrome,safari,edge}-{desktop,mobile}`, `bot`, `other`). The raw IP and UA never reach the row. Regression test `test_page_views_stores_hashed_ip_and_ua_class` locks it in.
- [x] **#60 `contact_submissions` privacy:** same treatment on both the HTML form handler (`app/routes/contact.py`) and the JSON API path (`app/routes/api.py`). The rate-limit read (`count_recent_submissions`) now sees the same hash the write produces, so the 5-per-window cap still functions per-IP. Regression test `test_contact_submission_stores_hashed_ip_only`.
- [x] Classifier unit test covers Firefox / Chrome / Safari / Edge, mobile/desktop split, bot detection (curl, Googlebot, python-requests), and the empty-UA fallback.
- [ ] Deferred: migration to backfill historical rows; admin dashboard "hashed IP prefix" widget. Operators who want to wipe the legacy raw-IP rows can run `python manage.py purge-analytics --days 0` today.

### 24.3 — Log-injection and stack-trace hygiene (#22)

- [x] **#22 CSP-report log injection:** new `sanitize_log_field` helper in `app/services/logging.py` does the three-step clean: escape CR/LF/tab to their visible backslash form, strip ANSI escape sequences (so a crafted payload can't rewrite an operator's tailing terminal), and truncate to 500 chars with an explicit `…` marker. The CSP-report handler routes `violated-directive`, `blocked-uri`, and `document-uri` through it before the `%s` formatter.
- [x] Five regression tests: CR/LF/tab escape, ANSI strip, truncation, `None` → `'-'`, end-to-end injection rejection via a crafted POST to `/csp-report`.
- [ ] Deferred: apply the same helper to the request-ID echo path in `app/services/logging.py` (current regex already rejects non-alphanumeric) — out of scope for this phase since the existing validator is strict.

### 24.4 — `Server: gunicorn` header removal (#14)  [COMPLETED]

- [ ] Strip or rewrite the `Server` response header. Two acceptable fixes: (a) set `app.after_request` to pop `Server` (simplest, works for any WSGI server); (b) document the Caddy `header Server "resume-site"` snippet in `docs/PRODUCTION.md` and recommend (a) as the belt inside the suspenders. Ship (a) in-tree.
- [ ] Regression test: every route returns no `Server` header (or exactly the rewritten value).

---

## Phase 25 — Operational Hygiene (Unbounded Tables, Purge Timers, Hot-Path Writes)

*The pattern below — "purge function exists but nothing ever calls it" — appears four times across the v0.3.0 codebase. Fix the pattern once with a single scheduled-purge subsystem instead of four bespoke timers.*

### 25.1 — One `scheduled-tasks` timer that purges everything (#42, #55, #62, #68)

- [x] New CLI command `manage.py purge-all` calls the purge function for every retention-managed table. Retention days read from the settings registry: `page_views_retention_days` (default 90, #55), `login_retention_days` (default 30, #42), `webhook_retention_days` (default 30, #42), `activity_log_retention_days` (default 90, #62/#68). Individual failures do not abort other purges; exit code is non-zero if any errored. Writes a `purge_last_success_<table>` timestamp after each success so the admin dashboard can surface freshness.
- [x] Integration test `tests/test_purge_all.py` seeds one expired row per table, invokes the CLI as a subprocess, and asserts every table purged plus the four freshness stamps landed.
- [ ] Deferred to follow-up: `resume-site-purge.timer`/`.service` systemd units, compose.yaml cron snippet, admin dashboard "Retention" card. The CLI is the substantive piece — host-level timer plumbing is operator-specific and trivial to wire up once the CLI exists.

### 25.2 — `page_views` off the hot path (#49)

- [ ] `track_page_view` currently `INSERT` + `COMMIT`s on every public GET. Under burst load that takes the SQLite write lock on the hot path and contends with every other writer.
- [ ] Replace with a ring-buffered, bounded `queue.Queue` (cap 10k events) + a single background drainer thread that flushes in batches (`INSERT INTO page_views SELECT … FROM temp`) every 2 s or at 500 pending events, whichever first. Queue-full returns silently (analytics is best-effort; dropping a page view is better than blocking the response). Drainer thread started in `create_app` and torn down at exit.
- [ ] Under benchmark `scripts/benchmark_routes.py`: document the per-request savings (target: remove ~1.5 ms p50 from the landing page). Update `PERFORMANCE.md`.
- [ ] Tests: (a) queue-full behaviour, (b) drainer flush on shutdown, (c) correctness under concurrent writers.

### 25.3 — Bounded webhook-dispatch thread pool (#47)

- [x] `dispatch_event_async` rewritten to submit deliveries to a module-level `concurrent.futures.ThreadPoolExecutor` (max 16 workers, shared across all events). Returns `Future` objects instead of `Thread`. Queue bounded at 1000 pending tasks — overflow increments `webhook_drops_total` and logs a WARNING with the event name and subscriber id. The existing `_join_for_tests` kwarg still works, calling `future.result(timeout=10)` on each.
- [x] Updated the one test that asserted `.daemon` (`test_async_threads_are_daemon` → `test_async_dispatch_returns_futures`) to verify the Future contract.
- [ ] Deferred: `webhook_max_workers` settings registry entry (currently hardcoded); Prometheus scrape-time integration of `webhook_drops_total` (counter is in-memory only for now); 500-event burst test.

---

## Phase 27 — Correctness Bugs and Reliability

*Functional bugs with varying blast radius. Ordered by user-visibility.*

### 27.1 — Admin bulk actions don't send CSRF token (#20)

- [x] `window.bulkAction` now sends the `X-CSRFToken` header (read from the CSRF token variable already injected into the admin base template). Flask-WTF accepts the request; bulk actions no longer 400.

### 27.2 — Review submission atomicity (#26)

- [ ] `create_review` + `mark_token_used` span two statements without a transaction. Under concurrent submission, the token can be double-used.
- [ ] Wrap both calls in `with db:` (Python sqlite3 context-manager rolls back on exception, commits on success). Revalidate the token inside the transaction. Regression test: two threads submit the same token concurrently — exactly one wins, the other sees `error: token_already_used`.

### 27.3 — Contact-form SMTP failures surface to the operator (#23)

- [x] `send_contact_email` now emits a WARNING log on any SMTP failure (`SMTP delivery failed: <ExceptionType> (host=... port=...)`). The exception type only, not the message body, so a server-side detail leak in the SMTP error string doesn't flow into log aggregators. The submission is already persisted by the route; this adds operator visibility without changing the "no data lost on SMTP failure" contract.
- [ ] Deferred to follow-up: `mail_send_errors_total{reason}` Prometheus counter; admin-dashboard "Recent SMTP failures" widget driven by a new `contact_submissions.smtp_status` column (needs migration 013).

### 27.4 — Form-validation tightening (#24, #25, #39)

- [x] **#39 Email validator** — replaced `'@' in email and '.' in email` with a real regex (`local@domain.tld` with TLD ≥ 2, rejects consecutive dots, rejects leading/trailing dots). Applied to the HTML contact form; the API path already had stricter validation.
- [x] **#25 JSON settings validation** — closed in 23.6; cross-linked here for completeness.
- [ ] **#24 `content_format` on HTML admin routes** — deferred. The API-side validator exists; the HTML admin path's content_format write is uncommon and low-risk.

### 27.5 — Null-byte handling in contact fields (#13)

- [x] HTML contact form now rejects any field containing `\x00` with a user-visible flash. No row written to the DB. Regression test `test_form_null_byte_in_name_rejected` asserts the submission is dropped and no row appears (was previously stored verbatim). API-side path deferred — the pre-existing `hash_client_ip` path cleans the IP; the name/email/message fields flow through the same Phase 23.5 header-injection guard which also rejects NUL.

### 27.6 — Open redirect on `/set-locale/<lang>` (#21, #40)

- [x] `app/routes/locale.py` now compares the Referer's scheme + netloc to the current request; same-origin redirects go through, everything else (including scheme-relative `//evil.example`) falls back to `/`. Three regression tests: external Referer rejected, same-origin accepted, scheme-relative rejected.

### 27.7 — `/csp-report` rate limit + content-type gate (#32)

- [x] Accept only `application/csp-report`, `application/json`, or empty content-type (browsers occasionally omit the header); other types silently 204'd without logging. 60/minute per-IP rate limit via Flask-Limiter. Two regression tests: `text/plain` POST returns 204 without hitting the log, `application/csp-report` POST is processed normally.

---

## Phase 37 — API Compatibility and Deprecation Policy

*The one real wire-compatibility gap identified during the v0.3.5 split debate. The v0.3.0 infrastructure already solves "my data survives upgrade" via `schema_version` tracking, reversibility checker, `INSERT OR IGNORE` seeds, additive migrations, `pre-restore-*` sidecars, and the `upgrade-simulation` CI job (see `docs/UPGRADE.md`). What it does **not** solve: "my API consumer or webhook subscriber survives a field rename." v0.3.2 closes that gap with a formal policy document plus the HTTP plumbing to enforce it.*

*Important framing: no endpoints are being deprecated in v0.3.2. This phase ships the **machinery**. The first actual use will be the v0.4.0 multi-user work when `/api/v1/admin/*` routes are reshaped around roles — and because the machinery is in place, that reshape can be announced with a sunset date rather than a breaking release.*

### 37.1 — `docs/API_COMPATIBILITY.md`  [COMPLETED]

- [ ] New doc formalising the compat contract for every `/api/v1/*` endpoint and every webhook payload. Three sections:
  - **Guaranteed stable within a major prefix (`/api/v1/`):** URL prefix, documented field names and types in the OpenAPI spec, webhook envelope shape (`event`, `timestamp`, `data` keys), error-code taxonomy, pagination envelope shape, HMAC signature algorithm, `Content-Language` / `Vary: Accept-Language` headers.
  - **Allowed to change within a major prefix (non-breaking):** addition of new fields (consumers must tolerate unknown keys), addition of new error codes (consumers must tolerate unknown codes), addition of new events, addition of new endpoints, tightening of input validation (always backward-compatible from a server perspective).
  - **Deprecation process:** field/endpoint flagged with `deprecated: true` in the OpenAPI spec for at least one full release; `Sunset` response header carries an RFC 3339 removal date at least one minor release in the future; `CHANGELOG.md` "Deprecated" section lists every such flag; removal only in the release named by the `Sunset` header, and only if the flag has been live for at least one prior release.
  - **Breaking change triggers a prefix bump:** `/api/v2/` only for genuinely breaking changes (field rename, type change, removed endpoint with no sunset notice, altered webhook envelope). `/api/v1/` continues to be served during a documented overlap window — minimum two minor releases.
- [ ] Cross-references added to `README.md` (API section), `docs/PRODUCTION.md` (upgrade section), and `docs/API.md`.

### 37.2 — `Sunset` / `Deprecation` HTTP headers

- [ ] `app/routes/api.py` gains a `@deprecated(sunset_date, replacement=None, reason=None)` decorator that:
  - Sets `Deprecation: true` header (RFC 9745 draft — widely enough implemented to be useful).
  - Sets `Sunset: <HTTP-date>` header (RFC 8594) from the passed `sunset_date`.
  - Sets `Link: <replacement_url>; rel="successor-version"` when a replacement is named.
  - Logs the deprecated call at INFO on `app.api.deprecation` with the request id + endpoint + source (user-agent + optional `X-Client-ID`) so operators can see who's still calling.
  - No-ops on responses that have already set these headers (idempotent across decorator stacking).
- [ ] Matching webhook-envelope deprecation: a `deprecated: true` optional key on the webhook `data` payload when the event schema is flagged for removal; a `sunset` ISO-8601 key carries the same date as the HTTP header. Webhook consumers can subscribe to a warning log on first seeing the flag.
- [ ] New metric `resume_site_deprecated_api_calls_total{endpoint}` — lets operators see if any consumer is still hitting a deprecated endpoint as the sunset date approaches.

### 37.3 — OpenAPI spec support

- [ ] Extend the v0.3.0 drift-guard test (`tests/test_openapi_spec.py`) to assert that every operation flagged `deprecated: true` in the spec has the `@deprecated` decorator applied on the Flask route, with the `Sunset` date matching. An operation can't be marked deprecated in only one of the two places.
- [ ] New regression test: a deprecated endpoint served through the test client emits all three response headers (`Deprecation`, `Sunset`, `Link`) and the INFO log line on `app.api.deprecation`.

### 37.4 — CHANGELOG enforcement

- [ ] `CHANGELOG.md` gains a permanent "Deprecated" section under the `[Unreleased]` header. Every `@deprecated` decorator addition requires a matching CHANGELOG entry in the same PR; CI `quality` job greps the diff to enforce this. Zero-cost guardrail — similar to the SQL-interpolation grep from Phase 12.5.

---

## Phase Sequencing

```
Phase 23  (Auth / session / input)    ─── File-disjoint from 24/25. Land 23.2 first (get_client_ip helper).
Phase 24  (Info disclosure)           ─── After 23.2 — shares the client-ip helper.
Phase 25  (Operational hygiene)       ─── After 23.2 — shares the client-ip helper (contact rate limit).
Phase 27  (Bugs)                      ─── Parallel with 25. Disjoint files except 27.4 (shares form-validation helpers with 23.6).
Phase 37  (API compat policy)         ─── Parallel with everything. Docs + decorator + test. File-disjoint from app-code phases.
```

### Parallel Work Streams

```
Stream A (Security + info disclosure):  23 → 24 ──────────────── → release
Stream B (Ops + bugs):                  ─── 25 + 27 ──────────── → release
Stream C (Compat policy):               37 (any sprint; no code-path dependency)
```

---

## New Database Migrations (v0.3.2)

| Migration | Tables/Changes | Phase |
|---|---|---|
| `012_analytics_privacy.sql` | Backfill `page_views.ip_address` to SHA-256 hash; coarsen `user_agent`. Same for `contact_submissions`. | 24.2 |
| `013_contact_smtp_status.sql` | `contact_submissions.smtp_status TEXT NOT NULL DEFAULT 'sent' CHECK(smtp_status IN ('sent','failed','retrying'))` | 27.3 |

---

## New CLI Commands (v0.3.2)

| Command | Purpose | Phase |
|---|---|---|
| `manage.py purge-all` | Purge every retention-managed table in one transaction | 25.1 |

---

## New Settings (v0.3.2)

| Key | Category | Default | Phase |
|---|---|---|---|
| `webhook_max_workers` | Security | `auto` | 25.3 |
| `login_attempts_retention_days` | Security | `30` | 25.1 |
| `webhook_deliveries_retention_days` | Security | `30` | 25.1 |
| `admin_activity_log_retention_days` | Security | `90` | 25.1 |
| `page_views_retention_days` | Security | `90` | 25.1 |
| `canonical_host` | Security | `""` (optional) | 23.5 (#57) |

(`trusted_proxies` already shipped in v0.3.1 as part of the interim 22.6 fix — v0.3.2 extends its usage to every call site via `get_client_ip()`.)

---

## New Documentation (v0.3.2)

| Document | Purpose | Phase |
|---|---|---|
| `docs/API_COMPATIBILITY.md` | Formal API/webhook compat contract + deprecation flow | 37.1 |
| `CHANGELOG.md` — "Deprecated" section | Running list of flagged-for-removal endpoints/fields/events | 37.4 |

---

## Success Criteria

v0.3.2 ships when:

**Security:**
1. All Phase 23/24 audit issues are resolved, closed, or explicitly deferred with written justification.
2. `trusted_proxies` is the only gate for `X-Forwarded-For` trust across all five historical call sites (admin, contact, api, analytics, login_throttle) — the interim v0.3.1 copy in `admin.py` is deleted.
3. `secret_key` weakness is fatal at boot, not advisory. v0.3.2 release notes carry the **Breaking change** entry.
4. Constant-time admin login: median wall-clock delta between valid-user and invalid-user login < 20% of the scrypt cost.
5. `/readyz` returns no absolute path / exception type / migration filename to unauthenticated callers.

**Operational:**
6. No retention-managed table can grow unbounded — `resume-site-purge.timer` ships and the admin dashboard reflects per-table purge status.
7. `page_views` writes are off the request hot path; landing-page p50 improves by ~1.5 ms in `PERFORMANCE.md`.
8. Webhook dispatch is bounded — no admin action can spawn unbounded threads.

**Correctness:**
9. Admin bulk actions, open redirect on `/set-locale`, null-byte contact fields, and `/csp-report` rate limit are all fixed — no test in `tests/test_security.py`, `tests/test_admin.py`, or `tests/test_resilience.py` is marked `xfail`.
10. Contact SMTP failures are visible to operators via dashboard widget + `mail_send_errors_total` counter + `contact_submissions.smtp_status`.

**Compatibility:**
11. `docs/API_COMPATIBILITY.md` is published.
12. The `@deprecated` decorator exists and is drift-guarded by the OpenAPI test.
13. CHANGELOG `[Unreleased]` carries a permanent "Deprecated" section (empty is fine for v0.3.2 — no deprecations yet).

**Release:**
14. Ships through the Phase 35 release gate inherited from v0.3.1 (Trivy green, cosign verify green on a clean-machine probe, `/healthz` + `/readyz` green, amd64 + arm64 smoke-tested, tag matrix pushed atomically).

---

## Version Tagging

- `v0.3.1` — Keystone baseline
- `v0.3.2-rc.1` — feature freeze; release-gate dry run
- `v0.3.2` — stable (Shield); second release through the gate
