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
