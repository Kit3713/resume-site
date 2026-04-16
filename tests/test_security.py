"""
Security Tests — Phase 7.3

Verifies the security controls implemented in Phase 6:
- CSRF protection: POST without a valid token returns 400 (tested with a
  dedicated fixture that enables CSRF, separate from the standard test
  suite which disables it for convenience).
- Security response headers: X-Content-Type-Options, X-Frame-Options,
  X-XSS-Protection, Referrer-Policy, Permissions-Policy present on every
  response; Cache-Control no-store on admin routes.
- HTML sanitization: <script> tags and event handlers are stripped by
  sanitize_html() before content reaches the database.
"""

import pytest

from app import create_app
from tests.conftest import _init_test_db, _write_test_config

# ============================================================
# FIXTURE: App with CSRF enabled
# ============================================================


@pytest.fixture
def csrf_app(tmp_path):
    """A test app with CSRF protection turned ON (opposite of the default fixture).

    Used exclusively for CSRF rejection tests. All other tests use the
    app fixture (CSRF disabled) to avoid threading token generation into
    every form submission.
    """
    config_path = _write_test_config(tmp_path)
    flask_app = create_app(config_path=config_path)
    flask_app.config['TESTING'] = True
    # CSRF enabled — do NOT set WTF_CSRF_ENABLED = False here
    flask_app.config['WTF_CSRF_SECRET_KEY'] = 'csrf-test-secret'
    _init_test_db(str(tmp_path / 'test.db'))
    return flask_app


@pytest.fixture
def csrf_client(csrf_app):
    """Test client backed by the CSRF-enabled app."""
    return csrf_app.test_client()


# ============================================================
# CSRF PROTECTION
# ============================================================


def test_csrf_contact_post_without_token_rejected(csrf_client):
    """POST to /contact without a CSRF token should return 400."""
    response = csrf_client.post(
        '/contact',
        data={
            'name': 'Hacker',
            'email': 'h@evil.com',
            'message': 'CSRF attack',
            'website': '',
        },
    )
    assert response.status_code == 400


def test_csrf_admin_login_post_without_token_rejected(csrf_client):
    """POST to /admin/login without a CSRF token should return 400."""
    response = csrf_client.post(
        '/admin/login',
        data={
            'username': 'admin',
            'password': 'somepassword',
        },
    )
    assert response.status_code == 400


def test_csrf_get_requests_not_affected(csrf_client):
    """GET requests must never be rejected by CSRF (tokens only apply to POST)."""
    response = csrf_client.get('/')
    assert response.status_code == 200


def test_csrf_review_post_without_token_rejected(csrf_client):
    """POST to /review/<token> without a CSRF token should return 400."""
    response = csrf_client.post(
        '/review/fake-token-xyz',
        data={
            'reviewer_name': 'Attacker',
            'message': 'Injected review',
        },
    )
    assert response.status_code == 400


# ============================================================
# SECURITY RESPONSE HEADERS — public pages
# ============================================================


def test_header_x_content_type_options_public(client):
    """X-Content-Type-Options: nosniff must be present on public pages."""
    response = client.get('/')
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'


def test_header_x_frame_options_public(client):
    """X-Frame-Options: DENY must be present on public pages."""
    response = client.get('/')
    assert response.headers.get('X-Frame-Options') == 'DENY'


def test_header_x_xss_protection_public(client):
    """X-XSS-Protection: 0 must be present (disables legacy XSS filter)."""
    response = client.get('/')
    assert response.headers.get('X-XSS-Protection') == '0'


def test_header_referrer_policy_public(client):
    """Referrer-Policy must be set on public pages."""
    response = client.get('/')
    assert response.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'


def test_header_permissions_policy_public(client):
    """Permissions-Policy must be present and disable camera/mic/geo."""
    response = client.get('/')
    policy = response.headers.get('Permissions-Policy', '')
    assert 'camera=()' in policy
    assert 'microphone=()' in policy
    assert 'geolocation=()' in policy


def test_headers_present_on_portfolio(client):
    """Security headers must also be present on non-root public pages."""
    response = client.get('/portfolio')
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('X-Frame-Options') == 'DENY'


def test_headers_present_on_404(client):
    """Security headers must be set even on 404 responses."""
    response = client.get('/this-page-does-not-exist')
    assert response.status_code == 404
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'


# ============================================================
# SECURITY RESPONSE HEADERS — admin pages
# ============================================================


def test_header_cache_control_on_admin(client):
    """Admin pages must carry Cache-Control: no-store to prevent proxy caching."""
    response = client.get('/admin/login')
    cc = response.headers.get('Cache-Control', '')
    assert 'no-store' in cc


def test_header_cache_control_on_admin_dashboard_redirect(auth_client):
    """Unauthenticated admin redirect should still carry no-store header."""
    # auth_client is authenticated, so this is the authenticated dashboard
    response = auth_client.get('/admin/')
    cc = response.headers.get('Cache-Control', '')
    assert 'no-store' in cc


def test_header_no_cache_on_admin_post_redirect(auth_client):
    """Admin POST responses (redirects) should also carry no-store."""
    response = auth_client.post(
        '/admin/services/add',
        data={
            'title': 'Test',
            'description': 'Desc',
            'icon': '',
            'sort_order': '0',
        },
    )
    cc = response.headers.get('Cache-Control', '')
    assert 'no-store' in cc


def test_header_cache_not_forced_on_public(client):
    """Public pages should NOT have the admin's no-store Cache-Control."""
    response = client.get('/')
    cc = response.headers.get('Cache-Control', '')
    assert 'no-store' not in cc


# ============================================================
# HTML SANITIZATION (unit tests for sanitize_html)
# ============================================================


def test_sanitize_strips_script_tag():
    """<script> tags must be removed by sanitize_html."""
    from app.services.content import sanitize_html

    result = sanitize_html('<p>Hello</p><script>alert(1)</script>')
    assert '<script>' not in result
    assert 'alert' not in result


def test_sanitize_strips_onclick_handler():
    """Inline event handlers (onclick etc.) must be removed."""
    from app.services.content import sanitize_html

    result = sanitize_html('<p onclick="stealCookies()">Click me</p>')
    assert 'onclick' not in result
    assert 'stealCookies' not in result


def test_sanitize_strips_javascript_href():
    """javascript: protocol in href attributes must be removed."""
    from app.services.content import sanitize_html

    result = sanitize_html('<a href="javascript:alert(1)">link</a>')
    assert 'javascript:' not in result


def test_sanitize_preserves_allowed_tags():
    """Legitimate Quill output tags (p, strong, em, h2, etc.) must survive."""
    from app.services.content import sanitize_html

    html = '<h2>Title</h2><p><strong>Bold</strong> and <em>italic</em>.</p>'
    result = sanitize_html(html)
    assert '<h2>' in result
    assert '<strong>' in result
    assert '<em>' in result


def test_sanitize_preserves_safe_anchor():
    """Anchors with http href and safe attributes must be preserved."""
    from app.services.content import sanitize_html

    result = sanitize_html('<a href="https://example.com" target="_blank">link</a>')
    assert 'href="https://example.com"' in result


def test_sanitize_strips_iframe():
    """<iframe> is not in the allowlist and must be removed."""
    from app.services.content import sanitize_html

    result = sanitize_html('<iframe src="https://evil.com"></iframe>')
    assert '<iframe' not in result


def test_sanitize_strips_style_attribute():
    """style= attributes (potential CSS injection) must be stripped."""
    from app.services.content import sanitize_html

    result = sanitize_html('<p style="background: url(evil.com)">text</p>')
    assert 'style=' not in result


def test_sanitize_handles_empty_string():
    """sanitize_html('') must return '' without raising."""
    from app.services.content import sanitize_html

    assert sanitize_html('') == ''


def test_sanitize_handles_plain_text():
    """Plain text without HTML tags must be returned safely."""
    from app.services.content import sanitize_html

    result = sanitize_html('Hello world')
    assert 'Hello world' in result


# ============================================================
# HTML SANITIZATION — integration (via admin route)
# ============================================================


def test_content_save_strips_xss(auth_client, populated_db):
    """Saving a content block with a <script> tag should store sanitized HTML."""
    auth_client.post(
        '/admin/content/edit/about',
        data={
            'title': 'About',
            'content': '<p>Safe content</p><script>alert("xss")</script>',
        },
    )

    # Fetch the public page that renders the about block.
    # We check for the XSS payload specifically (alert("xss")), not for <script>
    # in general — the page legitimately includes theme and GSAP script tags.
    response = auth_client.get('/')
    assert b'alert(&quot;xss&quot;)' not in response.data
    assert b'alert("xss")' not in response.data


# ============================================================
# CSP HEADER
# ============================================================


def test_header_csp_enforced(client):
    """Content-Security-Policy header must be enforced with nonce."""
    response = client.get('/')
    csp = response.headers.get('Content-Security-Policy', '')
    assert "default-src 'self'" in csp
    assert 'cdnjs.cloudflare.com' in csp
    assert 'fonts.googleapis.com' in csp
    assert "'nonce-" in csp
    assert 'report-uri /csp-report' in csp
    assert 'Content-Security-Policy-Report-Only' not in response.headers


# ============================================================
# CACHE-CONTROL ON STATIC ASSETS
# ============================================================


def test_header_cache_control_on_static(client):
    """Static asset responses should have long-lived Cache-Control."""
    response = client.get('/static/css/style.css')
    cc = response.headers.get('Cache-Control', '')
    assert 'public' in cc
    assert 'max-age' in cc


# ============================================================
# FILE UPLOAD SIZE LIMIT
# ============================================================


def test_upload_exceeding_size_limit_rejected(auth_client, app):
    """Uploading a file that exceeds MAX_UPLOAD_SIZE should be rejected."""
    import io

    # Set a very small limit for this test
    app.config['MAX_UPLOAD_SIZE'] = 1024  # 1 KB

    # Create a PNG that exceeds the limit (valid PNG header + padding)
    png_header = b'\x89PNG\r\n\x1a\n'
    data = png_header + b'\x00' * 2048  # 2 KB total

    response = auth_client.post(
        '/admin/photos/upload',
        data={
            'photo': (io.BytesIO(data), 'big.png'),
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert response.status_code == 200
    # Should show an error about file size
    body = response.data.lower()
    assert b'too large' in body or b'exceeds' in body or b'size' in body


# ============================================================
# RATE LIMITING
# ============================================================


def test_rate_limiting_contact_returns_429(app):
    """Exceeding the rate limit on contact form should return 429."""
    # Create a client with rate limiting enabled
    app.config['RATELIMIT_ENABLED'] = True
    client = app.test_client()

    # Flood the contact endpoint beyond the limit (10/min)
    for i in range(15):
        client.post(
            '/contact',
            data={
                'name': f'Test {i}',
                'email': f'test{i}@example.com',
                'message': 'Rate limit test',
                'website': '',
            },
        )

    # The last requests should be rate-limited
    response = client.post(
        '/contact',
        data={
            'name': 'Final',
            'email': 'final@example.com',
            'message': 'Should be limited',
            'website': '',
        },
    )
    assert response.status_code == 429


def test_rate_limiting_admin_login_returns_429(app):
    """Exceeding the rate limit on admin login should return 429 (brute force protection)."""
    app.config['RATELIMIT_ENABLED'] = True
    client = app.test_client()

    # Flood the login endpoint beyond the limit (5/min)
    for i in range(8):
        client.post(
            '/admin/login',
            data={
                'username': 'admin',
                'password': f'wrong-password-{i}',
            },
        )

    response = client.post(
        '/admin/login',
        data={
            'username': 'admin',
            'password': 'another-wrong-password',
        },
    )
    assert response.status_code == 429


# ============================================================
# OPEN REDIRECT (login ?next=)
# ============================================================


def test_login_rejects_absolute_next_url(client):
    """?next=https://evil.com must NOT redirect offsite after login."""
    response = client.post(
        '/admin/login?next=https://evil.com/phish',
        data={'username': 'admin', 'password': 'testpassword123'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    # Must redirect to the local dashboard, not the attacker URL
    location = response.headers.get('Location', '')
    assert 'evil.com' not in location
    assert '/admin' in location


def test_login_rejects_scheme_relative_next_url(client):
    """?next=//evil.com should also be rejected (scheme-relative URLs)."""
    response = client.post(
        '/admin/login?next=//evil.com/phish',
        data={'username': 'admin', 'password': 'testpassword123'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers.get('Location', '')
    assert 'evil.com' not in location


def test_login_accepts_relative_next_path(client):
    """?next=/admin/photos is a safe same-origin path and should be honored."""
    response = client.post(
        '/admin/login?next=/admin/photos',
        data={'username': 'admin', 'password': 'testpassword123'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers.get('Location', '').endswith('/admin/photos')


# ============================================================
# SESSION COOKIE FLAGS
# ============================================================


def test_session_cookie_flags_configured(app):
    """Session cookie must have HTTPONLY and SAMESITE configured."""
    assert app.config.get('SESSION_COOKIE_HTTPONLY') is True
    assert app.config.get('SESSION_COOKIE_SAMESITE') == 'Lax'
    # Test config sets session_cookie_secure: false; the production default is True.
    assert app.config.get('SESSION_COOKIE_SECURE') is False


# ============================================================
# XSS: MARKDOWN BLOG SANITIZATION (M3)
# ============================================================


def test_markdown_blog_post_strips_inline_script(app):
    """Markdown posts rendered to HTML must have <script> tags stripped."""
    from app.services.blog import render_post_content

    post = {
        'content_format': 'markdown',
        'content': 'Hello\n\n<script>alert(1)</script>\n\nworld',
    }
    rendered = render_post_content(post)
    assert '<script' not in rendered.lower()
    assert 'alert(1)' not in rendered  # Sanitizer should drop the script body


def test_markdown_blog_post_strips_event_handlers(app):
    """Markdown posts must have inline event handlers stripped."""
    from app.services.blog import render_post_content

    post = {
        'content_format': 'markdown',
        'content': '<img src="x" onerror="alert(1)">',
    }
    rendered = render_post_content(post)
    assert 'onerror' not in rendered.lower()


# ============================================================
# XSS: SERVICE DESCRIPTION SANITIZATION (M4)
# ============================================================


def test_service_description_sanitized_on_create(app):
    """Service descriptions must be stripped of dangerous tags on write."""
    import sqlite3

    from app.services.service_items import add_service

    db = sqlite3.connect(app.config['DATABASE_PATH'])
    db.row_factory = sqlite3.Row
    try:
        add_service(db, 'Evil', '<script>alert(1)</script><p>ok</p>', '', 1)
        row = db.execute("SELECT description FROM services WHERE title = 'Evil'").fetchone()
        assert '<script' not in row['description'].lower()
        assert '<p>ok</p>' in row['description']
    finally:
        db.close()
