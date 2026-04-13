"""Tests for the Pillow image pipeline (app/services/photos.py).

Focuses on behaviors that are easy to regress silently:
    * EXIF metadata is stripped on upload (privacy).
    * JPEG outputs are progressive.
    * Small images are still re-encoded (previous pipeline only re-saved
      when downscaling, leaving GPS data in any image < 2000px).
"""

import io

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.services.photos import process_upload


def _build_jpeg_with_exif(width=800, height=600):
    """Return a JPEG byte stream carrying a plausible EXIF block.

    Uses Pillow's built-in EXIF API rather than the `piexif` package so
    the test has no extra runtime dependency.
    """
    img = Image.new('RGB', (width, height), color='red')
    buf = io.BytesIO()
    # Pillow's Exif() accepts standard EXIF tags; 0x010f = Make, 0x0110 = Model.
    exif = img.getexif()
    exif[0x010F] = 'TestCamera Inc.'
    exif[0x0110] = 'Secret Phone Model'
    img.save(buf, format='JPEG', exif=exif.tobytes())
    buf.seek(0)
    return buf


def _read_back(app, storage_name):
    """Open the processed photo as a fresh PIL Image for assertions."""
    photo_path = app.config['PHOTO_STORAGE'] + '/' + storage_name
    return Image.open(photo_path)


class TestExifStripping:
    def test_uploaded_jpeg_has_no_exif(self, app):
        """Regression test for Phase 13.7 privacy deliverable: GPS/device
        metadata embedded by phone cameras must not survive upload."""
        buf = _build_jpeg_with_exif()
        storage = FileStorage(buf, filename='camera.jpg', content_type='image/jpeg')

        with app.app_context():
            result = process_upload(storage)

        assert isinstance(result, dict), f'process_upload returned: {result!r}'

        with _read_back(app, result['storage_name']) as img:
            exif = img.getexif()
            # Pillow returns an empty-ish Exif object when no metadata is present.
            # Any populated tag count would indicate leakage.
            assert len(exif) == 0, f'EXIF survived: {dict(exif)!r}'


class TestProgressiveJpeg:
    def test_uploaded_jpeg_is_progressive(self, app):
        """Progressive JPEGs render in passes on slow connections, improving
        perceived load. The old pipeline saved baseline JPEG."""
        buf = _build_jpeg_with_exif(width=400, height=300)
        storage = FileStorage(buf, filename='small.jpg', content_type='image/jpeg')

        with app.app_context():
            result = process_upload(storage)

        assert isinstance(result, dict)
        with _read_back(app, result['storage_name']) as img:
            # Pillow exposes JPEG progressive flag via info['progressive']
            assert img.info.get('progressive') == 1, f'not progressive: {img.info!r}'


class TestSmallImagesAreReencoded:
    def test_small_image_is_not_byte_identical_to_upload(self, app):
        """Previously, images under 2000px skipped Pillow's save() path
        entirely — meaning EXIF and baseline-encoded JPEG passed through
        untouched. Ensure we always re-encode."""
        buf = _build_jpeg_with_exif(width=400, height=300)
        original_bytes = buf.getvalue()

        storage = FileStorage(
            io.BytesIO(original_bytes), filename='tiny.jpg', content_type='image/jpeg'
        )

        with app.app_context():
            result = process_upload(storage)

        assert isinstance(result, dict)
        with open(app.config['PHOTO_STORAGE'] + '/' + result['storage_name'], 'rb') as f:
            stored_bytes = f.read()
        assert stored_bytes != original_bytes, 'file was not re-encoded'


class TestRejectsInvalidUploads:
    def test_bad_extension_returns_none(self, app):
        storage = FileStorage(io.BytesIO(b'nope'), filename='x.exe')
        with app.app_context():
            result = process_upload(storage)
        assert result is None

    def test_mismatched_magic_bytes_returns_error(self, app):
        # A .jpg filename with non-JPEG magic bytes
        storage = FileStorage(io.BytesIO(b'not-a-jpeg'), filename='fake.jpg')
        with app.app_context():
            result = process_upload(storage)
        assert isinstance(result, str)
        assert 'content does not match' in result.lower()


if __name__ == '__main__':
    # Allow running the file directly for quick manual checks.
    pytest.main([__file__, '-v'])
