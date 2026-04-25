"""
API Deprecation Decorator — Phase 37.2

Provides the ``@deprecated`` decorator for marking REST API endpoints
that are scheduled for removal. The decorator stamps the response with
the standard deprecation headers (RFC 9745 ``Deprecation`` + RFC 8594
``Sunset`` + RFC 8288 ``Link: rel="successor-version"``), logs a
structured INFO line so operators can see who is still calling the
route, and increments a per-endpoint Prometheus counter.

The machinery lands in this module ahead of the first real usage so
the API contract change can be reviewed in isolation. No existing
route is decorated by this PR — the first ``@deprecated`` call site
will accompany the v0.4.0 ``/api/v2/`` rollout.

Stdlib only — ``functools.wraps`` for the decorator, ``email.utils``
for the locale-safe HTTP-date format. The metric counter is borrowed
from :mod:`app.services.metrics` so deprecated-call accounting shares
the existing exposition machinery.
"""

from __future__ import annotations

import functools
import logging
from datetime import UTC, date, datetime
from email.utils import format_datetime

from flask import g, has_request_context, make_response, request

from app.services.metrics import deprecated_api_calls_total

_log = logging.getLogger('app.api.deprecation')


def _to_http_date(sunset_date: str | date | datetime) -> str:
    """Convert a sunset date to an RFC 7231 HTTP-date.

    Uses ``email.utils.format_datetime`` rather than ``strftime('%a, %d
    %b %Y ...')`` so the day / month names are emitted in the C locale
    regardless of the host's ``LC_TIME`` setting — the HTTP spec
    requires English abbreviations.
    """
    if isinstance(sunset_date, datetime):
        parsed = sunset_date if sunset_date.tzinfo else sunset_date.replace(tzinfo=UTC)
    elif isinstance(sunset_date, date):
        parsed = datetime(sunset_date.year, sunset_date.month, sunset_date.day, tzinfo=UTC)
    else:
        parsed = datetime.strptime(sunset_date, '%Y-%m-%d').replace(tzinfo=UTC)
    return format_datetime(parsed, usegmt=True)


def _to_iso_date(sunset_date: str | date | datetime) -> str:
    """Render the sunset as a plain ``YYYY-MM-DD`` string for marker comparison."""
    if isinstance(sunset_date, datetime):
        return sunset_date.date().isoformat()
    if isinstance(sunset_date, date):
        return sunset_date.isoformat()
    return sunset_date


def deprecated(
    sunset_date: str | date | datetime,
    *,
    replacement: str | None = None,
    reason: str | None = None,
):
    """Mark a Flask view as deprecated.

    Args:
        sunset_date: ISO-8601 ``YYYY-MM-DD`` date when the endpoint will
            stop being served. Translated into an RFC 7231 HTTP-date for
            the ``Sunset`` response header.
        replacement: Optional URL of the successor endpoint. When set,
            adds a ``Link: <url>; rel="successor-version"`` header.
        reason: Optional human-readable explanation. Recorded in the
            log line so operators can see *why* an endpoint went away.

    Effects on every call:

    * Sets ``Deprecation: true`` (RFC 9745 draft).
    * Sets ``Sunset: <HTTP-date>`` derived from ``sunset_date``.
    * Sets ``Link: <replacement>; rel="successor-version"`` when
      ``replacement`` is supplied.
    * Logs an INFO record on ``app.api.deprecation`` with the request
      id (when in a request context), endpoint name, ``User-Agent``,
      and optional ``X-Client-ID`` header value so operators can see
      who's still calling.
    * Increments
      ``resume_site_deprecated_api_calls_total{endpoint=<name>}``.

    Idempotent across decorator stacking: in a stack of two
    ``@deprecated`` calls, the inner wrapper runs first and stamps the
    headers, log line, and counter; the outer wrapper sees
    ``Deprecation`` already set on the response and skips its own
    writes. This keeps a route's surface clean (no double-count, no
    duplicate log line, no header overwrite) even when middleware
    composition accidentally produces a stack.
    """
    sunset_http_date = _to_http_date(sunset_date)
    sunset_iso = _to_iso_date(sunset_date)

    def _decorator(view):
        endpoint_name = getattr(view, '__name__', '<view>')

        @functools.wraps(view)
        def _wrapped(*args, **kwargs):
            response = make_response(view(*args, **kwargs))
            # Idempotency: in a stack of two @deprecated calls the inner
            # wrapper runs first (it's closer to the view), and by the
            # time we get here on the outer wrapper, Deprecation is
            # already set. Bow out so we don't double-count, double-log,
            # or overwrite the inner decorator's chosen sunset / link.
            if response.headers.get('Deprecation'):
                return response

            response.headers['Deprecation'] = 'true'
            response.headers['Sunset'] = sunset_http_date
            if replacement:
                response.headers['Link'] = f'<{replacement}>; rel="successor-version"'

            if has_request_context():
                request_id = getattr(g, 'request_id', None) or '-'
                user_agent = request.headers.get('User-Agent', '-')
                client_id = request.headers.get('X-Client-ID', '-')
            else:
                request_id = user_agent = client_id = '-'

            _log.info(
                'deprecated API call: endpoint=%s sunset=%s reason=%s '
                'request_id=%s user_agent=%s client_id=%s',
                endpoint_name,
                sunset_date,
                reason or '-',
                request_id,
                user_agent,
                client_id,
            )

            deprecated_api_calls_total.inc(label_values=(endpoint_name,))
            return response

        # Markers consumed by the OpenAPI drift-guard test (Phase 37.3) so the
        # spec's ``deprecated: true`` + ``x-sunset`` keys can be cross-checked
        # against the actual decorator on the route.
        _wrapped.__deprecated_sunset__ = sunset_iso
        _wrapped.__deprecated_replacement__ = replacement
        _wrapped.__deprecated_reason__ = reason
        return _wrapped

    return _decorator
