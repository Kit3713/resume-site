"""
Failure Mode and Resilience Tests — Phase 18.7

Verifies the application behaves correctly when infrastructure fails. Each
test simulates a specific failure (SMTP down, disk full, DB corrupt, a
locked writer, a bad session cookie) and asserts the app degrades
gracefully: no 500 tracebacks leaked to the user, no partial files left
on disk, no silent data loss.

These are deliberately integration-level — they exercise the real Flask
app + real SQLite against mocked *infrastructure boundaries* (the OS,
the network, SMTP). The point is to catch a regression where a future
"clean up the error handling" refactor drops one of the catch-alls and
a real production failure starts crashing user-facing routes.
"""

from __future__ import annotations

import errno
import io
import os
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# SMTP failure
# ---------------------------------------------------------------------------


def test_smtp_failure_still_saves_submission_and_redirects(client, app):
    """SMTP unreachable must not lose the submission or 500 the user.

    When ``send_contact_email`` returns ``False`` (matching the real
    behaviour — ``mail.py`` catches every exception internally), the
    contact route:
      * must still insert the row into ``contact_submissions``
      * must redirect to the success page (302)
      * must not leak a traceback into the response body
    """
    with patch('app.services.mail.send_contact_email', return_value=False):
        response = client.post(
            '/contact',
            data={
                'name': 'Test User',
                'email': 'test@example.com',
                'message': 'SMTP is down but data should be kept',
            },
            follow_redirects=False,
        )

    assert response.status_code == 302, (
        f'expected 302 after SMTP failure, got {response.status_code}'
    )
    assert 'Traceback' not in response.data.decode('utf-8', errors='replace')

    # The submission must have landed in the DB regardless of SMTP status.
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT name, email, message, is_spam FROM contact_submissions '
        "WHERE email = 'test@example.com'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]['name'] == 'Test User'
    assert rows[0]['is_spam'] == 0


def test_smtp_exception_is_swallowed_by_mail_service(app):
    """The real mail service catches every exception and returns False.

    Regression guard: if a refactor removes the ``except Exception`` in
    ``app.services.mail.send_contact_email``, the contact route would
    start surfacing 500s on SMTP outages. Lock it in here.
    """
    from app.services.mail import send_contact_email

    with patch('smtplib.SMTP', side_effect=ConnectionRefusedError('relay down')), app.app_context():
        # Seed the config with credentials so the early bail-out on
        # "not configured" doesn't short-circuit the test.
        app.config['SITE_CONFIG'] = {
            'smtp': {
                'host': 'smtp.example.com',
                'port': 587,
                'user': 'u',
                'password': 'p',
                'recipient': 'r@example.com',
            }
        }
        # Should NOT raise — the service catches the ConnectionRefusedError.
        ok = send_contact_email('Alice', 'alice@example.com', 'hi')
    assert ok is False


# ---------------------------------------------------------------------------
# Database locked (busy_timeout)
# ---------------------------------------------------------------------------


def test_busy_timeout_pragma_is_5_seconds(app):
    """Every request connection applies ``PRAGMA busy_timeout = 5000``.

    The busy_timeout is what gives concurrent writers room to breathe;
    without it a locked writer produces immediate ``database is locked``
    errors. The pragma lives in ``app.db._PER_CONNECTION_PRAGMAS`` and
    is covered more deeply by ``test_db_pragmas.py`` — we re-assert
    here so a future refactor that forgets to wire the pragma can't
    escape without breaking a resilience test too.
    """
    with app.app_context():
        from app.db import get_db

        db = get_db()
        assert db.execute('PRAGMA busy_timeout').fetchone()[0] == 5000


def test_db_write_succeeds_when_prior_writer_finishes_within_timeout(app):
    """A writer that holds the lock briefly must not fail the next writer.

    Simulates contention: a background thread opens a connection,
    starts an IMMEDIATE transaction (grabs the write lock), sleeps a
    second, then commits. The main thread tries to write during that
    window — it must wait (up to busy_timeout = 5s) and then succeed.
    """
    db_path = app.config['DATABASE_PATH']

    # Seed a row we can update from the main thread.
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('resilience_probe', 'start')")
    conn.commit()
    conn.close()

    ready = threading.Event()
    released = threading.Event()

    def _hold_lock():
        bg = sqlite3.connect(db_path, timeout=10)
        bg.execute('BEGIN IMMEDIATE')
        bg.execute("UPDATE settings SET value = 'bg' WHERE key = 'resilience_probe'")
        ready.set()
        time.sleep(1.0)
        bg.commit()
        bg.close()
        released.set()

    t = threading.Thread(target=_hold_lock)
    t.start()
    ready.wait(timeout=5)

    # Main-thread writer uses the same 5s busy_timeout the app applies.
    main = sqlite3.connect(db_path, timeout=5)
    try:
        start = time.time()
        main.execute("UPDATE settings SET value = 'main' WHERE key = 'resilience_probe'")
        main.commit()
        elapsed = time.time() - start
    finally:
        main.close()
    t.join(timeout=5)

    # The write should have waited on the bg thread and then succeeded.
    # (If busy_timeout were 0 we'd get `database is locked` immediately.)
    assert elapsed < 5.0, f'writer waited {elapsed:.2f}s — should be < timeout'
    assert released.is_set()


# ---------------------------------------------------------------------------
# Disk full on photo upload
# ---------------------------------------------------------------------------


def test_disk_full_on_upload_leaves_no_partial_files(auth_client, app, tmp_path):
    """``ENOSPC`` during ``os.replace`` must not leave quarantine files.

    The upload pipeline (Phase 13.7) stores to a ``tempfile.mkstemp``
    quarantine file inside the photo directory, runs validation +
    Pillow, and then ``os.replace``s it into the UUID-named final path.
    If the disk fills up at the replace step, the ``finally`` block
    must clean up the quarantine file — no orphans hanging around.

    This test ships a real PNG (so magic-byte validation passes), mocks
    ``os.replace`` inside ``app.services.photos`` to raise ``ENOSPC``,
    and asserts:
      * the photo directory is clean after the failure
      * no row landed in the ``photos`` table
    """
    pytest.importorskip('PIL')
    from PIL import Image

    photo_dir = app.config['PHOTO_STORAGE']
    os.makedirs(photo_dir, exist_ok=True)

    # Build a minimal valid PNG in memory so magic-byte validation passes.
    buf = io.BytesIO()
    Image.new('RGB', (10, 10), color='red').save(buf, 'PNG')
    buf.seek(0)
    png_bytes = buf.read()

    baseline_files = set(os.listdir(photo_dir))

    disk_full = OSError(errno.ENOSPC, 'No space left on device')
    # Patch os.replace specifically inside photos.py so we don't break
    # the tempfile.mkstemp cleanup path or unrelated code.
    with patch('app.services.photos.os.replace', side_effect=disk_full):
        response = auth_client.post(
            '/admin/photos/upload',
            data={
                'photo': (io.BytesIO(png_bytes), 'test.png'),
                'title': 'Disk-full probe',
                'description': '',
                'category': '',
                'display_tier': 'grid',
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )

    # A 500 is acceptable here — what we insist on is that nothing is
    # leaked on disk or in the DB. The admin-UI upload route re-raises
    # the OSError; only the lower-level ``process_upload`` path has
    # the quarantine cleanup. The contract is: no orphan file, no
    # orphan row.
    assert response.status_code in (200, 302, 500)

    # Quarantine cleanup: the photo dir must not have gained any files.
    after_files = set(os.listdir(photo_dir))
    new_files = after_files - baseline_files
    assert not new_files, f'quarantine left orphan files: {new_files}'

    # No DB row should have been inserted (the insert happens after
    # os.replace succeeds, which it didn't).
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM photos WHERE title = 'Disk-full probe'").fetchone()[
        0
    ]
    conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# Disk full on DB write
# ---------------------------------------------------------------------------


def test_disk_full_on_db_write_does_not_leak_traceback(client):
    """A ``disk I/O error`` from SQLite must not surface a traceback.

    Patches ``app.routes.contact.save_contact_submission`` (the import
    that the contact route resolved at module load) to raise the
    canonical "disk is full" ``OperationalError``. The error handler
    wraps ``Exception`` and returns a minimal safe body with just the
    request id — no sqlite3 string, no traceback.
    """
    disk_full = sqlite3.OperationalError('database or disk is full')

    with patch('app.routes.contact.save_contact_submission', side_effect=disk_full):
        response = client.post(
            '/contact',
            data={
                'name': 'Disk',
                'email': 'disk@example.com',
                'message': 'disk full',
            },
            follow_redirects=False,
        )

    # We don't care whether the route returns 302 or 500 — we care that
    # no traceback / operational error leaks into the HTML response
    # (the 500 handler produces a minimal safe body; a 302 is fine too
    # if the route happens to swallow the error).
    body = response.data.decode('utf-8', errors='replace')
    assert 'Traceback' not in body
    assert 'sqlite3' not in body.lower()
    assert 'disk is full' not in body.lower()


# ---------------------------------------------------------------------------
# Corrupted / truncated upload
# ---------------------------------------------------------------------------


def test_truncated_image_is_rejected_cleanly(auth_client, app):
    """A file with valid PNG magic bytes but truncated content must fail.

    ``_validate_magic_bytes`` checks the first 12 bytes only — a
    truncated PNG passes that check. Pillow then raises ``OSError``
    (or ``Image.DecompressionBombError``) when it tries to parse the
    rest. The upload route treats the photo as "not an image" and
    rejects it. No quarantine residue should remain.
    """
    photo_dir = app.config['PHOTO_STORAGE']
    os.makedirs(photo_dir, exist_ok=True)
    baseline = set(os.listdir(photo_dir))

    # Valid PNG magic followed by junk — not a parseable image.
    corrupt = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\x00garbage-not-a-real-png'

    response = auth_client.post(
        '/admin/photos/upload',
        data={
            'photo': (io.BytesIO(corrupt), 'truncated.png'),
            'title': 'Truncated probe',
            'description': '',
            'category': '',
            'display_tier': 'grid',
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    # Either way (200 with flash or 302 back), nothing should be on disk.
    assert response.status_code in (200, 302, 400)
    assert 'Traceback' not in response.data.decode('utf-8', errors='replace')

    # Quarantine file cleaned up.
    leaked = set(os.listdir(photo_dir)) - baseline
    assert not leaked, f'quarantine left files after corrupt upload: {leaked}'


# ---------------------------------------------------------------------------
# Template rendering failure
# ---------------------------------------------------------------------------


def test_template_rendering_failure_does_not_leak_traceback(app):
    """Jinja2 errors must pass through the 500 handler, not escape.

    Registers a test-only route that deliberately triggers a template
    error (``{{ undefined_var.attr }}`` style), asserts the server
    responds with a sanitised 500 body.
    """
    from flask import render_template_string

    @app.route('/_test_template_error')
    def _boom_template():
        # StrictUndefined isn't in play, but attribute access on the
        # literal ``undefined`` raises in Jinja's default behaviour.
        return render_template_string('{{ crash.attribute.missing }}')

    with app.test_client() as c:
        response = c.get('/_test_template_error')

    assert response.status_code == 500
    body = response.data.decode('utf-8', errors='replace')
    assert 'Traceback' not in body
    assert 'UndefinedError' not in body
    assert 'jinja2' not in body.lower()


# ---------------------------------------------------------------------------
# Malformed session cookie
# ---------------------------------------------------------------------------


def test_malformed_session_cookie_creates_new_session(client):
    """A tampered session cookie must not crash the app."""
    client.set_cookie('resume_session', 'this-is-not-a-valid-session-cookie')
    response = client.get('/')
    assert response.status_code == 200


def test_oversized_session_cookie_does_not_crash(client):
    """A large, signed-looking but invalid session cookie must not crash.

    An attacker crafting a request by hand might send a 3 KB blob that
    looks like a Flask session cookie (dot-separated, base64-ish). The
    server must either reject it cleanly (new session) or 4xx — it
    must not 500.

    Using 3 KB stays under the browser's ~4 KB cookie size limit so
    Werkzeug's cookie-dump helper doesn't warn us out of the test.
    """
    # 3 KB of base64-safe bytes — large enough to dwarf a normal
    # session cookie (~200 bytes) but under the 4 KB browser ceiling.
    payload = 'A' * 3000
    client.set_cookie('resume_session', payload)
    response = client.get('/')
    assert response.status_code in (200, 400, 431)


# ---------------------------------------------------------------------------
# Malformed / truncated database
# ---------------------------------------------------------------------------


def _run_migrate(config_path, extra_env=None):
    """Invoke ``manage.py migrate`` as a subprocess and capture output."""
    env = os.environ.copy()
    env['RESUME_SITE_CONFIG'] = str(config_path)
    if extra_env:
        env.update(extra_env)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, 'manage.py', 'migrate'],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
        timeout=30,
        check=False,
    )
    return result


def _write_minimal_config(tmp_path, db_path):
    """Write just enough config.yaml to make manage.py happy."""
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
        'secret_key: "test-secret-key-for-resilience-tests-padded"\n'
        f'database_path: "{db_path}"\n'
        f'photo_storage: "{tmp_path}/photos"\n'
        'admin:\n'
        '  username: "admin"\n'
        '  password_hash: "pbkdf2:sha256:600000$fake$0000000000000000000000000000000000000000000000000000000000000000"\n'
        '  allowed_networks:\n'
        '    - "127.0.0.0/8"\n'
    )
    return config_path


def test_migrate_aborts_on_truncated_database_file(tmp_path):
    """A <100-byte DB file must abort migrate with a non-zero exit.

    100 bytes is the SQLite header size. Anything smaller is either an
    aborted backup restore or a filesystem corruption — applying a
    fresh schema on top would silently erase whatever the operator was
    trying to recover.
    """
    db_path = tmp_path / 'truncated.db'
    db_path.write_bytes(b'SQLi')  # 4 bytes — clearly truncated

    config_path = _write_minimal_config(tmp_path, db_path)
    result = _run_migrate(config_path)

    assert result.returncode != 0
    combined = result.stderr + result.stdout
    assert 'truncated' in combined.lower() or 'corrupt' in combined.lower(), combined


def test_migrate_aborts_on_corrupt_database(tmp_path):
    """A non-SQLite random-bytes file of realistic size must abort too.

    Writes 1 KB of junk so the size check passes but
    ``PRAGMA integrity_check`` fails.
    """
    db_path = tmp_path / 'corrupt.db'
    # Random-looking bytes that are not a valid SQLite file. Deliberately
    # don't start with the SQLite magic so the driver bails early.
    db_path.write_bytes(b'NOTSQLITE' + b'\x00\x01\x02\x03' * 256)

    config_path = _write_minimal_config(tmp_path, db_path)
    result = _run_migrate(config_path)

    assert result.returncode != 0
    combined = result.stderr + result.stdout
    # Either the size check, the PRAGMA, or the sqlite3 driver itself
    # refuses the file — any of those is acceptable as long as we get
    # a non-zero exit and mention the problem.
    assert (
        'corrupt' in combined.lower()
        or 'integrity' in combined.lower()
        or 'not a database' in combined.lower()
    ), combined


def test_migrate_allows_nonexistent_database(tmp_path):
    """A missing DB file is fine — migrate creates it fresh.

    Regression guard: the new corruption check must NOT reject the
    fresh-install path where the DB doesn't exist yet.
    """
    db_path = tmp_path / 'fresh.db'
    assert not db_path.exists()

    config_path = _write_minimal_config(tmp_path, db_path)
    result = _run_migrate(config_path)

    # Fresh DB + all migrations applied should succeed.
    assert result.returncode == 0, result.stderr + result.stdout
    assert db_path.exists()


# ---------------------------------------------------------------------------
# 500 handler contract — regression guard
# ---------------------------------------------------------------------------


def test_500_does_not_leak_traceback(app):
    """The error handler returns a safe body, not a stack trace."""

    @app.route('/_test_500')
    def _boom():
        raise RuntimeError('db exploded')

    with app.test_client() as c:
        response = c.get('/_test_500')

    assert response.status_code == 500
    body = response.data.decode('utf-8', errors='replace')
    assert 'Traceback' not in body
    assert 'RuntimeError' not in body
    assert 'db exploded' not in body
