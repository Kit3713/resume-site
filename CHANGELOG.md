# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
