# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in resume-site, please report it responsibly.

**Do not open a public issue.** Instead, email the maintainer directly or use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) feature on this repository.

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

I'll acknowledge receipt within 48 hours and work on a fix.

## Scope

This policy covers the resume-site application code. It does not cover your deployment infrastructure (Caddy, Podman, host OS, etc.) — those are your responsibility to secure.

## Design Considerations

- Admin panel access is restricted to configured private IP ranges
- Admin authentication uses hashed passwords (Werkzeug PBKDF2)
- No personal data is stored in the public repository
- Contact form uses honeypot fields for bot mitigation
- Review submissions require invite tokens
