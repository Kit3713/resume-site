"""
Admin Login Lockout — Phase 13.6

Application-level lockout that complements Flask-Limiter's per-minute
rate limit. The Limiter resets its counter every minute; this module
tracks failures across a sliding window persisted in SQLite so bursts
just under the rate limit (e.g. 4 attempts / minute for 10 minutes)
still trigger a lockout.

Parameters:

* **threshold** — number of failed attempts from one IP within the
  window that triggers a lockout. Default 10.
* **window_minutes** — how far back to look when counting failures.
  Default 15.
* **lockout_minutes** — once triggered, how long the IP stays locked.
  Default 15.

All three are admin-configurable in the Security category of the
settings registry (see :mod:`app.services.settings_svc`).

Privacy: the ``login_attempts`` table stores a SHA-256 hash of the IP
(see :func:`app.services.logging.hash_client_ip`) rather than the raw
address. The hash is salted with the app's ``secret_key`` so log files
alone cannot be joined across deployments.

This module is dependency-free (no Flask imports). It receives an open
sqlite3 connection and a client IP hash, so it's equally callable from
the route handler and from tests without an application context.
"""

from __future__ import annotations

import sqlite3
from collections import namedtuple
from datetime import UTC, datetime, timedelta

LockoutStatus = namedtuple(
    'LockoutStatus',
    ('locked', 'failures_in_window', 'seconds_remaining'),
)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _inc_login_attempts(outcome: str) -> None:
    """Increment the ``login_attempts_total`` counter, tolerating test isolation.

    The metrics module is imported lazily so a misconfigured test
    environment that stubs the registry doesn't blow up the core login
    path. Metrics are an observability concern; a broken counter must
    never break authentication.
    """
    import contextlib

    with contextlib.suppress(Exception):
        from app.services.metrics import login_attempts_total

        login_attempts_total.inc(label_values=(outcome,))


def record_failed_login(
    db: sqlite3.Connection, ip_hash: str, *, now: datetime | None = None
) -> None:
    """Insert a failed-login row for ``ip_hash`` at ``now`` (UTC)."""
    db.execute(
        'INSERT INTO login_attempts (ip_hash, success, created_at) VALUES (?, 0, ?)',
        (ip_hash, _iso(_now(now))),
    )
    db.commit()
    _inc_login_attempts('invalid')


def record_successful_login(
    db: sqlite3.Connection, ip_hash: str, *, now: datetime | None = None
) -> None:
    """Insert a successful-login row.

    We keep the failure rows in place — a correct password after nine
    bad ones must not "rescue" an attacker's lockout. Successful rows
    exist only so an operator / future audit log can see the full
    attempt timeline.
    """
    db.execute(
        'INSERT INTO login_attempts (ip_hash, success, created_at) VALUES (?, 1, ?)',
        (ip_hash, _iso(_now(now))),
    )
    db.commit()
    _inc_login_attempts('success')


def check_lockout(
    db: sqlite3.Connection,
    ip_hash: str,
    *,
    threshold: int,
    window_minutes: int,
    lockout_minutes: int,
    now: datetime | None = None,
) -> LockoutStatus:
    """Return the :class:`LockoutStatus` for ``ip_hash`` right now.

    Rules:
        * Count failed attempts in the last ``window_minutes``.
        * If the count is below ``threshold`` → not locked.
        * If it hits the threshold, the lockout lasts for
          ``lockout_minutes`` starting at the *most recent* failure in
          the window. Once those minutes have elapsed without a new
          failure, the lockout expires naturally.

    Defensively clamps inputs: non-positive thresholds or windows
    disable the check (locked=False). That keeps misconfiguration from
    accidentally locking every admin out.
    """
    if threshold <= 0 or window_minutes <= 0:
        return LockoutStatus(locked=False, failures_in_window=0, seconds_remaining=0)

    now_dt = _now(now)
    window_start = now_dt - timedelta(minutes=window_minutes)

    rows = db.execute(
        'SELECT created_at FROM login_attempts '
        'WHERE ip_hash = ? AND success = 0 AND created_at >= ? '
        'ORDER BY created_at DESC',
        (ip_hash, _iso(window_start)),
    ).fetchall()

    failures = len(rows)
    if failures < threshold:
        return LockoutStatus(locked=False, failures_in_window=failures, seconds_remaining=0)

    # Threshold reached. The lockout runs from the most recent failure,
    # so a determined attacker can't keep probing at just-under-the-
    # threshold rate and reset the clock by spacing failures out.
    latest_failure_iso = rows[0][0]
    try:
        latest = datetime.strptime(latest_failure_iso, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    except ValueError:
        # Defensive: if the row's timestamp is malformed, fall back to
        # treating the lockout as fresh (maximally conservative).
        latest = now_dt

    expires = latest + timedelta(minutes=lockout_minutes)
    seconds_remaining = int((expires - now_dt).total_seconds())

    if seconds_remaining <= 0:
        return LockoutStatus(locked=False, failures_in_window=failures, seconds_remaining=0)

    # A lockout is currently in force — surface it as the `locked`
    # outcome so ResumeBruteForce can distinguish "attempted-while-
    # locked" from "credential mismatch". The increment happens only
    # when the result is actually locked so the counter reflects
    # attempts turned away, not every probe of the lockout state.
    _inc_login_attempts('locked')
    return LockoutStatus(
        locked=True,
        failures_in_window=failures,
        seconds_remaining=seconds_remaining,
    )


def purge_old_attempts(db: sqlite3.Connection, retention_days: int) -> int:
    """Delete login_attempt rows older than ``retention_days``.

    Returns the number of rows removed. Intended for a future ``manage.py``
    purge command alongside the existing ``purge-analytics`` — not wired
    up yet in this commit.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    cursor = db.execute(
        'DELETE FROM login_attempts WHERE created_at < ?',
        (_iso(cutoff),),
    )
    db.commit()
    return cursor.rowcount
