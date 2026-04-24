"""
Lightweight Page View Analytics

Provides a simple, privacy-respecting page view counter that stores visit
data in SQLite. No cookies, no tracking scripts, no third-party services.

Registered as a Flask before_request handler by the app factory, this module
logs every public GET request to the page_views table. Static assets, admin
pages, and photo serving routes are excluded to keep the data meaningful.

Phase 25.2 (#49) — off the hot path
------------------------------------
Prior to v0.3.2-beta-7, every public GET issued an INSERT+COMMIT
synchronously before the response returned. Under burst load, the
SQLite write lock contended with every other writer and put ~1.5 ms
on the p50 of the landing page.

The new design uses a bounded ``queue.Queue`` fed by the request
handler and drained in batches by a single daemon thread. The drainer
opens its own SQLite connection (thread-local), flushes when the
queue reaches ``_DRAIN_BATCH`` pending events OR every
``_DRAIN_INTERVAL`` seconds, whichever fires first. Queue full → drop
silently (analytics is best-effort; dropping a page view beats
blocking the response). The drainer is flushed on process shutdown
via ``atexit`` so the final batch doesn't get lost.

Tests bypass the queue (``app.config['TESTING']``) so assertions that
read ``page_views`` immediately after a GET still work without having
to sleep for a drain window.

Data retention is configurable via the ``page_views_retention_days``
setting, and old records can be purged with ``python manage.py
purge-all`` (Phase 25.1).
"""

from __future__ import annotations

import atexit
import contextlib
import queue
import sqlite3
import threading
import time

from flask import current_app, request

_QUEUE_MAX = 10_000
_DRAIN_INTERVAL = 2.0  # seconds
_DRAIN_BATCH = 500

_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
_drainer_started = False
_drainer_lock = threading.Lock()
_shutdown = threading.Event()
_dropped_total = 0  # exposed for /metrics + tests


def _drainer_loop(db_path: str) -> None:
    """Background worker that flushes queued page views into SQLite.

    Opens its own ``sqlite3`` connection (the request-scoped one
    managed by ``app.db`` belongs to the request thread and must not
    be shared). Flushes on the first of: ``_DRAIN_INTERVAL`` elapsed
    or ``_DRAIN_BATCH`` pending events.
    """
    last_flush = time.monotonic()
    while not _shutdown.is_set():
        batch: list[tuple] = []
        # Block up to the interval for the first event so an idle
        # process doesn't busy-loop.
        try:
            first = _queue.get(timeout=_DRAIN_INTERVAL)
            batch.append(first)
        except queue.Empty:
            continue
        # Drain up to the batch cap without blocking.
        for _ in range(_DRAIN_BATCH - 1):
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        _flush_batch(db_path, batch)
        last_flush = time.monotonic()
        # Yield so a spinning producer doesn't starve the drainer.
        time.sleep(0)
        if time.monotonic() - last_flush > _DRAIN_INTERVAL:
            last_flush = time.monotonic()
    # Final flush on shutdown.
    _flush_remaining(db_path)


def _flush_batch(db_path: str, batch: list[tuple]) -> None:
    """INSERT a batch of page_views rows in one transaction."""
    if not batch:
        return
    # Analytics is best-effort — a failed flush is logged and dropped.
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            conn.executemany(
                'INSERT INTO page_views (path, referrer, user_agent, ip_address) '
                'VALUES (?, ?, ?, ?)',
                batch,
            )
            conn.commit()
        finally:
            conn.close()


def _flush_remaining(db_path: str) -> None:
    """Empty the queue in one final batch. Called from atexit."""
    remaining: list[tuple] = []
    while True:
        try:
            remaining.append(_queue.get_nowait())
        except queue.Empty:
            break
    _flush_batch(db_path, remaining)


def _start_drainer_if_needed(db_path: str) -> None:
    """Lazy-start the drainer thread on first enqueue.

    Daemon thread so the process can exit without joining; the atexit
    hook signals shutdown and flushes whatever's left.
    """
    global _drainer_started
    if _drainer_started:
        return
    with _drainer_lock:
        if _drainer_started:
            return
        t = threading.Thread(
            target=_drainer_loop,
            args=(db_path,),
            daemon=True,
            name='page-views-drainer',
        )
        t.start()
        _drainer_started = True
        atexit.register(_signal_shutdown, db_path)


def _signal_shutdown(db_path: str) -> None:
    """atexit callback — signal the drainer and flush the queue."""
    _shutdown.set()
    _flush_remaining(db_path)


def track_page_view() -> None:
    """Enqueue a page view. Runs before every request.

    Only tracks:
    - GET requests (skips POST, PUT, DELETE, etc.)
    - Public pages (skips /static/, /admin, /photos/, /favicon, etc.)

    Under ``app.config['TESTING']`` writes directly to the DB so
    assertions reading ``page_views`` immediately after a GET still
    see the row without having to wait for the drain interval.
    """
    if request.method != 'GET':
        return

    path = request.path
    if path.startswith(
        (
            '/static/',
            '/admin',
            '/photos/',
            '/favicon',
            '/healthz',
            '/readyz',
            '/set-locale',
            '/csp-report',
        )
    ):
        return

    with contextlib.suppress(Exception):
        from app.services.logging import classify_user_agent, hash_client_ip
        from app.services.request_ip import get_client_ip

        client_ip = get_client_ip(request)
        ip_hash = hash_client_ip(client_ip or '', current_app.secret_key or '')
        ua_class = classify_user_agent(request.user_agent.string)

        row = (path, request.referrer or '', ua_class, ip_hash)

        if current_app.config.get('TESTING'):
            # Synchronous path for tests — assertions reading page_views
            # immediately after a client.get(...) would otherwise race
            # the drainer.
            from app.db import get_db

            db = get_db()
            db.execute(
                'INSERT INTO page_views (path, referrer, user_agent, ip_address) '
                'VALUES (?, ?, ?, ?)',
                row,
            )
            db.commit()
            return

        # Production path — enqueue; drainer handles the write.
        _start_drainer_if_needed(current_app.config['DATABASE_PATH'])
        try:
            _queue.put_nowait(row)
        except queue.Full:
            global _dropped_total
            _dropped_total += 1


def get_dropped_total() -> int:
    """Return the count of page views dropped due to queue-full.

    Exported for the /metrics endpoint and for tests that want to
    assert back-pressure behaviour.
    """
    return _dropped_total
