"""
Error Taxonomy — Phase 18.9

Categorises every error the application can surface into one of five
operational buckets so metrics, logs, and alerting rules can talk about
them uniformly. The taxonomy is orthogonal to the ``DomainError``
hierarchy in :mod:`app.exceptions`: domain exceptions remain the
service-layer contract for recoverable business-logic failures,
while this module's categories are the classification used by
operators.

Categories (see :class:`ErrorCategory`):

* ``ClientError``   — 4xx except auth. Bad input, missing fields,
  invalid tokens. Expected, non-alarming. Rate may spike with a buggy
  client release or a bot, but individual events don't page operators.
* ``AuthError``     — 401 / 403. Failed login, invalid API token, IP
  restriction. Security-relevant — spikes warrant investigation.
* ``ExternalError`` — SMTP failure, DNS resolution, upstream CDN
  timeout. Infrastructure health, may need operator action but usually
  resolves without intervention.
* ``DataError``     — SQLite corruption, migration failure, constraint
  violation at the storage boundary. Critical — never should happen in
  steady state.
* ``InternalError`` — Unhandled exceptions, assertion failures, bugs.
  Any non-zero rate here is a bug and should page.

This module is deliberately stdlib-only. It does not import Flask or
the logging or metrics modules so it can be safely imported from any
layer (services, routes, tests). The integration points that emit the
log record and increment the counter live in :mod:`app` next to the
``_log_request`` hook.
"""

from __future__ import annotations

import socket
import sqlite3


class ErrorCategory:
    """String constants for the operational error categories.

    A class rather than :class:`enum.Enum` so the values are plain
    strings — they flow straight into log fields, metric labels, and
    alerting rules without serialisation.
    """

    CLIENT = 'ClientError'
    AUTH = 'AuthError'
    EXTERNAL = 'ExternalError'
    DATA = 'DataError'
    INTERNAL = 'InternalError'

    ALL = frozenset(
        {
            CLIENT,
            AUTH,
            EXTERNAL,
            DATA,
            INTERNAL,
        }
    )


# ---------------------------------------------------------------------------
# Infrastructure-concern exception classes
#
# The DomainError hierarchy in app/exceptions.py covers recoverable
# business-logic failures. These two cover the "something outside our
# process went wrong" and "something at the storage layer went wrong"
# cases so service-layer code can raise them explicitly when it wants a
# specific category in metrics.
# ---------------------------------------------------------------------------


class ExternalError(Exception):
    """Raised when an external system (SMTP, DNS, upstream HTTP) fails.

    Carries an optional cause chain so handlers can inspect the
    underlying transport error without re-wrapping the message.
    """


class DataError(Exception):
    """Raised when SQLite or the on-disk state is unexpectedly broken."""


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------


def categorize_status(status_code):
    """Return the :class:`ErrorCategory` constant for an HTTP status.

    ``None`` or 2xx/3xx return ``None`` — the caller uses the absence
    of a category to mean "not an error, don't count it".

    Args:
        status_code: An HTTP status code (int) or ``None``.

    Returns:
        str | None: An :class:`ErrorCategory` value or ``None``.
    """
    if status_code is None:
        return None
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        return None
    if status < 400:
        return None
    if status in (401, 403):
        return ErrorCategory.AUTH
    if status < 500:
        return ErrorCategory.CLIENT
    return ErrorCategory.INTERNAL


def categorize_exception(exc, status_code=None):
    """Return the category for an exception, optionally biased by status.

    Precedence:
        1. Explicit subclasses: :class:`ExternalError`, :class:`DataError`.
        2. Stdlib signatures that map cleanly:
           * ``sqlite3.DatabaseError`` / ``OperationalError`` → ``DataError``
           * ``socket.timeout`` / ``ConnectionError`` / ``OSError`` with
             a network-looking errno → ``ExternalError``
        3. :class:`app.exceptions.DomainError` subclasses → ``ClientError``
           (domain errors surface as 4xx to the user).
        4. Fallback: if ``status_code`` is supplied, use
           :func:`categorize_status`; otherwise ``InternalError`` (a bug).

    Args:
        exc: The raised exception instance.
        status_code: Optional response status code the handler plans to
            emit. Used only when the exception itself doesn't match any
            specific rule.

    Returns:
        str: An :class:`ErrorCategory` value.
    """
    if isinstance(exc, ExternalError):
        return ErrorCategory.EXTERNAL
    if isinstance(exc, DataError):
        return ErrorCategory.DATA

    if isinstance(exc, sqlite3.DatabaseError):
        # OperationalError, IntegrityError, etc. all inherit from DatabaseError.
        return ErrorCategory.DATA

    if isinstance(exc, (socket.timeout, ConnectionError, TimeoutError)):
        return ErrorCategory.EXTERNAL

    # DomainError without importing it at module scope (avoid circular
    # imports if app/exceptions.py ever grows a dependency).
    try:
        from app.exceptions import DomainError

        if isinstance(exc, DomainError):
            return ErrorCategory.CLIENT
    except ImportError:  # pragma: no cover — same package
        pass

    status_based = categorize_status(status_code)
    if status_based is not None:
        return status_based

    return ErrorCategory.INTERNAL
