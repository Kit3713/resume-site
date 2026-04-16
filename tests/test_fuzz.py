"""
Property-based fuzz tests — Phase 13.8

Uses Hypothesis to verify that core input-handling functions never crash,
never return dangerous output, and always satisfy their documented contracts
when given arbitrary (including adversarial) inputs.

CI budget: @settings(max_examples=50) keeps each test under ~5 seconds.
Locally, remove the setting or set max_examples=1000 for deeper coverage.
"""

from __future__ import annotations

import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ============================================================
# slugify — app/services/text.py
# ============================================================


@given(text=st.text(min_size=0, max_size=500))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_slugify_never_crashes(text):
    from app.services.text import slugify

    result = slugify(text)
    assert isinstance(result, str)


@given(text=st.text(min_size=0, max_size=500))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_slugify_output_is_url_safe(text):
    from app.services.text import slugify

    result = slugify(text)
    if result:
        assert re.match(r'^[\w][\w-]*[\w]$|^[\w]$', result), (
            f'slugify({text!r}) produced non-URL-safe output: {result!r}'
        )
        assert not result.startswith('-')
        assert not result.endswith('-')


@given(text=st.text(min_size=0, max_size=500))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_slugify_no_consecutive_hyphens(text):
    from app.services.text import slugify

    result = slugify(text)
    assert '--' not in result


# ============================================================
# _calculate_reading_time — app/services/blog.py
# ============================================================


@given(content=st.text(min_size=0, max_size=5000))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_reading_time_never_crashes(content):
    from app.services.blog import _calculate_reading_time

    result = _calculate_reading_time(content)
    assert isinstance(result, int)
    assert result >= 0


@given(content=st.text(min_size=0, max_size=5000))
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_reading_time_with_html_format(content):
    from app.services.blog import _calculate_reading_time

    result = _calculate_reading_time(content, content_format='html')
    assert isinstance(result, int)
    assert result >= 0


# ============================================================
# sanitize_html — app/services/content.py
# ============================================================


@given(html=st.text(min_size=0, max_size=2000))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_sanitize_html_never_crashes(html):
    from app.services.content import sanitize_html

    result = sanitize_html(html)
    assert isinstance(result, str)


@given(html=st.text(min_size=0, max_size=2000))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_sanitize_html_no_script_tags(html):
    from app.services.content import sanitize_html

    result = sanitize_html(html)
    assert '<script' not in result.lower()
    assert 'javascript:' not in result.lower()
    assert 'onerror=' not in result.lower()
    assert 'onload=' not in result.lower()


XSS_PAYLOADS = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '<svg onload=alert(1)>',
    '<body onload=alert(1)>',
    '<a href="javascript:alert(1)">click</a>',
    '<div style="background:url(javascript:alert(1))">',
    '"><script>alert(1)</script>',
    "'-alert(1)-'",
    '<iframe src="data:text/html,<script>alert(1)</script>">',
    '<math><mtext><table><mglyph><svg><mtext><style><img src=x onerror=alert(1)>',
]


def test_sanitize_html_xss_payloads():
    from app.services.content import sanitize_html

    for payload in XSS_PAYLOADS:
        result = sanitize_html(payload)
        assert '<script' not in result.lower(), f'XSS survived: {payload!r} -> {result!r}'
        assert 'onerror=' not in result.lower(), f'Event handler survived: {payload!r} -> {result!r}'
        assert 'onload=' not in result.lower(), f'Event handler survived: {payload!r} -> {result!r}'


# ============================================================
# _validate_magic_bytes — app/services/photos.py
# ============================================================


@given(data=st.binary(min_size=0, max_size=100))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_validate_magic_bytes_never_crashes(data):
    import io

    from app.services.photos import _validate_magic_bytes

    class FakeFile:
        def __init__(self, content):
            self._stream = io.BytesIO(content)

        def read(self, n=-1):
            return self._stream.read(n)

        def seek(self, pos, whence=0):
            return self._stream.seek(pos, whence)

    fake = FakeFile(data)
    for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.exe'):
        result = _validate_magic_bytes(fake, ext)
        assert isinstance(result, bool)
        fake.seek(0)


@given(data=st.binary(min_size=0, max_size=100))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_validate_magic_bytes_rejects_random_data(data):
    import io

    from app.services.photos import _validate_magic_bytes

    class FakeFile:
        def __init__(self, content):
            self._stream = io.BytesIO(content)

        def read(self, n=-1):
            return self._stream.read(n)

        def seek(self, pos, whence=0):
            return self._stream.seek(pos, whence)

    if not data.startswith((b'\xff\xd8\xff', b'\x89PNG', b'GIF87a', b'GIF89a', b'RIFF')):
        fake = FakeFile(data)
        for ext in ('.jpg', '.png', '.gif', '.webp'):
            result = _validate_magic_bytes(fake, ext)
            assert result is False, f'Random data accepted as {ext}'
            fake.seek(0)


# ============================================================
# HTTP layer — fuzz the Flask test client
# ============================================================


@given(path=st.text(alphabet=st.characters(whitelist_categories=('L', 'N', 'P')), min_size=1, max_size=100))
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_http_random_paths_no_500(app, path):
    with app.test_client() as client:
        response = client.get(f'/{path}')
        assert response.status_code != 500, f'500 on GET /{path}'


@given(
    method=st.sampled_from(['GET', 'POST', 'PUT', 'DELETE', 'PATCH']),
    path=st.sampled_from(['/', '/portfolio', '/blog', '/contact', '/api/v1/site', '/admin/login']),
)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_http_random_methods_no_500(app, method, path):
    with app.test_client() as client:
        response = client.open(path, method=method)
        assert response.status_code != 500, f'500 on {method} {path}'
