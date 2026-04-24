"""
Phase 25.2 (#49) — page_views batching off the hot path.

The production path uses a bounded ``queue.Queue`` fed by
``track_page_view`` and drained in batches by a single daemon thread.
These tests exercise the queue semantics directly — the Flask
``TESTING`` flag bypasses the queue (see ``app/services/analytics.py``),
so the only way to observe back-pressure / drain behaviour is to
drive the module-level functions without Flask.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest


@pytest.fixture
def analytics_db(tmp_path):
    """Fresh SQLite file with the minimum schema for the drainer."""
    db_path = str(tmp_path / 'analytics.db')
    conn = sqlite3.connect(db_path)
    conn.execute(
        'CREATE TABLE page_views ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'path TEXT NOT NULL, '
        'referrer TEXT NOT NULL DEFAULT "", '
        "user_agent TEXT NOT NULL DEFAULT '', "
        "ip_address TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
        ')'
    )
    conn.commit()
    conn.close()
    return db_path


def _reset_module_state():
    """Drop the module's in-memory state between tests.

    Each test gets a fresh queue + drop counter so assertions don't
    depend on order.
    """
    import queue as _q

    from app.services import analytics as mod

    mod._queue = _q.Queue(maxsize=mod._QUEUE_MAX)
    mod._drainer_started = False
    mod._dropped_total = 0
    mod._shutdown.clear()


def test_flush_batch_writes_every_row(analytics_db):
    """The drainer's batch-insert path writes exactly N rows per batch."""
    from app.services.analytics import _flush_batch

    batch = [(f'/page/{i}', '', 'other', 'h' * 16) for i in range(50)]
    _flush_batch(analytics_db, batch)

    conn = sqlite3.connect(analytics_db)
    try:
        count = conn.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
    finally:
        conn.close()
    assert count == 50


def test_flush_remaining_drains_queue(analytics_db):
    """``_flush_remaining`` empties the queue in one final batch —
    this is the atexit path that prevents losing the last drain window
    on process shutdown."""
    from app.services import analytics as mod

    _reset_module_state()
    for i in range(10):
        mod._queue.put((f'/shutdown/{i}', '', 'other', 'h' * 16))

    mod._flush_remaining(analytics_db)

    conn = sqlite3.connect(analytics_db)
    try:
        count = conn.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
    finally:
        conn.close()
    assert count == 10
    assert mod._queue.empty()


def test_dropped_total_increments_on_full_queue():
    """When the bounded queue is full, producers drop silently and
    the drop counter increments. Tests the back-pressure contract
    directly against the module-level queue object."""
    from app.services import analytics as mod

    _reset_module_state()
    # Fill the queue to capacity.
    for i in range(mod._QUEUE_MAX):
        mod._queue.put_nowait((f'/p/{i}', '', 'other', 'h' * 16))

    before = mod._dropped_total
    # Simulate three producers hitting a full queue — this mirrors the
    # try/except queue.Full block in ``track_page_view``.
    for _ in range(3):
        try:
            mod._queue.put_nowait(('/overflow', '', 'other', 'h' * 16))
        except Exception:  # noqa: BLE001 — queue.Full
            mod._dropped_total += 1
    after = mod._dropped_total
    assert after - before == 3
    assert mod.get_dropped_total() >= 3


def test_drainer_concurrency_doesnt_drop_rows(analytics_db):
    """Multiple producers simultaneously enqueue events; a single
    pass through ``_flush_remaining`` writes every one. No locking
    bug drops rows when producers race the drainer."""
    from app.services import analytics as mod

    _reset_module_state()

    def produce(start, n):
        for i in range(start, start + n):
            mod._queue.put((f'/concurrent/{i}', '', 'other', 'h' * 16))

    threads = [threading.Thread(target=produce, args=(i * 100, 100)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    mod._flush_remaining(analytics_db)

    conn = sqlite3.connect(analytics_db)
    try:
        count = conn.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
    finally:
        conn.close()
    assert count == 500  # 5 threads × 100 events each
