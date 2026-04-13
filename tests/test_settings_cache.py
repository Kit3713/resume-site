"""Tests for the settings TTL cache (Phase 12.1).

The cache lives in app/services/settings_svc.py and is consulted by the
request-time `inject_settings` context processor. These tests pin down the
behavior contract: hits within TTL avoid the DB, writes invalidate, and
distinct db paths get isolated entries.
"""

import sqlite3
import time

import pytest

from app.services import settings_svc
from app.services.settings_svc import (
    DEFAULT_SETTINGS_TTL,
    get_all_cached,
    invalidate_cache,
    save_many,
    set_one,
)


@pytest.fixture
def db(app):
    """Open a raw sqlite3 connection to the test app's database."""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clean_cache():
    """Every test in this module starts with an empty cache."""
    invalidate_cache()
    yield
    invalidate_cache()


def test_cache_hit_avoids_db(app, db, monkeypatch):
    """A second call within TTL must not re-query the database."""
    db_path = app.config['DATABASE_PATH']

    # Prime the cache (cold read goes to DB)
    first = get_all_cached(db, db_path)
    assert isinstance(first, dict)

    # Replace get_all with a sentinel that fails if called again
    def _fail(_):
        raise AssertionError('cache miss — get_all should not be called')

    monkeypatch.setattr(settings_svc, 'get_all', _fail)

    # Second call inside the TTL window must hit cache
    second = get_all_cached(db, db_path)
    assert second == first


def test_cache_returns_independent_copies(app, db):
    """Mutating the returned dict must not poison cached state."""
    db_path = app.config['DATABASE_PATH']
    first = get_all_cached(db, db_path)
    first['site_title'] = 'mutated'
    second = get_all_cached(db, db_path)
    assert second.get('site_title') != 'mutated'


def test_invalidate_forces_reread(app, db):
    """After invalidate_cache(), a direct DB write becomes visible."""
    db_path = app.config['DATABASE_PATH']
    get_all_cached(db, db_path)  # prime

    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('site_title', 'fresh')")
    db.commit()

    # Without invalidate, we'd still see the cached value
    invalidate_cache()
    assert get_all_cached(db, db_path)['site_title'] == 'fresh'


def test_save_many_invalidates(app, db):
    """save_many must clear the cache so the next read is fresh."""
    db_path = app.config['DATABASE_PATH']
    get_all_cached(db, db_path)  # prime

    save_many(db, {'site_title': 'after-save-many'})

    assert get_all_cached(db, db_path)['site_title'] == 'after-save-many'


def test_set_one_invalidates(app, db):
    """set_one must clear the cache so the next read is fresh."""
    db_path = app.config['DATABASE_PATH']
    get_all_cached(db, db_path)  # prime

    set_one(db, 'site_title', 'after-set-one')

    assert get_all_cached(db, db_path)['site_title'] == 'after-set-one'


def test_distinct_db_paths_are_isolated(app, db, tmp_path):
    """Each db_path keeps its own cache entry — no cross-app bleed."""
    db_path = app.config['DATABASE_PATH']
    other_path = str(tmp_path / 'other.db')

    primary = get_all_cached(db, db_path)
    # Different path => independent cache slot, even if same connection
    invalidate_cache(other_path)  # no-op on missing key
    again = get_all_cached(db, db_path)
    assert again == primary


def test_invalidate_specific_path_keeps_others(app, db, tmp_path):
    """Targeted invalidation must not nuke unrelated cache entries."""
    db_path = app.config['DATABASE_PATH']
    sentinel_path = '/nonexistent/sentinel.db'

    # Prime real entry
    primary = get_all_cached(db, db_path)
    # Manually plant a sentinel under a different key
    settings_svc._settings_cache[sentinel_path] = (
        time.monotonic() + 60,
        {'sentinel': 'value'},
    )

    invalidate_cache(db_path)

    assert sentinel_path in settings_svc._settings_cache
    # And primary cache slot is gone
    assert db_path not in settings_svc._settings_cache
    # Re-read still works
    assert get_all_cached(db, db_path) == primary


def test_ttl_default_is_reasonable():
    """Sanity check: TTL is short enough that admin edits feel snappy
    even if invalidation is somehow skipped."""
    assert 1.0 <= DEFAULT_SETTINGS_TTL <= 120.0
