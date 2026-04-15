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


def track_page_view():
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
        ('/static/', '/admin', '/photos/', '/favicon', '/healthz', '/readyz', '/set-locale')
    ):
        return

    # Never let analytics tracking break the actual page response —
    # a failed page_view insert is not worth a 500 error.
    with contextlib.suppress(Exception):
        from app.db import get_db

        db = get_db()

        # Extract the real client IP from the proxy chain
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip and ',' in client_ip:
            # X-Forwarded-For may contain multiple IPs; the leftmost is the client
            client_ip = client_ip.split(',')[0].strip()

        db.execute(
            'INSERT INTO page_views (path, referrer, user_agent, ip_address) VALUES (?, ?, ?, ?)',
            (path, request.referrer or '', request.user_agent.string, client_ip or ''),
        )
        db.commit()
