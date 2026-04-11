# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
- Expanded CSS: ~1700 lines covering all public pages, responsive layouts, masonry grid, card components, form styling
- Expanded test suite covering all public page routes, contact form submission, review token flow, and analytics

### Added — GitHub Community Files
- MIT LICENSE file
- GitHub Actions CI workflow (pytest + flake8 on Python 3.11/3.12 + container build)
- Issue templates (bug report, feature request)
- Pull request template with checklist
- CONTRIBUTING.md
- SECURITY.md
- CHANGELOG.md
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
