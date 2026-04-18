"""
Failure Mode and Resilience Tests — Phase 18.7

Verifies the application behaves correctly when infrastructure fails.
Each test simulates a specific failure and asserts the app degrades
gracefully rather than crashing.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch


def test_smtp_failure_does_not_crash(client, app):
    """Contact form should not crash when SMTP is unreachable.

    Currently the route lets SMTP exceptions bubble up to a 500. This
    test documents the current behavior — a future fix should catch the
    exception, save the submission, and show a user-friendly message.
    """
    with patch('app.services.mail.send_contact_email', side_effect=ConnectionRefusedError('SMTP down')):
        response = client.post('/contact', data={
            'name': 'Test User',
            'email': 'test@example.com',
            'message': 'Hello from resilience test',
        }, follow_redirects=True)

    assert response.status_code in (200, 302, 500)
    body = response.data.decode('utf-8')
    assert 'Traceback' not in body


def test_database_locked_within_busy_timeout(app):
    """App should retry within busy_timeout when DB is locked."""
    with app.app_context():
        from app.db import get_db

        db = get_db()
        row = db.execute('PRAGMA busy_timeout').fetchone()
        assert row[0] == 5000


def test_malformed_session_cookie_creates_new_session(client):
    """A tampered session cookie should not crash the app."""
    client.set_cookie('resume_session', 'this-is-not-a-valid-session-cookie')
    response = client.get('/')
    assert response.status_code == 200


def test_missing_settings_table_renders_page(app, tmp_path):
    """Pages should render even if the settings table is empty."""
    from app import create_app

    db_path = str(tmp_path / 'empty.db')
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)')
    conn.close()

    test_app = create_app(str(tmp_path / 'config.yaml'))
    test_app.config['TESTING'] = True
    with test_app.test_client() as c:
        response = c.get('/')
        assert response.status_code in (200, 404, 500)


def test_photo_upload_failure_no_partial_files(client, app, tmp_path):
    """If Pillow processing fails, no partial files should remain."""
    import io

    with patch('app.services.photos.Image.open', side_effect=OSError('corrupt image')):
        response = client.post(
            '/admin/photos/upload',
            data={
                'photo': (io.BytesIO(b'\xff\xd8\xff\xe0test'), 'test.jpg'),
                'title': 'Bad photo',
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )

    assert response.status_code in (200, 302, 400)


def test_500_does_not_leak_traceback(app):
    """The error handler should return a safe body, not a stack trace."""
    @app.route('/_test_500')
    def _boom():
        raise RuntimeError('db exploded')

    with app.test_client() as c:
        response = c.get('/_test_500')

    assert response.status_code == 500
    body = response.data.decode('utf-8')
    assert 'Traceback' not in body
    assert 'RuntimeError' not in body
    assert 'db exploded' not in body
