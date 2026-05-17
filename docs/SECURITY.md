# DAST Operator Runbook

> **Scope:** Operator-facing documentation for the Dynamic Application
> Security Testing (DAST) pipeline added in Phase 30 (v0.3.3). For the
> project-wide security policy and vulnerability-disclosure process,
> see the top-level [`SECURITY.md`](../SECURITY.md) in the repo root.
>
> **Related files:**
> [`.github/workflows/security-scan.yml`](../.github/workflows/security-scan.yml) ·
> [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) ·
> [`zap-config.yaml`](../zap-config.yaml) ·
> [`.zap/auth-context.xml`](../.zap/auth-context.xml) ·
> [`THREAT_MODEL.md`](../THREAT_MODEL.md)

---

## 1. What this pipeline does

The DAST pipeline runs the OWASP ZAP scanner against a freshly built
container image of the application. It catches a different category of
issue than the static-analysis CI gates (ruff, bandit, pip-audit,
detect-secrets, Trivy) — runtime issues that only manifest when the app
is actually serving HTTP:

* Missing or weak HTTP response headers
* Cookie attribute regressions (HttpOnly, SameSite, Secure)
* CSP / X-Frame-Options drift
* Reflected XSS via crafted query parameters
* Open redirect via the `?next=` parameter
* SQL injection via dynamic-spider URL fuzzing (passive baseline scans
  see structural patterns; the active full scan probes payloads)
* CSRF token absence on admin forms (authenticated mode)
* Information disclosure via error pages, debug routes, or stack traces

The pipeline runs in three ways:

| Trigger | Workflow | Blocking? | When |
|---|---|---|---|
| Every PR + push to `main` | `ci.yml` calls `security-scan.yml` | Yes (baseline); No (auth) | On every change |
| Weekly cron | `security-scan.yml` standalone | No (the cron isn't gating publish) | Mondays 04:00 UTC |
| On-demand | `workflow_dispatch` | No | Ops runs from Actions tab |

Phase 30 made the per-PR run the primary gate. The weekly cron remains
as a safety net that catches drift from base-image updates, transitive
dependency upgrades, or new ZAP rules.

---

## 2. Reading the report artifact

Every scan uploads two artifacts to the workflow run:

* `zap-baseline-report` — HTML + JSON reports from the passive scan
* `zap-full-scan-auth-report` — HTML + JSON reports from the
  authenticated active scan (when that job runs)
* `dast-container-logs-baseline` / `dast-container-logs-auth` —
  application stdout/stderr for post-mortem debugging

**Retention:** 30 days from the workflow run (configured in
`actions/upload-artifact` `retention-days: 30`).

To download a report:

1. Open the workflow run on the GitHub Actions tab.
2. Scroll to the bottom of the summary page.
3. Click the artifact name. GitHub will serve a ZIP containing the
   HTML report (`report_html.html`), the JSON (`report_json.json`), and
   the raw XML output.
4. Open `report_html.html` in any browser.

**What to look at first:**

* The **summary card** shows total findings by severity. A clean run
  should be `0` Medium / `0` High / `0` Critical.
* The **alert detail** section lists every finding with the rule ID,
  severity, request/response excerpt, and remediation reference.
* For an authenticated scan, **check that "Logged In Indicator"
  was matched** in the report's authentication summary. If ZAP
  silently fell back to unauthenticated traversal, every `/admin/*`
  finding is a false negative and the auth context needs tuning.

---

## 3. Local re-run procedure

You do not need GitHub Actions to reproduce a scan. The same ZAP
container the CI uses runs fine on a developer workstation.

### 3.1 Prerequisites

* Docker (or Podman with `alias docker=podman`)
* The resume-site image you want to scan, either built locally
  (`docker build -t resume-site:dev -f Containerfile .`) or pulled
  from GHCR (`docker pull ghcr.io/kit3713/resume-site:main`).
* `zap-config.yaml` (in the repo root — the ruleset).
* For authenticated scans: `.zap/auth-context.xml` and a real
  `password_hash` for the test admin user.

### 3.2 Baseline scan (matches the CI baseline job)

```bash
# 1. Boot the target.
cat > /tmp/dast-config.yaml <<'EOF'
secret_key: "local-dast-key-not-for-production-but-long-enough-now"  # pragma: allowlist secret
database_path: "/app/data/site.db"
photo_storage: "/app/photos"
session_cookie_secure: false
admin:
  username: "admin"
  password_hash: "pbkdf2:sha256:600000$abc$0000000000000000000000000000000000000000000000000000000000000000"  # pragma: allowlist secret
  allowed_networks:
    - "0.0.0.0/0"
EOF

mkdir -p /tmp/dast-data /tmp/dast-photos
docker run -d --name dast-target \
  -p 8080:8080 \
  -v /tmp/dast-config.yaml:/app/config.yaml:ro \
  -v /tmp/dast-data:/app/data \
  -v /tmp/dast-photos:/app/photos \
  resume-site:dev

# 2. Wait for /readyz.
until curl -fsS http://localhost:8080/readyz; do sleep 1; done

# 3. Run the scan. ``--network host`` lets the ZAP container reach
#    the target on localhost. ``-c`` points at the ruleset.
docker run --rm --network host \
  -v "$(pwd):/zap/wrk:ro" \
  zaproxy/zap-stable zap-baseline.py \
  -t http://localhost:8080 \
  -c zap-config.yaml \
  -r /zap/wrk/report.html
# (drop the trailing -r flag if you don't have a writable mount;
#  the HTML report still appears in stdout truncated form)

# 4. Tear down.
docker stop dast-target && docker rm dast-target
```

### 3.3 Authenticated scan (matches the CI full-scan-auth job)

The authenticated scan needs three extra ingredients on top of the
baseline:

1. A **working admin credential**, generated with werkzeug:
   ```bash
   python -c "from werkzeug.security import generate_password_hash; \
       print(generate_password_hash('ci-dast-test-pw-not-a-real-secret'))"
   ```
   Paste the output into the `password_hash:` field of the config above.

2. The **rendered context file** with credentials substituted in:
   ```bash
   export ZAP_ADMIN_USERNAME=admin
   export ZAP_ADMIN_PASSWORD=ci-dast-test-pw-not-a-real-secret
   export ZAP_ADMIN_USERNAME_B64=$(printf '%s' "$ZAP_ADMIN_USERNAME" | base64 -w0)
   mkdir -p /tmp/zap-wrk
   envsubst < .zap/auth-context.xml > /tmp/zap-wrk/auth-context.xml
   ```

3. The **full-scan invocation** (note `zap-full-scan.py`, not baseline):
   ```bash
   docker run --rm --network host \
     -v "$(pwd):/zap/wrk:ro" \
     -v /tmp/zap-wrk:/zap/wrk-rendered:ro \
     zaproxy/zap-stable zap-full-scan.py \
     -t http://localhost:8080 \
     -c zap-config.yaml \
     -n /zap/wrk-rendered/auth-context.xml \
     -U 1 \
     -j \
     -m 5 \
     -T 10
   ```

The full scan takes ~20 minutes on a developer workstation; the
baseline finishes in ~5 minutes.

---

## 4. Triage workflow — how to handle a new finding

The pipeline blocks merge on any Medium-or-higher finding that is
**not** in the documented allowlist. When the gate fires:

### 4.1 Reproduce the finding

1. Download the HTML report artifact (see §2).
2. Find the alert by rule ID.
3. Copy the request URL + method + payload from the alert detail.
4. Reproduce locally with `curl` against a freshly booted dev container
   (matching the §3.2 setup).
5. Confirm the finding represents a real regression — ZAP occasionally
   flags structural absences that are intentional (e.g. the
   `Server: gunicorn` header).

### 4.2 Decide: fix, downgrade, or allowlist

* **Fix** — the finding is a real bug. Open an issue, write a failing
  test that asserts the fixed behaviour, ship the fix in the same PR
  or a follow-up. This is the **default** outcome — most findings are
  fixable.

* **Downgrade** — the rule is firing at a higher severity than the
  actual risk warrants in our deployment context. Rare. Add a `WARN`
  entry to `zap-config.yaml` with a justification line:
  ```yaml
  10063   WARN   (Permissions-Policy header missing — optional;
                  we opt out because our headers config is audited
                  separately. Issue: #NNN)
  ```
  `WARN` keeps the finding in the report at LOW severity so it never
  blocks the build.

* **Allowlist** — the finding is a documented false positive or
  accepted risk. Add an `IGNORE` entry:
  ```yaml
  10037   IGNORE   (Server: gunicorn — engine version not exposed;
                    intentionally kept for reverse-proxy diagnostics.
                    Issue: #14)
  ```

  **Every IGNORE entry MUST carry an inline justification** with at
  least one of: an issue link, a CHANGELOG reference, or a one-sentence
  description that a reviewer can audit six months from now. A bare
  `IGNORE` with no context is reverted on sight.

### 4.3 Allowlist entries — current contents

These are documented in [`zap-config.yaml`](../zap-config.yaml). The
table below cross-references them for quick scanning; the YAML file
itself is the source of truth.

| Rule | Action | Why |
|---|---|---|
| 10037 | IGNORE | `Server: gunicorn` header — intentionally exposed for reverse-proxy diagnostics; doesn't leak version. |
| 10015 | IGNORE | Cache-Control missing on `/admin/login` GET — Flask-Login bootstrap, downstream admin pages DO set `no-store`. |
| 10202 | FAIL  | Anti-CSRF tokens — admin forms must carry one; locked to FAIL. |
| 10054 | FAIL  | Cookie without SameSite — session cookie sets `SameSite=Lax`; locked to FAIL. |
| 10020 | WARN  | X-Frame-Options — we emit DENY; rule sometimes flags meta-tag fallback at LOW. |
| 10038 | WARN  | CSP — we emit a strict CSP from after_request; modern rule may flag report-only header. |
| 10063 | WARN  | Permissions-Policy — optional; staging area for first-run review. |
| 10096 | WARN  | Timestamp Disclosure — often a false positive on Cookie / ETag values. |
| 10109 | WARN  | Modern Web Application — informational. |
| 40018-22, 40027 | IGNORE | SQL Injection — driver-specific rules for stacks we don't run (we use SQLite). |
| 90017, 90019, 90020 | IGNORE | XSLT / SSI / OS Command Injection — tech-stack mismatches or active-scan-only signals. |

---

## 5. Authenticated vs baseline scan trade-offs

Two scans run on every PR. They catch different things; both are
useful but they have different operational profiles.

### 5.1 Baseline scan (passive)

* **What it does:** crawls public surface, looks at response
  headers + body for structural issues. No payloads injected.
* **Coverage:** ~80% of the public URL space in ~10 minutes.
* **Blind spots:** admin routes (gets bounced at `/admin/login`),
  authenticated-only JS, anything the spider can't discover (single-
  page-app routes without progressive enhancement).
* **CI status:** BLOCKING. A Medium+ finding outside the allowlist
  fails the build.
* **Tuning cost:** low — the ruleset stabilised in v0.3.0 Phase 13.9
  and rarely changes.

### 5.2 Full scan with authentication

* **What it does:** logs in as a CI-provisioned admin, crawls every
  `/admin/*` route, AND injects active-scan payloads (XSS strings, SQL
  injection patterns, path traversal sequences).
* **Coverage:** ~95% of the URL space (public + admin) in ~20 minutes.
* **Blind spots:** anything that requires multi-step state (file
  uploads of specific magic bytes, drag-drop ordering interactions —
  those are Playwright's job in Phase 31).
* **CI status:** ADVISORY (continue-on-error) for the first iteration.
  The auth context (`.zap/auth-context.xml`) is a starting point and
  ZAP's context-tuning loop is iterative — login form structure
  changes, CSRF-token extraction regex breaks, redirect-target shifts
  all cause silent unauthenticated fallback that produces uninformative
  reports. The flag flips to BLOCKING once the context proves stable
  over 2-3 consecutive clean CI runs. Tracking issue: TODO (filed
  when Phase 30 merges).
* **Tuning cost:** medium-high. Expect to revisit the context XML once
  per quarter as the admin UI evolves.

### 5.3 When to use which locally

* **PR pre-flight:** run the baseline. 5 minutes, catches the
  regressions that would block CI.
* **Security audit / pentest prep:** run the full authenticated scan.
  20 minutes, but covers the admin attack surface that an opportunistic
  attacker would only see post-compromise — which is exactly what an
  audit should cover.
* **Header / cookie changes:** baseline is enough — those rules fire
  on the first response and don't need auth.
* **New admin form / new admin route:** full scan. Headers + CSRF
  + cookie attribute checks all need authenticated traversal to fire
  on `/admin/*` URLs.

---

## 6. Artifact retention policy

| Artifact | Retention | Why |
|---|---|---|
| `zap-baseline-report` | 30 days | Operator review window; the 30-day default matches GitHub's free-tier limit and balances triage time against storage cost. |
| `zap-full-scan-auth-report` | 30 days | Same. |
| `dast-container-logs-baseline` | 30 days | Post-mortem debugging when a scan fails to start. |
| `dast-container-logs-auth` | 30 days | Same. |

**Why not longer:** the underlying image is preserved by the
`publish` job (every passing CI run tags a build), so the *artifact*
that matters long-term is the image SHA, not the scan report. The
report is the audit trail for "did this commit pass DAST"; once a
release is cut, the answer is recorded in the release notes / CHANGELOG
and the scan report is no longer load-bearing.

**To extend retention** for a specific incident response: download the
artifact and attach it to the relevant issue / CHANGELOG entry within
the 30-day window.

---

## 7. Threat model — what DAST catches and what it doesn't

This section is a short cross-reference. The canonical threat model
lives in [`THREAT_MODEL.md`](../THREAT_MODEL.md).

### 7.1 What DAST catches

* **Network-layer findings:** missing security headers, weak TLS
  configuration (out of scope here — we test against HTTP behind a
  TLS-terminating reverse proxy), cookie attribute regressions.
* **Reflected XSS / open redirect:** the active scanner injects
  payloads into every reachable parameter and watches for un-escaped
  reflection.
* **Information disclosure:** stack traces in error responses, debug
  routes left enabled, version banners.
* **CSRF absence:** the authenticated scan asserts state-changing
  endpoints require a CSRF token (paired with the
  `Anti-CSRF Tokens` rule, which we lock to FAIL).
* **Path traversal / directory listing:** request fuzzing exercises
  `../`, `%2e%2e%2f`, and similar sequences against every endpoint.
* **Known-bad URLs:** ZAP ships a built-in list of common vulnerable
  paths (`/.env`, `/wp-admin/`, `/.git/config`); we don't expose any
  of those, but the check guards against regression.

### 7.2 What DAST DOESN'T catch

* **Logic flaws** — IDOR where the URL pattern is structurally
  identical between authorised and unauthorised access (e.g.
  `/admin/post/<id>/edit` returning 200 for any `<id>` regardless of
  ownership). Logic flaws need targeted manual / property-based
  testing (Phase 13.8 hypothesis suite catches some).
* **Authentication weaknesses beyond cookie attributes** — weak
  password policy, timing-attack feasibility on the login form,
  session-fixation across logout. These are covered by the dedicated
  tests in `tests/test_login_throttle.py` / `tests/test_admin.py`.
* **Storage layer** — SQL injection against parameterised queries is
  structurally not present (the CI grep guard in Phase 28.1 enforces
  this); ZAP fuzzing of parameters is a belt-and-braces second pass.
* **Supply chain** — vulnerable transitive dependencies are caught by
  pip-audit + Trivy, not DAST.
* **File upload abuse** — magic-byte validation + Pillow processing
  defences are exercised by `tests/test_photos.py` and the
  property-based fuzz suite (Phase 13.8); DAST sees them only as
  HTTP status responses.
* **Race conditions / concurrency** — Phase 32's load-test gate
  catches the load-related ones; ZAP's single-threaded baseline
  doesn't induce concurrency.
* **JavaScript-rendered surface** — the AJAX spider helps, but Quill
  + GSAP + Sortable.js interactions are Phase 31 Playwright territory.

### 7.3 Defence in depth

DAST is **one** gate of many. Treating it as a sufficient security
check would be a category error. The complete CI security gate stack
is:

1. `ruff` + `bandit` — static-analysis SAST (every push).
2. `pip-audit` — dependency CVE check (every push).
3. `detect-secrets` — pre-commit + pre-push secret leak detection.
4. `Trivy` — container CVE + secret scan (every push).
5. **`security-scan` (DAST) — this pipeline (every push).**
6. `migrate-dryrun` — schema-change soundness gate (every push).
7. `release-verify` — multi-arch smoke test at release time.

A finding in any one of those layers blocks publish. DAST catches the
runtime regressions the other six can't see; the other six catch the
structural / build-time issues DAST can't see.

---

## 8. Updating this runbook

Every change to the DAST pipeline (workflow, ruleset, context file)
should land with a matching update here. The runbook is the operator
contract — if it falls out of date with the actual workflow behaviour,
the runbook is wrong, not the workflow. Fix the runbook in the same
PR.

In particular:

* New `IGNORE` / `WARN` entry in `zap-config.yaml` → update the
  table in §4.3.
* New scan job in `security-scan.yml` → add a row to the §1 table.
* Change to artifact retention → update §6.
* New category of finding DAST can / can't catch → update §7.
