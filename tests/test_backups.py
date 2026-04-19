"""
Backup / Restore Tests — Phase 17.1

Verifies app.services.backups:
- create_backup: archive name format, contents, --db-only, missing photos,
  missing config, atomicity on error, settings side-effect.
- list_backups: ignores .tmp files and pre-restore-* sidecars, sorts newest-first.
- prune_backups: retention, ValueError on keep < 1.
- restore_backup: round-trip, pre-restore sidecar, error paths.
- _safe_extract: rejects traversal, absolute paths, symlinks, corrupted archives.

Tests exercise the service module directly — no Flask app needed. A seeded
SQLite file and a populated photos directory per test give us a realistic
round-trip. tmp_path keeps every test isolated.
"""

from __future__ import annotations

import gzip
import os
import re
import sqlite3
import tarfile
import time
from datetime import UTC, datetime
from io import BytesIO

import pytest

from app.services.backups import (
    ARCHIVE_PREFIX,
    ARCHIVE_SUFFIX,
    SIDECAR_PREFIX,
    TMP_SUFFIX,
    BackupError,
    BackupSecurityError,
    _safe_extract,
    create_backup,
    list_backups,
    prune_backups,
    restore_backup,
)

# Ensure `gzip` stays a linter-visible import; a future test may assert on
# gzip framing without needing to re-import.
_ = gzip


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    """Return a path to a tiny seeded SQLite file with a settings table."""
    path = tmp_path / 'site.db'
    conn = sqlite3.connect(str(path))
    conn.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute("INSERT INTO settings (key, value) VALUES ('site_title', 'Test Site')")
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def seeded_photos(tmp_path):
    """Return a photos directory containing two small files."""
    photos = tmp_path / 'photos'
    photos.mkdir()
    (photos / 'foo.jpg').write_bytes(b'\xff\xd8\xff\xe0fake-jpeg')
    (photos / 'bar.png').write_bytes(b'\x89PNG\r\n\x1a\nfake-png')
    return str(photos)


@pytest.fixture
def seeded_config(tmp_path):
    """Return a config.yaml path with a handful of plausible values."""
    cfg = tmp_path / 'config.yaml'
    cfg.write_text(
        'secret_key: "test-secret"\ndatabase_path: "data/site.db"\nphoto_storage: "photos"\n'
    )
    return str(cfg)


@pytest.fixture
def output_dir(tmp_path):
    """Return an empty, existing backup output directory."""
    out = tmp_path / 'backups'
    out.mkdir()
    return str(out)


@pytest.fixture
def fixed_now():
    """A fixed UTC timestamp used for deterministic archive names."""
    return datetime(2026, 4, 1, 12, 34, 56, tzinfo=UTC)


# ---------------------------------------------------------------------------
# create_backup — happy path
# ---------------------------------------------------------------------------


def test_create_backup_produces_well_named_archive(
    seeded_db, seeded_photos, seeded_config, output_dir, fixed_now
):
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=output_dir,
        now=fixed_now,
    )

    assert os.path.isfile(archive)
    name = os.path.basename(archive)
    assert name == f'{ARCHIVE_PREFIX}20260401-123456{ARCHIVE_SUFFIX}'
    assert re.match(
        rf'^{re.escape(ARCHIVE_PREFIX)}\d{{8}}-\d{{6}}{re.escape(ARCHIVE_SUFFIX)}$', name
    )

    # Gzip magic bytes
    with open(archive, 'rb') as f:
        assert f.read(2) == b'\x1f\x8b'


def test_create_backup_contains_db_photos_and_config(
    seeded_db, seeded_photos, seeded_config, output_dir
):
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=output_dir,
    )
    with tarfile.open(archive, 'r:gz') as tar:
        names = tar.getnames()
    assert 'db/site.db' in names
    assert 'config.yaml' in names
    assert any(n.startswith('photos/') and n.endswith('foo.jpg') for n in names)
    assert any(n.startswith('photos/') and n.endswith('bar.png') for n in names)


def test_db_only_archive_contains_only_db(seeded_db, seeded_photos, seeded_config, output_dir):
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=output_dir,
        db_only=True,
    )
    with tarfile.open(archive, 'r:gz') as tar:
        names = tar.getnames()
    assert 'db/site.db' in names
    assert 'config.yaml' not in names
    assert not any(n.startswith('photos/') for n in names)


def test_backup_succeeds_when_photos_dir_missing(seeded_db, seeded_config, output_dir, capsys):
    missing = os.path.join(os.path.dirname(output_dir), 'does-not-exist')
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=missing,
        config_path=seeded_config,
        output_dir=output_dir,
    )
    assert os.path.isfile(archive)
    with tarfile.open(archive, 'r:gz') as tar:
        assert not any(n.startswith('photos/') for n in tar.getnames())
    assert 'photos directory not found' in capsys.readouterr().err


def test_backup_succeeds_when_config_missing(seeded_db, seeded_photos, output_dir, capsys):
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=seeded_photos,
        config_path=os.path.join(os.path.dirname(output_dir), 'no-config.yaml'),
        output_dir=output_dir,
    )
    assert os.path.isfile(archive)
    with tarfile.open(archive, 'r:gz') as tar:
        assert 'config.yaml' not in tar.getnames()
    assert 'config file not found' in capsys.readouterr().err


def test_backup_creates_output_dir_if_missing(seeded_db, seeded_photos, seeded_config, tmp_path):
    out = str(tmp_path / 'nested' / 'more-nested' / 'backups')
    assert not os.path.isdir(out)
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=out,
    )
    assert os.path.isfile(archive)


# ---------------------------------------------------------------------------
# create_backup — atomicity
# ---------------------------------------------------------------------------


def test_backup_cleans_up_tmp_on_failure(
    seeded_db, seeded_photos, seeded_config, output_dir, monkeypatch
):
    """If tar.add raises mid-build, no final archive or .tmp remains."""
    original_add = tarfile.TarFile.add
    call_count = {'n': 0}

    def flaky_add(self, *args, **kwargs):
        call_count['n'] += 1
        if call_count['n'] == 2:
            raise OSError('simulated disk error')
        return original_add(self, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, 'add', flaky_add)

    with pytest.raises(OSError, match='simulated disk error'):
        create_backup(
            db_path=seeded_db,
            photos_dir=seeded_photos,
            config_path=seeded_config,
            output_dir=output_dir,
        )

    leftovers = os.listdir(output_dir)
    assert not any(n.endswith(ARCHIVE_SUFFIX) for n in leftovers)
    assert not any(n.endswith(TMP_SUFFIX) for n in leftovers)


def test_backup_writes_settings_row(seeded_db, output_dir):
    create_backup(
        db_path=seeded_db,
        photos_dir=None,
        config_path=None,
        output_dir=output_dir,
        db_only=True,
    )
    conn = sqlite3.connect(seeded_db)
    row = conn.execute("SELECT value FROM settings WHERE key = 'backup_last_success'").fetchone()
    conn.close()
    assert row is not None
    # Must be ISO-8601 / datetime.fromisoformat parseable.
    # fromisoformat in 3.11+ accepts trailing 'Z' only in 3.11+ partially; our
    # own format uses 'Z' so strip it for the parse.
    stamp = row[0].rstrip('Z')
    assert datetime.fromisoformat(stamp)


# ---------------------------------------------------------------------------
# list_backups / prune_backups
# ---------------------------------------------------------------------------


def test_list_backups_on_empty_dir_returns_empty(output_dir):
    assert list_backups(output_dir) == []


def test_list_backups_on_missing_dir_returns_empty(tmp_path):
    assert list_backups(str(tmp_path / 'nope')) == []


def test_list_backups_ignores_tmp_and_sidecars(output_dir):
    # Valid archive
    valid = os.path.join(output_dir, f'{ARCHIVE_PREFIX}20260401-000000{ARCHIVE_SUFFIX}')
    with open(valid, 'wb') as f:
        f.write(b'\x1f\x8b' + b'\x00' * 8)
    # Temp file
    with open(os.path.join(output_dir, f'{ARCHIVE_PREFIX}20260402-000000{TMP_SUFFIX}'), 'wb') as f:
        f.write(b'incomplete')
    # Pre-restore sidecar
    os.mkdir(os.path.join(output_dir, f'{SIDECAR_PREFIX}20260401-123456'))
    # Unrelated file
    with open(os.path.join(output_dir, 'notes.txt'), 'w') as f:
        f.write('hi')

    entries = list_backups(output_dir)
    assert [e.name for e in entries] == [os.path.basename(valid)]


def test_list_backups_sorted_newest_first(seeded_db, output_dir):
    # Two archives with distinct, well-separated mtimes
    a = create_backup(
        db_path=seeded_db,
        photos_dir=None,
        config_path=None,
        output_dir=output_dir,
        db_only=True,
        now=datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC),
    )
    # bump mtime to be earlier — so a should be "newer"
    os.utime(a, (time.time() - 10, time.time() - 10))

    b = create_backup(
        db_path=seeded_db,
        photos_dir=None,
        config_path=None,
        output_dir=output_dir,
        db_only=True,
        now=datetime(2026, 4, 2, 0, 0, 0, tzinfo=UTC),
    )
    # b has default mtime (now)

    entries = list_backups(output_dir)
    assert [e.name for e in entries] == [os.path.basename(b), os.path.basename(a)]


def test_prune_keeps_newest_n(seeded_db, output_dir):
    archives = []
    for day in range(1, 6):  # 5 archives
        archive = create_backup(
            db_path=seeded_db,
            photos_dir=None,
            config_path=None,
            output_dir=output_dir,
            db_only=True,
            now=datetime(2026, 4, day, 0, 0, 0, tzinfo=UTC),
        )
        # Stamp mtime so "newest" is deterministic — later-day = newer
        target = time.time() - (10 - day)
        os.utime(archive, (target, target))
        archives.append(archive)

    deleted = prune_backups(output_dir, keep=2)
    assert len(deleted) == 3
    remaining = {e.name for e in list_backups(output_dir)}
    # Newest two (days 4 and 5) retained
    assert os.path.basename(archives[3]) in remaining
    assert os.path.basename(archives[4]) in remaining


def test_prune_rejects_zero_or_negative(tmp_path):
    # The function raises before it touches the directory, so any path is
    # fine — we use tmp_path to keep the check deterministic across runs.
    unused = str(tmp_path / 'unused')
    with pytest.raises(ValueError, match='at least 1'):
        prune_backups(unused, keep=0)
    with pytest.raises(ValueError, match='at least 1'):
        prune_backups(unused, keep=-3)


# ---------------------------------------------------------------------------
# restore_backup — happy path + safety
# ---------------------------------------------------------------------------


def test_restore_round_trip(seeded_db, seeded_photos, seeded_config, output_dir, tmp_path):
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=output_dir,
    )

    # Wipe the DB and photos to simulate disaster
    os.unlink(seeded_db)
    for f in os.listdir(seeded_photos):
        os.unlink(os.path.join(seeded_photos, f))

    sidecar = restore_backup(
        archive_path=archive,
        db_path=seeded_db,
        photos_dir=seeded_photos,
        output_dir=output_dir,
    )

    # DB restored and contains the seed row
    conn = sqlite3.connect(seeded_db)
    row = conn.execute("SELECT value FROM settings WHERE key = 'site_title'").fetchone()
    conn.close()
    assert row is not None and row[0] == 'Test Site'

    # Photos restored
    assert os.path.isfile(os.path.join(seeded_photos, 'foo.jpg'))
    assert os.path.isfile(os.path.join(seeded_photos, 'bar.png'))

    # Sidecar exists (even though we wiped, sidecar captures that empty state)
    assert os.path.isdir(sidecar)
    assert sidecar.startswith(os.path.join(output_dir, SIDECAR_PREFIX))


def test_restore_creates_sidecar_with_previous_db(seeded_db, seeded_photos, output_dir):
    archive = create_backup(
        db_path=seeded_db,
        photos_dir=None,
        config_path=None,
        output_dir=output_dir,
        db_only=True,
    )

    # Mutate DB so sidecar captures a distinguishable state
    conn = sqlite3.connect(seeded_db)
    conn.execute("UPDATE settings SET value = 'Mutated' WHERE key = 'site_title'")
    conn.commit()
    conn.close()

    sidecar = restore_backup(
        archive_path=archive,
        db_path=seeded_db,
        photos_dir=seeded_photos,
        output_dir=output_dir,
    )

    sidecar_db = os.path.join(sidecar, os.path.basename(seeded_db))
    assert os.path.isfile(sidecar_db)
    conn = sqlite3.connect(sidecar_db)
    row = conn.execute("SELECT value FROM settings WHERE key = 'site_title'").fetchone()
    conn.close()
    assert row[0] == 'Mutated'  # pre-restore value preserved in sidecar


def test_restore_rejects_missing_file(seeded_db, seeded_photos, output_dir):
    with pytest.raises(FileNotFoundError):
        restore_backup(
            archive_path=os.path.join(output_dir, 'no-such.tar.gz'),
            db_path=seeded_db,
            photos_dir=seeded_photos,
            output_dir=output_dir,
        )


def test_restore_rejects_wrong_extension(seeded_db, seeded_photos, output_dir, tmp_path):
    wrong = tmp_path / 'bad-name.zip'
    wrong.write_bytes(b'irrelevant')
    with pytest.raises(BackupError, match='must end with'):
        restore_backup(
            archive_path=str(wrong),
            db_path=seeded_db,
            photos_dir=seeded_photos,
            output_dir=output_dir,
        )


def test_restore_rejects_corrupted_archive(seeded_db, seeded_photos, output_dir, tmp_path):
    corrupted = tmp_path / 'garbage.tar.gz'
    corrupted.write_bytes(b'not a real tarball at all')
    with pytest.raises(BackupError, match='corrupted'):
        restore_backup(
            archive_path=str(corrupted),
            db_path=seeded_db,
            photos_dir=seeded_photos,
            output_dir=output_dir,
        )


# ---------------------------------------------------------------------------
# _safe_extract — security
# ---------------------------------------------------------------------------


def _build_evil_tar(tmp_path, members):
    """Return a path to a tar.gz containing the given (arcname, TarInfo) pairs.

    Each ``members`` entry is a callable that receives an empty TarInfo and
    returns the configured TarInfo and its payload (bytes or None).
    """
    path = tmp_path / 'evil.tar.gz'
    with tarfile.open(str(path), 'w:gz') as tar:
        for configure in members:
            info, payload = configure()
            if payload is not None:
                tar.addfile(info, BytesIO(payload))
            else:
                tar.addfile(info)
    return str(path)


def test_safe_extract_rejects_traversal(tmp_path):
    def traversal_member():
        info = tarfile.TarInfo(name='../outside.txt')
        info.size = 4
        info.type = tarfile.REGTYPE
        return info, b'evil'

    evil = _build_evil_tar(tmp_path, [traversal_member])
    target = tmp_path / 'target'
    target.mkdir()
    with tarfile.open(evil, 'r:gz') as tar, pytest.raises(BackupSecurityError, match='traversal'):
        _safe_extract(tar, str(target))


def test_safe_extract_rejects_absolute_path(tmp_path):
    def absolute_member():
        info = tarfile.TarInfo(name='/etc/passwd')
        info.size = 4
        info.type = tarfile.REGTYPE
        return info, b'evil'

    evil = _build_evil_tar(tmp_path, [absolute_member])
    target = tmp_path / 'target'
    target.mkdir()
    with tarfile.open(evil, 'r:gz') as tar, pytest.raises(BackupSecurityError, match='absolute'):
        _safe_extract(tar, str(target))


def test_safe_extract_rejects_symlink(tmp_path):
    def symlink_member():
        info = tarfile.TarInfo(name='link')
        info.type = tarfile.SYMTYPE
        info.linkname = '/etc/passwd'
        return info, None

    evil = _build_evil_tar(tmp_path, [symlink_member])
    target = tmp_path / 'target'
    target.mkdir()
    with tarfile.open(evil, 'r:gz') as tar, pytest.raises(BackupSecurityError, match='non-regular'):
        _safe_extract(tar, str(target))


def test_safe_extract_accepts_clean_archive(tmp_path):
    # Build a clean archive via create_backup, then re-extract with _safe_extract
    db = tmp_path / 'site.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE t (x INTEGER)')
    conn.commit()
    conn.close()

    out = tmp_path / 'out'
    out.mkdir()
    archive = create_backup(
        db_path=str(db),
        photos_dir=None,
        config_path=None,
        output_dir=str(out),
        db_only=True,
    )

    target = tmp_path / 'extracted'
    target.mkdir()
    with tarfile.open(archive, 'r:gz') as tar:
        _safe_extract(tar, str(target))

    assert os.path.isfile(os.path.join(target, 'db', 'site.db'))


# ---------------------------------------------------------------------------
# Regression: atomic rename makes .tmp invisible to list_backups
# ---------------------------------------------------------------------------


def test_tmp_file_is_not_listed(seeded_db, output_dir):
    # A half-written .tmp file with no corresponding final archive
    tmp_name = f'{ARCHIVE_PREFIX}20260401-000000{TMP_SUFFIX}'
    with open(os.path.join(output_dir, tmp_name), 'wb') as f:
        f.write(b'\x1f\x8bpartial')

    assert list_backups(output_dir) == []


# ---------------------------------------------------------------------------
# Phase 21.5 — deep round-trip guarantees
#
# The Phase 17.1 tests above cover the happy path (one row, one photo,
# one config) at breadth. The upgrade-survivability story (Phase 21.5)
# needs one more property: a backup taken of a live site must round-trip
# *every* row of *every* user table — not just the ones the original
# fixtures exercise. These tests populate a realistic, multi-table DB
# + photo set, archive it, wipe, restore, and prove the post-restore
# state is byte-exact at the DB-content and photo-bytes levels.
# ---------------------------------------------------------------------------


def _hash_table(conn, table):
    """Return a deterministic tuple of every row in ``table``.

    We sort by rowid-like columns so differing iteration order doesn't
    surface as a spurious diff. ``sqlite_sequence`` and any internal
    FTS shadow tables are skipped by the caller, not here.
    """
    rows = conn.execute(
        f'SELECT * FROM "{table}" ORDER BY rowid'  # noqa: S608 — test helper; table name is read from sqlite_master on the fixture-owned DB.
    ).fetchall()
    return tuple(tuple(r) for r in rows)


def _snapshot_all_user_data(db_path):
    """Hash every user table's contents.

    Returns ``{table_name: tuple_of_rows}``. Skips SQLite-internal
    tables and FTS shadow tables — those are derived from the trigger
    wiring in the source tables and don't define the canonical state
    we care about for a restore.
    """
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE '%_data' AND name NOT LIKE '%_idx' "
                "AND name NOT LIKE '%_config' AND name NOT LIKE '%_content' "
                "AND name NOT LIKE '%_docsize'"
            ).fetchall()
        ]
        return {t: _hash_table(conn, t) for t in tables}
    finally:
        conn.close()


@pytest.fixture
def multi_table_db(tmp_path):
    """Return a path to a richly-populated SQLite file.

    Five user tables, each with a handful of rows covering interesting
    content types (TEXT, INTEGER, BLOB, NULLable columns, rows with
    embedded quotes / unicode). Deliberately no JOINs or FKs — the goal
    is to prove the archive preserves content, not to re-test the
    schema integrity the migrate runner already covers.
    """
    path = tmp_path / 'rich.db'
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            joined_at TEXT
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            title TEXT,
            body TEXT
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE blobs (
            id INTEGER PRIMARY KEY,
            payload BLOB
        );
        CREATE TABLE unicode_notes (
            id INTEGER PRIMARY KEY,
            text TEXT
        );
    """)
    conn.execute(
        'INSERT INTO users (name, email, joined_at) VALUES '
        "('Alice', 'alice@example.com', '2026-01-01T00:00:00Z'),"
        "('Bob', 'bob@example.com', '2026-02-02T00:00:00Z'),"
        "('Carol', NULL, '2026-03-03T00:00:00Z')"
    )
    conn.execute(
        'INSERT INTO posts (user_id, title, body) VALUES '
        "(1, 'Hello', 'World'),"
        "(2, 'Quote: ''Don''t'' worry', 'Body with ''apostrophes'''),"
        "(3, 'Newlines', 'line1\nline2\nline3')"
    )
    conn.execute(
        'INSERT INTO settings (key, value) VALUES '
        "('site_title', 'Round Trip'),"
        "('blog_enabled', 'true'),"
        "('null_value', '')"
    )
    conn.execute('INSERT INTO blobs (payload) VALUES (?)', (bytes(range(256)),))
    conn.execute(
        'INSERT INTO unicode_notes (text) VALUES '
        "('日本語のテキスト'),"
        "('العربية مع علامات'),"
        "('Emoji: 🚀 🌟 ✨')"
    )
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def photos_with_bytes(tmp_path):
    """A photos dir with three files whose bytes we assert round-trip."""
    photos = tmp_path / 'photos_bytes'
    photos.mkdir()
    fixtures = {
        'alpha.jpg': b'\xff\xd8\xff\xe0' + (b'A' * 100),
        'beta.png': b'\x89PNG\r\n\x1a\n' + (b'B' * 512),
        'gamma.webp': bytes(range(256)) * 4,  # 1024 bytes, full byte-range
    }
    for name, body in fixtures.items():
        (photos / name).write_bytes(body)
    return str(photos), fixtures


def test_round_trip_preserves_every_row(
    multi_table_db, seeded_photos, seeded_config, output_dir, tmp_path
):
    """Every user-table row must survive backup → wipe → restore."""
    before = _snapshot_all_user_data(multi_table_db)

    archive = create_backup(
        db_path=multi_table_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=output_dir,
    )

    # Wipe the database. The photos/config aren't part of this assertion
    # — we only care that every row of every user table round-trips.
    os.unlink(multi_table_db)

    restore_backup(
        archive_path=archive,
        db_path=multi_table_db,
        photos_dir=seeded_photos,
        output_dir=output_dir,
    )

    after = _snapshot_all_user_data(multi_table_db)
    assert after == before, (
        'Restored DB differs from pre-backup snapshot. Affected tables: '
        + ', '.join(sorted(k for k in before if before[k] != after.get(k)))
    )


def test_round_trip_preserves_blob_bytes(multi_table_db, seeded_photos, output_dir):
    """BLOB columns must round-trip byte-for-byte through tar.gz."""
    conn = sqlite3.connect(multi_table_db)
    original = conn.execute('SELECT payload FROM blobs WHERE id = 1').fetchone()[0]
    conn.close()

    archive = create_backup(
        db_path=multi_table_db,
        photos_dir=None,
        config_path=None,
        output_dir=output_dir,
        db_only=True,
    )
    os.unlink(multi_table_db)
    restore_backup(
        archive_path=archive,
        db_path=multi_table_db,
        photos_dir=seeded_photos,
        output_dir=output_dir,
    )
    conn = sqlite3.connect(multi_table_db)
    restored = conn.execute('SELECT payload FROM blobs WHERE id = 1').fetchone()[0]
    conn.close()

    assert restored == original
    assert len(restored) == 256  # full byte-range preserved


def test_round_trip_preserves_photo_bytes(seeded_db, photos_with_bytes, output_dir):
    """Every file in the photos dir must restore with identical bytes."""
    photos_dir, fixtures = photos_with_bytes

    archive = create_backup(
        db_path=seeded_db,
        photos_dir=photos_dir,
        config_path=None,
        output_dir=output_dir,
    )

    # Wipe photos. DB is untouched — this test isolates the photo path.
    for name in os.listdir(photos_dir):
        os.unlink(os.path.join(photos_dir, name))

    restore_backup(
        archive_path=archive,
        db_path=seeded_db,
        photos_dir=photos_dir,
        output_dir=output_dir,
    )

    for name, expected_bytes in fixtures.items():
        path = os.path.join(photos_dir, name)
        assert os.path.isfile(path), f'Expected photo {name} to be restored'
        with open(path, 'rb') as fh:
            restored_bytes = fh.read()
        assert restored_bytes == expected_bytes, (
            f'Photo {name} restored with different bytes than were backed up'
        )


def test_round_trip_preserves_schema_version(
    multi_table_db, seeded_photos, seeded_config, output_dir
):
    """schema_version rows from the pre-backup DB must be present after
    restore. This is what makes a restored DB upgrade-ready — the
    migrate runner skips already-applied migrations, so if we lost the
    schema_version rows the restored DB would attempt to re-apply every
    migration from scratch against a populated schema and fail.
    """
    conn = sqlite3.connect(multi_table_db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        INSERT OR IGNORE INTO schema_version (version, name) VALUES
            (1, '001_baseline.sql'),
            (2, '002_blog_tables.sql'),
            (11, '011_content_translations.sql');
    """)
    conn.commit()
    conn.close()

    archive = create_backup(
        db_path=multi_table_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=output_dir,
    )
    os.unlink(multi_table_db)
    restore_backup(
        archive_path=archive,
        db_path=multi_table_db,
        photos_dir=seeded_photos,
        output_dir=output_dir,
    )

    conn = sqlite3.connect(multi_table_db)
    versions = {r[0] for r in conn.execute('SELECT version FROM schema_version').fetchall()}
    conn.close()
    assert versions == {1, 2, 11}


def test_round_trip_backup_is_restorable_to_a_different_path(
    multi_table_db, seeded_photos, seeded_config, output_dir, tmp_path
):
    """A backup made on host A should restore to a fresh path on host B.
    Mirrors the cold-recovery workflow in docs/UPGRADE.md — the archive
    carries its own copy of the DB at ``db/<basename>``, which
    ``restore_backup`` extracts to whatever path the caller points it
    at.
    """
    # Snapshot before the backup — ``create_backup`` writes a
    # ``backup_last_success`` row into the *source* DB AFTER the archive
    # is assembled, so the archive contains the pre-write state.
    # Comparing against a post-backup source snapshot would falsely flag
    # that extra row as a diff.
    source_snapshot = _snapshot_all_user_data(multi_table_db)

    archive = create_backup(
        db_path=multi_table_db,
        photos_dir=seeded_photos,
        config_path=seeded_config,
        output_dir=output_dir,
    )

    new_db = str(tmp_path / 'recovered' / 'rich.db')
    new_photos = str(tmp_path / 'recovered' / 'photos')
    os.makedirs(os.path.dirname(new_db), exist_ok=True)
    os.makedirs(new_photos, exist_ok=True)

    restore_backup(
        archive_path=archive,
        db_path=new_db,
        photos_dir=new_photos,
        output_dir=output_dir,
    )

    recovered = _snapshot_all_user_data(new_db)
    assert recovered == source_snapshot
