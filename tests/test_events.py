"""
Event Bus Tests — Phase 19.1

Unit tests for app.events (the bus itself) plus integration tests that
confirm the two wired-up emissions actually fire:

* ``security.internal_error`` from the errorhandler(Exception) after an
  unhandled exception.
* ``backup.completed`` from app.services.backups.create_backup after a
  successful backup.

The bus is module-level state, so every test clears the registry in
setup + teardown. This keeps handlers registered by one test from
leaking into another.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

import app.events as events_mod
from app.events import (
    Events,
    clear,
    emit,
    handler_count,
    register,
    unregister,
)

# ---------------------------------------------------------------------------
# Auto-applied cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_bus_between_tests():
    """Guarantee each test sees an empty registry on entry AND exit."""
    clear()
    yield
    clear()


# ---------------------------------------------------------------------------
# Events constants
# ---------------------------------------------------------------------------


def test_event_names_are_stable_strings():
    # Stable values matter — they become structured-log fields and
    # (eventually) webhook dispatch keys.
    assert Events.CONTACT_SUBMITTED == 'contact.submitted'
    assert Events.REVIEW_SUBMITTED == 'review.submitted'
    assert Events.REVIEW_APPROVED == 'review.approved'
    assert Events.BLOG_PUBLISHED == 'blog.published'
    assert Events.BLOG_UPDATED == 'blog.updated'
    assert Events.SETTINGS_CHANGED == 'settings.changed'
    assert Events.PHOTO_UPLOADED == 'photo.uploaded'
    assert Events.BACKUP_COMPLETED == 'backup.completed'
    assert Events.API_TOKEN_CREATED == 'api.token_created'
    assert Events.SECURITY_LOGIN_FAILED == 'security.login_failed'
    assert Events.SECURITY_RATE_LIMITED == 'security.rate_limited'
    assert Events.SECURITY_INTERNAL_ERROR == 'security.internal_error'


def test_events_all_covers_every_constant():
    explicit = {
        Events.CONTACT_SUBMITTED,
        Events.REVIEW_SUBMITTED,
        Events.REVIEW_APPROVED,
        Events.BLOG_PUBLISHED,
        Events.BLOG_UPDATED,
        Events.SETTINGS_CHANGED,
        Events.PHOTO_UPLOADED,
        Events.BACKUP_COMPLETED,
        Events.API_TOKEN_CREATED,
        Events.SECURITY_LOGIN_FAILED,
        Events.SECURITY_RATE_LIMITED,
        Events.SECURITY_INTERNAL_ERROR,
    }
    assert explicit == Events.ALL


# ---------------------------------------------------------------------------
# register / emit
# ---------------------------------------------------------------------------


def test_emit_with_no_handlers_returns_zero():
    assert emit(Events.CONTACT_SUBMITTED, name='Alice') == 0


def test_emit_dispatches_to_single_handler():
    captured = []
    register(Events.CONTACT_SUBMITTED, lambda **payload: captured.append(payload))

    ran = emit(Events.CONTACT_SUBMITTED, name='Alice', email='a@b.co')

    assert ran == 1
    assert captured == [{'name': 'Alice', 'email': 'a@b.co'}]


def test_emit_dispatches_in_registration_order():
    order = []
    register(Events.BLOG_PUBLISHED, lambda **_: order.append('first'))
    register(Events.BLOG_PUBLISHED, lambda **_: order.append('second'))
    register(Events.BLOG_PUBLISHED, lambda **_: order.append('third'))

    emit(Events.BLOG_PUBLISHED, slug='x')

    assert order == ['first', 'second', 'third']


def test_emit_same_callback_twice_fires_twice():
    captured = []

    def handler(**payload):
        captured.append(payload)

    register(Events.REVIEW_SUBMITTED, handler)
    register(Events.REVIEW_SUBMITTED, handler)

    assert emit(Events.REVIEW_SUBMITTED, rating=5) == 2
    assert len(captured) == 2


def test_emit_does_not_cross_event_boundaries():
    captured = []
    register(Events.CONTACT_SUBMITTED, lambda **_: captured.append('contact'))
    register(Events.REVIEW_SUBMITTED, lambda **_: captured.append('review'))

    emit(Events.CONTACT_SUBMITTED)

    assert captured == ['contact']


# ---------------------------------------------------------------------------
# Fail-open semantics
# ---------------------------------------------------------------------------


def test_handler_exception_does_not_propagate(caplog):
    order = []

    def broken(**_):
        raise RuntimeError('deliberate test failure')

    def ok(**_):
        order.append('ok')

    register(Events.PHOTO_UPLOADED, broken)
    register(Events.PHOTO_UPLOADED, ok)

    with caplog.at_level(logging.WARNING, logger='app.events'):
        # emit must NOT raise
        ran = emit(Events.PHOTO_UPLOADED, filename='x.jpg')

    # Both handlers ran, even though the first raised.
    assert ran == 2
    assert order == ['ok']
    # And the failure was logged.
    assert any('event handler raised' in r.getMessage() for r in caplog.records)


def test_multiple_handlers_all_raising_still_complete(caplog):
    def broken_one(**_):
        raise ValueError('one')

    def broken_two(**_):
        raise ValueError('two')

    register(Events.SETTINGS_CHANGED, broken_one)
    register(Events.SETTINGS_CHANGED, broken_two)

    with caplog.at_level(logging.WARNING, logger='app.events'):
        ran = emit(Events.SETTINGS_CHANGED)

    assert ran == 2  # both were attempted
    assert len([r for r in caplog.records if 'event handler raised' in r.getMessage()]) == 2


# ---------------------------------------------------------------------------
# Registry inspection / unregister / clear
# ---------------------------------------------------------------------------


def test_handler_count_reflects_registrations():
    assert handler_count(Events.API_TOKEN_CREATED) == 0
    register(Events.API_TOKEN_CREATED, lambda **_: None)
    register(Events.API_TOKEN_CREATED, lambda **_: None)
    assert handler_count(Events.API_TOKEN_CREATED) == 2


def test_unregister_removes_first_matching_handler():
    calls = []

    def a(**_):
        calls.append('a')

    def b(**_):
        calls.append('b')

    register(Events.BLOG_UPDATED, a)
    register(Events.BLOG_UPDATED, b)

    unregister(Events.BLOG_UPDATED, a)
    emit(Events.BLOG_UPDATED)

    assert calls == ['b']


def test_unregister_unknown_handler_is_noop():
    # Must not raise even if the handler was never registered.
    unregister(Events.BACKUP_COMPLETED, lambda **_: None)
    unregister('completely.bogus', lambda **_: None)


def test_clear_drops_everything():
    register(Events.CONTACT_SUBMITTED, lambda **_: None)
    register(Events.BLOG_PUBLISHED, lambda **_: None)
    clear()
    assert handler_count(Events.CONTACT_SUBMITTED) == 0
    assert handler_count(Events.BLOG_PUBLISHED) == 0


# ---------------------------------------------------------------------------
# Re-entrancy safety
# ---------------------------------------------------------------------------


def test_handler_that_registers_another_handler_doesnt_corrupt_dispatch():
    """A handler calling ``register`` during dispatch must not affect the
    current fan-out (because we snapshot the handler list up front)."""
    order = []

    def first(**_):
        order.append('first')
        # Attempt to add a new handler mid-dispatch; it must NOT fire
        # during THIS emit (would otherwise be appended to the live list).
        register(Events.CONTACT_SUBMITTED, lambda **__: order.append('injected'))

    def second(**_):
        order.append('second')

    register(Events.CONTACT_SUBMITTED, first)
    register(Events.CONTACT_SUBMITTED, second)

    emit(Events.CONTACT_SUBMITTED)

    assert order == ['first', 'second']
    # The injected handler DOES fire on the next emit, though.
    emit(Events.CONTACT_SUBMITTED)
    assert order == ['first', 'second', 'first', 'second', 'injected']


# ---------------------------------------------------------------------------
# Unknown event names still dispatch (callers may emit bespoke events)
# ---------------------------------------------------------------------------


def test_bespoke_event_name_roundtrips():
    captured = []
    register('my.custom.event', lambda **p: captured.append(p))

    emit('my.custom.event', kind='test')

    assert captured == [{'kind': 'test'}]


# ---------------------------------------------------------------------------
# Integration: errorhandler(Exception) fires security.internal_error
# ---------------------------------------------------------------------------


def test_errorhandler_emits_security_internal_error(app):
    captured = []

    def recorder(**payload):
        captured.append(payload)

    register(Events.SECURITY_INTERNAL_ERROR, recorder)

    @app.route('/__events_boom')
    def _boom():
        raise RuntimeError('deliberate')

    client = app.test_client()
    response = client.get('/__events_boom')

    assert response.status_code == 500
    assert len(captured) == 1
    payload = captured[0]
    assert payload['method'] == 'GET'
    assert payload['path'] == '/__events_boom'
    assert payload['exception_type'] == 'RuntimeError'
    assert payload['category'] == 'InternalError'
    # Request ID correlates with the response header
    assert payload['request_id'] == response.headers.get('X-Request-ID')


def test_errorhandler_does_not_emit_for_httpexception(app):
    captured = []
    register(Events.SECURITY_INTERNAL_ERROR, lambda **p: captured.append(p))

    client = app.test_client()
    client.get('/this-does-not-exist-xyz')  # 404 via Flask default

    # 404 is not a bug — must not fire the internal-error event.
    assert captured == []


def test_errorhandler_payload_carries_no_traceback(app):
    captured = []
    register(Events.SECURITY_INTERNAL_ERROR, lambda **p: captured.append(p))

    @app.route('/__events_leaky')
    def _boom():
        raise RuntimeError('secret credential leaked in message')

    app.test_client().get('/__events_leaky')

    assert captured
    for value in captured[0].values():
        # Neither the exception message nor a traceback string may leak
        # into the event payload — future webhook subscribers are
        # third-party destinations.
        assert 'secret credential' not in str(value)
        assert 'Traceback' not in str(value)


# ---------------------------------------------------------------------------
# Integration: create_backup fires backup.completed
# ---------------------------------------------------------------------------


def test_create_backup_emits_backup_completed(tmp_path):
    from app.services.backups import create_backup

    db_path = str(tmp_path / 'site.db')
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
    conn.commit()
    conn.close()

    output_dir = str(tmp_path / 'backups')
    captured = []
    register(Events.BACKUP_COMPLETED, lambda **payload: captured.append(payload))

    archive = create_backup(
        db_path=db_path,
        photos_dir=None,
        config_path=None,
        output_dir=output_dir,
        db_only=True,
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload['archive_path'] == archive
    assert payload['db_only'] is True
    assert payload['size_bytes'] > 0


def test_create_backup_event_failure_does_not_break_backup(tmp_path, monkeypatch):
    """A misbehaving subscriber must not prevent a successful backup."""
    from app.services.backups import create_backup

    db_path = str(tmp_path / 'site.db')
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
    conn.commit()
    conn.close()

    def broken(**_):
        raise RuntimeError('webhook simulation failure')

    register(Events.BACKUP_COMPLETED, broken)

    # Still returns the archive path; does NOT raise.
    archive = create_backup(
        db_path=db_path,
        photos_dir=None,
        config_path=None,
        output_dir=str(tmp_path / 'backups'),
        db_only=True,
    )
    assert archive
    import os

    assert os.path.isfile(archive)


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_has_no_hidden_flask_import():
    # The bus must remain importable without Flask (CLI, migrations, tests).
    # Any accidental Flask import would surface here as an attribute on the
    # module namespace.
    assert 'Flask' not in dir(events_mod)
    assert 'request' not in dir(events_mod)


# ===========================================================================
# Phase 19.1 completion — HTML/admin route emissions
# ===========================================================================
#
# These tests cover the eight remaining event types (contact.submitted,
# review.submitted, review.approved, blog.published, blog.updated,
# photo.uploaded, settings.changed, security.rate_limited) emitted from
# the browser-facing routes. The API-side emissions for the same event
# types are covered by tests/test_api.py — these confirm subscribers
# see the same shape regardless of source.


# ---------------------------------------------------------------------------
# contact.submitted (HTML form)
# ---------------------------------------------------------------------------


def test_html_contact_form_emits_contact_submitted(client, smtp_mock):
    captured = []
    register(Events.CONTACT_SUBMITTED, lambda **p: captured.append(p))

    response = client.post(
        '/contact',
        data={
            'name': 'Alice',
            'email': 'alice@example.com',
            'message': 'Hello there.',
        },
    )
    assert response.status_code in (200, 302)
    assert len(captured) == 1
    payload = captured[0]
    assert payload['source'] == 'public_form'
    assert payload['is_spam'] is False
    assert isinstance(payload['submission_id'], int)
    # Real submissions should have triggered the SMTP relay.
    assert smtp_mock and smtp_mock[0][1] == 'alice@example.com'


def test_html_contact_form_honeypot_emits_with_is_spam_true(client, smtp_mock):
    captured = []
    register(Events.CONTACT_SUBMITTED, lambda **p: captured.append(p))

    response = client.post(
        '/contact',
        data={
            'name': 'Bot',
            'email': 'bot@example.com',
            'message': 'spam content',
            'website': 'https://spam.example.com',  # honeypot triggered
        },
    )
    assert response.status_code in (200, 302)
    assert len(captured) == 1
    assert captured[0]['is_spam'] is True
    # Honeypot path must NOT relay via SMTP.
    assert smtp_mock == []


# ---------------------------------------------------------------------------
# review.submitted (HTML token URL)
# ---------------------------------------------------------------------------


def test_html_review_form_emits_review_submitted(client, populated_db):
    captured = []
    register(Events.REVIEW_SUBMITTED, lambda **p: captured.append(p))

    # populated_db seeds review_tokens row with token='test-token-abc123' (type='recommendation').
    response = client.post(
        '/review/test-token-abc123',
        data={
            'reviewer_name': 'Bob',
            'reviewer_title': 'Engineer',
            'relationship': 'Coworker',
            'message': 'Great to work with.',
            'rating': '5',
        },
    )
    assert response.status_code in (200, 302)
    assert len(captured) == 1
    payload = captured[0]
    assert payload['source'] == 'public_token'
    assert payload['review_type'] == 'recommendation'
    assert payload['has_rating'] is True
    assert isinstance(payload['review_id'], int)
    assert isinstance(payload['token_id'], int)


# ---------------------------------------------------------------------------
# review.approved (admin UI)
# ---------------------------------------------------------------------------


def test_admin_review_approve_emits_review_approved(auth_client, populated_db):
    captured = []
    register(Events.REVIEW_APPROVED, lambda **p: captured.append(p))

    # populated_db seeds an already-approved review at id=1; the approve
    # action is idempotent, so re-approving is fine for this test.
    response = auth_client.post(
        '/admin/reviews/1/update',
        data={'action': 'approve', 'display_tier': 'featured'},
    )
    assert response.status_code in (200, 302)
    assert len(captured) == 1
    payload = captured[0]
    assert payload['review_id'] == 1
    assert payload['display_tier'] == 'featured'
    assert payload['source'] == 'admin_ui'


def test_admin_review_reject_does_not_emit_review_approved(auth_client, populated_db):
    """reject / update_tier are admin housekeeping — webhook subscribers
    don't typically care, so only approve fires the event."""
    captured = []
    register(Events.REVIEW_APPROVED, lambda **p: captured.append(p))

    auth_client.post('/admin/reviews/1/update', data={'action': 'reject'})
    assert captured == []


# ---------------------------------------------------------------------------
# blog.published / blog.updated (admin UI)
# ---------------------------------------------------------------------------


def _enable_blog_html(app, enabled=True):
    """Helper — flip blog_enabled and bust the settings cache."""
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('blog_enabled', ?)",
        ('true' if enabled else 'false',),
    )
    conn.commit()
    conn.close()
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()


def test_admin_blog_new_publish_emits_blog_published(auth_client, app):
    _enable_blog_html(app)
    captured_pub = []
    captured_upd = []
    register(Events.BLOG_PUBLISHED, lambda **p: captured_pub.append(p))
    register(Events.BLOG_UPDATED, lambda **p: captured_upd.append(p))

    response = auth_client.post(
        '/admin/blog/new',
        data={
            'title': 'Hello World',
            'content': '<p>Body.</p>',
            'action': 'publish',
        },
    )
    assert response.status_code in (200, 302)
    assert len(captured_pub) == 1
    assert captured_upd == []
    payload = captured_pub[0]
    assert payload['title'] == 'Hello World'
    assert payload['slug']  # server-generated
    assert payload['source'] == 'admin_ui'
    assert payload['status'] == 'published'


def test_admin_blog_new_save_emits_blog_updated(auth_client, app):
    _enable_blog_html(app)
    captured_pub = []
    captured_upd = []
    register(Events.BLOG_PUBLISHED, lambda **p: captured_pub.append(p))
    register(Events.BLOG_UPDATED, lambda **p: captured_upd.append(p))

    auth_client.post(
        '/admin/blog/new',
        data={'title': 'Draft Post', 'content': '<p>WIP.</p>', 'action': 'save'},
    )
    assert captured_pub == []
    assert len(captured_upd) == 1
    assert captured_upd[0]['status'] == 'draft'


def test_admin_blog_delete_emits_blog_updated_with_deleted_status(auth_client, app):
    _enable_blog_html(app)
    # Create a post first to delete.
    auth_client.post(
        '/admin/blog/new',
        data={'title': 'Goner', 'content': '<p>x</p>', 'action': 'save'},
    )
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(app.config['DATABASE_PATH'])
    post_id = conn.execute('SELECT id FROM blog_posts WHERE title = ?', ('Goner',)).fetchone()[0]
    conn.close()

    captured = []
    register(Events.BLOG_UPDATED, lambda **p: captured.append(p))

    auth_client.post(f'/admin/blog/{post_id}/delete')
    assert len(captured) == 1
    assert captured[0]['status'] == 'deleted'
    assert captured[0]['title'] == 'Goner'
    assert captured[0]['source'] == 'admin_ui'


# ---------------------------------------------------------------------------
# photo.uploaded (admin UI)
# ---------------------------------------------------------------------------


def test_admin_photo_upload_emits_photo_uploaded(auth_client):
    from io import BytesIO

    from PIL import Image

    captured = []
    register(Events.PHOTO_UPLOADED, lambda **p: captured.append(p))

    buf = BytesIO()
    Image.new('RGB', (50, 50), color=(0, 200, 0)).save(buf, 'PNG')
    buf.seek(0)

    response = auth_client.post(
        '/admin/photos/upload',
        data={
            'photo': (buf, 'green.png'),
            'title': 'Green Square',
            'category': 'test',
            'display_tier': 'grid',
        },
        content_type='multipart/form-data',
    )
    assert response.status_code in (200, 302)
    assert len(captured) == 1
    payload = captured[0]
    assert payload['title'] == 'Green Square'
    assert payload['category'] == 'test'
    assert payload['display_tier'] == 'grid'
    assert payload['source'] == 'admin_ui'
    assert isinstance(payload['photo_id'], int)
    assert payload['file_size'] > 0


# ---------------------------------------------------------------------------
# settings.changed (admin UI)
# ---------------------------------------------------------------------------


def test_admin_settings_save_emits_settings_changed(auth_client):
    captured = []
    register(Events.SETTINGS_CHANGED, lambda **p: captured.append(p))

    response = auth_client.post(
        '/admin/settings',
        data={
            'site_title': 'My Updated Site',
            'site_tagline': 'A new tagline',
        },
    )
    assert response.status_code in (200, 302)
    assert len(captured) == 1
    payload = captured[0]
    assert payload['source'] == 'admin_ui'
    assert 'site_title' in payload['keys']
    assert 'site_tagline' in payload['keys']
    # csrf_token must be excluded so subscribers don't see noise.
    assert 'csrf_token' not in payload['keys']


# ---------------------------------------------------------------------------
# security.rate_limited (errorhandler 429)
# ---------------------------------------------------------------------------


def test_429_response_emits_security_rate_limited(app):
    from flask import abort

    captured = []
    register(Events.SECURITY_RATE_LIMITED, lambda **p: captured.append(p))

    @app.route('/__events_429')
    def _force_429():
        abort(429, description='3 per 1 minute')

    response = app.test_client().get('/__events_429')
    assert response.status_code == 429
    assert len(captured) == 1
    payload = captured[0]
    assert payload['method'] == 'GET'
    assert payload['endpoint'] == '/__events_429'
    assert payload['limit'] == '3 per 1 minute'


def test_429_handler_is_observability_only_response_unchanged(app):
    """The handler must NOT change the body / status — Flask's default
    response (and any Retry-After header set by Flask-Limiter) has to
    still reach the client."""
    from flask import abort

    @app.route('/__events_429_body')
    def _force_429():
        abort(429)

    response = app.test_client().get('/__events_429_body')
    assert response.status_code == 429
    # Default werkzeug 429 body mentions "Too Many Requests".
    assert b'Too Many Requests' in response.data
