# resume-site v0.3.1 Roadmap

> **Codename:** Keystone
> **Status:** Planning
> **Baseline:** v0.3.0 (Forge — API-first, observable, hardened platform)
> **Target:** Critical exploitable fixes from the 2026-04-18/19 audit + the first release that ships through the publication gate + the nine polish items that remained unchecked from v0.3.0. First release to honour the tag matrix, cosign attestation, and stop-ship checklist.

---

## Why "Keystone"

Every v0.3.x release after this one inherits the release gate. v0.3.1 is the release where the gate goes live, and the first release pulled through it. Scope is deliberately narrow on purpose: critical security first (users need those fixes now), gate second (all subsequent releases depend on it), polish last (it was already 95% done in v0.3.0 — checking those boxes is finishing-pass, not new work).

If v0.3.1 ships through its own gate cleanly, v0.3.2 and v0.3.3 each become "fix the bullets, hit the same green." If it doesn't, we keep iterating here until the gate is trustworthy. No feature work lands outside these three phases — the scope discipline is the point.

---

## Scope Summary

| Category | Source | Phase |
|---|---|---|
| Critical exploitable findings (debug mode, stored XSS, SSRF, session-cookie leak, public bind, XFF trust) | Issues #15, #16, #17, #19, #41, #43, #44, #58, #59, #63, #66 | 22 |
| Release publication gate (GHCR tag matrix, cosign, release-notes template, stop-ship checklist) | v0.3.0 Phase 21.5 carry-over | 35 |
| v0.3.0 polish carry-over (CSS/JS min, blog cover preview, drag-drop upload, translation dashboard, profile CLI, in-app alerts, event-handler migration, k8s manifests, docs cross-ref) | v0.3.0 Phases 12.3, 14.4, 15.3, 18.3, 18.10, 18.11, 19.1, 21.4 | 36 |

### Out of Scope (→ v0.3.2 / v0.3.3 / v0.4.0)

- Tier-2 security hardening — session race, constant-time creds, secret-key fatality, header injection, input validation → **v0.3.2**
- Information disclosure (analytics privacy, log injection, Server header) → **v0.3.2**
- Operational hygiene (purge timer, page_views hot path, webhook thread pool) → **v0.3.2**
- Correctness bugs (CSRF on bulk, review atomicity, SMTP surface, form validation, open redirect, /csp-report rate limit) → **v0.3.2**
- API deprecation policy + `Sunset`/`Deprecation` headers → **v0.3.2**
- Performance wins (translations N+1, Gunicorn preload, admin paginate, Pillow draft, metrics scan cost) → **v0.3.3**
- CI hygiene, redundancy closeout, DAST, Playwright, load-test gate, mutation, edge-case methodology → **v0.3.3**
- Multi-user, public login, RBAC, OAuth/OIDC, PostgreSQL, plugin architecture → **v0.4.0+**

---

## Phase 22 — Critical Exploitable Findings

*First stop. Each item below is either directly exploitable today or one misconfiguration away from being exploitable. No other work lands until these do.*

### 22.1 — Kill the dev-server debug entry point (#15)

- [x] `app.py` currently runs `app.run(debug=True, port=5000)` unconditionally. Running `python app.py` opens the Werkzeug interactive `/console` — arbitrary code execution.
- [x] Gate on `RESUME_SITE_DEV=1` env var *and* explicit `--debug` CLI flag; default to `debug=False`. Log a warning line when debug is enabled.
- [x] Add a CI grep-guard that fails on any `debug=True` literal in `app.py` or `app/__init__.py`.
- [x] Regression test in `tests/test_security.py`: `GET /console` on a freshly-booted app must return 404.

### 22.2 — Stored HTML / JS injection — every write path must sanitize (#17, #41, #44, #63)

- [x] **#63 Fail-closed sanitizer:** `app/services/content.py:sanitize_html` currently returns input unchanged when `nh3` is unimportable. Move `nh3` to a hard runtime dependency (it's already in `requirements.txt`) and make the missing-import path raise at app boot, not at render time. Delete the `_HAS_NH3` fallback branch.
- [x] **#41 Translation save sanitation:** `app/routes/admin.py:1121` (translation save) and the services-layer callers all skip `sanitize_html()` before `save_translation()`. Every translatable field with `content_format='html'` goes through the same sanitizer the default-locale save path uses.
- [x] **#44 Admin-search FTS snippet:** `admin/search.html:32` renders `{{ result.snippet | safe }}`. Drop `| safe`; let Jinja autoescape. The snippet is attacker-controlled — public review text flows into the FTS index.
- [x] **#17 `javascript:` in custom nav links:** Validate the `url` field of every `custom_nav_links` entry server-side at `save_settings` time. Allow only `http://`, `https://`, `/` (relative), and `mailto:`. Reject everything else with a 400 and a user-visible error. Add a template-side defence (`|safe` is already *not* used here, but the `href=` binding should still run through a `safe_url` filter).
- [x] Add `tests/test_sanitizer_contract.py`: property-based test asserting every HTML-accepting write path strips `<script>`, `on*=` handlers, and `javascript:` schemes.

### 22.3 — Webhook SSRF — block private ranges and stop following redirects (#19, #43, #59)

- [x] **#19 URL allowlist at write time:** Reject webhook URLs whose resolved host is loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`, `fe80::/10`), RFC 1918 private (`10/8`, `172.16/12`, `192.168/16`), or CGNAT (`100.64/10`). DNS-resolve at create time; re-resolve at delivery time to defeat DNS rebinding. Apply the same gate to both admin-HTML and API create/update routes.
- [x] **#43/#59 Stop following redirects:** `app/services/webhooks.py:deliver_now` uses `urllib.request.urlopen` which installs `HTTPRedirectHandler` by default. Replace with an `OpenerDirector` that installs a no-op redirect handler that raises on `3xx`, so any redirect response lands in the delivery log as a failure instead of silently fetching the redirect target.
- [x] Settings registry entry `webhook_allow_private_targets` (default `false`, Security category) for the rare operator who genuinely needs to call an internal service; documented as a foot-gun.
- [x] Tests in `tests/test_webhooks.py`: explicit cases for each CIDR family + 302/307/308 refusal.

### 22.4 — Raw API token written to client-side session cookie (#58)

- [x] The one-time-reveal flow stashes the raw token in `session['_api_token_reveal']['raw']`. Flask default sessions are **client-side signed, not encrypted** — the plaintext token lands in the browser's cookie jar.
- [x] Replace with a server-side single-use handoff: after generation, store the raw token in a new `api_token_reveals` table keyed by a random `reveal_id`; put only the `reveal_id` in the session. The `/admin/api-tokens/reveal` route looks up, deletes, and returns. Expire stale reveal rows after 5 minutes via a request-time prune.
- [x] Same pattern for `manage.py rotate-api-token`'s CLI output (never stored anywhere except the admin's terminal).
- [x] Tests assert: (a) no `resume_session` cookie response body ever contains the token bytes; (b) reveal row is deleted after first GET; (c) expired reveal returns 410 Gone.

### 22.5 — Close the public-exposure hole (#66)

- [x] `compose.yaml` ports `"8080:8080"` binds `0.0.0.0` by default. Change to `"127.0.0.1:8080:8080"` so the container is only reachable through the reverse proxy on localhost.
- [x] Same fix on the Quadlet `resume-site.container` `PublishPort=` line.
- [x] `docs/PRODUCTION.md` gains a loud callout in the "Reverse proxy" section: if you're exposing 8080 directly to the public internet, the X-Forwarded-For trust model the app ships with is unsafe (see #16 / #34).

### 22.6 — Admin IP allowlist — don't trust X-Forwarded-For unconditionally (#16)

- [x] `app/routes/admin.py:104` picks the first comma-separated value from `X-Forwarded-For` verbatim. When the app is reached directly (not via Caddy), XFF is attacker-controlled.
- [x] Introduce a `trusted_proxies` CIDR list in `config.yaml` (default empty). Only consult `X-Forwarded-For` when `request.remote_addr` is inside `trusted_proxies`; otherwise fall back to `remote_addr`.
- [x] Companion fix: `#34` — the same logic lives in five other places (contact rate limit, API rate limit, analytics, `/metrics` access control, login throttle). The full extraction into `get_client_ip()` lands in v0.3.2 Phase 23.2; for v0.3.1 the immediate fix is applied to `admin.py` with a TODO comment pointing at 23.2 so the inconsistency is short-lived.

---

## Phase 35 — Release Publication Gate

*The CI publish + Trivy + cosign machinery shipped in v0.3.0 Phase 21.1–21.3. The **process** around it — tag matrix, release-notes template, README/PRODUCTION reorientation, stop-ship gate — never did. v0.3.1 is the first release that actually honours it.*

- [x] **GHCR as the canonical release surface:** `README.md` and `docs/PRODUCTION.md` reorder so the first install instruction is `podman pull ghcr.io/<owner>/resume-site:v0.3.1`. Source-tree install demoted to a "Development" sub-section. Compose / Quadlet examples reference the GHCR image by **digest-pinned** tag, not the moving `v0.3.1` alias.
- [x] **Tag matrix per release:** Push `v0.3.1`, `v0.3`, `v0`, and `latest` — all four manifests pointing at the same digest. CI `publish` job extended to push `v0.3.1` + advance `v0.3` / `v0` aliases via `docker buildx imagetools create`; `:latest` is held back to the new `release-verify` job and advanced only after multi-arch smoke. `:main` continues to track trunk; documented as non-production.
- [x] **Multi-arch verification before `latest` promotion:** Automated via the new `release-verify` CI job that pulls both `linux/amd64` and `linux/arm64` of the just-pushed image, boots each (arm64 via QEMU emulation), and asserts `/healthz` + `/readyz` are green before promoting `:latest`. Pre-release tags never move `:latest` regardless.
- [x] **Release-notes template:** `.github/RELEASE_TEMPLATE.md` with the three required lines — `podman pull ghcr.io/<owner>/resume-site:vX.Y.Z`, the image digest (`sha256:...`), the `cosign verify` command — plus a required "Breaking changes" section and a "Migration notes" section. A release without those lines doesn't ship.
- [x] **Stop-ship gate:** Documented in `docs/PRODUCTION.md` §12.2 as a single rule table — `quality` / `test` / `container-build` / `container-scan` (Trivy HIGH/CRITICAL with available fix) / `publish` / `release-verify` / clean-machine `cosign verify` / release-notes-template compliance are each full stops, not ratchets.
- [ ] **Dry-run the gate on v0.3.1-rc.1:** Before the stable tag, cut `v0.3.1-rc.1` against the same gate. Everything the stable release has to do, the RC has to do. This is the final proof that the process works. _(Awaits the actual RC tag — release-time action.)_

---

## Phase 36 — v0.3.0 Polish Carry-Over

*The polish items that stayed unchecked in v0.3.0 because each one was bounded but nobody had the half-day. They move to v0.3.1 because they aren't blockers for anything else, and v0.3.1 is the first release where they'd actually be user-visible anyway. Individually independent — each lands in its own small PR.*

### 36.1 — Frontend minification (v0.3.0 Phase 12.3)

- [x] **CSS minification:** Served minified in production, original in dev. Philosophy: stdlib-only — implement as a request-time middleware (not a build step), cached per-fingerprint against the `static_hashed()` hash from Phase 12.3. `rcssmin` is stdlib-compatible and considered acceptable; alternatively hand-roll a single-pass regex minifier (the file is 58 KB — cost is negligible).
- [ ] **JavaScript audit:** Profile `main.js` for unused functions, redundant event listeners, GSAP animations firing on hidden elements. Same for `admin.js`. Delete what's unused; document the deletions in CHANGELOG under "Removed."
- [x] **JavaScript minification:** Same middleware pattern as CSS.

### 36.2 — Admin image-upload polish (v0.3.0 Phase 14.4)

- [x] **Blog cover image preview:** Client-side thumbnail on file select, mirror of the photo-upload preview shipped in 14.4. Pure JS — `URL.createObjectURL` + a dedicated preview `<img>` below the file input.
- [x] **Drag-and-drop upload zone for photo manager:** Reuses the existing `process_upload` pipeline; only the client-side dropzone handler + highlight CSS are new. Must fall back to the `<input type="file">` when drag-drop isn't available.

### 36.3 — Translation completeness dashboard (v0.3.0 Phase 15.3)

- [x] Admin dashboard widget: per-locale coverage matrix — rows = content type, columns = configured locale, cell = `translated / total` plus a colour band. Data source: the six `_translations` tables from migration 011 via a single `LEFT JOIN` + `GROUP BY` per content type. No new migrations.

### 36.4 — `manage.py profile` CLI (v0.3.0 Phase 18.3)

- [x] Thin wrapper over the existing `scripts/benchmark_routes.py` that lives in `manage.py` so the docs can reference one command. Same output as the script: per-route p50/p95/query-count/response-size. `--routes /,/portfolio,/blog` to scope the probe; defaults to the full top-5 from `PERFORMANCE.md`.

### 36.5 — In-app alerting widget (v0.3.0 Phase 18.10)

- [x] Admin dashboard card showing active alerts by severity. Reads from the in-memory `resume_site_errors_total` counter (same source as the "Errors (since restart)" card) and applies the thresholds from `docs/alerting-rules.yaml` to decide what's active. Parses the YAML once at startup and caches the parsed rules. No new runtime dependency — PyYAML is already in `requirements.txt`.

### 36.6 — Observability cross-reference (v0.3.0 Phase 18.11)

- [x] Add a one-line pointer from `docs/PRODUCTION.md` (monitoring section) to `docs/OBSERVABILITY_RUNBOOK.md`. Five-minute doc edit; flagged because v0.3.0 closed without it.

### 36.7 — Subsystems as event-bus handlers (v0.3.0 Phase 19.1)

- [x] Migrate analytics, activity log, and metrics to subscribe to bus events instead of being called directly from route handlers. No behaviour change — every existing subscriber sees the same payload. Demonstrates the bus extension model and deletes three direct call paths from `app/routes/`. Regression test: a route that emits `photo.uploaded` causes the analytics counter and metrics gauge to update *without* the route calling them directly.

### 36.8 — K8s / Nomad commented-out examples (v0.3.0 Phase 21.4)

- [ ] Commented example k8s Deployment + Service + Ingress manifests in `docs/PRODUCTION.md`. Not an officially supported deployment shape, but the image is designed to work in orchestrated environments. The readiness-probe block already in `compose.yaml` documents the contract; this is the full manifest form operators have asked for. Include the `initialDelaySeconds: 5, failureThreshold: 3` probe pair from Phase 21.2.

---

## Phase Sequencing

```
Phase 22  (Critical security)     ─── Land first. Blocks release gate dry run.
Phase 35  (Release gate)          ─── In parallel with 22 where files don't overlap — 35 touches .github/ and docs, 22 touches app/.
Phase 36  (Polish carry-over)     ─── Incremental; any order between 22 and 35. Each sub-item is a standalone PR.
```

### Parallel Work Streams

```
Stream A (Security fixes):       22 ────────────────────→
Stream B (Gate + release infra): 35 ────────────────────→ (needs 22 done before RC dry run)
Stream C (Polish):               36.1-36.8 (any order, any sprint)
```

Streams A and B are file-disjoint. Stream C items are individually independent and can merge whenever they're ready.

---

## New Database Migrations (v0.3.1)

None. All Phase 22 fixes are code-only or settings-registry additions. Phase 35 is CI / docs / release-process only. Phase 36 items touch no schema.

---

## New CLI Commands (v0.3.1)

| Command | Purpose | Phase |
|---|---|---|
| `manage.py profile` | Per-route p50/p95/query-count/response-size (wraps `scripts/benchmark_routes.py`) | 36.4 |

---

## New Settings (v0.3.1)

| Key | Category | Default | Phase |
|---|---|---|---|
| `webhook_allow_private_targets` | Security | `false` | 22.3 |
| `trusted_proxies` | Security | `""` (empty CIDR list) | 22.6 (interim; full rollout in v0.3.2 Phase 23.2) |

---

## New Documentation (v0.3.1)

| Document | Purpose | Phase |
|---|---|---|
| `.github/RELEASE_TEMPLATE.md` | Required release-notes skeleton | 35 |
| `docs/PRODUCTION.md` updates | GHCR-first install; reverse-proxy XFF callout; k8s/Nomad commented manifests | 35, 22.5, 36.8 |
| `README.md` updates | `podman pull` as the primary install path | 35 |

---

## Success Criteria

v0.3.1 ships when:

1. Every issue in the Phase 22 scope (#15, #16, #17, #19, #41, #43, #44, #58, #59, #63, #66) is resolved or explicitly deferred with written justification.
2. `v0.3.1-rc.1` is cut and passes the full Phase 35 release-gate dry run (Trivy green, cosign verify green on a clean-machine probe, `/healthz` + `/readyz` green, both amd64 and arm64 smoke-tested).
3. The GHCR tag matrix is published (`v0.3.1`, `v0.3`, `v0`, `latest`) atomically and all four manifests resolve to the same digest.
4. `README.md` and `docs/PRODUCTION.md` both lead with `podman pull ghcr.io/...:v0.3.1`, not source install.
5. `.github/RELEASE_TEMPLATE.md` exists and the v0.3.1 release notes honour it (pull command, digest, cosign verify line, Breaking changes, Migration notes).
6. The eight Phase 36 polish sub-items are checked or explicitly re-deferred to v0.3.2 with rationale (no silent drop).

---

## Version Tagging

- `v0.3.0` — baseline (Forge)
- `v0.3.1-rc.1` — feature freeze; release-gate dry run
- `v0.3.1` — stable (Keystone); first release through the gate
