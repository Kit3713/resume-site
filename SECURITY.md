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
- `Content-Security-Policy` (report-only initially)
- `Strict-Transport-Security` when behind HTTPS

### Container
- Non-root user in container image (v0.2.0+)
- Minimal base image with no unnecessary tooling
- Read-only filesystem compatible (writable volumes for data only)
- Dependencies pinned with hashes

### Supply Chain
- All Python dependencies pinned with version and hash
- `pip-audit` in CI pipeline
- Minimal dependency tree — every dependency justified

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue
2. Email: *(add a contact email or use GitHub's private vulnerability reporting)*
3. Include: description, reproduction steps, impact assessment
4. Expected response: acknowledgment within 48 hours, fix timeline within 7 days

## Known Limitations

- SQLite does not support row-level locking — concurrent admin writes are serialized via `busy_timeout`
- Admin authentication is single-user in v0.1.x/v0.2.x (multi-user planned for v0.3.0)
- Content Security Policy may require tuning for custom CSS injection and CDN-hosted scripts (GSAP, Google Fonts)
- The review token system uses URL-based bearer tokens — treat review links as sensitive
