"""Phase 31 — Playwright browser-based test suite.

Verifies the GSAP scroll animations, Quill editor, theme-editor live preview,
Sortable.js drag-drop, photo upload drop zone, CSP nonce enforcement, and the
CDN-unavailability fallback path — none of which the request-level test suite
in ``tests/`` can exercise.

These tests boot against a live HTTP server (``BASE_URL``, default
``http://localhost:5000``), not the Flask test client, and require
``pytest-playwright`` + a Chromium browser. CI installs both in the
``browser-tests`` job; local runners use ``playwright install chromium``.
"""
