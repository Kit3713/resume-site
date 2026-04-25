"""
Photo Processing and Serving Service

Handles two responsibilities:
1. Upload processing: Validates, saves, and optimizes uploaded images
   using Pillow (resizing images larger than 2000px).
2. File serving: Serves photo files from the configured storage directory
   via Flask's send_from_directory for proper caching and content types.

Security:
- File extension whitelist (.jpg, .jpeg, .png, .gif, .webp only).
- Magic byte validation: verifies the file's actual content matches its
  claimed extension, preventing disguised executables.
- Null byte rejection: filenames containing null bytes are rejected to
  prevent null byte injection attacks.
- File size limit: enforced before writing to disk (configurable via
  max_upload_size in config.yaml, default 10 MB).
- UUID-based storage filenames prevent path traversal.

Storage layout:
  photos/
    <uuid>.jpg       — Optimized image (resized if > 2000px)
    <uuid>.png       — PNG images are kept as PNG but optimized
    <uuid>.webp      — WebP images optimized at 85% quality
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import uuid
from typing import Any

from flask import Response, abort, current_app, send_from_directory
from PIL import Image
from werkzeug.datastructures import FileStorage

_log = logging.getLogger('app.photos')

# Magic byte signatures for each allowed image format.
# These are the first N bytes of a valid file of that type.
_MAGIC_BYTES = {
    '.jpg': [b'\xff\xd8\xff'],
    '.jpeg': [b'\xff\xd8\xff'],
    '.png': [b'\x89PNG\r\n\x1a\n'],
    '.gif': [b'GIF87a', b'GIF89a'],
    '.webp': [b'RIFF'],  # Full check: RIFF....WEBP (bytes 8-11 = "WEBP")
}

_DEFAULT_MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


def _bump_disk_usage_cache(delta_bytes: int) -> None:
    """Adjust the cached photo-directory total by ``delta_bytes``.

    Phase 26.5 (#36) — keeps ``photos_disk_usage_bytes`` in step with
    upload / delete activity so the /metrics gauge can be served in
    O(1) instead of walking the photo tree on every Prometheus scrape.
    A downward drift past zero is clamped to zero so a missed-bump bug
    can't take the gauge negative; reconciliation in ``manage.py
    purge-all`` bounds steady-state drift to that cadence.

    Wrapped in ``contextlib.suppress`` because gauge accuracy must
    never block the surrounding upload / delete — a slow or unhealthy
    DB at bump time would otherwise turn a Prometheus nicety into a
    user-facing 500.
    """
    import contextlib

    if delta_bytes == 0:
        return
    with contextlib.suppress(Exception):
        from app.db import get_db
        from app.services.settings_svc import set_one

        db = get_db()
        row = db.execute(
            'SELECT value FROM settings WHERE key = ?',
            ('photos_disk_usage_bytes',),
        ).fetchone()
        try:
            current = int(row['value']) if row else 0
        except (TypeError, ValueError):
            current = 0
        new_total = max(0, current + delta_bytes)
        # ``set_one`` runs the upsert, commits, and busts the TTL cache
        # so /metrics sees the fresh total without waiting on the 30 s
        # window.
        set_one(db, 'photos_disk_usage_bytes', new_total)


def _photo_storage_total_bytes(photo_dir: str) -> int:
    """Walk ``photo_dir`` once and return the total bytes of all files.

    Used by the `/metrics` first-init fall-back and the
    ``manage.py purge-all`` reconciliation step. Centralised here so
    the metrics route doesn't have to import ``os.walk`` plumbing and
    so tests can stub a single function to verify it isn't called on
    the request hot path.
    """
    if not photo_dir or not os.path.isdir(photo_dir):
        return 0
    return sum(
        os.path.getsize(os.path.join(dirpath, f))
        for dirpath, _dirnames, filenames in os.walk(photo_dir)
        for f in filenames
    )


def _get_photo_dir():
    """Resolve the photo storage directory to an absolute path."""
    photo_dir = current_app.config['PHOTO_STORAGE']
    if not os.path.isabs(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            photo_dir,
        )
    return photo_dir


def _validate_magic_bytes(file_storage, ext):
    """Verify the file's magic bytes match its claimed extension.

    Reads the first 12 bytes without consuming the stream (seeks back
    to the start after reading). Returns True if the bytes match the
    expected signature for the given extension, False otherwise.
    """
    signatures = _MAGIC_BYTES.get(ext)
    if not signatures:
        return False

    header = file_storage.read(12)
    file_storage.seek(0)

    if not header:
        return False

    for sig in signatures:
        if header[: len(sig)] == sig:
            # Extra check for WebP: bytes 8-12 must be "WEBP"
            return not (ext == '.webp' and header[8:12] != b'WEBP')

    return False


def _check_file_size(file_storage):
    """Check the uploaded file's size against the configured limit.

    Seeks to the end to determine size, then seeks back to the start.
    Returns (size_bytes, error_message). error_message is None if OK.
    """
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)

    max_size = current_app.config.get('MAX_UPLOAD_SIZE', _DEFAULT_MAX_UPLOAD_SIZE)
    if isinstance(max_size, str):
        max_size = int(max_size)

    if size > max_size:
        max_mb = max_size / (1024 * 1024)
        return size, f'File exceeds maximum upload size ({max_mb:.0f} MB).'

    return size, None


def process_upload(file_storage: FileStorage) -> dict[str, Any] | str | None:
    """Process an uploaded photo: validate, save to disk, and optimize.

    The upload workflow:
    1. Reject filenames containing null bytes.
    2. Validate the file extension (jpg, png, gif, webp only).
    3. Verify magic bytes match the claimed extension.
    4. Check file size against the configured limit.
    5. Generate a UUID-based storage filename to prevent collisions.
    6. Save to the photo storage directory.
    7. If the image exceeds 2000px on any dimension, resize it down.
    8. Return metadata dict for database insertion.

    Args:
        file_storage: A Werkzeug FileStorage object from request.files.

    Returns:
        dict with keys: storage_name, filename, mime_type, width, height,
        file_size — on success.
        None — if the file type is invalid.
        str — if there's a specific error message (size limit, magic bytes).
    """
    filename = file_storage.filename or 'upload.jpg'

    # Reject filenames with null bytes (null byte injection attack)
    if '\x00' in filename:
        return 'Invalid filename.'

    ext = os.path.splitext(filename)[1].lower() or '.jpg'

    # Whitelist of allowed image extensions
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        return None

    # Verify magic bytes match the claimed file type
    if not _validate_magic_bytes(file_storage, ext):
        return 'File content does not match its extension.'

    # Check file size before writing to disk
    reported_size, size_error = _check_file_size(file_storage)
    if size_error:
        return size_error

    # Generate a unique storage filename (UUID prevents collisions and path traversal)
    storage_name = f'{uuid.uuid4().hex}{ext}'

    photo_dir = _get_photo_dir()
    os.makedirs(photo_dir, exist_ok=True)

    # Phase 13.7: Upload quarantine — save to a temp file first, validate,
    # then move to the final location. Failed uploads leave no partial files.
    quarantine_fd, quarantine_path = tempfile.mkstemp(suffix=ext, dir=photo_dir)
    try:
        os.close(quarantine_fd)
        file_storage.save(quarantine_path)

        # Optional antivirus scan (Phase 13.7)
        scan_error = _run_antivirus_scan(quarantine_path)
        if scan_error:
            return scan_error

        file_path = os.path.join(photo_dir, storage_name)
        file_size = os.path.getsize(quarantine_path)

        # Optimize the image with Pillow.
        # EXIF handling (Phase 13.7): by default Pillow's save() drops EXIF
        # metadata (GPS, camera model, timestamps). The upload_preserve_exif
        # setting opts in to keeping it.
        preserve_exif = _should_preserve_exif()

        # Phase 18.7 — we reject uploads Pillow can't fully parse.
        # Historically we swallowed ``OSError`` and set ``width=height=None``,
        # letting the file promote anyway. That left corrupt / truncated
        # images in storage, broke responsive-variant generation, and
        # surfaced as missing thumbnails on the public site. Cleaner to
        # reject up front — the caller returns a user-friendly error
        # and the ``finally`` block deletes the quarantine file.
        try:
            with Image.open(quarantine_path) as img:
                # Phase 26.4: Pillow's Image.draft() asks libjpeg-turbo to
                # emit a smaller image during DCT decoding. On 24 MP DSLR
                # JPEGs this is documented to be 4-8× faster than decoding
                # at full size and resizing afterwards. The full pipeline
                # (resize -> strip EXIF -> save) works the same on the
                # already-downscaled buffer.
                max_dim = 2000
                if img.format == 'JPEG':
                    img.draft('RGB', (max_dim, max_dim))

                width, height = img.size
                exif_data = img.info.get('exif') if preserve_exif else None

                if width > max_dim or height > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.LANCZOS)

                save_kwargs: dict[str, Any] = {}
                if exif_data:
                    save_kwargs['exif'] = exif_data

                if ext in ('.jpg', '.jpeg'):
                    img.save(
                        quarantine_path,
                        'JPEG',
                        quality=85,
                        optimize=True,
                        progressive=True,
                        **save_kwargs,
                    )
                elif ext == '.png':
                    img.save(quarantine_path, 'PNG', optimize=True)
                elif ext == '.webp':
                    img.save(quarantine_path, 'WebP', quality=85, **save_kwargs)

                width, height = img.size
                file_size = os.path.getsize(quarantine_path)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            _log.warning(
                'rejecting corrupt/truncated upload %r: %s',
                filename,
                exc,
            )
            return 'Image file is corrupt or truncated.'

        # Quarantine passed — promote to final location
        os.replace(quarantine_path, file_path)
        quarantine_path = None  # prevent cleanup

        # Phase 12.3: Generate responsive variants (640w, 1024w) + WebP
        _generate_responsive_variants(file_path, storage_name, ext, photo_dir)
    finally:
        if quarantine_path and os.path.exists(quarantine_path):
            os.unlink(quarantine_path)

    # Phase 26.5 (#36): bump the cached photo-directory total by the
    # full delta written to disk for this upload — primary file plus
    # whatever variants `_generate_responsive_variants` actually
    # produced. Stat'ing the handful of UUID-prefixed siblings is
    # O(variants), unlike the O(directory) walk we used to do on
    # every /metrics scrape.
    _bump_disk_usage_cache(_stat_storage_files(photo_dir, storage_name))

    # Map file extensions to MIME types
    mime_map = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
    }

    return {
        'storage_name': storage_name,
        'filename': filename,
        'mime_type': mime_map.get(ext, 'image/jpeg'),
        'width': width,
        'height': height,
        'file_size': file_size,
    }


def _variant_storage_names(storage_name: str) -> list[str]:
    """Return the storage_name plus every responsive-variant filename.

    Single source of truth for the upload / delete / stat call sites
    that need to enumerate every on-disk file backing a single photo
    row. Variants that haven't been generated (``_generate_responsive_variants``
    skips upscales for narrow originals) won't exist on disk; callers
    use ``os.path.exists`` / ``os.path.getsize`` to skip those.
    """
    base, ext = os.path.splitext(storage_name)
    return [
        storage_name,
        f'{base}.webp',
        *[f'{base}_{w}w{ext}' for w in _RESPONSIVE_WIDTHS],
    ]


def _stat_storage_files(photo_dir: str, storage_name: str) -> int:
    """Sum the on-disk bytes for ``storage_name`` plus its responsive variants.

    Used by the upload cache-bump path so the gauge moves in lockstep
    with what landed on disk, without paying for an ``os.walk`` of
    the whole photo tree.
    """
    total = 0
    for name in _variant_storage_names(storage_name):
        path = os.path.join(photo_dir, name)
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total


_RESPONSIVE_WIDTHS = (640, 1024)


def _generate_responsive_variants(
    file_path: str, storage_name: str, ext: str, photo_dir: str
) -> None:
    """Generate smaller responsive variants and a WebP version of the uploaded image.

    Creates ``<uuid>_640w.<ext>``, ``<uuid>_1024w.<ext>`` and
    ``<uuid>.webp`` (if the source isn't already WebP).
    Failures are logged but never propagate — the full-size original
    is always available as a fallback.
    """
    if ext == '.gif':
        return

    base = os.path.splitext(storage_name)[0]

    try:
        with Image.open(file_path) as img:
            src_width = img.size[0]

            for w in _RESPONSIVE_WIDTHS:
                if src_width <= w:
                    continue
                variant = img.copy()
                ratio = w / src_width
                new_h = int(img.size[1] * ratio)
                variant = variant.resize((w, new_h), Image.LANCZOS)

                variant_name = f'{base}_{w}w{ext}'
                variant_path = os.path.join(photo_dir, variant_name)
                if ext in ('.jpg', '.jpeg'):
                    variant.save(variant_path, 'JPEG', quality=80, optimize=True, progressive=True)
                elif ext == '.png':
                    variant.save(variant_path, 'PNG', optimize=True)
                elif ext == '.webp':
                    variant.save(variant_path, 'WebP', quality=80)

            if ext != '.webp':
                webp_name = f'{base}.webp'
                webp_path = os.path.join(photo_dir, webp_name)
                img.save(webp_path, 'WebP', quality=80)
    except Exception:  # noqa: BLE001 — variants are optional; never break the upload
        _log.warning('failed to generate responsive variants for %s', storage_name)


def get_srcset_urls(storage_name: str) -> dict[str, str | None]:
    """Return URLs for responsive variants of a photo.

    Returns a dict with keys: ``original``, ``webp``, ``w640``, ``w1024``.
    Missing variants return None.
    """
    photo_dir = _get_photo_dir()
    base, ext = os.path.splitext(storage_name)

    result: dict[str, str | None] = {
        'original': storage_name,
        'webp': None,
        'w640': None,
        'w1024': None,
    }

    webp_name = f'{base}.webp'
    if ext != '.webp' and os.path.isfile(os.path.join(photo_dir, webp_name)):
        result['webp'] = webp_name

    for w in _RESPONSIVE_WIDTHS:
        variant_name = f'{base}_{w}w{ext}'
        if os.path.isfile(os.path.join(photo_dir, variant_name)):
            result[f'w{w}'] = variant_name

    return result


def _run_antivirus_scan(file_path: str) -> str | None:
    """Run the configured antivirus scanner on a file, if configured.

    Returns an error message string if the scan rejects the file,
    None if the scan passes or is not configured.
    """
    import contextlib

    from app.services.settings_svc import get_all_cached

    settings = {}
    with contextlib.suppress(Exception):
        from app.db import get_db

        db = get_db()
        settings = get_all_cached(db, current_app.config['DATABASE_PATH'])

    scan_cmd = settings.get('upload_scan_command', '').strip()
    if not scan_cmd:
        return None

    try:
        result = subprocess.run(  # noqa: S603 — command is admin-configured, not user input
            [scan_cmd, file_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            _log.warning('antivirus scan rejected file: cmd=%s path=%s', scan_cmd, file_path)
            return 'File rejected by antivirus scan.'
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.error('antivirus scan failed: cmd=%s error=%s', scan_cmd, exc)
        return 'Antivirus scan failed. Upload rejected for safety.'

    return None


def _should_preserve_exif() -> bool:
    """Check whether the upload_preserve_exif setting is enabled."""
    import contextlib

    from app.services.settings_svc import get_all_cached

    settings = {}
    with contextlib.suppress(Exception):
        from app.db import get_db

        db = get_db()
        settings = get_all_cached(db, current_app.config['DATABASE_PATH'])

    return str(settings.get('upload_preserve_exif', 'false')).lower() in {
        '1',
        'true',
        'yes',
        'on',
    }


def serve_photo(storage_name: str) -> Response:
    """Serve a photo file from the configured storage directory.

    Uses Flask's send_from_directory for proper Content-Type headers,
    cache control, and security (prevents path traversal).

    Args:
        storage_name: The UUID-based filename stored in the photos table.

    Returns:
        Response: The file response, or aborts with 404 if not found.
    """
    photo_dir = _get_photo_dir()
    if not os.path.exists(os.path.join(photo_dir, storage_name)):
        abort(404)
    return send_from_directory(photo_dir, storage_name)


def delete_photo_file(storage_name: str) -> None:
    """Delete a photo file and its responsive variants from the storage directory.

    Called by the admin photo delete route after removing the database record.
    Silently handles the case where the file doesn't exist (already deleted
    or never successfully saved).

    Args:
        storage_name: The UUID-based filename to delete.
    """
    import contextlib

    photo_dir = _get_photo_dir()
    # Phase 26.5 (#36): tally the bytes about to disappear *before*
    # the unlinks so the disk-usage gauge can be debited by the same
    # delta. Stat'ing siblings that don't exist is fine — getsize
    # raises OSError and we skip silently.
    deleted_bytes = 0
    for name in _variant_storage_names(storage_name):
        path = os.path.join(photo_dir, name)
        if os.path.exists(path):
            with contextlib.suppress(OSError):
                deleted_bytes += os.path.getsize(path)
            os.remove(path)
    if deleted_bytes:
        _bump_disk_usage_cache(-deleted_bytes)
