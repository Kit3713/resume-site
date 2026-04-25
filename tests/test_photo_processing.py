"""Tests for the Pillow image pipeline (app/services/photos.py).

Focuses on behaviors that are easy to regress silently:
    * EXIF metadata is stripped on upload (privacy).
    * JPEG outputs are progressive.
    * Small images are still re-encoded (previous pipeline only re-saved
      when downscaling, leaving GPS data in any image < 2000px).
    * Phase 26.4: Image.draft() on JPEGs preserves the variant ladder
      and produces output within 1% byte tolerance of the pre-change
      pipeline. The DCT-level downscale is documented 4-8× faster on
      24 MP DSLR inputs without changing pixel-level semantics enough
      to matter.
"""

import io
import os

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


def _build_24mp_jpeg(width=6000, height=4000, exif=None):
    """Return a ~24 MP JPEG byte stream with a smooth gradient.

    Real DSLR JPEGs are 6000×4000 (Nikon D750, Canon 6D Mark II) = 24 MP.
    The gradient gives every 8×8 DCT block non-trivial frequency content
    so libjpeg-turbo's draft() codepath actually has work to skip; a
    flat image would short-circuit block coding and miss the regression.
    Built via Pillow's C-level ``linear_gradient`` + ``resize`` + ``merge``
    (~200 ms) instead of a 24 M-iteration Python pixel loop.
    """
    r = Image.linear_gradient('L').resize((width, height))
    g = Image.linear_gradient('L').rotate(90).resize((width, height))
    b = Image.eval(r, lambda v: (v + 64) % 256)
    img = Image.merge('RGB', (r, g, b))

    buf = io.BytesIO()
    save_kwargs = {'exif': exif} if exif else {}
    img.save(buf, format='JPEG', quality=90, **save_kwargs)
    buf.seek(0)
    return buf


class TestImageDraftJpeg:
    """Phase 26.4 regression suite — Image.draft() on JPEG uploads."""

    def test_24mp_jpeg_produces_full_variant_ladder(self, app):
        """A 24 MP JPEG must still produce all three variants on disk:
        the 640w and 1024w responsive variants plus the optimised
        original (capped at 2000 px). Image.draft() shrinks the
        decoded buffer; if anything in the rest of the pipeline reads
        a stale image dimension it'd skip a variant."""
        buf = _build_24mp_jpeg()
        storage = FileStorage(buf, filename='dslr.jpg', content_type='image/jpeg')

        with app.app_context():
            result = process_upload(storage)

        assert isinstance(result, dict), f'process_upload returned: {result!r}'

        photo_dir = app.config['PHOTO_STORAGE']
        base, ext = os.path.splitext(result['storage_name'])

        for name, label in (
            (result['storage_name'], 'main 2000 px variant'),
            (f'{base}_640w{ext}', '640w variant'),
            (f'{base}_1024w{ext}', '1024w variant'),
        ):
            assert os.path.isfile(os.path.join(photo_dir, name)), f'{label} missing'

        with _read_back(app, result['storage_name']) as img:
            assert max(img.size) <= 2000, f'long edge exceeded 2000 px: {img.size}'

    def test_24mp_jpeg_main_variant_within_1pct_of_pre_change(self, app, monkeypatch):
        """The 2000 px variant produced with Image.draft() must be
        byte-for-byte within 1% of the pre-change pipeline at the same
        JPEG quality setting. ``draft()`` operates at libjpeg's DCT
        scale, so the resampled output isn't bit-identical to a
        full-resolution decode followed by LANCZOS, but the high-quality
        save (quality=85) absorbs the tiny coefficient-level difference."""
        source_bytes = _build_24mp_jpeg().getvalue()

        def _run_pipeline():
            storage = FileStorage(
                io.BytesIO(source_bytes),
                filename='dslr.jpg',
                content_type='image/jpeg',
            )
            with app.app_context():
                result = process_upload(storage)
            assert isinstance(result, dict)
            photo_path = os.path.join(app.config['PHOTO_STORAGE'], result['storage_name'])
            with open(photo_path, 'rb') as f:
                return f.read()

        with_draft = _run_pipeline()

        # Pre-change pipeline: draft() patched to a no-op. monkeypatch
        # auto-restores when the test ends.
        monkeypatch.setattr(Image.Image, 'draft', lambda self, mode, size: None)
        without_draft = _run_pipeline()

        # Outputs may differ slightly in length (JPEG entropy coding),
        # so count differing bytes on the overlap and add length skew.
        # Threshold is differences as a fraction of the LARGER buffer
        # so a shorter "with_draft" doesn't artificially inflate the
        # ratio.
        compare_len = min(len(with_draft), len(without_draft))
        differing = sum(
            1
            for a, b in zip(
                with_draft[:compare_len],
                without_draft[:compare_len],
                strict=True,
            )
            if a != b
        )
        length_skew = abs(len(with_draft) - len(without_draft))
        total_diff_ratio = (differing + length_skew) / max(len(with_draft), len(without_draft))
        assert total_diff_ratio < 0.01, (
            f'output diverged from pre-change pipeline beyond 1%: '
            f'{total_diff_ratio:.4%} '
            f'(differing bytes={differing}, length skew={length_skew})'
        )

    def test_24mp_jpeg_strips_exif(self, app):
        """EXIF stripping must still work after the Image.draft() call
        is added. Pillow's draft() fast-path can preserve metadata that
        a slow-path decode would have dropped, so we re-assert the
        privacy contract on a 24 MP path."""
        exif = Image.Exif()
        exif[0x010F] = 'TestCamera Inc.'
        exif[0x0110] = 'Secret DSLR Model'
        buf = _build_24mp_jpeg(exif=exif.tobytes())

        storage = FileStorage(buf, filename='dslr.jpg', content_type='image/jpeg')

        with app.app_context():
            result = process_upload(storage)

        assert isinstance(result, dict), f'process_upload returned: {result!r}'

        with _read_back(app, result['storage_name']) as out:
            out_exif = out.getexif()
            assert len(out_exif) == 0, f'EXIF survived: {dict(out_exif)!r}'


if __name__ == '__main__':
    # Allow running the file directly for quick manual checks.
    pytest.main([__file__, '-v'])
