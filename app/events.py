"""
Synchronous Event Bus — Phase 19.1

A deliberately minimal event dispatcher that the application uses to
decouple "something happened" from "things that want to react". The
roadmap's longer-term goal is a webhook / notification system on top of
this bus (Phase 19.2); the bus itself is useful today because it lets
us attach optional side-effects (structured logs, future metrics, the
activity log, webhook delivery) without threading them through every
service call site.

Contract:

* **Synchronous.** Handlers run in registration order, on the calling
  thread. No queues, no async, no threads — if you need those, wrap a
  handler that enqueues.
* **Fail-open.** A handler that raises is logged at WARNING and
  skipped. The caller of :func:`emit` is never affected by a bad
  handler. This is on purpose: the bus is an observability layer, not
  a control-flow mechanism. A malformed webhook URL must not prevent a
  contact-form submission from succeeding.
* **Dependency-free.** No Flask import, no database, no threading
  primitives beyond a single module-level lock. Safe to import from any
  layer (services, routes, CLI, tests).

Event names are exposed as constants on the :class:`Events` namespace so
typos fail at import time rather than at dispatch time. Unknown event
names are still dispatchable — a caller can fire a bespoke event — but
the registry-keyed registration API enforces spelling for the
canonical set.
"""

from __future__ import annotations

import contextlib
import logging
import threading

_log = logging.getLogger('app.events')


class Events:
    """Canonical event name constants.

    Keep values in ``<domain>.<past-tense-verb>`` form so the JSON log
    line — "event": "backup.completed" — reads naturally.
    """

    # User-originated events
    CONTACT_SUBMITTED = 'contact.submitted'
    REVIEW_SUBMITTED = 'review.submitted'
    REVIEW_APPROVED = 'review.approved'

    # Admin / content events
    BLOG_PUBLISHED = 'blog.published'
    BLOG_UPDATED = 'blog.updated'
    SETTINGS_CHANGED = 'settings.changed'
    PHOTO_UPLOADED = 'photo.uploaded'

    # Infrastructure events
    BACKUP_COMPLETED = 'backup.completed'
    API_TOKEN_CREATED = 'api.token_created'  # noqa: S105 — event name, not a credential

    # Security-relevant events — any subscriber is authoritative for
    # operational alerting, not the app itself.
    SECURITY_LOGIN_FAILED = 'security.login_failed'
    SECURITY_RATE_LIMITED = 'security.rate_limited'
    SECURITY_INTERNAL_ERROR = 'security.internal_error'

    ALL = frozenset(
        {
            CONTACT_SUBMITTED,
            REVIEW_SUBMITTED,
            REVIEW_APPROVED,
            BLOG_PUBLISHED,
            BLOG_UPDATED,
            SETTINGS_CHANGED,
            PHOTO_UPLOADED,
            BACKUP_COMPLETED,
            API_TOKEN_CREATED,
            SECURITY_LOGIN_FAILED,
            SECURITY_RATE_LIMITED,
            SECURITY_INTERNAL_ERROR,
        }
    )


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

# {event_name: [callback, ...]}  — insertion-ordered (dispatch order).
_handlers: dict[str, list] = {}
_lock = threading.Lock()


def register(event_name, callback):
    """Register ``callback`` to be invoked when ``event_name`` is emitted.

    Same callback can be registered multiple times — the caller owns
    dedup if they care. Registration is thread-safe.

    Args:
        event_name: One of the :class:`Events` constants. Registering an
            unknown name is allowed but will only fire if some caller
            emits the same bespoke string.
        callback: A callable taking keyword arguments (the payload).
    """
    with _lock:
        _handlers.setdefault(event_name, []).append(callback)


def unregister(event_name, callback):
    """Remove ``callback`` from ``event_name``'s handler list.

    Removes only the first occurrence — handlers registered multiple
    times require multiple unregister calls. No-op if the handler isn't
    registered. Used primarily by tests.
    """
    with _lock:
        handlers = _handlers.get(event_name)
        if not handlers:
            return
        with contextlib.suppress(ValueError):
            handlers.remove(callback)


def clear():
    """Drop every registered handler. Test-only — production never calls this."""
    with _lock:
        _handlers.clear()


def handler_count(event_name):
    """Return the number of handlers currently registered for ``event_name``."""
    with _lock:
        return len(_handlers.get(event_name, ()))


def emit(event_name, **payload):
    """Dispatch ``event_name`` to every registered handler.

    Handlers run synchronously, in registration order, under the lock.
    Exceptions raised by a handler are caught, logged at WARNING, and
    swallowed — the bus never propagates a handler failure back to the
    emitter. This guarantees that a broken webhook cannot break a
    contact-form submission.

    Args:
        event_name: The event identifier (usually an :class:`Events` constant).
        **payload: Keyword arguments forwarded to every handler.

    Returns:
        int: The number of handlers that ran (regardless of whether they
        raised). Returned mostly to make emit-with-no-handlers
        distinguishable in tests.
    """
    # Snapshot under the lock so a handler that mutates the registry
    # (register / unregister inside another handler) doesn't corrupt
    # dispatch. The lock is released before invoking handlers — we
    # don't want to serialise unrelated side-effects.
    with _lock:
        snapshot = list(_handlers.get(event_name, ()))

    ran = 0
    for handler in snapshot:
        ran += 1
        try:
            handler(**payload)
        except Exception as exc:  # noqa: BLE001 — fail-open per module docstring
            _log.warning(
                'event handler raised: event=%s handler=%r exception=%r',
                event_name,
                handler,
                exc,
            )
    return ran
