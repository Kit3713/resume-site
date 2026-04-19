"""
Edge-case tests for photo uploads — Phase 18.13.

Covers upload-size boundaries, concurrent uploads, Unicode filenames, and
every allowed image format. Exercises both the service-level
``process_upload`` helper (faster, deterministic) and the ``POST
/api/v1/portfolio`` HTTP surface (catches middleware + request-body
parsing regressions).
"""

from __future__ import annotations

import io
import os
import threading

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def no_rate_limits(app):
    app.config['RATELIMIT_ENABLED'] = False
    yield
    app.config['RATELIMIT_ENABLED'] = True


@pytest.fixture
def api_write_token(app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        return generate_token(get_db(), name='photos-edge', scope='read,write').raw


# ---------------------------------------------------------------------------
# Image builders — small valid images for each allowed format
# ---------------------------------------------------------------------------


def _build_image(fmt: str, width: int = 50, height: int = 50) -> bytes:
    img = Image.new('RGB', (width, height), color='blue')
    buf = io.BytesIO()
    # JPEG doesn't support alpha; WebP/PNG/GIF do, but RGB is fine everywhere.
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf.getvalue()


def _build_noisy_image(fmt: str, width: int = 800, height: int = 600) -> bytes:
    """Build an image with per-pixel noise so it doesn't compress well.

    A flat-color image of the same dimensions compresses to a few KB. The
    size-limit test needs a body that blows past a tiny cap regardless of
    encoder efficiency.
    """
    import os as _os

    img = Image.frombytes('RGB', (width, height), _os.urandom(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Valid image types — every allowed extension round-trips through process_upload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'ext,pillow_fmt',
    [
        ('.jpg', 'JPEG'),
        ('.jpeg', 'JPEG'),
        ('.png', 'PNG'),
        ('.gif', 'GIF'),
        ('.webp', 'WEBP'),
    ],
)
def test_every_allowed_format_uploads_cleanly(app, ext, pillow_fmt):
    from app.services.photos import process_upload

    data = _build_image(pillow_fmt)
    storage = FileStorage(io.BytesIO(data), filename=f'pic{ext}')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, dict), f'{ext} rejected: {result!r}'
    assert result['mime_type'].startswith('image/')
    # Storage name is a UUID hex + extension — never the original filename
    assert result['storage_name'].endswith(ext)
    assert result['filename'] == f'pic{ext}'


# ---------------------------------------------------------------------------
# Disallowed extensions — empty-string, unknown, double-extensions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'filename',
    [
        'file.exe',  # executable
        'file.php',  # webshell-ish
        'file.svg',  # XML-based image — intentionally not on allowlist
        'file.bmp',  # older format, not allowlisted
        'file.tiff',  # multi-page, not allowlisted
        'file.html',  # plain HTML
    ],
)
def test_disallowed_extensions_return_none(app, filename):
    """Extensions outside the allowlist return ``None`` so the API wraps
    them in a ``VALIDATION_ERROR`` envelope with reason ``invalid_type``.
    """
    from app.services.photos import process_upload

    storage = FileStorage(io.BytesIO(b'anything'), filename=filename)
    with app.app_context():
        result = process_upload(storage)
    assert result is None, f'{filename!r} accepted: {result!r}'


@pytest.mark.parametrize('filename', ['file', 'README', 'noext'])
def test_extensionless_filename_rejected(app, filename):
    """Files with no extension default to ``.jpg`` then fail the magic-byte
    check. The rejection is a string error rather than ``None`` because the
    code treats missing extensions as an implicit JPEG claim.
    """
    from app.services.photos import process_upload

    storage = FileStorage(io.BytesIO(b'anything'), filename=filename)
    with app.app_context():
        result = process_upload(storage)
    # Either ``None`` (invalid type) or a string error (magic bytes mismatch)
    # is a valid rejection — both result in a 400 from the API layer.
    assert result is None or isinstance(result, str)


def test_disguised_extension_rejected_by_magic_bytes(app):
    """``exploit.exe`` renamed to ``exploit.jpg`` must not survive magic-byte check."""
    from app.services.photos import process_upload

    payload = b'MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00'  # DOS/PE header
    storage = FileStorage(io.BytesIO(payload), filename='exploit.jpg')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, str)
    assert 'content does not match' in result.lower()


def test_webp_header_missing_webp_marker_rejected(app):
    """``RIFF....WEBP`` — bytes 8-11 must be the literal string WEBP."""
    from app.services.photos import process_upload

    fake = b'RIFF____NOTWEBPstuff'
    storage = FileStorage(io.BytesIO(fake), filename='x.webp')
    with app.app_context():
        result = process_upload(storage)
    assert result is not None
    assert isinstance(result, str), 'non-WebP RIFF should be rejected'


# ---------------------------------------------------------------------------
# Size boundaries
# ---------------------------------------------------------------------------


def test_zero_byte_file_rejected(app):
    from app.services.photos import process_upload

    storage = FileStorage(io.BytesIO(b''), filename='empty.jpg')
    with app.app_context():
        result = process_upload(storage)
    # Empty file → magic bytes mismatch → error string
    assert isinstance(result, str)


def test_file_just_under_size_limit_accepted(app):
    from app.services.photos import process_upload

    # Force a small size limit so we can hit "just under" quickly
    app.config['MAX_UPLOAD_SIZE'] = 256 * 1024  # 256 KB
    # A ~120x120 JPEG lands well under 256 KB but is a real image
    data = _build_image('JPEG', width=120, height=120)
    assert len(data) < app.config['MAX_UPLOAD_SIZE']
    storage = FileStorage(io.BytesIO(data), filename='under.jpg')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, dict)


def test_file_over_size_limit_rejected(app):
    from app.services.photos import process_upload

    app.config['MAX_UPLOAD_SIZE'] = 4 * 1024  # 4 KB — tight enough that even
    # a random-noise image decoded as JPEG will exceed it.
    data = _build_noisy_image('JPEG', width=400, height=300)
    assert len(data) > app.config['MAX_UPLOAD_SIZE'], (
        f'test image is only {len(data)} bytes — expected >4 KB'
    )
    storage = FileStorage(io.BytesIO(data), filename='over.jpg')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, str)
    assert 'exceeds' in result.lower()


def test_oversized_dimensions_are_downscaled(app):
    """A 3000x2000 image must be resized to fit within 2000x2000."""
    from app.services.photos import process_upload

    data = _build_image('JPEG', width=3000, height=2000)
    storage = FileStorage(io.BytesIO(data), filename='huge.jpg')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, dict)
    assert max(result['width'], result['height']) <= 2000


# ---------------------------------------------------------------------------
# Filename edge cases
# ---------------------------------------------------------------------------


def test_null_byte_filename_rejected(app):
    from app.services.photos import process_upload

    storage = FileStorage(io.BytesIO(b'\xff\xd8\xff'), filename='mal\x00icious.jpg')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, str)
    assert 'invalid' in result.lower()


@pytest.mark.parametrize(
    'filename',
    [
        'résumé.jpg',  # accented
        '山田.jpg',  # CJK
        '📸.jpg',  # emoji
        'RTL عربية.jpg',  # RTL script
        'spaces in name.jpg',
        'with-dashes.jpg',
        'UPPERCASE.JPG',  # uppercase extension
    ],
)
def test_unicode_filenames_accepted(app, filename):
    from app.services.photos import process_upload

    data = _build_image('JPEG')
    storage = FileStorage(io.BytesIO(data), filename=filename)
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, dict), f'{filename!r} rejected: {result!r}'
    # The original filename is preserved verbatim for the admin UI
    assert result['filename'] == filename
    # The on-disk storage name is a UUID — filename is sanitized away
    assert filename.split('.')[0] not in result['storage_name']


def test_path_traversal_filename_still_stores_under_uuid(app):
    """A filename like ``../../../etc/passwd.jpg`` must not escape the photo dir.

    ``process_upload`` keeps the original filename for display only; the
    on-disk path is always ``<uuid>.<ext>``, so path traversal can't
    smuggle files outside PHOTO_STORAGE.
    """
    from app.services.photos import process_upload

    data = _build_image('JPEG')
    storage = FileStorage(io.BytesIO(data), filename='../../../etc/passwd.jpg')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, dict)
    # Nothing in the storage name should contain any path separator
    assert '/' not in result['storage_name']
    assert '\\' not in result['storage_name']
    assert '..' not in result['storage_name']


def test_extremely_long_filename_accepted(app):
    from app.services.photos import process_upload

    filename = ('x' * 5_000) + '.jpg'
    data = _build_image('JPEG')
    storage = FileStorage(io.BytesIO(data), filename=filename)
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Truncated / corrupt images
# ---------------------------------------------------------------------------


def test_truncated_jpeg_rejected(app):
    """Valid magic bytes but a truncated body — Pillow can't decode."""
    from app.services.photos import process_upload

    # Valid JPEG header but missing the rest
    fake = b'\xff\xd8\xff\xe0' + b'\x00' * 20
    storage = FileStorage(io.BytesIO(fake), filename='bad.jpg')
    with app.app_context():
        result = process_upload(storage)
    assert isinstance(result, str)
    assert 'corrupt' in result.lower() or 'truncat' in result.lower()


def test_decompression_bomb_rejected_gracefully(app):
    """A Pillow ``DecompressionBombError`` on a malicious image must return
    a user-facing error, not 500 up the stack.
    """
    # A 1x1 PNG with header claiming ridiculous dimensions is hard to forge
    # without a library. Instead, mock Image.open to raise the bomb error.
    import unittest.mock

    from app.services.photos import process_upload

    data = _build_image('JPEG')
    storage = FileStorage(io.BytesIO(data), filename='bomb.jpg')
    with (
        app.app_context(),
        unittest.mock.patch(
            'app.services.photos.Image.open',
            side_effect=Image.DecompressionBombError('mock bomb'),
        ),
    ):
        result = process_upload(storage)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# API surface — POST /api/v1/portfolio
# ---------------------------------------------------------------------------


def test_api_upload_without_photo_part_returns_400(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/portfolio',
        data={'title': 'No file here'},
        headers={'Authorization': f'Bearer {api_write_token}'},
        content_type='multipart/form-data',
    )
    assert response.status_code == 400
    assert response.get_json()['details']['field'] == 'photo'


def test_api_upload_invalid_display_tier_returns_400_and_cleans_up(
    app, client, no_rate_limits, api_write_token
):
    data = _build_image('JPEG')
    response = client.post(
        '/api/v1/portfolio',
        data={
            'photo': (io.BytesIO(data), 'valid.jpg'),
            'display_tier': 'bogus-tier',
        },
        headers={'Authorization': f'Bearer {api_write_token}'},
        content_type='multipart/form-data',
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body['details']['field'] == 'display_tier'
    assert 'featured' in body['details']['allowed']
    # Photo dir should contain nothing since the bad tier triggered cleanup
    photo_dir = app.config['PHOTO_STORAGE']
    if os.path.isdir(photo_dir):
        leftover = [n for n in os.listdir(photo_dir) if n != '.gitkeep']
        assert leftover == [], f'leftover files after cleanup: {leftover!r}'


def test_api_upload_accepts_valid_jpeg(client, no_rate_limits, api_write_token):
    data = _build_image('JPEG')
    response = client.post(
        '/api/v1/portfolio',
        data={
            'photo': (io.BytesIO(data), 'ok.jpg'),
            'title': 'A photo',
            'display_tier': 'grid',
        },
        headers={'Authorization': f'Bearer {api_write_token}'},
        content_type='multipart/form-data',
    )
    assert response.status_code == 201
    assert response.get_json()['data']['title'] == 'A photo'


# ---------------------------------------------------------------------------
# Concurrency — the UUID filename guarantees no collision under parallel upload
# ---------------------------------------------------------------------------


def test_concurrent_uploads_get_distinct_storage_names(app, no_rate_limits, api_write_token):
    """N simultaneous uploads must each land at a unique storage path.

    ``_PHOTO_PUBLIC_FIELDS`` deliberately strips ``storage_name`` from the
    API response, so verify uniqueness by querying the DB directly.
    """
    import sqlite3

    successful_ids: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def upload():
        try:
            data = _build_image('JPEG')
            with app.test_client() as c:
                response = c.post(
                    '/api/v1/portfolio',
                    data={
                        'photo': (io.BytesIO(data), 'pic.jpg'),
                        'display_tier': 'grid',
                    },
                    headers={'Authorization': f'Bearer {api_write_token}'},
                    content_type='multipart/form-data',
                )
                if response.status_code == 201:
                    with lock:
                        successful_ids.append(response.get_json()['data']['id'])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=upload) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(successful_ids) == 6

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        storage_names = [
            r[0]
            for r in conn.execute(
                f'SELECT storage_name FROM photos WHERE id IN ({",".join(["?"] * len(successful_ids))})',  # noqa: S608  # nosec B608 — placeholder count derived from list length, values are bound parameters
                successful_ids,
            )
        ]
    finally:
        conn.close()

    assert len(storage_names) == len(set(storage_names)), storage_names
    assert len(storage_names) == 6
