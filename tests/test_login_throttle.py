"""
Login Lockout Tests — Phase 13.6

Verifies :mod:`app.services.login_throttle`:

* Unit: record_failed_login / record_successful_login persist rows.
* Unit: check_lockout counts only failures in the window and respects
  the threshold + lockout-minutes contract.
* Unit: non-positive threshold or window disables the check (fail-safe
  against misconfiguration).
* Integration: POSTing bad creds to /admin/login eventually returns 429
  with a Retry-After header, and emits security.login_failed with
  reason='locked' on further attempts during the lockout.
* Integration: a correct password is NOT accepted once the IP is
  locked — lockout wins.

These tests touch Flask-Limiter's 5/min rate limit, which would
interfere if we made lots of POSTs from one client. We bypass it by
creating a fresh test app whose Limiter is disabled for the test
duration (see ``throttle_app`` fixture).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.events import Events, clear, register
from app.services.login_throttle import (
    LockoutStatus,
    check_lockout,
    purge_old_attempts,
    record_failed_login,
    record_successful_login,
)

# ---------------------------------------------------------------------------
# Unit tests — pure service module, no Flask app needed
# ---------------------------------------------------------------------------


@pytest.fixture
def throttle_db(tmp_path):
    """An isolated sqlite3 connection with just the login_attempts table."""
    path = tmp_path / 'throttle.db'
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE login_attempts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_hash    TEXT    NOT NULL,
            success    INTEGER NOT NULL,
            created_at TEXT    NOT NULL
        );
        CREATE INDEX idx_login_attempts_ip_hash_created_at
            ON login_attempts(ip_hash, created_at);
        """
    )
    yield conn
    conn.close()


def test_record_failed_and_successful_login_insert_rows(throttle_db):
    record_failed_login(throttle_db, 'ip-a')
    record_successful_login(throttle_db, 'ip-a')

    rows = throttle_db.execute('SELECT ip_hash, success FROM login_attempts ORDER BY id').fetchall()
    assert rows == [('ip-a', 0), ('ip-a', 1)]


def test_check_lockout_under_threshold_not_locked(throttle_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    for i in range(3):
        record_failed_login(throttle_db, 'ip-a', now=now + timedelta(seconds=i))

    status = check_lockout(
        throttle_db,
        'ip-a',
        threshold=10,
        window_minutes=15,
        lockout_minutes=15,
        now=now + timedelta(seconds=10),
    )
    assert status == LockoutStatus(locked=False, failures_in_window=3, seconds_remaining=0)


def test_check_lockout_at_threshold_locks(throttle_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    for i in range(10):
        record_failed_login(throttle_db, 'ip-a', now=now + timedelta(seconds=i))

    status = check_lockout(
        throttle_db,
        'ip-a',
        threshold=10,
        window_minutes=15,
        lockout_minutes=15,
        now=now + timedelta(seconds=30),
    )
    assert status.locked is True
    assert status.failures_in_window == 10
    # Latest failure at +9s, lockout 15 min; check at +30s → ~14:30 remaining.
    assert 14 * 60 <= status.seconds_remaining <= 15 * 60


def test_check_lockout_counts_only_failures_in_window(throttle_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    # 9 old failures (outside the 15-minute window)
    for _i in range(9):
        record_failed_login(throttle_db, 'ip-a', now=now - timedelta(hours=1))
    # 3 fresh failures (inside the window)
    for _i in range(3):
        record_failed_login(throttle_db, 'ip-a', now=now - timedelta(minutes=1))

    status = check_lockout(
        throttle_db,
        'ip-a',
        threshold=10,
        window_minutes=15,
        lockout_minutes=15,
        now=now,
    )
    assert status.locked is False
    assert status.failures_in_window == 3


def test_check_lockout_isolated_per_ip(throttle_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    for _ in range(10):
        record_failed_login(throttle_db, 'attacker', now=now)

    status_for_attacker = check_lockout(
        throttle_db,
        'attacker',
        threshold=10,
        window_minutes=15,
        lockout_minutes=15,
        now=now + timedelta(minutes=1),
    )
    status_for_legit = check_lockout(
        throttle_db,
        'legit-user',
        threshold=10,
        window_minutes=15,
        lockout_minutes=15,
        now=now + timedelta(minutes=1),
    )
    assert status_for_attacker.locked is True
    assert status_for_legit.locked is False


def test_check_lockout_ignores_successful_rows(throttle_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    for i in range(20):
        record_successful_login(throttle_db, 'ip-a', now=now + timedelta(seconds=i))

    status = check_lockout(
        throttle_db,
        'ip-a',
        threshold=10,
        window_minutes=15,
        lockout_minutes=15,
        now=now + timedelta(minutes=1),
    )
    assert status.locked is False
    assert status.failures_in_window == 0


def test_check_lockout_expires_after_duration(throttle_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    for i in range(10):
        record_failed_login(throttle_db, 'ip-a', now=now + timedelta(seconds=i))

    # 20 min after the latest failure → lockout (15 min) should be over.
    status = check_lockout(
        throttle_db,
        'ip-a',
        threshold=10,
        window_minutes=60,  # wider so failures still in window
        lockout_minutes=15,
        now=now + timedelta(minutes=20),
    )
    assert status.locked is False


def test_check_lockout_zero_or_negative_threshold_disables_feature(throttle_db):
    # 50 failures, but threshold=0 → feature off, never locked.
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    for i in range(50):
        record_failed_login(throttle_db, 'ip-a', now=now + timedelta(seconds=i))

    assert not check_lockout(
        throttle_db, 'ip-a', threshold=0, window_minutes=15, lockout_minutes=15
    ).locked
    assert not check_lockout(
        throttle_db, 'ip-a', threshold=-1, window_minutes=15, lockout_minutes=15
    ).locked


def test_check_lockout_zero_or_negative_window_disables_feature(throttle_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    for i in range(50):
        record_failed_login(throttle_db, 'ip-a', now=now + timedelta(seconds=i))

    assert not check_lockout(
        throttle_db, 'ip-a', threshold=10, window_minutes=0, lockout_minutes=15
    ).locked


def test_purge_old_attempts_removes_rows_past_retention(throttle_db):
    now = datetime.now(UTC)
    record_failed_login(throttle_db, 'ip-a', now=now - timedelta(days=40))
    record_failed_login(throttle_db, 'ip-b', now=now - timedelta(days=1))

    removed = purge_old_attempts(throttle_db, retention_days=30)
    assert removed == 1
    remaining = throttle_db.execute('SELECT ip_hash FROM login_attempts').fetchall()
    assert [r[0] for r in remaining] == ['ip-b']


def test_purge_with_non_positive_retention_is_noop(throttle_db):
    record_failed_login(throttle_db, 'ip-a')
    assert purge_old_attempts(throttle_db, retention_days=0) == 0
    assert purge_old_attempts(throttle_db, retention_days=-7) == 0
    assert throttle_db.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Integration — real /admin/login route
#
# The default test admin config has password_hash set (tests/conftest.py).
# We submit wrong credentials until the lockout kicks in, then confirm
# the 429 response + event emission, then confirm the correct password
# is still refused while locked.
# ---------------------------------------------------------------------------


@pytest.fixture
def throttle_app(app, monkeypatch):
    """The standard test app, but with Flask-Limiter disabled so we can
    make more than 5 POST requests per minute from one client.

    Also pre-registers an event collector for security.login_failed so
    each integration test can assert on the emitted payloads.
    """
    from app import limiter

    # Flask-Limiter's 5/min rate would block us before we hit the
    # lockout threshold of 10. Disable it for these tests — the lockout
    # itself (not the rate limit) is what we're validating.
    monkeypatch.setattr(limiter, 'enabled', False)

    clear()
    collected = []
    register(Events.SECURITY_LOGIN_FAILED, lambda **p: collected.append(p))
    yield app, collected
    clear()


def _tune_lockout(app, threshold=3, window=15, duration=15):
    """Lower the lockout threshold so tests don't need 10 POSTs."""
    from app.services.settings_svc import invalidate_cache

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.executemany(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        [
            ('login_lockout_threshold', str(threshold)),
            ('login_lockout_window_minutes', str(window)),
            ('login_lockout_duration_minutes', str(duration)),
        ],
    )
    conn.commit()
    conn.close()
    invalidate_cache()


def test_failed_login_records_attempt_and_emits_event(throttle_app):
    app, events = throttle_app
    _tune_lockout(app, threshold=10)

    client = app.test_client()
    client.post('/admin/login', data={'username': 'admin', 'password': 'wrong-password'})

    assert len(events) == 1
    payload = events[0]
    assert payload['reason'] == 'invalid_credentials'
    assert payload['username'] == 'admin'
    assert payload['ip_hash']


def test_lockout_fires_after_threshold_and_returns_429(throttle_app):
    app, events = throttle_app
    _tune_lockout(app, threshold=3)

    client = app.test_client()
    for _ in range(3):
        client.post(
            '/admin/login',
            data={'username': 'admin', 'password': 'wrong-password'},
        )

    # Fourth attempt — IP is now locked, even with WRONG credentials.
    response = client.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'wrong-password'},
    )
    assert response.status_code == 429
    assert int(response.headers.get('Retry-After', '0')) > 0

    # And the emitted event records the lockout reason specifically.
    reasons = [e['reason'] for e in events]
    assert reasons.count('invalid_credentials') == 3
    assert 'locked' in reasons


def test_lockout_refuses_correct_password_while_locked(throttle_app):
    """A correct password must NOT rescue a locked IP."""
    app, _events = throttle_app
    _tune_lockout(app, threshold=3)

    client = app.test_client()
    for _ in range(3):
        client.post(
            '/admin/login',
            data={'username': 'admin', 'password': 'wrong-password'},
        )

    # The conftest fixture sets password to 'testpassword123'.
    response = client.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'testpassword123'},
    )
    assert response.status_code == 429, 'locked IPs must be rejected regardless of credentials'


def test_threshold_zero_disables_lockout_even_with_many_failures(throttle_app):
    app, _events = throttle_app
    _tune_lockout(app, threshold=0)

    client = app.test_client()
    # Twenty failures, no lockout kicks in because threshold=0 disables.
    for _ in range(20):
        response = client.post(
            '/admin/login',
            data={'username': 'admin', 'password': 'wrong-password'},
        )
    # Last response is a normal 200 rendering the login page with flash.
    assert response.status_code == 200


def test_ip_isolation_one_lockout_does_not_block_another_ip(throttle_app):
    """Locking one IP must not block a different IP.

    The hash used for lockout keys on ``request.remote_addr``, so two
    requests from different loopback addresses inside 127.0.0.0/8
    produce different ``client_ip_hash`` values and are tracked
    independently. We use 127.0.0.1 and 127.0.0.2 so both remain inside
    the admin's allowed_networks (the IP restriction runs BEFORE the
    lockout check).
    """
    app, _events = throttle_app
    _tune_lockout(app, threshold=3)

    # First IP: lock 127.0.0.1 (the default remote_addr).
    attacker = app.test_client()
    for _ in range(3):
        attacker.post(
            '/admin/login',
            data={'username': 'admin', 'password': 'wrong'},
        )
    locked = attacker.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'wrong'},
    )
    assert locked.status_code == 429

    # Second IP: also inside 127.0.0.0/8 so IP restriction passes, but
    # a distinct remote_addr so the lockout key differs.
    legit = app.test_client()
    response = legit.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'wrong'},
        environ_base={'REMOTE_ADDR': '127.0.0.2'},
    )
    # Legit IP has only one failure — well under threshold=3.
    assert response.status_code == 200
