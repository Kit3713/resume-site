"""
Migration Helpers — Phase 21.2 extraction

Pure-Python helpers for inspecting the migration state of a SQLite
database. Originally lived as private functions in ``manage.py`` (the
CLI) but Phase 21.2's readiness probe needs to call them from a Flask
route — and importing ``manage.py`` from the route layer would drag in
``argparse`` plus every CLI subcommand. So the helpers move here, the
CLI re-exports them, and the route imports them directly.

Design contract:

* **Read-only.** None of these functions mutate the database — they
  inspect ``schema_version`` and the ``migrations/`` directory only.
  Mutation (applying migrations) stays in ``manage.py`` since it's
  CLI-driven by design.
* **Stdlib only.** ``os`` + ``sqlite3``. No Flask, no app factory.
  Safe to import from any layer.
* **Forgiving on missing inputs.** Empty migrations directory returns
  ``[]`` rather than raising. A connection without ``schema_version``
  returns an empty applied-versions set rather than tripping
  ``OperationalError`` — :func:`ensure_schema_version_table` is the
  caller's opt-in to materialise the table.
"""

from __future__ import annotations

import os
import sqlite3


def get_migrations_dir() -> str:
    """Return the absolute path to the project's ``migrations/`` directory.

    Resolved relative to this file so it works identically when called
    from the CLI, the route layer, or a test runner with an unusual
    working directory.
    """
    # app/services/migrations.py → app/ → <repo>
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo_root, 'migrations')


def list_migration_files(migrations_dir: str | None = None) -> list[tuple[int, str]]:
    """Return sorted ``(version_int, filename)`` pairs for every migration.

    A migration file qualifies when its name ends in ``.sql`` and starts
    with a digit. The leading number-block (split on the first
    underscore) is the version. Files that fail to parse a version are
    silently skipped — same forgiving contract as the original
    ``manage.py`` helper.

    Args:
        migrations_dir: Override the default. Useful for tests that
            ship a controlled migrations tree.
    """
    if migrations_dir is None:
        migrations_dir = get_migrations_dir()
    if not os.path.isdir(migrations_dir):
        return []
    files = []
    for fname in sorted(os.listdir(migrations_dir)):
        if fname.endswith('.sql') and fname[0].isdigit():
            try:
                version = int(fname.split('_')[0])
                files.append((version, fname))
            except ValueError:
                # Filename starts with a digit but has no parseable
                # version prefix (``9notamigration.sql``) — skip
                # silently to mirror the CLI behaviour.
                continue
    return files


def ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create the ``schema_version`` tracking table when missing.

    Idempotent — wraps ``CREATE TABLE IF NOT EXISTS``. The CLI calls
    this before every migrate; the readiness probe deliberately does
    NOT (the probe is read-only).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    conn.commit()


def get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of version numbers recorded in ``schema_version``.

    Returns ``set()`` when the table doesn't exist, so a caller probing
    a fresh database doesn't have to special-case the
    ``OperationalError``. The CLI calls
    :func:`ensure_schema_version_table` first; the readiness probe
    relies on this fallback to detect "not yet migrated" cleanly.
    """
    try:
        rows = conn.execute('SELECT version FROM schema_version').fetchall()
    except Exception:  # noqa: BLE001 — table missing or DB unreadable; both mean "no versions".
        return set()
    return {row[0] for row in rows}


def get_pending_migrations(
    conn: sqlite3.Connection, migrations_dir: str | None = None
) -> list[tuple[int, str]]:
    """Return ``(version, filename)`` pairs for every migration NOT yet applied.

    Convenience used by the readiness probe. The CLI doesn't call it —
    it has its own apply loop with stricter ordering checks.
    """
    applied = get_applied_versions(conn)
    return [entry for entry in list_migration_files(migrations_dir) if entry[0] not in applied]
