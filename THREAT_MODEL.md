# Threat Model

**Version:** v0.3.0  
**Last reviewed:** 2026-04-16  
**Phase:** 13.1 of `ROADMAP_v0.3.0.md`

This document enumerates the attack surface, threat actors, mitigations,
residual risks, and incident response outline for resume-site.

---

## 1. Attack Surface

### 1.1 Public Routes

| Surface | Entry Points | Input Types |
|---|---|---|
| Landing / portfolio / blog | `GET /`, `/portfolio`, `/blog`, `/blog/<slug>` | URL path, query params (`page`, `per_page`, `tag`, `category`) |
| Contact form | `POST /contact` | Form body (name, email, message, honeypot) |
| Review submission | `POST /review/<token>` | Form body (name, message, rating), URL token |
| RSS feed | `GET /blog/rss.xml` | None |
| Sitemap | `GET /sitemap.xml` | None |
| Locale switch | `GET /locale/<code>` | URL path segment |
| REST API (public reads) | `GET /api/v1/*` | Query params, `Accept-Language` header |
| REST API (contact) | `POST /api/v1/contact` | JSON body |
| Health probes | `GET /healthz`, `GET /readyz` | None |
| Metrics | `GET /metrics` | None (IP-gated) |

### 1.2 Admin Routes

| Surface | Entry Points | Auth |
|---|---|---|
| Admin login | `POST /admin/login` | Username + password |
| Admin dashboard / editors | `GET/POST /admin/*` | Session cookie + IP restriction |
| API tokens admin | `GET/POST /admin/api-tokens/*` | Session + IP |
| Webhooks admin | `GET/POST /admin/webhooks/*` | Session + IP |

### 1.3 API Routes (Authenticated)

| Surface | Entry Points | Auth |
|---|---|---|
| Blog CRUD | `POST/PUT/DELETE /api/v1/blog/*` | Bearer token (`write` scope) |
| Portfolio CRUD | `POST/PUT/DELETE /api/v1/portfolio/*` | Bearer token (`write` scope) |
| Admin settings / analytics | `GET/PUT /api/v1/admin/*` | Bearer token (`admin` scope) |
| Backup trigger | `POST /api/v1/admin/backup` | Bearer token (`admin` scope) |
| Webhook management | `*/api/v1/admin/webhooks/*` | Bearer token (`admin` scope) |

### 1.4 File Upload

| Surface | Constraints |
|---|---|
| Photo upload (admin UI) | `POST /admin/photos/upload` — session + IP |
| Photo upload (API) | `POST /api/v1/portfolio` — Bearer + `write` scope |
| Validation | Magic bytes (JPEG/PNG/GIF/WebP), max file size, Pillow processing, EXIF stripping |

### 1.5 SMTP Relay

Outbound only. The app sends emails for contact form notifications. No
inbound mail processing.

### 1.6 SQLite Database

Single-file database at a configurable path. No network socket — access
is through the application process only.

### 1.7 Container Boundary

OCI container running as non-root user. Writable volumes for `/app/data`
(database), `/app/photos` (uploads), and `/app/backups` (archives).

---

## 2. Threat Actors

| Actor | Capability | Motivation |
|---|---|---|
| **Anonymous internet user** | Can reach all public routes. No credentials. | Vandalism, spam, reconnaissance, opportunistic exploitation |
| **Authenticated API consumer** | Possesses a scoped Bearer token (`read`, `write`, or `admin`). | Data exfiltration (if read), content defacement (if write), full control (if admin) |
| **Compromised reverse proxy** | Can inject headers (`X-Forwarded-For`, `X-Request-ID`), modify TLS termination, intercept traffic | Man-in-the-middle, session hijacking, header injection |
| **Supply chain attacker** | Compromised PyPI package, CDN-hosted JS (GSAP, Swagger UI, Google Fonts) | Backdoor, cryptominer, data exfiltration via injected code |

---

## 3. Mitigations by Phase

| Threat | Mitigation | Phase |
|---|---|---|
| SQL injection | Parameterized queries everywhere; CI grep guard blocks f-string SQL | 5 (v0.2.0) |
| XSS (stored) | `nh3` HTML sanitizer on save (allowlisted tags) | 5 (v0.2.0) |
| XSS (reflected) | Jinja2 auto-escaping enabled by default | 5 (v0.2.0) |
| CSRF | Flask-WTF CSRF tokens on all state-changing forms; API exempt (uses Bearer tokens) | 5 (v0.2.0) |
| Brute force (login) | Sliding-window IP lockout (login_attempts table), Flask-Limiter 5/min burst | 13.6 |
| Brute force (API) | Per-scope rate limiting (60/30/10 per min read/write/admin) | 13.4 |
| Session hijacking | `HttpOnly`, `SameSite=Lax`, `Secure` cookie attributes; no remember-me token | 13.6 |
| Token leakage | API tokens stored as SHA-256 hashes; raw shown once; refused in query strings | 13.4 |
| File upload abuse | Magic byte validation, max size, Pillow processing, EXIF stripping | 12.2 |
| Path traversal (uploads) | `secure_filename()` on all uploaded filenames | 5 (v0.2.0) |
| Credential stuffing | IP-hash-based lockout; constant-time password comparison | 13.6 |
| Admin exposure | IP restriction (`allowed_networks` CIDR list) on all admin routes | 5 (v0.2.0) |
| Information disclosure | Error responses contain only request ID; no stack traces, paths, or schema hints | 18.9 |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store` on admin | 7 (v0.2.0) |
| CSP | `Content-Security-Policy-Report-Only` with script/style/font/img directives | 7 (v0.2.0) |
| Dependency vulns | `pip-audit` in CI (advisory), Trivy CVE scan on container images (blocking) | 12.5, 21.3 |
| Secret leakage | `detect-secrets` pre-commit hook; no secrets in structured logs (PII scrubbing) | 12.5, 18.1 |
| Supply chain (container) | Cosign keyless OIDC signing on every published image | 21.3 |
| Supply chain (Python) | `requirements.txt` pinned with hashes | 5 (v0.2.0) |
| Webhook SSRF | Webhook URLs validated (http/https scheme only); delivery timeouts (5s default) | 19.2 |
| Spam | Honeypot field on contact form; per-IP hourly cap (5 submissions) | 5 (v0.2.0) |

---

## 4. OWASP Top 10 (2021) Mapping

| # | Category | Status | Controls |
|---|---|---|---|
| A01 | Broken Access Control | Mitigated | IP restriction on admin, Bearer token scopes on API, CSRF tokens on forms, `@login_required` on every admin route |
| A02 | Cryptographic Failures | Mitigated | Passwords hashed with pbkdf2/argon2, API tokens SHA-256 hashed, session cookie signed with HMAC-SHA512, `Secure` cookie flag, HTTPS expected |
| A03 | Injection | Mitigated | Parameterized SQL (CI-enforced), Jinja2 auto-escaping, `nh3` HTML sanitizer, `secure_filename()` |
| A04 | Insecure Design | Partially mitigated | Threat model (this document), defense-in-depth layers, but no formal security review by external auditor |
| A05 | Security Misconfiguration | Mitigated | Startup security audit warns on weak keys / missing SMTP / open admin; security headers on every response; CSP in report-only mode |
| A06 | Vulnerable Components | Mitigated | `pip-audit` in CI, Trivy CVE scanning, `detect-secrets`, pinned deps with hashes |
| A07 | Auth Failures | Mitigated | Login lockout, constant-time comparison, IP restriction, no default credentials (config required) |
| A08 | Software/Data Integrity | Mitigated | Cosign image signing, `requirements.txt` hash verification, CSRF tokens |
| A09 | Logging/Monitoring Failures | Mitigated | Structured JSON logging, request ID correlation, error categorization, Prometheus metrics, alerting rules |
| A10 | SSRF | Partially mitigated | Webhook URLs limited to http/https; no user-controlled outbound HTTP except webhooks. No SSRF protection on webhook target resolution (private IP ranges not blocked) |

---

## 5. Residual Risks and Accepted Trade-offs

| Risk | Impact | Likelihood | Acceptance Rationale |
|---|---|---|---|
| CSP in report-only mode | XSS payloads execute if injected despite sanitizer | Low (sanitizer is strong) | Enforcement planned in Phase 13.2; nonce infrastructure needed first |
| No WAF-lite request filter | Common attack patterns (path traversal in query params, SQL injection probes) reach the app | Low (parameterized queries prevent exploitation) | Planned in Phase 13.3 |
| Single admin account | No separation of duties; compromised password = full access | Medium for high-value targets | Multi-user + RBAC deferred to v0.4.0; IP restriction is the primary control |
| Client-side sessions | Session data readable (not encrypted, only signed) | Low (payload contains no secrets beyond CSRF token) | Documented in SECURITY.md; server-side sessions revisited in v0.4.0 |
| Webhook SSRF to private networks | Admin-configured webhook URL could target internal services | Low (requires admin scope Bearer token or admin UI access) | Only admin users can configure webhooks; private IP blocking deferred |
| CDN dependency (GSAP, fonts) | Compromised CDN could inject malicious JS | Low (CDN providers have strong security) | CSP script-src restricts to known CDN origins; self-hosting deferred |
| No fuzz testing yet | Edge cases in input handling may hide crashes or vulns | Medium | Planned in Phase 13.8 (Hypothesis) |
| SQLite file permissions | DB file readable by any user on the host | Low (container user namespace isolates) | Startup audit warns if world-readable; container is the primary deployment |

---

## 6. Incident Response Outline

### 6.1 Database Compromise

**Indicators:** Unexpected admin activity log entries, modified content,
new API tokens.

**Response:**
1. Rotate `secret_key` (`manage.py rotate-secret-key` when available, or
   manually in `config.yaml`). This invalidates all sessions.
2. Revoke all API tokens (`manage.py revoke-api-token` for each).
3. Change admin password hash in `config.yaml`.
4. Restore from the most recent clean backup (`manage.py restore --from <archive>`).
5. Review the activity log for the full scope of changes.
6. Audit `allowed_networks` — was the admin IP restriction bypassed?

### 6.2 Container Breach

**Indicators:** Unexpected processes, modified files outside writable
volumes, outbound network connections.

**Response:**
1. Stop the container immediately: `podman stop resume-site`.
2. Preserve the container filesystem for forensics: `podman export resume-site > forensics.tar`.
3. Pull a fresh image from GHCR and verify with cosign.
4. Restore data from backup to a new container.
5. Rotate all secrets (secret_key, admin password, API tokens).
6. Review host-level access — was the container runtime compromised?

### 6.3 API Token Leak

**Indicators:** Unexpected API usage patterns, token `last_used_at`
activity outside normal hours.

**Response:**
1. Immediately revoke the leaked token: `manage.py revoke-api-token --id <N>`.
2. Audit the activity log for actions taken with the token.
3. If the token had `admin` scope, treat as a database compromise (6.1).
4. Generate a new token for the legitimate consumer.
5. Review token distribution — was it exposed in logs, a repo, or a URL?

### 6.4 Spam / Abuse Flood

**Indicators:** High contact submission volume, `is_spam=true` entries,
rate limiter 429 responses spiking.

**Response:**
1. Check the `contact_submissions` table for patterns (IP hash, timing).
2. If the flood is bypassing the honeypot, temporarily disable the
   contact form: set `contact_form_enabled=false` in admin settings.
3. Review rate limiter settings — tighten the per-IP cap if needed.
4. Consider adding the source IP range to the reverse proxy's block list.
