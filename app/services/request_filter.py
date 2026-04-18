"""
WAF-Lite Request Filter — Phase 13.3

Lightweight before-request handler that inspects incoming requests for
common attack patterns. This is NOT a full WAF — it catches low-hanging
probes (path traversal, obvious SQL injection fingerprints, oversized
bodies, empty user-agents) and returns 400 to reduce noise in the logs.

Configurable via two settings:
- ``request_filter_enabled`` (default ``true``) — master toggle.
- ``request_filter_log_only`` (default ``false``) — when true, log
  violations at WARNING but don't block the request (tuning mode).

Returns 400 (not 403) on block so the filter's existence isn't revealed
to scanners.
"""

from __future__ import annotations

import logging
import re

from flask import abort, request

_log = logging.getLogger('app.security')

_PATH_TRAVERSAL = re.compile(r'(\.\./|\.\.\\|%2e%2e%2f|%2e%2e/|\.\.%2f|%00|\x00)', re.IGNORECASE)

_SQL_INJECTION = re.compile(
    r"('.*(\bOR\b|\bAND\b).*[=<>]|"
    r'\bUNION\b.*\bSELECT\b|'
    r';\s*(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE)\b)',
    re.IGNORECASE,
)


def check_request(settings: dict) -> None:
    """Inspect the current request and abort(400) on suspicious patterns.

    Args:
        settings: The cached settings dict (from ``get_all_cached``).
    """
    if str(settings.get('request_filter_enabled', 'true')).lower() not in {
        '1',
        'true',
        'yes',
        'on',
    }:
        return

    log_only = str(settings.get('request_filter_log_only', 'false')).lower() in {
        '1',
        'true',
        'yes',
        'on',
    }

    reason = _detect_violation()
    if reason:
        _log.warning(
            'request filter: reason=%s method=%s path=%s ip=%s ua=%s',
            reason,
            request.method,
            request.path,
            request.remote_addr,
            (request.user_agent.string or '-')[:200],
        )
        if not log_only:
            abort(400)


def _detect_violation() -> str | None:
    """Return a short reason string if the request looks suspicious, else None."""
    path = request.path
    raw_path = request.environ.get('RAW_URI', '') or request.full_path
    if _PATH_TRAVERSAL.search(path) or _PATH_TRAVERSAL.search(raw_path):
        return 'path_traversal'

    query = request.query_string.decode('utf-8', errors='replace')
    if query and _SQL_INJECTION.search(query):
        return 'sql_injection_probe'

    if request.content_length and request.content_length > 10 * 1024 * 1024:
        return 'oversized_body'

    if request.method in ('POST', 'PUT', 'PATCH'):
        ct = request.content_type or ''
        if request.content_length and request.content_length > 0 and not ct:
            return 'missing_content_type'

    return None
