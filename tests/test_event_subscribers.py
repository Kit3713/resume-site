"""
Phase 36.7 regression tests — subsystem side-effects via the event bus.

The roadmap acceptance criterion:

    A route that emits photo.uploaded causes the analytics counter and
    metrics gauge to update *without* the route calling them directly.

These tests emit events directly (no HTTP) and then observe the side-
effects in the activity log table and the metrics registry. That isolates
the bus contract from the route code.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.events import Events, clear, emit
from app.services.event_subscribers import register_all, reset_for_tests


@pytest.fixture
def bus(app):
    """Reset the bus + subscriber registration so each test starts clean."""
    clear()
    reset_for_tests()
    register_all()
    yield
    clear()
    reset_for_tests()


def _activity_rows(app) -> list[sqlite3.Row]:
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            'SELECT action, category, detail FROM admin_activity_log ORDER BY id DESC LIMIT 5'
        ).fetchall()
    finally:
        conn.close()


def _counter_value(name: str, labels: tuple[str, ...] = ()) -> int:
    from app.services.metrics import get_registry

    counter = get_registry()._metrics[name]
    return int(counter._values.get(labels, 0))


# ---------------------------------------------------------------------------
# Photo upload → activity log entry + metric counter
# ---------------------------------------------------------------------------


def test_photo_uploaded_writes_activity_log(app, bus):
    with app.test_request_context():
        emit(
            Events.PHOTO_UPLOADED,
            photo_id=1,
            title='Sunset in Kyoto',
            category='travel',
            source='test',
        )

    rows = _activity_rows(app)
    assert rows, 'expected an activity log row to be written by the subscriber'
    assert rows[0]['action'] == 'Uploaded photo'
    assert rows[0]['category'] == 'photos'
    assert rows[0]['detail'] == 'Sunset in Kyoto'


def test_photo_uploaded_increments_counter(app, bus):
    before = _counter_value('resume_site_photo_uploads_total')
    with app.test_request_context():
        emit(Events.PHOTO_UPLOADED, photo_id=42, title='Probe', source='test')
    after = _counter_value('resume_site_photo_uploads_total')
    assert after == before + 1


# ---------------------------------------------------------------------------
# Blog events → activity log (publish / delete)
# ---------------------------------------------------------------------------


def test_blog_published_writes_activity_log(app, bus):
    with app.test_request_context():
        emit(
            Events.BLOG_PUBLISHED,
            post_id=9,
            slug='hello',
            title='Hello, world!',
            status='published',
            source='test',
        )

    rows = _activity_rows(app)
    assert rows[0]['action'] == 'Published post'
    assert rows[0]['category'] == 'blog'
    assert rows[0]['detail'] == 'Hello, world!'


def test_blog_deleted_writes_activity_log(app, bus):
    with app.test_request_context():
        emit(
            Events.BLOG_UPDATED,
            post_id=9,
            slug='hello',
            title='Hello, world!',
            status='deleted',
            source='test',
        )

    rows = _activity_rows(app)
    assert rows[0]['action'] == 'Deleted post'
    assert rows[0]['category'] == 'blog'


def test_blog_updated_without_delete_is_silent(app, bus):
    """BLOG_UPDATED with status != 'deleted' does not log (preserves v0.3.0 behaviour)."""
    with app.test_request_context():
        emit(
            Events.BLOG_UPDATED,
            post_id=9,
            slug='hello',
            title='Saving a draft',
            status='draft',
            source='test',
        )

    rows = _activity_rows(app)
    # Nothing from this emit should land as a 'Saving a draft' row.
    assert not any(r['detail'] == 'Saving a draft' for r in rows)


# ---------------------------------------------------------------------------
# Contact form → metric counter
# ---------------------------------------------------------------------------


def test_contact_submitted_increments_counter(app, bus):
    before = _counter_value('resume_site_contact_submissions_total', ('false',))
    with app.test_request_context():
        emit(Events.CONTACT_SUBMITTED, submission_id=1, is_spam=False, source='test')
    after = _counter_value('resume_site_contact_submissions_total', ('false',))
    assert after == before + 1


def test_contact_submitted_spam_labelled_separately(app, bus):
    before = _counter_value('resume_site_contact_submissions_total', ('true',))
    with app.test_request_context():
        emit(Events.CONTACT_SUBMITTED, submission_id=2, is_spam=True, source='test')
    after = _counter_value('resume_site_contact_submissions_total', ('true',))
    assert after == before + 1


# ---------------------------------------------------------------------------
# Roadmap acceptance criterion: emit → side-effects without the route
# ---------------------------------------------------------------------------


def test_emit_alone_drives_all_photo_side_effects(app, bus):
    """emit(PHOTO_UPLOADED) is sufficient — no route call required."""
    counter_before = _counter_value('resume_site_photo_uploads_total')

    with app.test_request_context():
        emit(
            Events.PHOTO_UPLOADED,
            photo_id=100,
            title='Integration Probe',
            category='',
            source='test',
        )

    # Both side-effects happened — the log row AND the counter bump.
    assert _counter_value('resume_site_photo_uploads_total') == counter_before + 1
    rows = _activity_rows(app)
    assert rows[0]['action'] == 'Uploaded photo'
    assert rows[0]['detail'] == 'Integration Probe'
