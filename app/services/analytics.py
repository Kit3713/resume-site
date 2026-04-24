"""
Lightweight Page View Analytics

Provides a simple, privacy-respecting page view counter that stores visit
data in SQLite. No cookies, no tracking scripts, no third-party services.

Registered as a Flask before_request handler by the app factory, this module
logs every public GET request to the page_views table. Static assets, admin
pages, and photo serving routes are excluded to keep the data meaningful.

Data retention is configurable via the analytics_retention_days setting,
and old records can be purged with `python manage.py purge-analytics`.
"""

import contextlib

from flask import request


def track_page_view() -> None:
    """Log a page view to the database. Runs before every request.

    Only tracks:
    - GET requests (skips POST, PUT, DELETE, etc.)
    - Public pages (skips /static/, /admin, /photos/, /favicon)

    Extracts the client IP from X-Forwarded-For when behind a reverse proxy
    (Caddy), falling back to request.remote_addr for direct connections.

    Silently catches all exceptions to ensure analytics never break the
    actual page response — a failed page view insert is not worth a 500 error.
    """
    if request.method != 'GET':
        return

    path = request.path
    if path.startswith(
        (
            '/static/',
            '/admin',
            '/photos/',
            '/favicon',
            '/healthz',
            '/readyz',
            '/set-locale',
            '/csp-report',
        )
    ):
        return

    # Never let analytics tracking break the actual page response —
    # a failed page_view insert is not worth a 500 error.
    with contextlib.suppress(Exception):
        from app.db import get_db

        db = get_db()

        # Phase 23.2 — route the XFF decision through the one helper
        # (see app/services/request_ip.py). Before the extraction, this
        # trusted X-Forwarded-For blindly (audit #34), which let a
        # direct-exposure caller spoof their origin.
        from app.services.request_ip import get_client_ip

        client_ip = get_client_ip(request)

        # Phase 24.2 (#45) — hash the IP and discard the full UA before
        # the INSERT. The raw client IP never reaches the page_views
        # table; the UA is collapsed to a coarse browser+form class.
        # The hash is salted with the app's secret_key so log files
        # cannot be joined across deployments to re-identify visitors.
        from flask import current_app as _app

        from app.services.logging import classify_user_agent, hash_client_ip

        ip_hash = hash_client_ip(client_ip or '', _app.secret_key or '')
        ua_class = classify_user_agent(request.user_agent.string)

        db.execute(
            'INSERT INTO page_views (path, referrer, user_agent, ip_address) VALUES (?, ?, ?, ?)',
            (path, request.referrer or '', ua_class, ip_hash),
        )
        db.commit()
