"""
Backup and Restore Service — Phase 17.1

Stdlib-only implementation of full-site backup/restore. Used by the
``manage.py backup`` and ``manage.py restore`` CLI commands, and will be
reused by a future admin-panel "Download backup" button.

Design:
    * SQLite database is captured via the online backup API
      (``sqlite3.Connection.backup``), which is safe to run while the
      app is serving requests — it streams pages under a shared lock
      without blocking writers for more than a few milliseconds per batch.

    * Backups are atomic: we build at ``<name>.tar.gz.tmp`` and rename
      to the final name only on success. A crash or exception leaves no
      partial archive with a legitimate-looking name.

    * Restore always copies the current state to a timestamped sidecar
      (``pre-restore-YYYYMMDD-HHMMSS/``) before extraction — recoverable
      even if the user restored the wrong archive.

    * Tar extraction is validated by ``_safe_extract`` against path
      traversal, absolute paths, and non-regular-file member types
      (symlinks, hardlinks, devices, FIFOs). On Python 3.12+ the
      ``filter='data'`` argument is additionally applied as
      defence-in-depth.

No third-party dependencies are introduced — just ``tarfile``,
``sqlite3``, ``shutil``, and ``datetime`` from the standard library.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import sys
import tarfile
from collections import namedtuple
from datetime import UTC, datetime

ARCHIVE_PREFIX = 'resume-site-backup-'
ARCHIVE_SUFFIX = '.tar.gz'
TMP_SUFFIX = '.tar.gz.tmp'
SIDECAR_PREFIX = 'pre-restore-'

# Issue #89 — backups carry secret_key, password_hash, SMTP credentials,
# and the entire site DB. tarfile/copytree honour the process umask,
# which would otherwise leak these as 0o644. Force operator-only modes
# on every artifact (archive + pre-restore sidecar).
_BACKUP_FILE_MODE = 0o600
_BACKUP_DIR_MODE = 0o700

# Arcnames — the layout inside a backup archive.
_ARCNAME_DB = 'db/site.db'
_ARCNAME_CONFIG = 'config.yaml'
_ARCNAME_PHOTOS_ROOT = 'photos'

BackupEntry = namedtuple('BackupEntry', 'path name size_bytes mtime')


class BackupError(Exception):
    """Raised for any backup or restore failure with a user-facing message."""


class BackupSecurityError(BackupError):
    """Raised when an archive member would escape the target directory or
    is a non-regular-file type that the restore path refuses to materialise.
    """


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _format_timestamp(now):
    """Format ``now`` as ``YYYYMMDD-HHMMSS`` in UTC.

    Args:
        now: A timezone-aware ``datetime``. If ``None`` the current UTC
            time is used.

    Returns:
        str: The formatted timestamp.
    """
    if now is None:
        now = datetime.now(UTC)
    return now.strftime('%Y%m%d-%H%M%S')


# ---------------------------------------------------------------------------
# Permission helpers — Phase v0.3.3-beta-2 #89
# ---------------------------------------------------------------------------


def _lock_down_tree(root):
    """Recursively chmod ``root``: dirs to 0o700, regular files to 0o600.

    Used after ``shutil.copytree`` because copytree honours the source
    file modes (which can be 0o644 from a default umask) — we don't want
    the operator-readable photos sidecar to outlive the backup as a
    world-readable mirror of the original tree.
    """
    if not os.path.isdir(root):
        return
    os.chmod(root, _BACKUP_DIR_MODE)
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            with contextlib.suppress(OSError):
                os.chmod(os.path.join(dirpath, d), _BACKUP_DIR_MODE)
        for f in filenames:
            with contextlib.suppress(OSError):
                os.chmod(os.path.join(dirpath, f), _BACKUP_FILE_MODE)


# ---------------------------------------------------------------------------
# SQLite online backup
# ---------------------------------------------------------------------------


def _sqlite_online_backup(src_path, dst_path):
    """Copy ``src_path`` to ``dst_path`` using SQLite's online backup API.

    The source is opened read-only via URI, so this function is safe to
    run against the live application database. The destination is
    created fresh (any existing file is overwritten).
    """
    src_uri = f'file:{os.path.abspath(src_path)}?mode=ro'
    src = sqlite3.connect(src_uri, uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


# ---------------------------------------------------------------------------
# Tar safety
# ---------------------------------------------------------------------------


def _safe_extract(tar, target_dir):
    """Extract ``tar`` into ``target_dir`` after validating every member.

    Rejects (raises ``BackupSecurityError``):
      * absolute member paths,
      * members containing ``..`` components,
      * members whose resolved destination escapes ``target_dir``,
      * symlinks, hardlinks, device nodes, and FIFOs.

    Only regular files and directories are materialised. On Python 3.12+
    the additional ``filter='data'`` keyword is passed as belt-and-braces
    hardening.
    """
    target_root = os.path.realpath(target_dir)
    members = tar.getmembers()

    for member in members:
        if member.name.startswith('/') or os.path.isabs(member.name):
            raise BackupSecurityError(f'archive contains absolute path: {member.name!r}')
        if '..' in member.name.split('/'):
            raise BackupSecurityError(f'archive contains traversal component: {member.name!r}')
        if not (member.isfile() or member.isdir()):
            raise BackupSecurityError(
                f'archive contains non-regular member {member.name!r} (type={member.type!r})'
            )
        resolved = os.path.realpath(os.path.join(target_root, member.name))
        if resolved != target_root and not resolved.startswith(target_root + os.sep):
            raise BackupSecurityError(f'archive member would escape target: {member.name!r}')

    # Defence-in-depth: use the 3.12+ data filter when available. Our own
    # checks above are the contract; the filter is a secondary gate. Both
    # extractall calls are annotated with `noqa: S202` / `nosec B202` — the
    # static scanners can't see the _safe_extract contract enforced above,
    # but every caller goes through this function.
    try:
        tar.extractall(target_dir, members=members, filter='data')  # noqa: S202  # nosec B202
    except TypeError:
        tar.extractall(target_dir, members=members)  # noqa: S202  # nosec B202


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_backup(
    db_path: str,
    photos_dir: str | None,
    config_path: str | None,
    output_dir: str,
    *,
    db_only: bool = False,
    now: datetime | None = None,
) -> str:
    """Create a timestamped backup archive.

    The archive is written atomically: we build at a ``.tar.gz.tmp``
    sibling and ``os.replace`` to the final name only when the tarball
    is fully written and closed. On any error the temporary file is
    removed so callers never see a half-built archive.

    Args:
        db_path: Path to the live SQLite database. Captured via
            ``sqlite3.Connection.backup`` — safe under live traffic.
        photos_dir: Directory containing uploaded photos. Missing dir
            is logged to stderr and skipped; the backup still succeeds.
        config_path: Path to ``config.yaml``. May be ``None``; unreadable
            or missing is logged and skipped.
        output_dir: Directory to write the archive into. Created if
            necessary.
        db_only: If true, only the database is archived (fast snapshots).
        now: Optional ``datetime`` override used by tests for deterministic
            archive names.

    Returns:
        str: Absolute path of the finalised archive.

    Raises:
        BackupError: on any unrecoverable failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = _format_timestamp(now)
    final_name = f'{ARCHIVE_PREFIX}{timestamp}{ARCHIVE_SUFFIX}'
    final_path = os.path.join(output_dir, final_name)
    tmp_path = final_path[: -len(ARCHIVE_SUFFIX)] + TMP_SUFFIX

    # Stage the DB to a temp file using the online backup API, then
    # add it to the tar by path (so tarfile records correct size + name).
    # The staging file lives inside output_dir so we can clean it up on error.
    staged_db = os.path.join(output_dir, f'.stage-{timestamp}-site.db')

    try:
        _sqlite_online_backup(db_path, staged_db)

        with tarfile.open(tmp_path, 'w:gz') as tar:
            tar.add(staged_db, arcname=_ARCNAME_DB)

            if not db_only:
                if photos_dir and os.path.isdir(photos_dir):
                    tar.add(photos_dir, arcname=_ARCNAME_PHOTOS_ROOT)
                elif photos_dir:
                    print(
                        f'WARNING: photos directory not found: {photos_dir} — skipping.',
                        file=sys.stderr,
                    )

                if config_path and os.path.isfile(config_path):
                    try:
                        tar.add(config_path, arcname=_ARCNAME_CONFIG)
                    except OSError as e:
                        print(
                            f'WARNING: could not read config file {config_path}: {e} — skipping.',
                            file=sys.stderr,
                        )
                elif config_path:
                    print(
                        f'WARNING: config file not found: {config_path} — skipping.',
                        file=sys.stderr,
                    )

        # chmod *before* replace — POSIX rename preserves the source mode,
        # so the final-named file is never world-readable.
        os.chmod(tmp_path, _BACKUP_FILE_MODE)
        os.replace(tmp_path, final_path)
    except Exception:
        # Clean up partial state — never leave a .tmp lying around.
        for leftover in (tmp_path, staged_db):
            with contextlib.suppress(OSError):
                os.unlink(leftover)
        raise
    else:
        with contextlib.suppress(OSError):
            os.unlink(staged_db)

    # Update the diagnostic setting. Failure here must NOT unwind the
    # backup — the archive on disk is what matters. Best-effort only.
    try:
        _record_backup_success(db_path, now=now)
    except Exception as e:  # noqa: BLE001 — diagnostic write, never fatal
        print(
            f'WARNING: backup succeeded but could not update backup_last_success setting: {e}',
            file=sys.stderr,
        )

    # Phase 19.1 event bus — fire backup.completed with a minimal payload
    # (path, size, and the db_only flag). Lazy import so CLI paths that
    # never trigger events don't pay the import cost.
    try:
        from app.events import Events as _Events
        from app.events import emit as _emit

        size_bytes = os.path.getsize(final_path) if os.path.isfile(final_path) else 0
        _emit(
            _Events.BACKUP_COMPLETED,
            archive_path=final_path,
            db_only=db_only,
            size_bytes=size_bytes,
        )
    except Exception as e:  # noqa: BLE001 — event failure never breaks the backup
        print(
            f'WARNING: backup succeeded but event emission failed: {e}',
            file=sys.stderr,
        )

    return final_path


def _record_backup_success(db_path, now=None):
    """Write ``backup_last_success`` = ISO-8601 UTC timestamp into the
    settings table. Not registered in SETTINGS_REGISTRY — this is
    diagnostic metadata consumed by a future admin-dashboard widget,
    not a user-editable toggle.
    """
    if now is None:
        now = datetime.now(UTC)
    iso = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
            ('backup_last_success', iso),
        )
        conn.commit()
    finally:
        conn.close()


def list_backups(output_dir: str) -> list[BackupEntry]:
    """Return archives in ``output_dir`` sorted newest-first.

    Ignores in-flight ``.tar.gz.tmp`` files and ``pre-restore-*``
    sidecar directories. Returns ``[]`` for a missing or empty dir.
    """
    if not os.path.isdir(output_dir):
        return []

    entries = []
    with os.scandir(output_dir) as it:
        for entry in it:
            if not entry.is_file():
                continue
            if not (entry.name.startswith(ARCHIVE_PREFIX) and entry.name.endswith(ARCHIVE_SUFFIX)):
                continue
            stat = entry.stat()
            entries.append(
                BackupEntry(
                    path=entry.path,
                    name=entry.name,
                    size_bytes=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )

    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries


def prune_backups(output_dir: str, keep: int) -> list[str]:
    """Delete all but the ``keep`` newest archives in ``output_dir``.

    Args:
        output_dir: Directory to prune.
        keep: Number of newest archives to retain. Must be ``>= 1``.

    Returns:
        list[str]: Absolute paths of the archives that were deleted.

    Raises:
        ValueError: if ``keep < 1``.
    """
    if keep < 1:
        raise ValueError('--keep must be at least 1')

    all_entries = list_backups(output_dir)
    victims = all_entries[keep:]
    deleted = []
    for entry in victims:
        try:
            os.unlink(entry.path)
            deleted.append(entry.path)
        except OSError as e:
            print(
                f'WARNING: failed to delete {entry.path}: {e}',
                file=sys.stderr,
            )
    return deleted


def restore_backup(
    archive_path: str,
    db_path: str,
    photos_dir: str | None,
    output_dir: str,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> str:
    """Restore the database and photos from ``archive_path``.

    Before extracting, the current DB and photos directory are copied
    into ``output_dir/pre-restore-YYYYMMDD-HHMMSS/`` as a safety net.
    The sidecar is created unconditionally — ``--force`` only gates the
    interactive confirmation prompt at the CLI layer, never the sidecar.

    Args:
        archive_path: Path to the ``.tar.gz`` backup to restore.
        db_path: Destination DB path (will be overwritten).
        photos_dir: Destination photos directory (will be replaced).
        output_dir: Where to put the pre-restore sidecar.
        force: Reserved for CLI interactive-prompt gating; unused here
            (the service always creates the sidecar).
        now: Injectable clock for deterministic sidecar names in tests.

    Returns:
        str: Absolute path of the pre-restore sidecar directory.

    Raises:
        FileNotFoundError: archive doesn't exist.
        BackupError: archive name is wrong, corrupted, or contains
            unsafe members (``BackupSecurityError``).
    """
    del force  # Semantics documented; the service layer never prompts.

    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f'backup file not found: {archive_path}')

    if not archive_path.endswith(ARCHIVE_SUFFIX):
        raise BackupError(f'archive name must end with {ARCHIVE_SUFFIX}: {archive_path!r}')

    os.makedirs(output_dir, exist_ok=True)

    # Safety-net sidecar: copy current state before touching anything.
    sidecar = os.path.join(output_dir, f'{SIDECAR_PREFIX}{_format_timestamp(now)}')
    os.makedirs(sidecar, exist_ok=True)
    os.chmod(sidecar, _BACKUP_DIR_MODE)
    if os.path.isfile(db_path):
        sidecar_db = os.path.join(sidecar, os.path.basename(db_path))
        shutil.copy2(db_path, sidecar_db)
        os.chmod(sidecar_db, _BACKUP_FILE_MODE)
    if photos_dir and os.path.isdir(photos_dir):
        sidecar_photos = os.path.join(sidecar, _ARCNAME_PHOTOS_ROOT)
        # copytree honours source modes — re-chmod the whole tree.
        shutil.copytree(photos_dir, sidecar_photos, symlinks=False)
        _lock_down_tree(sidecar_photos)

    # Extract to a staging directory so we can swap atomically.
    staging = os.path.join(output_dir, f'.restore-staging-{_format_timestamp(now)}')
    if os.path.exists(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)

    try:
        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                _safe_extract(tar, staging)
        except tarfile.ReadError as e:
            raise BackupError(f'archive is corrupted or not a gzip tar: {archive_path}') from e

        # DB — if the archive carried one, move it into place.
        extracted_db = os.path.join(staging, _ARCNAME_DB)
        if os.path.isfile(extracted_db):
            os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
            shutil.copy2(extracted_db, db_path)

        # Photos — wipe existing tree and restore from the archive if present.
        extracted_photos = os.path.join(staging, _ARCNAME_PHOTOS_ROOT)
        if os.path.isdir(extracted_photos):
            if photos_dir and os.path.isdir(photos_dir):
                shutil.rmtree(photos_dir)
            if photos_dir:
                shutil.copytree(extracted_photos, photos_dir, symlinks=False)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return sidecar
