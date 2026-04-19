# resume-site v0.3.5 Roadmap

> **Codename:** Anvil
> **Status:** Planning
> **Baseline:** v0.3.0 (Forge — API-first, observable, hardened platform)
> **Target:** Close every open audit finding from the 2026-04-18/19 pentest passes, finish the six major v0.3.0 initiatives that did not land, and ship the first release that actually honours the published-image release gate.

---

## Release Philosophy

v0.3.5 is a **finishing pass**, not a feature release. It exists because two things happened in the v0.3.0 cycle:

1. A deep pentest + code-audit pass in April 2026 filed **56 open issues** against the v0.3.0 codebase — most of them exploitable or operational-debt bugs that v0.3.0 did not stop to fix.
2. Six v0.3.0 initiatives were scoped as "major" but never completed: the DAST pipeline, the Playwright browser-test suite, the load-test CI regression gate, the mutation-testing baseline, the edge-case testing methodology, and the release-publication gate. Plugin architecture was *cut* from v0.3.0 and is **not** being carried forward (see v0.3.0 Phase 20 note).

Every item in this roadmap is either (a) a filed GitHub issue against v0.3.0 or (b) a v0.3.0 deferral. **Nothing in v0.3.5 is a v0.4.0 feature** — no multi-user, no RBAC, no PostgreSQL backend, no OAuth/OIDC, no public-facing login. Those stay deferred to v0.4.0.

### Ordering principle

Phases are ordered by pragmatism:

1. **Security first.** Exploitable findings before anything else — the project cannot ship knowingly-broken security.
2. **Then operational hygiene.** Unbounded tables, hot-path writes, and purge-timer gaps will take the site down under load even if nothing is exploited.
3. **Then performance.** Measurable wins documented in `PERFORMANCE.md` that v0.3.0 didn't get to.
4. **Then correctness bugs and reliability.**
5. **Then CI/tooling hygiene.**
6. **Then the six large v0.3.0 carry-overs** — these are the expensive, multi-PR initiatives; they land last so the tree is quiet and the baselines they measure against are stable.

---

## Scope Summary

| Category | Source | Phase |
|---|---|---|
| Critical exploitable findings (debug mode, stored XSS, SSRF, cookie leak, public bind) | Issues #15, #17, #19, #41, #43/#59, #44, #58, #63, #66 | 22 |
| Auth / session / header / input-validation hardening | Issues #16, #18, #33, #34, #35, #37, #38, #46, #48, #50, #51, #57, #67 | 23 |
| Information disclosure and privacy | Issues #14, #22, #45, #60, #65 | 24 |
| Operational hygiene — unbounded tables, purge timers, hot-path writes, thread limits | Issues #42, #47, #49, #55, #62, #68 | 25 |
| Performance — N+1, Gunicorn tuning, admin pagination, Pillow hot path, metrics scan, benchmark harness | Issues #28, #36, #52, #53, #54, #61, #64 | 26 |
| Bug fixes — CSRF token on bulk action, TOCTOU on review, form validation, null bytes, open redirect, csp-report rate limit | Issues #13, #20, #21/#40, #23, #24, #25, #26, #32, #39 | 27 |
| CI / packaging / tooling hygiene | Issues #27, #29, #30, #31 | 28 |
| Code audit redundancy closeout | Issue #56 | 29 |
| DAST pipeline (CI ZAP baseline) | v0.3.0 Phase 13.9 | 30 |
| Browser-based testing (Playwright) | v0.3.0 Phase 18.4 | 31 |
| Load-test CI regression gate | v0.3.0 Phase 18.6 | 32 |
| Mutation-testing baseline + CI integration | v0.3.0 Phase 18.8 | 33 |
| Edge-case testing methodology | v0.3.0 Phase 18.13 | 34 |
| Release publication gate (GHCR tag matrix, cosign, release-notes template, stop-ship gate) | v0.3.0 Phase 21.5 | 35 |

### Out of Scope (v0.4.0+) — explicitly not in v0.3.5

- Multiple admin / viewer accounts
- Public-facing login / registration
- Role-based access control (RBAC)
- OAuth2 / OIDC provider integration
- SaaS / multi-tenant mode
- PostgreSQL backend option
- Real-time features (WebSocket)
- Plugin architecture (cut from v0.3.0, not re-added)
- Plugin sandboxing
- Any v0.3.0 deferral that is *minor polish* (CSS/JS minification, blog cover image preview, translation completeness dashboard, K8s/Nomad example manifests, status-page endpoint, `manage.py profile` CLI, CDN-unavailability browser test, image-size regression gate, distroless base image) — those remain tracked against v0.3.0 as finishing work.

---

## Phase 22 — Critical Exploitable Findings

*First stop. Each item below is either directly exploitable today or one misconfiguration away from being exploitable. No other work lands until these do.*

### 22.1 — Kill the dev-server debug entry point (#15)

- [ ] `app.py` currently runs `app.run(debug=True, port=5000)` unconditionally. Running `python app.py` opens the Werkzeug interactive `/console` — arbitrary code execution.
- [ ] Gate on `RESUME_SITE_DEV=1` env var *and* explicit `--debug` CLI flag; default to `debug=False`. Log a warning line when debug is enabled.
- [ ] Add a CI grep-guard that fails on any `debug=True` literal in `app.py` or `app/__init__.py`.
- [ ] Regression test in `tests/test_security.py`: `GET /console` on a freshly-booted app must return 404.

### 22.2 — Stored HTML / JS injection — every write path must sanitize (#17, #41, #44, #63)

- [ ] **#63 Fail-closed sanitizer:** `app/services/content.py:sanitize_html` currently returns input unchanged when `nh3` is unimportable. Move `nh3` to a hard runtime dependency (it's already in `requirements.txt`) and make the missing-import path raise at app boot, not at render time. Delete the `_HAS_NH3` fallback branch.
- [ ] **#41 Translation save sanitation:** `app/routes/admin.py:1121` (translation save) and the services-layer callers all skip `sanitize_html()` before `save_translation()`. Every translatable field with `content_format='html'` goes through the same sanitizer the default-locale save path uses.
- [ ] **#44 Admin-search FTS snippet:** `admin/search.html:32` renders `{{ result.snippet | safe }}`. Drop `| safe`; let Jinja autoescape. The snippet is attacker-controlled — public review text flows into the FTS index.
- [ ] **#17 `javascript:` in custom nav links:** Validate the `url` field of every `custom_nav_links` entry server-side at `save_settings` time. Allow only `http://`, `https://`, `/` (relative), and `mailto:`. Reject everything else with a 400 and a user-visible error. Add a template-side defence (`|safe` is already *not* used here, but the `href=` binding should still run through a `safe_url` filter).
- [ ] Add `tests/test_sanitizer_contract.py`: property-based test asserting every HTML-accepting write path strips `<script>`, `on*=` handlers, and `javascript:` schemes.

### 22.3 — Webhook SSRF — block private ranges and stop following redirects (#19, #43, #59)

- [ ] **#19 URL allowlist at write time:** Reject webhook URLs whose resolved host is loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`, `fe80::/10`), RFC 1918 private (`10/8`, `172.16/12`, `192.168/16`), or CGNAT (`100.64/10`). DNS-resolve at create time; re-resolve at delivery time to defeat DNS rebinding. Apply the same gate to both admin-HTML and API create/update routes.
- [ ] **#43/#59 Stop following redirects:** `app/services/webhooks.py:deliver_now` uses `urllib.request.urlopen` which installs `HTTPRedirectHandler` by default. Replace with an `OpenerDirector` that installs a no-op redirect handler that raises on `3xx`, so any redirect response lands in the delivery log as a failure instead of silently fetching the redirect target.
- [ ] Settings registry entry `webhook_allow_private_targets` (default `false`, Security category) for the rare operator who genuinely needs to call an internal service; documented as a foot-gun.
- [ ] Tests in `tests/test_webhooks.py`: explicit cases for each CIDR family + 302/307/308 refusal.

### 22.4 — Raw API token written to client-side session cookie (#58)

- [ ] The one-time-reveal flow stashes the raw token in `session['_api_token_reveal']['raw']`. Flask default sessions are **client-side signed, not encrypted** — the plaintext token lands in the browser's cookie jar.
- [ ] Replace with a server-side single-use handoff: after generation, store the raw token in a new `api_token_reveals` table keyed by a random `reveal_id`; put only the `reveal_id` in the session. The `/admin/api-tokens/reveal` route looks up, deletes, and returns. Expire stale reveal rows after 5 minutes via a request-time prune.
- [ ] Same pattern for `manage.py rotate-api-token`'s CLI output (never stored anywhere except the admin's terminal).
- [ ] Tests assert: (a) no `resume_session` cookie response body ever contains the token bytes; (b) reveal row is deleted after first GET; (c) expired reveal returns 410 Gone.

### 22.5 — Close the public-exposure hole (#66)

- [ ] `compose.yaml` ports `"8080:8080"` binds `0.0.0.0` by default. Change to `"127.0.0.1:8080:8080"` so the container is only reachable through the reverse proxy on localhost.
- [ ] Same fix on the Quadlet `resume-site.container` `PublishPort=` line.
- [ ] `docs/PRODUCTION.md` gains a loud callout in the "Reverse proxy" section: if you're exposing 8080 directly to the public internet, the X-Forwarded-For trust model the app ships with is unsafe (see #16 / #34).

### 22.6 — Admin IP allowlist — don't trust X-Forwarded-For unconditionally (#16)

- [ ] `app/routes/admin.py:104` picks the first comma-separated value from `X-Forwarded-For` verbatim. When the app is reached directly (not via Caddy), XFF is attacker-controlled.
- [ ] Introduce a `trusted_proxies` CIDR list in `config.yaml` (default empty). Only consult `X-Forwarded-For` when `request.remote_addr` is inside `trusted_proxies`; otherwise fall back to `remote_addr`.
- [ ] Companion fix: `#34` — the same logic lives in five other places (contact rate limit, API rate limit, analytics, `/metrics` access control, login throttle). Extract a single `get_client_ip(request)` helper that reads `trusted_proxies` once and use it everywhere. Delete the duplicates. This is scheduled in Phase 23 (23.2) but both phases share the helper.

---

## Phase 23 — Auth, Session, Header, and Input-Validation Hardening

*Tier-2 security. Each item is real, but requires a local pivot / timing measurement / specific deployment shape to be exploited. These are the "should have been right the first time" fixes that the v0.3.0 audit shook loose.*

### 23.1 — Session revocation: close both gaps (#33, #51)

- [ ] **#51 Bypass on `blog_admin_bp`:** `check_session_epoch` is registered on `admin_bp` only. The separate `blog_admin_bp` (mounted under the same `/admin` prefix) does **not** re-register it, so a captured cookie survives a logout for the lifetime of the cookie. Register the full admin middleware bundle on `blog_admin_bp` in `app/routes/blog_admin.py:59-62` and add a regression test that iterates every registered blueprint and asserts the middleware set matches `admin_bp`.
- [ ] **#33 Race across workers:** The current design bumps `_admin_session_epoch` in the settings table on logout. Other Gunicorn workers keep accepting the cookie for up to 30 s — the settings cache TTL. Pub-sub is overkill; the right fix is to bypass the cache specifically for this one key. Add a `settings_svc.get_uncached(key)` helper and have `check_session_epoch` call it. Accept the extra query per admin request.
- [ ] Regression test: two simultaneous clients with the same cookie, one logs out, the other's next admin request is 401 within 250 ms.

### 23.2 — One `get_client_ip()` helper to rule them all (#34)

- [ ] The `X-Forwarded-For`-trusting pattern is copy-pasted across five files: `admin.py`, `contact.py`, `api.py`, `analytics.py`, and `login_throttle.py`. Each is independently spoof-bypassable on non-proxied deployments.
- [ ] Extract `app/services/request_ip.py:get_client_ip(request)` that (a) consults the `trusted_proxies` config from 22.6, (b) walks the XFF chain right-to-left looking for the first untrusted IP, (c) falls back to `request.remote_addr` otherwise. Document the algorithm alongside the function.
- [ ] Replace every call site; delete the inlined duplicates. Preserve the behaviour for the login throttle hash.
- [ ] Unit tests: spoofed XFF with no trusted proxies, spoofed XFF with a trusted proxy, IPv6, chained XFF, empty XFF.

### 23.3 — Constant-time admin credentials (#38, #46)

- [ ] **#38 Constant-time username comparison:** `app/routes/admin.py:280-284` uses `==`. Switch to `hmac.compare_digest(username.encode(), admin_username.encode())`.
- [ ] **#46 Close the scrypt-skip side-channel:** Even with constant-time username compare, short-circuiting on `and` skips `check_password_hash` on username mismatch. Always run `check_password_hash` against a fixed dummy hash when the username doesn't match, so the wall-clock cost of a hit and a miss is indistinguishable at the rate-limit window. Dummy hash generated once at app boot from a random password.
- [ ] Regression test: time-of-login distribution (100 trials each) for valid vs. invalid usernames — fail the test if the median delta is > 20% of the scrypt cost.

### 23.4 — `secret_key` strength: fatal, not advisory (#48)

- [ ] `_validate_secret_key` currently warns on length < 32, warns on well-known placeholders, warns on weak keys. All three paths fall through to `return True`. Flip them all to `return False` (fatal at boot). Leave the "key is missing" path fatal as today.
- [ ] `CHANGELOG.md` note: operators with a weak key will now fail to start after upgrade; `manage.py rotate-secret-key` is the escape hatch. Call this out in the v0.3.5 release notes.

### 23.5 — Header injection and body-limit gaps

- [ ] **#35 Email header injection on contact form:** `app/services/mail.py` assigns user input directly to `Subject` and `Reply-To`. Strip CR/LF from every header value; reject inputs containing either byte. Use `email.utils.formataddr` for `Reply-To`. Regression test: POST a form with `name` containing `\r\nBcc: evil@example`, assert the sent message has exactly one recipient.
- [ ] **#37 `MAX_CONTENT_LENGTH`:** Set `app.config['MAX_CONTENT_LENGTH']` to 16 MB at app-factory time (enough for the 10 MB photo upload path + headroom). Gunicorn `--limit-request-line 8190 --limit-request-fields 100 --limit-request-field-size 8190` added to `docker-entrypoint.sh`. Reject chunked-encoding that exceeds the limit.
- [ ] **#57 Host header injection:** Build absolute URLs from `site_config['canonical_host']` (new config key), **never** from `request.url_root` / `request.host`, in `/sitemap.xml`, `/robots.txt`, and `/blog/feed.xml`. Fall back to a 400 if the `Host` header doesn't match the canonical set.
- [ ] **#67 `target="_blank"` tabnabbing:** `app/services/content.py:66-71` sets `link_rel=None`. Remove it — let nh3's default `rel="noopener noreferrer"` injection run. Regression test: an admin-authored `<a target="_blank">` renders with `rel="noopener noreferrer"`.

### 23.6 — Settings / upload input validation (#18, #25, #50)

- [ ] **#18 `save_many` bool flip:** Iterating `SETTINGS_REGISTRY` and writing `false` for any bool key not in the form is the root cause. Have admin forms emit hidden inputs for every bool in the category being saved (already the pattern in most templates — audit the rest). Server-side: only update keys present in the form; never write `false` for an absent key. Regression test: save the "Navigation" category and assert that unrelated bool keys in other categories are unchanged.
- [ ] **#25 JSON settings schema validation:** `nav_order`, `homepage_layout`, `custom_nav_links` are parsed with `contextlib.suppress(Exception)`. Replace with explicit per-setting validator functions registered on `SETTINGS_REGISTRY` entries. Rejected input → 400 with a pointer to the offending key. Tests: malformed JSON, wrong top-level type, extra keys, missing required keys.
- [ ] **#50 `display_tier` on photo upload:** `/admin/photos/upload` accepts `display_tier` verbatim. Validate against `{featured, grid, hidden}` before the INSERT. 400 on invalid, with the quarantine file cleaned up. Same fix already present on the REST API path — consolidate the validation into the service layer.

---

## Phase 24 — Information Disclosure and Privacy

*Nothing here is exploitable as a privilege escalation, but each leak either reveals attack surface or violates the "privacy-respecting" contract the docs already claim.*

### 24.1 — `/readyz` minimalism (#65)

- [ ] `/readyz` currently returns absolute paths, exception types, and pending-migration filenames to unauthenticated callers. Collapse the response to a flat `{"ready": false, "failed": "<check_name>"}` for external callers; emit the full detail at WARNING level on `app.readyz` with the request id attached, so operators retain the signal but the public endpoint doesn't.
- [ ] Optionally gate the detailed response on `metrics_allowed_networks` (same allowlist as `/metrics`). Disallowed clients get the collapsed body.
- [ ] Regression test: forge every failure mode, assert no absolute path / exception class name / migration filename leaks in the public response.

### 24.2 — Analytics and contact privacy (#45, #60)

- [ ] **#45 Honour the "privacy-respecting" docstring:** `page_views` stores raw IP + full UA. Use the existing `hash_client_ip` from `app/services/logging.py` and store only the SHA-256 (salted with `secret_key`). Truncate the UA to a coarse classifier (`"firefox|chrome|safari|edge|bot|other"` × desktop/mobile). Migration `012_analytics_privacy.sql` backfills by hashing / truncating existing rows; document the one-way transformation in `CHANGELOG.md`.
- [ ] **#60 `contact_submissions`:** Same treatment — hash the IP. Drop the full UA; keep the coarse classifier only. Keep the email address (it's the contact reply target).
- [ ] Admin dashboard panels updated to display the hashed IP prefix (first 8 hex chars) for the "recent submissions" and "top-N visitors" widgets — enough for "same visitor, different page" correlation without reversibility.

### 24.3 — Log-injection and stack-trace hygiene (#22)

- [ ] **#22 CSP-report log injection:** `directive`, `blocked-uri`, `document-uri` are logged verbatim via `%s` formatting. Wrap each in a `_sanitize_log_field` helper that (a) truncates to 500 chars, (b) replaces CR/LF/tab with `\\r`, `\\n`, `\\t`, (c) drops ANSI escape sequences. Same helper applied to the Request-ID echo path in `app/services/logging.py` as belt-and-braces.
- [ ] Regression test: POST a crafted CSP report containing `\r\nWARN Fake admin login success` and assert the log line is rendered as a single record with the escape visible.

### 24.4 — `Server: gunicorn` header removal (#14)

- [ ] Strip or rewrite the `Server` response header. Two acceptable fixes: (a) set `app.after_request` to pop `Server` (simplest, works for any WSGI server); (b) document the Caddy `header Server "resume-site"` snippet in `docs/PRODUCTION.md` and recommend (a) as the belt inside the suspenders. Ship (a) in-tree.
- [ ] Regression test: every route returns no `Server` header (or exactly the rewritten value).

---

## Phase 25 — Operational Hygiene (Unbounded Tables, Purge Timers, Hot-Path Writes)

*The pattern below — "purge function exists but nothing ever calls it" — appears four times across the v0.3.0 codebase. Fix the pattern once with a single scheduled-purge subsystem instead of four bespoke timers.*

### 25.1 — One `scheduled-tasks` timer that purges everything (#42, #55, #62, #68)

- [ ] New CLI command `manage.py purge-all` that calls the purge function for every retention-managed table in one transaction, reading the retention days from the settings registry: `page_views` (default 90, #55), `login_attempts` (default 30, #42), `webhook_deliveries` (default 30, #42), `admin_activity_log` (default 90, #62/#68). Exit code reflects any purge that hit an error; individual errors never abort the other purges.
- [ ] `resume-site-purge.timer` + `resume-site-purge.service` ship next to the backup units; fire daily at 03:30 local with `RandomizedDelaySec=30min`. `Persistent=true` so a host off at 03:30 catches up on next boot. Same `podman exec` pattern as the backup timer. Documented in `docs/PRODUCTION.md`.
- [ ] `compose.yaml` cron-equivalent snippet in the README ("operators who don't use systemd/Quadlets").
- [ ] Admin dashboard "Retention" card: per-table row count, oldest-row age, last-purge timestamp, configured retention. Reads from a new `purge_last_success` per-table setting that `manage.py purge-all` writes.
- [ ] Tests: unit-test every purge function's bounded correctness (N rows in → N-old rows deleted → N-new rows kept) + integration test of `purge-all` hitting every table.

### 25.2 — `page_views` off the hot path (#49)

- [ ] `track_page_view` currently `INSERT` + `COMMIT`s on every public GET. Under burst load that takes the SQLite write lock on the hot path and contends with every other writer.
- [ ] Replace with a ring-buffered, bounded `queue.Queue` (cap 10k events) + a single background drainer thread that flushes in batches (`INSERT INTO page_views SELECT … FROM temp`) every 2 s or at 500 pending events, whichever first. Queue-full returns silently (analytics is best-effort; dropping a page view is better than blocking the response). Drainer thread started in `create_app` and torn down at exit.
- [ ] Under benchmark `scripts/benchmark_routes.py`: document the per-request savings (target: remove ~1.5 ms p50 from the landing page). Update `PERFORMANCE.md`.
- [ ] Tests: (a) queue-full behaviour, (b) drainer flush on shutdown, (c) correctness under concurrent writers.

### 25.3 — Bounded webhook-dispatch thread pool (#47)

- [ ] `dispatch_event_async` spawns one daemon thread per subscriber, per event. A single bulk admin action (`/admin/bulk-action` publishing 50 posts to 5 webhooks = 250 threads) can trivially amplify.
- [ ] Replace with a module-level `concurrent.futures.ThreadPoolExecutor` (max workers = `max(4, min(32, subscriber_count * 2))`) shared across all events. Overflow events enqueue into the executor's work queue; bounded at 1000 pending tasks with a drop-oldest policy. Dropped events logged at WARNING and counted via a new `resume_site_webhook_drops_total` metric.
- [ ] Settings registry entry `webhook_max_workers` (default `auto`, Security category). `CHANGELOG.md` note about the semantic change (drops replace unbounded fan-out).
- [ ] Tests: (a) a 500-event burst completes without OOM, (b) drop counter increments when the queue overflows, (c) bus handler latency for the emitter remains bounded.

---

## Phase 26 — Performance

*Each item below is a measured regression against `PERFORMANCE.md` or a documented win that v0.3.0 left on the table. Land them in this order because 26.1 and 26.4 are the largest gains and the rest compose cleanly on top.*

### 26.1 — Eliminate the translations N+1 (#52)

- [ ] `overlay_posts_translations` is named like a batch loader but runs two queries per post. At the configured non-default locale, `/blog` (10 posts), the landing featured-posts strip (3 posts), and `/blog/feed.xml` (20 posts) each pay an extra 2N queries on the hot path.
- [ ] Rewrite `get_translated` and `get_all_translated` in `app/services/translations.py` so the list overlay does **one** `SELECT … WHERE parent_id IN (?,?,…,?) AND locale = ?` per table, joined to the pre-loaded caller rows in Python. Preserve the fallback chain.
- [ ] `tests/test_n_plus_1.py` gains three new cases (blog index, landing featured, feed) that assert `== 2` queries regardless of post count.
- [ ] `PERFORMANCE.md` row updated with the before/after numbers.

### 26.2 — Gunicorn `--preload` and worker recycling (#28, #53)

- [ ] **#53 `--preload`:** Add to `docker-entrypoint.sh`. Documented 500-800 ms cold-start win + lower steady-state RSS via CoW. Verify no thread-affinity landmines on the event bus or the `page_views` drainer (both are started post-fork via `worker_int`/`post_fork` hooks if needed).
- [ ] **#28 `--max-requests` / `--max-requests-jitter`:** Add `--max-requests 2000 --max-requests-jitter 200` so workers recycle. Guards against Pillow/Jinja/SQLite statement-cache creep. Documented in `PERFORMANCE.md` and `docs/PRODUCTION.md`.

### 26.3 — Paginate `/admin/blog` (#54)

- [ ] The admin blog list renders every row. Documented 8.3 ms at 150 posts; scales linearly. Reuse `app/services/pagination.py` — the public blog index already paginates. Default 25 posts per page; `?page=N` query string. Sticky filters on the paginator links. Regression test: page 1 returns the 25 newest, page 2 the next 25, `?status=draft` filter is preserved.

### 26.4 — Photo upload: `Image.draft()` for JPEG (#61)

- [ ] Before the `Image.open()` in `app/services/photos.py:189`, call `img.draft('RGB', (max_dim, max_dim))` when the detected format is JPEG. libjpeg-turbo will do a DCT-level downscale; documented 4-8× faster on 24 MP DSLR inputs.
- [ ] Preserve correctness: EXIF stripping still works, responsive variants still match the 640/1024/2000 ladder. Regression test: upload a 24 MP fixture, assert the final 2000 px variant is byte-for-byte within 1% of the pre-change output at the same quality setting.
- [ ] `PERFORMANCE.md` photo-upload row updated with the before/after numbers.

### 26.5 — `/metrics` disk-usage scrape cost (#36)

- [ ] Currently walks the entire photo directory on every Prometheus scrape. At 10k photos this is seconds per scrape.
- [ ] Cache the photo-directory size in the settings table (`photos_disk_usage_bytes`, `photos_disk_usage_updated_at`). Refresh in two places: (a) every photo upload/delete bumps the value by the file size delta (cheap); (b) the `manage.py purge-all` run (Phase 25.1) writes a ground-truth total as a reconciliation step. `/metrics` reads the cached value in O(1). Document the staleness window (max 24 h between reconciliations).
- [ ] Same pattern for the DB size gauge (stat the file, cheap — leave as-is).

### 26.6 — Benchmark harness sets its own log level (#64)

- [ ] `scripts/benchmark_routes.py` is documented to need `RESUME_SITE_LOG_LEVEL=WARNING`, but the script doesn't set it. Any contributor following the top-of-file docstring silently measures the stderr sink.
- [ ] Have the script `os.environ.setdefault('RESUME_SITE_LOG_LEVEL', 'WARNING')` at import time, **before** importing `app`. Print the effective level in the banner so it's obvious if the operator overrode it.

---

## Phase 27 — Correctness Bugs and Reliability

*Functional bugs with varying blast radius. Ordered by user-visibility.*

### 27.1 — Admin bulk actions don't send CSRF token (#20)

- [ ] `window.bulkAction` in `admin/base_admin.html:101-108` sends no CSRF header, so every bulk action 400s. Blocking bug on the v0.3.0 bulk-ops feature (Phase 14.3).
- [ ] Add the `X-CSRFToken` header from the `<meta name="csrf-token">` tag to the `fetch()` call. Regression test: a scripted POST via the admin UI returns 200 (or 303), never 400.

### 27.2 — Review submission atomicity (#26)

- [ ] `create_review` + `mark_token_used` span two statements without a transaction. Under concurrent submission, the token can be double-used.
- [ ] Wrap both calls in `with db:` (Python sqlite3 context-manager rolls back on exception, commits on success). Revalidate the token inside the transaction. Regression test: two threads submit the same token concurrently — exactly one wins, the other sees `error: token_already_used`.

### 27.3 — Contact-form SMTP failures surface to the operator (#23)

- [ ] Current behaviour: `app/services/mail.py` returns `False` on SMTP failure and the contact route redirects 302 regardless. The submission is persisted to `contact_submissions`, so nothing is lost, but the admin has no visibility.
- [ ] Add a new `mail_send_errors_total{reason}` counter (Prometheus), a WARNING log line (with the exception type, not the body), and an admin-dashboard "Recent SMTP failures" widget driven by a new `contact_submissions.smtp_status` column (`sent` / `failed` / `retrying`). Migration `013_contact_smtp_status.sql`.
- [ ] Regression test in `tests/test_resilience.py`: trigger `ConnectionRefusedError` on SMTP, assert the row is saved with `smtp_status='failed'`, the counter increments, and the log line is emitted.

### 27.4 — Form-validation tightening (#24, #25, #39)

- [ ] **#24 `content_format` on HTML admin routes:** Validate against `{html, markdown}` at form save time. 400 with a user-visible error on anything else. The API path (`app/routes/api.py:963-969`) already does this — extract the validator into a shared helper and use it in both places.
- [ ] **#25 JSON settings validation** — already scheduled in 23.6; cross-linked here for completeness.
- [ ] **#39 Email validator accepts `@.`, `a@.`, `a@a`, `@a`, etc.:** Replace the `'@' in email and '.' in email` check with a single regex from `email.utils.parseaddr` + a simple RFC-5321-ish validator (`LOCAL@DOMAIN.TLD`, TLD length ≥ 2, no consecutive dots, no leading/trailing dot in either side). Apply consistently to the HTML contact form and the API contact endpoint.

### 27.5 — Null-byte handling in contact fields (#13)

- [ ] Document and enforce the stripping at a single layer. Reject (400) any POST whose body contains `\x00` in a free-text field, on both HTML and API contact paths. Don't silently strip — rejection is easier to reason about.
- [ ] Regression test: `name=A\x00B` returns 400; the DB is never written; the JSON API returns `{error:'null_bytes_rejected'}`.

### 27.6 — Open redirect on `/set-locale/<lang>` (#21, #40)

- [ ] `app/routes/locale.py:20` redirects to `request.referrer` with no validation. Only redirect to a same-origin, in-app path; otherwise redirect to `/`.
- [ ] Validate with `urllib.parse.urlparse(referrer)` + compare scheme+netloc to the current request. Paths starting with `//` are relative-protocol and must be rejected.
- [ ] Regression test: a forged `Referer: https://evil.example/` request hits `/set-locale/en` and lands at `/`, not at evil.example.

### 27.7 — `/csp-report` rate limit + content-type gate (#32)

- [ ] Currently accepts any Content-Type and has no rate limit — it's an unauthenticated internet-facing write endpoint.
- [ ] Accept only `application/csp-report` or `application/json`; reject with 415 otherwise. Apply a 60/minute per-IP rate limit via Flask-Limiter (bypass-on-trusted-proxies as elsewhere). Log the drop at DEBUG, not WARNING, so a noisy bot doesn't pollute error budgets.

---

## Phase 28 — CI / Packaging / Tooling Hygiene

*None of this is user-visible. All of it is why regressions sneak in.*

### 28.1 — Fix the SQL-interpolation grep guard (#29)

- [ ] `.github/workflows/ci.yml` greps for `nosec B608` but not `noqa: S608`. Every intentional interpolation in the codebase carries **both** annotations; the CI grep is therefore false-negative for any new interpolation that only carries `noqa`.
- [ ] Update the grep to treat either annotation as an accepted suppression. Better still, replace the fragile grep with a ruff custom rule or a dedicated bandit invocation scoped to `app/` that already understands both annotation styles.

### 28.2 — Un-advisory `vulture` (#30)

- [ ] CI vulture step is `continue-on-error: true`. Flip to blocking. Any currently-surviving findings either get the allowlist entry or the code deletion they deserve.
- [ ] Pre-commit hook added (matches CI). `CONTRIBUTING.md` updated.

### 28.3 — Fix or retire `upgrade-simulation` (#31)

- [ ] The CI job is `continue-on-error: true` with a "Tracked: TODO" note stranded for months. Either root-cause the SELinux-on-bind-mount failure and flip blocking, or retire the job entirely and replace it with a simpler `podman run --rm ghcr.io/.../resume-site:main migrate --dry-run` probe that runs in `publish`. v0.3.5 picks one.

### 28.4 — Quadlet / systemd hardening (#27)

- [ ] Add the low-risk `systemd.exec` hardening directives to `resume-site.container` and `resume-site-backup.service`: `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `RestrictSUIDSGID=yes`, `LockPersonality=yes`, `MemoryDenyWriteExecute=yes` (test against Pillow first), `RestrictNamespaces=yes`, `SystemCallArchitectures=native`. Document each in comments with the rationale and the rollback procedure.
- [ ] Apply the same set to the new `resume-site-purge.service` (Phase 25.1).

---

## Phase 29 — Code Redundancy Audit Closeout (#56)

*Issue #56 is a tracking issue for ~40 redundancies across routes, services, models, templates, tests, and `manage.py`. v0.3.5 carves specific PRs out of it; the rest roll over as ongoing tech-debt.*

### 29.1 — Form-field extraction helper

- [ ] The pattern `request.form.get('field', '').strip()` is repeated across eight admin/blog/API files. Extract `app/services/form.py:get_stripped(form, key, default='')` and migrate every call site. Keep the behaviour byte-identical (`strip()` only, no case folding).

### 29.2 — CRUD service-layer pass

- [ ] Revisit the Phase 12.2 "deferred: CRUD base mixin" note. Now that the REST API write handlers have landed, the duplication between the HTML admin services and the API services is concrete. Extract a shared `update_fields(db, table, id, fields)` helper that handles the partial-update + validation + activity-log-emission triad; rewrite the services that duplicate it.

### 29.3 — Test fixture consolidation

- [ ] `#56` flags multiple ad-hoc admin-login fixtures across `tests/test_admin*.py`. Consolidate on the canonical `logged_in_admin_client` fixture and remove the variants.

### 29.4 — Roll the rest forward

- [ ] Close `#56` at v0.3.5 with a summary comment listing which bullets landed and which remain open as standalone issues. Don't keep a tracking issue indefinitely — it stops tracking once the half-life exceeds the release cycle.

---

## Phase 30 — DAST Pipeline (carry-over from v0.3.0 Phase 13.9)

*The static-analysis half of v0.3.0 shipped (ruff, bandit, pip-audit, detect-secrets). The dynamic half did not. v0.3.5 closes that gap.*

- [ ] **OWASP ZAP baseline scan in CI:** New `security-scan` job in `.github/workflows/ci.yml`. Runs `zap-baseline.py` against the container built by `container-build`, seeded with the test content from `seeds/`. Passes if zero MEDIUM+ findings; uploads the HTML report as a CI artifact either way. `needs: [test, container-build]`; blocks `publish` via the existing `needs` chain.
- [ ] **`zap-config.yaml`:** Tune the ruleset — exclude known-accepted findings (the Server-header fingerprint is fine once #14 lands; the admin-login form deliberately sends no Cache-Control: no-store because Flask-Login handles it). Every exclusion carries an inline comment with the issue link.
- [ ] **Authenticated-scan mode:** ZAP logs into the test app via the admin form, follows admin routes, and scans them under authentication. Test admin credentials provisioned by the CI seed step only.
- [ ] **Report retention:** CI artifact kept 30 days. Runbook in `docs/SECURITY.md` for operators to re-run locally against their own deployment.

---

## Phase 31 — Browser-Based Testing with Playwright (carry-over from v0.3.0 Phase 18.4)

*The v0.2.0 deferral that slid through v0.3.0. Playwright is the only way to catch regressions in the GSAP animations, the Quill editor, the theme-editor live preview, and the Sortable.js drag-drop wiring.*

- [ ] **Playwright dev dependency + CI job:** Add `playwright` + `playwright install --with-deps chromium` to the dev setup. New CI job `browser-tests` runs against the built container, `needs: container-build`. Screenshots + video on failure retained as artifacts.
- [ ] **Dark/light mode toggle:** `localStorage.setItem('theme', 'light')` then reload; assert `<html>` carries `data-theme="light"` and the computed `--color-bg` matches the light-theme custom property.
- [ ] **GSAP scroll animation:** Scroll to each section; assert the fade+slide class has been applied within 2 s; assert no JS errors in the console.
- [ ] **Quill editor content round-trip:** Admin login → content editor → type + format a paragraph → save → reload → assert content round-tripped byte-for-byte.
- [ ] **Photo upload drag-drop zone:** Drag a fixture PNG into the zone; assert the upload POST fires with the right multipart body; assert the photo appears in the grid.
- [ ] **Theme editor live preview:** Change the accent color; assert the iframe `document.documentElement.style` mirrors the change within 250 ms without a full reload.
- [ ] **Drag-drop reordering persistence:** Reorder three services; reload; assert the order persists.
- [ ] **CSP + nonce assertion:** A Playwright probe asserts every inline `<script>` on every visited page carries a valid nonce and no `'unsafe-inline'` fallback is present in any response.

---

## Phase 32 — Load-Test CI Regression Gate (carry-over from v0.3.0 Phase 18.6)

*v0.3.0 shipped the locust scenarios (`tests/loadtests/locustfile.py`). What it didn't ship: the baseline numbers, the CI gate that compares every PR against them, or the documented stress-test behaviour.*

- [ ] **Baseline run:** 50 concurrent users × 5 min against a seeded container on a dedicated runner class. Record p50/p95/p99 per endpoint in `PERFORMANCE.md`. Commit the numbers to `tests/loadtests/thresholds.json` so the CI gate has something to compare against.
- [ ] **`perf-regression` CI job:** 20 concurrent users × 60 s against the built container; fail if any endpoint's p95 exceeds its threshold by > 20%. Summary table in the job log. Thresholds bumped by hand when an intentional regression (e.g. translations JOIN) is accepted — each bump cites the justifying PR.
- [ ] **Memory leak probe:** Record process RSS at test start + end. WARN if +50% over the run. Advisory in v0.3.5; ratchet to blocking in v0.4.0.
- [ ] **Concurrency stress test:** 200 concurrent users × 30 s. Must not crash, must not 500, must not corrupt SQLite. Behaviour documented in `PERFORMANCE.md` — the goal is "degrades gracefully," not "stays fast."

---

## Phase 33 — Mutation-Testing Baseline + CI Integration (carry-over from v0.3.0 Phase 18.8)

*mutmut is configured; the baseline was never run. Without it, the v0.3.0 claim of "92% coverage with meaningful tests" is unverified.*

- [ ] **Full baseline run:** `mutmut run` against `app/`. Target: ≥ 70% killed. Record the score in `PERFORMANCE.md` under a new "Test Quality" section.
- [ ] **Surviving-mutant review:** Walk the survivor list for the hot-path modules (`app/services/{content,photos,webhooks,translations,settings_svc}.py`, `app/routes/{admin,api,contact,blog_admin}.py`, `app/__init__.py`). For each surviving mutant, either add a test that kills it or mark it `equivalent` with a one-line justification in `tests/MUTATION_EQUIVALENT.md`.
- [ ] **CI integration (advisory):** Nightly job running `mutmut run --paths-to-mutate=$(git diff --name-only main...HEAD)` on the PR delta. Reports killed/survived in the PR summary. Not blocking in v0.3.5 — ratchet to blocking in a later release once the baseline is stable.
- [ ] **`manage.py mutation-report`** updated to emit Markdown for pasting into PR descriptions.

---

## Phase 34 — Edge-Case Testing Methodology (carry-over from v0.3.0 Phase 18.13)

*The "3 assertions vs. 15" gap. v0.3.0 shipped lots of tests that verify features work but few that verify boundaries. v0.3.5 codifies the checklist and does the retroactive pass.*

- [ ] **`tests/TESTING_STANDARDS.md`:** The edge-case checklist from the v0.3.0 18.13 draft — empty/null, boundary, type mismatch, Unicode, length, concurrency, injection. Each category carries two or three concrete examples drawn from real bugs this codebase has had.
- [ ] **Retroactive pass (ranked):** Apply the checklist to `tests/test_admin.py`, `tests/test_api.py`, `tests/test_webhooks.py`, `tests/test_photos.py`, `tests/test_reviews.py`, `tests/test_settings.py`, `tests/test_blog_admin.py`. Track per-file completion in `tests/TESTING_STANDARDS.md`. Remaining files carry over as tech-debt issues — don't block v0.3.5 on 100% coverage.
- [ ] **New-code requirement:** `CONTRIBUTING.md` documents that every PR touching a function accepting user input must include the checklist-derived tests. Code-review checklist template in `.github/pull_request_template.md` references the file.
- [ ] **Linked to Phase 33:** surviving mutants often reveal the edge cases the test missed. Do 33 and 34 in the same sprint — each informs the other.

---

## Phase 35 — Release Publication Gate (carry-over from v0.3.0 Phase 21.5)

*The CI publish + Trivy + cosign machinery shipped in v0.3.0 Phase 21.1–21.3. The **process** around it — tag matrix, release-notes template, README/PRODUCTION reorientation, stop-ship gate — never did. v0.3.5 is the first release that actually honours it. Last phase on purpose: by the time we get here, the tree is quiet and the shipped image is the artifact.*

- [ ] **GHCR as the canonical release surface:** `README.md` and `docs/PRODUCTION.md` reorder so the first install instruction is `podman pull ghcr.io/<owner>/resume-site:v0.3.5`. Source-tree install demoted to a "Development" sub-section. Compose / Quadlet examples reference the GHCR image by **digest-pinned** tag, not the moving `v0.3.5` alias.
- [ ] **Tag matrix per release:** Push `v0.3.5`, `v0.3`, `v0`, and `latest` — all four manifests pointing at the same digest. CI `publish` job extended to push the whole matrix atomically (single `docker buildx imagetools create` at the end). `:main` continues to track trunk; documented as non-production.
- [ ] **Multi-arch verification before `latest` promotion:** Release checklist pulls both `linux/amd64` and `linux/arm64` variants on clean VMs and runs `/healthz` + `/readyz` before the `latest` alias is advanced. Automated via a new `release-verify` CI job that runs against the just-pushed image.
- [ ] **Release-notes template:** `.github/RELEASE_TEMPLATE.md` with the three required lines — `podman pull ghcr.io/<owner>/resume-site:vX.Y.Z`, the image digest (`sha256:...`), the `cosign verify` command — plus a required "Breaking changes" section and a "Migration notes" section. A release without those lines doesn't ship.
- [ ] **Stop-ship gate:** `publish` CI job fails → no release. Trivy HIGH/CRITICAL finding → no release. `cosign verify` fails on the clean-machine probe → no release. `/readyz` fails on the smoke test → no release. Each is a full stop, not a ratchet.
- [ ] **Dry-run the gate on v0.3.5-rc.1:** Before the stable tag, cut `v0.3.5-rc.1` against the same gate. Everything the stable release has to do, the RC has to do. This is the final proof that the process works.

---

## Phase Sequencing

```
Phase 22  (Critical)                 ─── Land first. Block everything else.
Phase 23  (Auth / Session / Input)   ─── Parallel with 22 where files don't overlap.
Phase 24  (Info disclosure)          ─── After 23 (shares the client-ip helper).
Phase 25  (Operational hygiene)      ─── After 22/23 — purge timers rely on 23.2's helper.
Phase 26  (Performance)              ─── After 25 (purge thread coordinates with page_views drainer).
Phase 27  (Bugs)                     ─── Parallel with 26; disjoint files.
Phase 28  (CI hygiene)               ─── Parallel with 22-27; doesn't touch app code.
Phase 29  (Redundancy audit)         ─── After 22-27 so the refactors don't conflict with fixes.
Phase 30  (DAST)                     ─── After all app-code phases; scans the final shape.
Phase 31  (Playwright)               ─── After 30 (same CI-container infra).
Phase 32  (Load-test gate)           ─── After 26 so baselines reflect post-optimization numbers.
Phase 33  (Mutation baseline)        ─── After 27 so the baseline is stable.
Phase 34  (Edge-case methodology)    ─── Paired with 33 — they inform each other.
Phase 35  (Release gate)             ─── Last. Requires everything above to be green.
```

### Parallel Work Streams

```
Stream A (Security fixes):     22 → 23 → 24 ──────────────────── → 30
Stream B (Ops + Perf):         ─────── → 25 → 26 ──────────────── → 32
Stream C (Bugs + CI):          27 + 28 (parallel) → 29 ────────── → 33 + 34 → 35
```

Streams A, B, and C are file-disjoint for the first three phases each. Phase 30 needs A done; Phase 32 needs B done; Phase 35 needs every stream done.

---

## New Database Migrations (v0.3.5)

| Migration | Tables/Changes | Phase |
|---|---|---|
| `012_analytics_privacy.sql` | Backfill `page_views.ip_address` to SHA-256 hash; coarsen `user_agent`. Same for `contact_submissions`. | 24 |
| `013_contact_smtp_status.sql` | `contact_submissions.smtp_status TEXT NOT NULL DEFAULT 'sent' CHECK(smtp_status IN ('sent','failed','retrying'))` | 27 |

---

## New CLI Commands (v0.3.5)

| Command | Purpose | Phase |
|---|---|---|
| `manage.py purge-all` | Purge every retention-managed table in one transaction | 25 |

---

## New Settings (v0.3.5)

| Key | Category | Default | Phase |
|---|---|---|---|
| `trusted_proxies` | Security | `""` (empty CIDR list) | 22/23 |
| `webhook_allow_private_targets` | Security | `false` | 22.3 |
| `webhook_max_workers` | Security | `auto` | 25.3 |
| `login_attempts_retention_days` | Security | `30` | 25.1 |
| `webhook_deliveries_retention_days` | Security | `30` | 25.1 |
| `admin_activity_log_retention_days` | Security | `90` | 25.1 |
| `photos_disk_usage_bytes` / `..._updated_at` | Internal (not user-facing) | auto-maintained | 26.5 |

---

## New Documentation (v0.3.5)

| Document | Purpose | Phase |
|---|---|---|
| `tests/TESTING_STANDARDS.md` | Edge-case checklist and retroactive-pass tracker | 34 |
| `tests/MUTATION_EQUIVALENT.md` | Surviving-mutant justifications | 33 |
| `.github/RELEASE_TEMPLATE.md` | Required release-notes skeleton | 35 |
| `.github/pull_request_template.md` | Updated to reference the edge-case checklist | 34 |
| `docs/SECURITY.md` (expanded) | DAST operator runbook | 30 |

---

## Success Criteria

v0.3.5 is ready to ship when:

**Security:**
1. All 56 open audit issues are resolved, closed, or explicitly deferred to v0.4.0+ with a written justification.
2. The ZAP baseline scan (Phase 30) passes with zero MEDIUM+ findings on a fresh build.
3. Every HTML write path runs through `sanitize_html` with nh3 as a hard dependency.
4. Webhook delivery cannot reach private IP ranges, loopback, or redirect targets.
5. `secret_key` weakness is fatal at boot, not advisory.
6. `get_client_ip()` is the only call site that consults `X-Forwarded-For`.

**Operational:**
7. No retention-managed table grows unbounded — `resume-site-purge.timer` ships and the admin dashboard reflects per-table purge status.
8. `/metrics` scrape cost is O(1) regardless of photo count.
9. `page_views` writes are off the request hot path.
10. Webhook dispatch is bounded — no admin action can spawn unbounded threads.

**Quality:**
11. Mutation score ≥ 70% on hot-path modules, baseline recorded, CI reports it on every PR (advisory).
12. Edge-case checklist applied retroactively to the seven priority test files.
13. Load-test baseline captured, CI regression gate active, stress-test behaviour documented.
14. Playwright browser tests cover the seven listed flows; CI runs them on every PR.

**Release:**
15. `v0.3.5-rc.1` is cut and passes the full release gate dry-run.
16. `v0.3.5` stable is published to GHCR with the full tag matrix, cosign signature, and release-notes template.
17. README + `docs/PRODUCTION.md` lead with `podman pull` from GHCR, not source install.
18. A clean-machine smoke test of the published image reaches `/healthz` and `/readyz` before the release is announced.

---

## Version Tagging

- `v0.3.0` — baseline (Forge)
- `v0.3.5-alpha.N` — tagged as phase groups 22-29 complete
- `v0.3.5-beta.1` — phases 30-34 complete; release-gate work remaining
- `v0.3.5-rc.1` — feature freeze; release-gate dry run (Phase 35)
- `v0.3.5` — stable release, Anvil, published to GHCR through the gate
