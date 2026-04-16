"""
Prometheus /metrics Endpoint — Phase 18.2

Exposes the module-level metrics registry defined in
``app/services/metrics.py`` as a Prometheus text-exposition response.

Two gates protect the endpoint:

1. **Feature flag** — the ``metrics_enabled`` setting (default ``false``).
   When disabled, ``/metrics`` returns 404 (not 403) so an observer
   cannot confirm the endpoint exists.

2. **IP allow-list** — the comma-separated ``metrics_allowed_networks``
   setting takes precedence; when empty, the check falls back to the
   admin's ``allowed_networks`` from ``config.yaml``. A mismatch also
   returns 404 rather than 403, preserving the "does this endpoint
   exist?" ambiguity.

Both responses emit a standard 404 body so scanners can't fingerprint
the metrics endpoint by response length.
"""

from __future__ import annotations

import contextlib

from flask import Blueprint, abort, current_app, request

from app.services.metrics import (
    CONTENT_TYPE,
    backup_last_success_timestamp,
    blog_posts_total,
    client_ip_in_networks,
    disk_usage_bytes,
    get_registry,
    parse_cidr_list,
    process_uptime_seconds,
    uptime_seconds,
)
from app.services.settings_svc import get_all_cached

metrics_bp = Blueprint('metrics', __name__)


def _resolve_allowed_networks(settings, site_config):
    """Return the effective CIDR allow-list for /metrics.

    Precedence: ``metrics_allowed_networks`` setting > admin
    ``allowed_networks`` in config.yaml. Both empty → fail closed
    (returns empty list, the caller denies).
    """
    override = parse_cidr_list(settings.get('metrics_allowed_networks', ''))
    if override:
        return list(override)
    admin_cfg = site_config.get('admin', {}) if site_config else {}
    return list(admin_cfg.get('allowed_networks', []))


def _client_ip():
    """Return the real client IP, honouring X-Forwarded-For from Caddy."""
    forwarded = request.headers.get('X-Forwarded-For', request.remote_addr)
    if forwarded and ',' in forwarded:
        forwarded = forwarded.split(',')[0].strip()
    return forwarded


@metrics_bp.route('/metrics')
def metrics():
    """Serve the Prometheus text exposition.

    Feature-flagged and IP-gated (see module docstring).
    """
    # --- Feature flag gate ---
    # A DB lookup failure is treated as "feature off" — /metrics must not
    # leak diagnostic info from an unhealthy app. ``contextlib.suppress``
    # keeps the code path compact without a bare except.
    settings = {}
    db = None
    with contextlib.suppress(Exception):
        from app.db import get_db

        db = get_db()
        settings = get_all_cached(db, current_app.config['DATABASE_PATH'])

    if str(settings.get('metrics_enabled', '')).strip().lower() not in {
        '1',
        'true',
        'yes',
        'on',
    }:
        abort(404)

    # --- IP allow-list gate ---
    site_config = current_app.config.get('SITE_CONFIG', {})
    allowed = _resolve_allowed_networks(settings, site_config)
    if not allowed or not client_ip_in_networks(_client_ip(), allowed):
        abort(404)

    # --- Render ---
    registry = get_registry()
    # Refresh process-uptime gauge at scrape time so it's always current.
    uptime_seconds.set(process_uptime_seconds())

    # Refresh scrape-time gauges (Phase 18.2 deferred batch).
    _refresh_blog_posts_gauge(db)
    _refresh_backup_timestamp_gauge(settings)
    _refresh_disk_usage_gauge(current_app.config)

    body = registry.render()
    return body, 200, {'Content-Type': CONTENT_TYPE}


def _refresh_blog_posts_gauge(db):
    """Set blog_posts_total gauge per status from the database."""
    if db is None:
        return
    with contextlib.suppress(Exception):
        rows = db.execute(
            'SELECT status, COUNT(*) as cnt FROM blog_posts GROUP BY status'
        ).fetchall()
        for row in rows:
            blog_posts_total.set(row['cnt'], label_values=(row['status'],))


def _refresh_backup_timestamp_gauge(settings):
    """Set backup_last_success_timestamp gauge from the settings value."""
    raw = settings.get('backup_last_success', '')
    if not raw:
        return
    with contextlib.suppress(Exception):
        from datetime import UTC, datetime

        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        backup_last_success_timestamp.set(dt.replace(tzinfo=UTC).timestamp())


def _refresh_disk_usage_gauge(app_config):
    """Set disk_usage_bytes gauge for the database and photo directories."""
    import os

    with contextlib.suppress(Exception):
        db_path = app_config.get('DATABASE_PATH', '')
        if db_path and os.path.isfile(db_path):
            disk_usage_bytes.set(os.path.getsize(db_path), label_values=('database',))

    with contextlib.suppress(Exception):
        photo_dir = app_config.get('PHOTO_STORAGE', '')
        if photo_dir and os.path.isdir(photo_dir):
            total = sum(
                os.path.getsize(os.path.join(dirpath, f))
                for dirpath, _dirnames, filenames in os.walk(photo_dir)
                for f in filenames
            )
            disk_usage_bytes.set(total, label_values=('photos',))
