# resume-site v0.3.3 Roadmap

> **Codename:** Proof
> **Status:** Planning
> **Baseline:** v0.3.2 (Shield — audit backlog closed)
> **Target:** Performance wins, CI/tooling hygiene, code-redundancy closeout, and the five large "prove it" testing initiatives that never landed in v0.3.0 — DAST, Playwright, load-test CI gate, mutation baseline, edge-case testing methodology. The release that turns the v0.3.0 claim "92% coverage with meaningful tests" into verified fact.

---

## Why "Proof"

v0.3.1 and v0.3.2 are about *shipping* and *hardening*. v0.3.3 is about *proving* — every optimisation measured, every test file reviewed against the edge-case checklist, every code path scanned by DAST, every PR judged against a performance baseline. The five big "prove it" initiatives (Phases 30-34) are the slowest work in the v0.3.x cycle because each one establishes a new baseline that every future PR is compared against. They land last so the tree is quiet and the baselines they capture are stable.

Expect this release to take multiple sprints. The success criteria are hard numbers, not shipped bullets.

---

## Scope Summary

| Category | Source | Phase |
|---|---|---|
| Performance — N+1, Gunicorn tuning, admin pagination, Pillow hot path, metrics scan, benchmark harness | Issues #28, #36, #52, #53, #54, #61, #64 | 26 |
| CI / packaging / tooling hygiene | Issues #27, #29, #30, #31 | 28 |
| Code audit redundancy closeout (#56) | Issue #56 | 29 |
| DAST pipeline (CI ZAP baseline) | v0.3.0 Phase 13.9 carry-over | 30 |
| Browser-based testing (Playwright) | v0.3.0 Phase 18.4 carry-over + two v0.3.0 Playwright-dependent items | 31 |
| Load-test CI regression gate | v0.3.0 Phase 18.6 carry-over | 32 |
| Mutation-testing baseline + CI integration | v0.3.0 Phase 18.8 carry-over | 33 |
| Edge-case testing methodology | v0.3.0 Phase 18.13 carry-over | 34 |

### Out of Scope (→ v0.4.0+)

- Multiple admin / viewer accounts, public login, RBAC, OAuth/OIDC → **v0.4.0**
- PostgreSQL backend → **v0.4.0**
- Plugin architecture (cut in v0.3.0; not re-opened)
- First-party `/status` endpoint → **v0.4.0** feature decision
- Distroless base image / image-size regression gate → **v0.4.0** if size becomes a bottleneck
- Real-time features (WebSocket) → **v0.4.0+**

---

## Phase 26 — Performance

*Each item below is a measured regression against `PERFORMANCE.md` or a documented win that v0.3.0 left on the table. Land them in this order because 26.1 and 26.4 are the largest gains and the rest compose cleanly on top.*

### 26.1 — Eliminate the translations N+1 (#52)

- [x] `overlay_posts_translations` rewritten to issue ONE `SELECT * FROM blog_post_translations WHERE post_id IN (?,?,…,?) AND locale IN (?, ?)` query and merge in Python. Before the rewrite every post in the listing paid two queries (parent re-fetch + per-post translation lookup); at 20 posts on the feed path that was 40 extra hot-path SELECTs.
- [x] Three regression tests in `tests/test_n_plus_1.py`: query count is 1 regardless of post count (3 vs 20), source row preserved when no translation matches the active or fallback locale, fast-path zero queries when active == fallback locale.
- [ ] Deferred: `PERFORMANCE.md` before/after capture — perf-diff work that's meaningful against production data, not local benchmarks.

### 26.2 — Gunicorn `--preload` and worker recycling (#28, #53)

- [x] **#53 `--preload`:** Added to `docker-entrypoint.sh`. 500-800 ms cold-start win + lower steady-state RSS via copy-on-write. The page_views drainer (25.2) and webhook thread pool (25.3) are started lazily on first use, *after* fork, so `--preload` is safe.
- [x] **#28 `--max-requests` / `--max-requests-jitter`:** Added `--max-requests 2000 --max-requests-jitter 200`. Workers recycle every ~2000 requests with a random 0-200 jitter so they don't all recycle simultaneously. Pairs with `--preload` — the recycled worker re-forks from the pre-loaded master, so recycling is cheap.

### 26.3 — Paginate `/admin/blog` (#54)

- [x] New `get_all_posts_paginated(db, status_filter, page, per_page)` in `app/services/blog.py` returns `(rows, total_count)`. The `/admin/blog` route wires it up; default 25 posts/page, invalid `?page=` falls back to 1. Paginator in `admin/blog_list.html` keeps `?status=` on Previous/Next links so filter + page compose. Two regression tests: 30 seeded posts split 25/5 across pages with disjoint titles; invalid `?page=not-a-number` renders page 1 instead of 500.

### 26.4 — Photo upload: `Image.draft()` for JPEG (#61)

- [ ] Before the `Image.open()` in `app/services/photos.py:189`, call `img.draft('RGB', (max_dim, max_dim))` when the detected format is JPEG. libjpeg-turbo will do a DCT-level downscale; documented 4-8× faster on 24 MP DSLR inputs.
- [ ] Preserve correctness: EXIF stripping still works, responsive variants still match the 640/1024/2000 ladder. Regression test: upload a 24 MP fixture, assert the final 2000 px variant is byte-for-byte within 1% of the pre-change output at the same quality setting.
- [ ] `PERFORMANCE.md` photo-upload row updated with the before/after numbers.

### 26.5 — `/metrics` disk-usage scrape cost (#36)

- [ ] Currently walks the entire photo directory on every Prometheus scrape. At 10k photos this is seconds per scrape.
- [ ] Cache the photo-directory size in the settings table (`photos_disk_usage_bytes`, `photos_disk_usage_updated_at`). Refresh in two places: (a) every photo upload/delete bumps the value by the file size delta (cheap); (b) the `manage.py purge-all` run (v0.3.2 Phase 25.1) writes a ground-truth total as a reconciliation step. `/metrics` reads the cached value in O(1). Document the staleness window (max 24 h between reconciliations).
- [ ] Same pattern for the DB size gauge (stat the file, cheap — leave as-is).

### 26.6 — Benchmark harness sets its own log level (#64)

- [x] `scripts/benchmark_routes.py` is documented to need `RESUME_SITE_LOG_LEVEL=WARNING`, but the script doesn't set it. Any contributor following the top-of-file docstring silently measures the stderr sink.
- [x] Have the script `os.environ.setdefault('RESUME_SITE_LOG_LEVEL', 'WARNING')` at import time, **before** importing `app`. Print the effective level in the banner so it's obvious if the operator overrode it.

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

- [ ] The CI job is `continue-on-error: true` with a "Tracked: TODO" note stranded for months. Either root-cause the SELinux-on-bind-mount failure and flip blocking, or retire the job entirely and replace it with a simpler `podman run --rm ghcr.io/.../resume-site:main migrate --dry-run` probe that runs in `publish`. v0.3.3 picks one.

### 28.4 — Quadlet / systemd hardening (#27)

- [ ] Add the low-risk `systemd.exec` hardening directives to `resume-site.container` and `resume-site-backup.service`: `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `RestrictSUIDSGID=yes`, `LockPersonality=yes`, `MemoryDenyWriteExecute=yes` (test against Pillow first), `RestrictNamespaces=yes`, `SystemCallArchitectures=native`. Document each in comments with the rationale and the rollback procedure.
- [ ] Apply the same set to the `resume-site-purge.service` added in v0.3.2 Phase 25.1.

---

## Phase 29 — Code Redundancy Audit Closeout (#56)

*Issue #56 is a tracking issue for ~40 redundancies across routes, services, models, templates, tests, and `manage.py`. v0.3.3 carves specific PRs out of it; the rest roll over as ongoing tech-debt.*

### 29.1 — Form-field extraction helper

- [ ] The pattern `request.form.get('field', '').strip()` is repeated across eight admin/blog/API files. Extract `app/services/form.py:get_stripped(form, key, default='')` and migrate every call site. Keep the behaviour byte-identical (`strip()` only, no case folding).

### 29.2 — CRUD service-layer pass

- [ ] Revisit the Phase 12.2 "deferred: CRUD base mixin" note. Now that the REST API write handlers have landed, the duplication between the HTML admin services and the API services is concrete. Extract a shared `update_fields(db, table, id, fields)` helper that handles the partial-update + validation + activity-log-emission triad; rewrite the services that duplicate it.

### 29.3 — Test fixture consolidation

- [ ] `#56` flags multiple ad-hoc admin-login fixtures across `tests/test_admin*.py`. Consolidate on the canonical `logged_in_admin_client` fixture and remove the variants.

### 29.4 — Roll the rest forward

- [x] Close `#56` at v0.3.3 with a summary comment listing which bullets landed and which remain open as standalone issues. Don't keep a tracking issue indefinitely — it stops tracking once the half-life exceeds the release cycle.

---

## Phase 30 — DAST Pipeline (carry-over from v0.3.0 Phase 13.9)

*The static-analysis half of v0.3.0 shipped (ruff, bandit, pip-audit, detect-secrets). The dynamic half did not. v0.3.3 closes that gap.*

- [ ] **OWASP ZAP baseline scan in CI:** New `security-scan` job in `.github/workflows/ci.yml`. Runs `zap-baseline.py` against the container built by `container-build`, seeded with the test content from `seeds/`. Passes if zero MEDIUM+ findings; uploads the HTML report as a CI artifact either way. `needs: [test, container-build]`; blocks `publish` via the existing `needs` chain.
- [ ] **`zap-config.yaml`:** Tune the ruleset — exclude known-accepted findings (the Server-header fingerprint is fine once v0.3.2 #14 lands; the admin-login form deliberately sends no Cache-Control: no-store because Flask-Login handles it). Every exclusion carries an inline comment with the issue link.
- [ ] **Authenticated-scan mode:** ZAP logs into the test app via the admin form, follows admin routes, and scans them under authentication. Test admin credentials provisioned by the CI seed step only.
- [ ] **Report retention:** CI artifact kept 30 days. Runbook in `docs/SECURITY.md` for operators to re-run locally against their own deployment.

---

## Phase 31 — Browser-Based Testing with Playwright (carry-over from v0.3.0 Phase 18.4)

*The v0.2.0 deferral that slid through v0.3.0. Playwright is the only way to catch regressions in the GSAP animations, the Quill editor, the theme-editor live preview, and the Sortable.js drag-drop wiring. v0.3.3 also absorbs the two Playwright-dependent v0.3.0 items that were parked waiting on this phase.*

- [ ] **Playwright dev dependency + CI job:** Add `playwright` + `playwright install --with-deps chromium` to the dev setup. New CI job `browser-tests` runs against the built container, `needs: container-build`. Screenshots + video on failure retained as artifacts.
- [ ] **Dark/light mode toggle:** `localStorage.setItem('theme', 'light')` then reload; assert `<html>` carries `data-theme="light"` and the computed `--color-bg` matches the light-theme custom property.
- [ ] **GSAP scroll animation:** Scroll to each section; assert the fade+slide class has been applied within 2 s; assert no JS errors in the console.
- [ ] **Quill editor content round-trip:** Admin login → content editor → type + format a paragraph → save → reload → assert content round-tripped byte-for-byte.
- [ ] **Photo upload drag-drop zone:** Drag a fixture PNG into the zone (the zone added in v0.3.1 Phase 36.2); assert the upload POST fires with the right multipart body; assert the photo appears in the grid.
- [ ] **Theme editor live preview:** Change the accent color; assert the iframe `document.documentElement.style` mirrors the change within 250 ms without a full reload.
- [ ] **Drag-drop reordering persistence:** Reorder three services; reload; assert the order persists.
- [ ] **CSP + nonce assertion (v0.3.0 Phase 13.2 carry-over, line 165):** Playwright probe asserts every inline `<script>` on every visited page carries a valid nonce and no `'unsafe-inline'` fallback is present in any response. Covers every public page, every admin page, every GSAP animation, every font load, every CDN script — the exhaustive CSP test that v0.3.0 deferred pending this phase.
- [ ] **CDN unavailability (v0.3.0 Phase 18.7 carry-over, line 497):** With the GSAP CDN (`cdnjs.cloudflare.com`) blocked at the network layer via Playwright's request routing, every page still renders and is fully functional (just without animations). Assert no JavaScript errors block page interaction.

---

## Phase 32 — Load-Test CI Regression Gate (carry-over from v0.3.0 Phase 18.6)

*v0.3.0 shipped the locust scenarios (`tests/loadtests/locustfile.py`). What it didn't ship: the baseline numbers, the CI gate that compares every PR against them, or the documented stress-test behaviour.*

- [ ] **Baseline run:** 50 concurrent users × 5 min against a seeded container on a dedicated runner class. Record p50/p95/p99 per endpoint in `PERFORMANCE.md`. Commit the numbers to `tests/loadtests/thresholds.json` so the CI gate has something to compare against. Must be run **after** Phase 26 so baselines reflect post-optimisation numbers.
- [ ] **`perf-regression` CI job:** 20 concurrent users × 60 s against the built container; fail if any endpoint's p95 exceeds its threshold by > 20%. Summary table in the job log. Thresholds bumped by hand when an intentional regression (e.g. translations JOIN) is accepted — each bump cites the justifying PR.
- [ ] **Memory leak probe:** Record process RSS at test start + end. WARN if +50% over the run. Advisory in v0.3.3; ratchet to blocking in v0.4.0.
- [ ] **Concurrency stress test:** 200 concurrent users × 30 s. Must not crash, must not 500, must not corrupt SQLite. Behaviour documented in `PERFORMANCE.md` — the goal is "degrades gracefully," not "stays fast."

---

## Phase 33 — Mutation-Testing Baseline + CI Integration (carry-over from v0.3.0 Phase 18.8)

*mutmut is configured; the baseline was never run. Without it, the v0.3.0 claim of "92% coverage with meaningful tests" is unverified.*

- [ ] **Full baseline run:** `mutmut run` against `app/`. Target: ≥ 70% killed. Record the score in `PERFORMANCE.md` under a new "Test Quality" section.
- [ ] **Surviving-mutant review:** Walk the survivor list for the hot-path modules (`app/services/{content,photos,webhooks,translations,settings_svc}.py`, `app/routes/{admin,api,contact,blog_admin}.py`, `app/__init__.py`). For each surviving mutant, either add a test that kills it or mark it `equivalent` with a one-line justification in `tests/MUTATION_EQUIVALENT.md`.
- [ ] **CI integration (advisory):** Nightly job running `mutmut run --paths-to-mutate=$(git diff --name-only main...HEAD)` on the PR delta. Reports killed/survived in the PR summary. Not blocking in v0.3.3 — ratchet to blocking in a later release once the baseline is stable.
- [ ] **`manage.py mutation-report`** updated to emit Markdown for pasting into PR descriptions.

---

## Phase 34 — Edge-Case Testing Methodology (carry-over from v0.3.0 Phase 18.13)

*The "3 assertions vs. 15" gap. v0.3.0 shipped lots of tests that verify features work but few that verify boundaries. v0.3.3 codifies the checklist and does the retroactive pass.*

- [ ] **`tests/TESTING_STANDARDS.md`:** The edge-case checklist from the v0.3.0 18.13 draft — empty/null, boundary, type mismatch, Unicode, length, concurrency, injection. Each category carries two or three concrete examples drawn from real bugs this codebase has had.
- [ ] **Retroactive pass (ranked):** Apply the checklist to `tests/test_admin.py`, `tests/test_api.py`, `tests/test_webhooks.py`, `tests/test_photos.py`, `tests/test_reviews.py`, `tests/test_settings.py`, `tests/test_blog_admin.py`. Track per-file completion in `tests/TESTING_STANDARDS.md`. Remaining files carry over as tech-debt issues — don't block v0.3.3 on 100% coverage.
- [ ] **New-code requirement:** `CONTRIBUTING.md` documents that every PR touching a function accepting user input must include the checklist-derived tests. Code-review checklist template in `.github/pull_request_template.md` references the file.
- [ ] **Linked to Phase 33:** surviving mutants often reveal the edge cases the test missed. Do 33 and 34 in the same sprint — each informs the other.

---

## Phase Sequencing

```
Phase 26  (Performance)            ─── File-disjoint from 28/29. Land first — baselines needed for 32.
Phase 28  (CI hygiene)             ─── Parallel with 26. Doesn't touch app code.
Phase 29  (Redundancy closeout)    ─── After 26 + 28 so refactors don't conflict.
Phase 30  (DAST)                   ─── After 26-29; scans the final shape.
Phase 31  (Playwright)             ─── After 30 (same CI-container infra).
Phase 32  (Load-test gate)         ─── After 26 so baselines reflect post-optimisation numbers.
Phase 33  (Mutation baseline)      ─── After 29 so the tree is stable.
Phase 34  (Edge-case methodology)  ─── Paired with 33 — each informs the other.
```

### Parallel Work Streams

```
Stream A (Perf + redundancy):  26 → 29 ────────────────── → 32
Stream B (CI hygiene):         28 ──────────────────────── →
Stream C (Proof infra):        ──── 30 → 31 ────────────── → 33 + 34
```

All three streams converge at the Phase 35 release gate inherited from v0.3.1.

---

## New Database Migrations (v0.3.3)

None expected. Phase 26.5 reuses the v0.3.2 settings-table pattern; Phase 29 is pure refactor; Phases 30-34 are CI + test infrastructure.

---

## New Settings (v0.3.3)

| Key | Category | Default | Phase |
|---|---|---|---|
| `photos_disk_usage_bytes` | Internal (not user-facing) | auto-maintained | 26.5 |
| `photos_disk_usage_updated_at` | Internal (not user-facing) | auto-maintained | 26.5 |

---

## New Documentation (v0.3.3)

| Document | Purpose | Phase |
|---|---|---|
| `tests/TESTING_STANDARDS.md` | Edge-case checklist and retroactive-pass tracker | 34 |
| `tests/MUTATION_EQUIVALENT.md` | Surviving-mutant justifications | 33 |
| `.github/pull_request_template.md` | Updated to reference the edge-case checklist | 34 |
| `docs/SECURITY.md` (expanded) | DAST operator runbook | 30 |
| `PERFORMANCE.md` (expanded) | Post-optimisation baselines, load-test numbers, mutation score, stress-test behaviour | 26, 32, 33 |

---

## Success Criteria

v0.3.3 ships when:

**Performance (hard numbers):**
1. Translations N+1 eliminated — `/blog` at non-default locale runs `== 2` queries regardless of post count, locked in by `tests/test_n_plus_1.py`.
2. Gunicorn `--preload` + `--max-requests`/`--max-requests-jitter` live in `docker-entrypoint.sh`; cold-start win documented in `PERFORMANCE.md` as 500-800 ms reduction.
3. `/metrics` scrape cost is O(1) regardless of photo count (measured at 10k photos in `PERFORMANCE.md`).
4. Photo upload uses `Image.draft()` for JPEG; 24 MP benchmark shows 4-8× speedup in `PERFORMANCE.md`.
5. `benchmark_routes.py` sets its own log level; first-line banner shows the effective value.

**CI + code hygiene:**
6. SQL-interpolation grep guard handles both `noqa: S608` and `nosec B608` annotations.
7. Vulture is blocking in CI (flipped from advisory).
8. `upgrade-simulation` is either blocking or retired — not indefinitely advisory.
9. `resume-site.container` + `resume-site-backup.service` + `resume-site-purge.service` carry the systemd hardening directive set.
10. Issue #56 closed with the specific PRs shipped in Phase 29.

**Quality infrastructure (the "proof" layer):**
11. DAST baseline scan in CI passes with zero MEDIUM+ findings on a fresh build. Authenticated-scan mode covers admin routes.
12. Playwright browser tests cover the seven listed flows + the two v0.3.0 Playwright-dependent carry-overs (CSP enforcement exhaustive check, CDN unavailability). CI runs them on every PR.
13. Load-test baseline captured in `PERFORMANCE.md`; `perf-regression` CI gate rejects PRs whose p95 exceeds threshold by > 20%. Stress-test behaviour (200 concurrent users × 30 s) documented.
14. Mutation score ≥ 70% on the nine hot-path modules; baseline in `PERFORMANCE.md`; advisory CI job running on PR deltas; `tests/MUTATION_EQUIVALENT.md` covers every surviving mutant.
15. Edge-case checklist applied to the seven priority test files; tracker current in `tests/TESTING_STANDARDS.md`; PR template references the checklist.

**Release:**
16. Ships through the Phase 35 release gate inherited from v0.3.1 (Trivy green, cosign verify green on a clean-machine probe, `/healthz` + `/readyz` green, amd64 + arm64 smoke-tested, tag matrix pushed atomically).

---

## Version Tagging

- `v0.3.2` — Shield baseline
- `v0.3.3-alpha.N` — tagged as 26, 28, 29 complete
- `v0.3.3-beta.N` — tagged as 30, 31 complete
- `v0.3.3-rc.1` — feature freeze; release-gate dry run
- `v0.3.3` — stable (Proof); third release through the gate
