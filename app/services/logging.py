"""
Structured Logging — Phase 18.1

Configures Python's standard :mod:`logging` module to emit one structured
record per request with a consistent schema. Works in two modes:

* **JSON** (default, production) — one JSON object per line, ready for
  ingestion by log aggregators (ELK, Loki, Cloudwatch).
* **Human** — a single human-readable line with timestamp, level, request
  correlation, and request metadata. Intended for local development.

Mode and level are configured purely through environment variables:

* ``RESUME_SITE_LOG_FORMAT`` — ``json`` (default) or ``human``.
* ``RESUME_SITE_LOG_LEVEL``  — ``DEBUG`` / ``INFO`` (default) / ``WARNING``
  / ``ERROR`` / ``CRITICAL``.

Env-based configuration is deliberate: logging has to work before the
database is available (startup, migrations, CLI one-shots) so it cannot
depend on the settings table.

Every record is enriched by :class:`_RequestContextFilter` with
``request_id`` (from ``flask.g.request_id`` — populated by the
``_assign_request_id`` before-request handler) and ``client_ip_hash`` (a
per-deployment hash of the remote address, see :func:`hash_client_ip`).
Outside request scope the filter substitutes the sentinel ``'-'`` so the
filter is safe on the root logger.

The request-logging hook itself lives in :mod:`app` (``_log_request``) —
this module just provides the plumbing.

No third-party dependencies. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime

try:  # Flask is present in every runtime context that calls the filter,
    # but tests construct records outside an app context.
    from flask import g, has_request_context
except ImportError:  # pragma: no cover — Flask is a hard dep
    g = None  # type: ignore[assignment]

    def has_request_context():  # type: ignore[misc]
        return False


LOG_FORMAT_JSON = 'json'
LOG_FORMAT_HUMAN = 'human'
_VALID_FORMATS = {LOG_FORMAT_JSON, LOG_FORMAT_HUMAN}

_ENV_FORMAT = 'RESUME_SITE_LOG_FORMAT'
_ENV_LEVEL = 'RESUME_SITE_LOG_LEVEL'

# Sentinel used when we have no flask.g context (CLI, background tasks).
_NO_CONTEXT = '-'

# Fields that make up a structured record. Anything else found on the
# LogRecord via ``extra={}`` is passed through verbatim in JSON mode.
_BASE_KEYS = (
    'timestamp',
    'level',
    'logger',
    'message',
    'module',
    'request_id',
    'client_ip_hash',
)

# These attributes are installed on every LogRecord by logging internals
# (and ours) and should NOT be re-emitted as "extra" fields in JSON output.
_STDLIB_LOG_ATTRS = frozenset(
    {
        'args',
        'asctime',
        'created',
        'exc_info',
        'exc_text',
        'filename',
        'funcName',
        'levelname',
        'levelno',
        'lineno',
        'message',
        'module',
        'msecs',
        'msg',
        'name',
        'pathname',
        'process',
        'processName',
        'relativeCreated',
        'stack_info',
        'thread',
        'threadName',
        'taskName',
        # Our own — handled by the formatter directly, not as extras.
        'request_id',
        'client_ip_hash',
    }
)


# ---------------------------------------------------------------------------
# IP hashing
# ---------------------------------------------------------------------------


def hash_client_ip(ip, salt):
    """Return a 16-character hex digest derived from ``salt + ip``.

    The hash is deliberately one-way so log files alone cannot be joined
    across deployments (different ``salt`` → different digests for the
    same IP). Within a deployment it is stable, so analytics ("same
    visitor hit 50 pages in 5 minutes") still work.

    Empty / ``None`` inputs hash the empty string so callers don't need
    to guard against ``request.remote_addr is None``.
    """
    material = f'{salt or ""}:{ip or ""}'.encode()
    return hashlib.sha256(material).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class _RequestContextFilter(logging.Filter):
    """Inject ``request_id`` and ``client_ip_hash`` from ``flask.g``.

    Installed on the root logger so every record — ours, Flask's,
    Werkzeug's — is enriched. Outside request context both fields fall
    back to ``'-'`` so downstream formatters have something to render.
    """

    def filter(self, record):  # type: ignore[override]
        if has_request_context() and g is not None:
            record.request_id = getattr(g, 'request_id', _NO_CONTEXT) or _NO_CONTEXT
            record.client_ip_hash = getattr(g, 'client_ip_hash', _NO_CONTEXT) or _NO_CONTEXT
        else:
            record.request_id = _NO_CONTEXT
            record.client_ip_hash = _NO_CONTEXT
        return True


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per record, newline-delimited.

    Schema: ``timestamp`` (ISO-8601 UTC, ``Z`` suffix), ``level``,
    ``logger``, ``message``, ``module``, ``request_id``, and
    ``client_ip_hash``. Any additional keys passed via ``extra={}`` on
    the log call (e.g. ``method``, ``path``, ``status_code``,
    ``duration_ms``, ``user_agent``) are included verbatim.
    """

    def format(self, record):  # type: ignore[override]
        payload = {
            'timestamp': _iso_timestamp(record.created),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'request_id': getattr(record, 'request_id', _NO_CONTEXT),
            'client_ip_hash': getattr(record, 'client_ip_hash', _NO_CONTEXT),
        }
        # Include any user-supplied extras. Guards against secrets sneaking
        # in via stdlib record internals by explicitly ignoring the stdlib
        # attribute set.
        for key, value in record.__dict__.items():
            if key in _STDLIB_LOG_ATTRS or key in payload:
                continue
            try:
                json.dumps(value)  # make sure it serialises
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value

        if record.exc_info:
            payload['exc_info'] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(',', ':'), ensure_ascii=False)


class _HumanFormatter(logging.Formatter):
    """Emit a single compact human-readable line per record.

    Example::

        2026-04-15T13:54:30Z [INFO ] app.request req=abcd1234 ip=0f0f
            GET /portfolio 200 42ms

    No ANSI colours — output goes to stderr which may be piped or
    captured by container log drivers.
    """

    def format(self, record):  # type: ignore[override]
        ts = _iso_timestamp(record.created)
        level = record.levelname.ljust(5)
        req_id = getattr(record, 'request_id', _NO_CONTEXT)
        ip = getattr(record, 'client_ip_hash', _NO_CONTEXT)

        base = f'{ts} [{level}] {record.name} req={req_id} ip={ip} {record.getMessage()}'
        if record.exc_info:
            base = f'{base}\n{self.formatException(record.exc_info)}'
        return base


def _iso_timestamp(created):
    """Format a Unix ``created`` epoch as ISO-8601 UTC with ``Z`` suffix."""
    return datetime.fromtimestamp(created, tz=UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging(app):
    """Install handlers + filter on the root logger and ``app.logger``.

    Idempotent — repeated calls replace the handler set rather than
    stacking duplicates. Honoured env vars:

    * ``RESUME_SITE_LOG_FORMAT``: ``json`` or ``human`` (default ``json``).
    * ``RESUME_SITE_LOG_LEVEL`` : logging level name (default ``INFO``).

    Args:
        app: The Flask application. Used only for ``app.logger`` access
            and for binding the handler to a known destination (stderr).
    """
    fmt_name = os.environ.get(_ENV_FORMAT, LOG_FORMAT_JSON).lower()
    if fmt_name not in _VALID_FORMATS:
        fmt_name = LOG_FORMAT_JSON

    level_name = os.environ.get(_ENV_LEVEL, 'INFO').upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.INFO

    formatter = _JsonFormatter() if fmt_name == LOG_FORMAT_JSON else _HumanFormatter()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.DEBUG)  # let the logger level gate, not the handler
    handler.setFormatter(formatter)
    handler.addFilter(_RequestContextFilter())

    # Install on root so Flask / Werkzeug / our modules all flow through
    # the same pipeline. Remove any previously-installed handlers so
    # repeated calls are idempotent (tests exercise this).
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # app.logger is distinct from root until explicitly delegated. Make
    # it propagate to root so we don't need a second handler set.
    app.logger.handlers.clear()
    app.logger.setLevel(level)
    app.logger.propagate = True


def get_logger(name):
    """Return a logger. Thin wrapper so callers have a stable import."""
    return logging.getLogger(name)
