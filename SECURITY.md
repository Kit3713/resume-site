# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :x: (upgrade recommended) |

## Security Model

resume-site uses a defense-in-depth approach:

### Network Layer
- Admin routes are restricted to configured private IP ranges (RFC 1918 + Tailscale CGNAT)
- Expected to run behind a TLS-terminating reverse proxy (Caddy, nginx)
- No direct internet exposure of Gunicorn

### Application Layer
- CSRF tokens on all state-changing forms (v0.2.0+)
- HTML content sanitized on save via allowlisted tags (v0.2.0+)
- File uploads validated by magic bytes, not just extension (v0.2.0+)
- Rate limiting on public POST endpoints
- Honeypot spam protection on contact form
- Parameterized SQL queries throughout (no string interpolation)
- Session timeout on admin login (v0.2.0+)

### Authentication
- Single admin account with hashed password (pbkdf2 or argon2)
- Session-based auth via Flask-Login
- Admin IP restriction as a second factor

### Response Headers (v0.2.0+)
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` restricting device APIs
- `Cache-Control: no-store` on admin pages

### Container
- Non-root user in container image (v0.2.0+)
- Minimal base image with no unnecessary tooling
- Read-only filesystem compatible (writable volumes for data only)
- Dependencies pinned with hashes

### Supply Chain
- All Python dependencies pinned with version and hash
- `pip-audit` in CI pipeline
- Minimal dependency tree — every dependency justified

## CVE Response Process (Phase 13.5)

When a CVE is discovered in a runtime dependency:

1. **Triage:** Check `pip-audit` output (CI runs this on every PR; pre-commit
   hook runs locally). Determine if the vulnerability is reachable from
   resume-site's usage of the package.
2. **Patch:** Bump the affected package in `requirements.in`, regenerate
   `requirements.txt` with hashes (`pip-compile --generate-hashes`), and
   verify the test suite passes.
3. **Container rebuild:** Push a new image with `docker build --pull --no-cache`
   to ensure the base image layer is fresh. The Trivy CVE scan in CI will
   verify the fix.
4. **Release:** If the CVE is HIGH or CRITICAL and reachable, cut a patch
   release (e.g., v0.3.1) immediately. MEDIUM+ findings are batched into
   the next planned release.
5. **Disclose:** Update `CHANGELOG.md` with the CVE ID, affected versions,
   and the fix version. If users are at risk, notify via a GitHub Security
   Advisory.

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue
2. Email: *(add a contact email or use GitHub's private vulnerability reporting)*
3. Include: description, reproduction steps, impact assessment
4. Expected response: acknowledgment within 48 hours, fix timeline within 7 days

### Session and Cookie Audit (Phase 13.6)

**Cookies set by the application:** Only one — the Flask session cookie
(`resume_session`).

| Attribute | Value | Rationale |
|---|---|---|
| `Name` | `resume_session` | Explicit name for auditing; avoids the generic `session` default |
| `HttpOnly` | `True` | Prevents JavaScript access — mitigates XSS session theft |
| `SameSite` | `Lax` | Blocks cross-site POST-based CSRF while allowing top-level navigation |
| `Secure` | `True` (production) | Cookie only sent over HTTPS; configurable via `session_cookie_secure: false` for local HTTP dev |
| `Path` | `/` (Flask default) | Cookie available to all routes |

**No remember-me cookie:** Flask-Login's `remember=True` is not used.
Sessions expire when the browser closes (no persistent token on disk).

**No custom `set_cookie()` calls:** Confirmed by codebase audit — no route
or middleware sets a cookie directly.

**Decision: Client-side vs. server-side sessions**

Flask's default session implementation stores all session data in a signed
(HMAC-SHA512) cookie. The cookie is tamper-proof but not encrypted — a
user can base64-decode it and read the contents.

Current session payload: `_user_id` (admin username), `_fresh` (bool),
`csrf_token` (random string), and optionally `locale` (language code).
None of these are secrets beyond the CSRF token, which is per-session
and only useful with the corresponding signed cookie.

**Decision: Keep client-side sessions.** Rationale:

1. The session payload is small (<500 bytes) and contains no sensitive
   data beyond the CSRF token.
2. Server-side sessions (Flask-Session + SQLite) add a dependency, a new
   table, and a cleanup job for expired sessions — complexity that
   doesn't pay for itself at this scale.
3. The single-admin model means there's at most one active session at a
   time. Session enumeration and session fixation risks are minimal.
4. If v0.4.0 adds multi-user auth, server-side sessions should be
   revisited (session revocation, concurrent session limits).

## Known Limitations

- SQLite does not support row-level locking — concurrent admin writes are serialized via `busy_timeout`
- Admin authentication is single-user in v0.1.x/v0.2.x (multi-user planned for v0.3.0)
- Content Security Policy may require tuning for custom CSS injection and CDN-hosted scripts (GSAP, Google Fonts)
- The review token system uses URL-based bearer tokens — treat review links as sensitive
