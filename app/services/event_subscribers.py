"""
Event-bus subscribers — Phase 36.7 (v0.3.0 Phase 19.1 carry-over).

Wires the activity log and domain metrics subsystems as subscribers on
the synchronous event bus instead of direct call sites inside every
route handler. The routes now emit events; these subscribers do the
logging and counter bumps.

Why bother: the bus is the single extension seam webhooks (Phase 19.2)
and external integrations already subscribe to. Making the core
subsystems use the same channel means any new route that emits a
canonical event gets the whole ecosystem — logs, metrics, webhooks —
without having to know which side-effects matter.

Fail-open: ``app.events.emit`` swallows handler exceptions, so a
broken subscriber cannot break the originating write path. The
subscribers themselves guard the Flask context lookup so synthetic
``emit()`` calls from tests work too.

Scope: the v0.3.1 migration deletes three specific direct-call paths
(photo upload log, blog publish log, blog delete log). Further routes
still call ``log_action`` directly — migrating the rest is a cleanup
candidate but not a v0.3.1 deliverable.
"""

from __future__ import annotations

import contextlib
import logging
import threading

from app.events import Events, register

_log = logging.getLogger(__name__)
_register_lock = threading.Lock()
_registered = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db_or_none():
    """Return the request-bound sqlite3 connection, or ``None`` outside a request."""
    with contextlib.suppress(Exception):
        from app.db import get_db

        return get_db()
    return None


# ---------------------------------------------------------------------------
# Activity-log subscribers
# ---------------------------------------------------------------------------


def _log_photo_uploaded(title='', category='', **_):
    db = _get_db_or_none()
    if db is None:
        return
    from app.services.activity_log import log_action

    log_action(db, 'Uploaded photo', 'photos', title or category or '')


def _log_blog_published(title='', **_):
    db = _get_db_or_none()
    if db is None:
        return
    from app.services.activity_log import log_action

    log_action(db, 'Published post', 'blog', title)


def _log_blog_updated(title='', status='', **_):
    """Handle BLOG_UPDATED. Branches on ``status`` to distinguish delete from save."""
    db = _get_db_or_none()
    if db is None:
        return
    from app.services.activity_log import log_action

    if status == 'deleted':
        log_action(db, 'Deleted post', 'blog', title)
    # Non-deleted BLOG_UPDATED events are save-as-draft / archive / unpublish.
    # Those were not logged directly in v0.3.0 — preserving that behaviour
    # means this subscriber is a no-op for non-delete status values.


# ---------------------------------------------------------------------------
# Metrics subscribers
# ---------------------------------------------------------------------------


def _inc_photo_uploads(**_):
    with contextlib.suppress(Exception):
        from app.services.metrics import photo_uploads_total

        photo_uploads_total.inc()


def _inc_contact_submissions(is_spam=False, **_):
    with contextlib.suppress(Exception):
        from app.services.metrics import contact_submissions_total

        contact_submissions_total.inc(label_values=(str(bool(is_spam)).lower(),))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all() -> None:
    """Attach every subscriber. Idempotent — safe to call multiple times.

    Tests that build fresh apps in one process rely on idempotency; the
    guard is a module-level flag rather than a per-app one because the
    event bus itself is process-global.
    """
    global _registered
    with _register_lock:
        if _registered:
            return
        _registered = True

    register(Events.PHOTO_UPLOADED, _log_photo_uploaded)
    register(Events.PHOTO_UPLOADED, _inc_photo_uploads)

    register(Events.BLOG_PUBLISHED, _log_blog_published)
    register(Events.BLOG_UPDATED, _log_blog_updated)

    register(Events.CONTACT_SUBMITTED, _inc_contact_submissions)


def reset_for_tests() -> None:
    """Drop the 'already registered' flag. Called by the test event-bus fixture."""
    global _registered
    with _register_lock:
        _registered = False
