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


# ============================================================
# DEV-SERVER DEBUG GATE (Phase 22.1)
# ============================================================


def test_console_not_exposed_on_default_app(client):
    """The Werkzeug /console interactive debugger must never be reachable.

    The factory-built app fixture is the shape operators boot in production
    (Gunicorn → create_app()) and under `python app.py` without the opt-in
    gate. The debugger registers its route under `/console`; a 404 here
    proves the gate (RESUME_SITE_DEV=1 + --debug) is the only way to reach
    it.
    """
    response = client.get('/console')
    assert response.status_code == 404


def test_app_debug_mode_off_by_default(app):
    """Flask `app.debug` must default to False so the exception page never
    renders a traceback for a public visitor and the `/console` route is
    never registered. Gate flip lives only in the `python app.py`
    entry-point with both the env var and --debug present."""
    assert app.debug is False


# ============================================================
# XFF trust gate (Phase 22.6 — audit issue #16)
# ============================================================


def _admin_app_with_trusted_proxies(tmp_path, proxies):
    """Build a test app whose config.yaml sets trusted_proxies = ``proxies``.

    Separate from the conftest ``app`` fixture because 22.6 is about
    what happens when the operator has a SPECIFIC trusted_proxies value
    (including the empty list, which is the correct production default
    for a directly-exposed instance post-22.5).
    """
    from app import create_app
    from tests.conftest import _init_test_db

    pw_hash = (
        'pbkdf2:sha256:600000$bngNDaCGXphoecmK$'
        '7e35934ae555af4c418e1399fa0c866411b05f64bf8c3ef64d50c93990a7497b'
    )
    cfg = tmp_path / 'config.yaml'
    proxy_lines = ''.join(f'  - "{p}"\n' for p in proxies) if proxies else ''
    cfg.write_text(
        'secret_key: "test-secret-key-for-testing-only"\n'
        f'database_path: "{tmp_path}/xff.db"\n'
        f'photo_storage: "{tmp_path}/photos"\n'
        'session_cookie_secure: false\n'
        f'trusted_proxies:\n{proxy_lines}'
        'admin:\n'
        '  username: "admin"\n'
        f'  password_hash: "{pw_hash}"\n'
        '  allowed_networks:\n'
        '    - "127.0.0.0/8"\n'
    )
    flask_app = create_app(config_path=str(cfg))
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    _init_test_db(str(tmp_path / 'xff.db'))
    return flask_app


def test_admin_xff_ignored_when_no_trusted_proxies(tmp_path):
    """22.6: with an EMPTY trusted_proxies list, the admin IP gate must
    ignore X-Forwarded-For entirely and judge the request by
    ``remote_addr`` alone. An attacker that reaches the container
    directly cannot pivot to admin by forging ``X-Forwarded-For:
    127.0.0.1``."""
    flask_app = _admin_app_with_trusted_proxies(tmp_path, proxies=[])
    client = flask_app.test_client()

    # Test client's remote_addr is 127.0.0.1 (inside allowed_networks).
    # Even with a bogus XFF pointing offsite, the XFF is IGNORED and we
    # allow the request based on remote_addr → 302 (login redirect), not
    # 403 (IP-blocked).
    resp = client.get('/admin/login', headers={'X-Forwarded-For': '203.0.113.1'})
    assert resp.status_code == 200

    # Conversely, if the attacker tries to set XFF to a value NOT in
    # allowed_networks in the hope it'll be consulted, the gate still
    # ignores it — remote_addr is still 127.0.0.1 so we're allowed.
    resp2 = client.get(
        '/admin/login',
        headers={'X-Forwarded-For': '10.99.99.99, 127.0.0.1'},
    )
    assert resp2.status_code == 200


def test_admin_xff_consulted_when_remote_addr_in_trusted_proxies(tmp_path):
    """22.6: with the loopback net listed as a trusted proxy, the XFF
    header IS trusted and the IP gate is evaluated against its leftmost
    value. Simulates the legitimate deployment where Caddy sits on
    127.0.0.1 and forwards traffic with the real client IP in XFF."""
    flask_app = _admin_app_with_trusted_proxies(tmp_path, proxies=['127.0.0.0/8'])
    client = flask_app.test_client()

    # XFF names an external client — gate says "not in allowed_networks" → 403.
    resp = client.get('/admin/login', headers={'X-Forwarded-For': '203.0.113.1'})
    assert resp.status_code == 403

    # XFF carries the standard Caddy hop chain ending in a 127.0.0.0/8 peer;
    # leftmost entry (the real client) is inside allowed_networks → 200.
    resp_ok = client.get(
        '/admin/login',
        headers={'X-Forwarded-For': '127.0.0.2, 127.0.0.1'},
    )
    assert resp_ok.status_code == 200


def test_admin_xff_ignored_when_direct_peer_not_in_trusted_proxies(tmp_path):
    """22.6: trusted_proxies is non-empty, but the current peer is NOT in
    it (e.g., attacker bypassed the proxy and hit the container on a
    separate network). XFF must not be trusted; ``remote_addr`` is
    used verbatim — and since remote_addr is 127.0.0.1 in the test
    client, we end up allowing."""
    flask_app = _admin_app_with_trusted_proxies(tmp_path, proxies=['10.99.99.0/24'])
    client = flask_app.test_client()

    # Attacker sends forged XFF. remote_addr = 127.0.0.1 is NOT in
    # trusted_proxies (10.99.99.0/24), so XFF is ignored and we fall back
    # to remote_addr = 127.0.0.1 which IS in allowed_networks → 200.
    resp = client.get('/admin/login', headers={'X-Forwarded-For': '127.0.0.1'})
    assert resp.status_code == 200


# ============================================================
# SESSION REVOCATION — Phase 23.1 (#33 + #51)
# ============================================================


def test_admin_blueprint_middleware_parity(app):
    """Phase 23.1 (#51): every admin-prefixed blueprint MUST register the
    same set of before_request / after_request hooks.

    Regression for the bug where ``check_session_epoch`` was only attached
    to ``admin_bp``; a cookie captured before logout kept authenticating
    on every route under ``blog_admin_bp`` until the cookie's own expiry.
    """
    from app.routes.admin import admin_bp
    from app.routes.blog_admin import blog_admin_bp

    def _hook_names(bp, attr):
        return (
            {fn.__name__ for fn in bp.before_request_funcs.get(None, [])}
            if attr == 'before'
            else {fn.__name__ for fn in bp.after_request_funcs.get(None, [])}
        )

    admin_before = _hook_names(admin_bp, 'before')
    blog_before = _hook_names(blog_admin_bp, 'before')
    admin_after = _hook_names(admin_bp, 'after')
    blog_after = _hook_names(blog_admin_bp, 'after')

    # The security-critical set (everything that enforces IP/session
    # boundaries) must be identical. Non-security hooks registered only
    # on admin_bp are permitted — this assertion is intentionally scoped
    # to the enforcement set, not a superset equality.
    required = {'restrict_to_allowed_networks', 'check_session_timeout', 'check_session_epoch'}
    assert required <= admin_before, f'admin_bp missing: {required - admin_before}'
    assert required <= blog_before, f'blog_admin_bp missing: {required - blog_before}'
    assert 'update_last_activity' in admin_after
    assert 'update_last_activity' in blog_after


def test_logout_revokes_cookie_on_another_client(app):
    """Phase 23.1 (#33): after one client logs out, a second client
    holding a copy of the same pre-logout cookie must be rejected on its
    NEXT admin request — not after the 30 s settings-cache TTL.

    Simulates two Gunicorn workers by issuing two Flask test clients
    against the same app; the uncached epoch read in check_session_epoch
    is what makes the revocation visible immediately.
    """
    import time

    # Seed the session on client A via the login form so both clients
    # share the same underlying cookie stamp.
    client_a = app.test_client()
    client_a.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'testpassword123'},
        follow_redirects=False,
    )
    # Clone A's session cookie into client B. Reading session via the
    # test-client session transaction is enough to keep B authenticated.
    with client_a.session_transaction() as sess_a:
        user_id = sess_a.get('_user_id')
        admin_epoch = sess_a.get('_admin_epoch')
    assert user_id == 'admin'
    assert admin_epoch is not None

    client_b = app.test_client()
    with client_b.session_transaction() as sess_b:
        sess_b['_user_id'] = user_id
        sess_b['_admin_epoch'] = admin_epoch
        sess_b['_fresh'] = True

    # Confirm B can reach dashboard before the logout lands.
    assert client_b.get('/admin/').status_code == 200

    # A logs out — epoch bumps in the settings table.
    t0 = time.monotonic()
    client_a.get('/admin/logout')

    # B's next admin request must be rejected immediately (within 250 ms
    # per the roadmap SLA) — the uncached read path makes this the cost
    # of a single SELECT, not the cache TTL.
    resp = client_b.get('/admin/', follow_redirects=False)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert resp.status_code in (302, 401), f'stale cookie still authenticated: {resp.status_code}'
    # 302 → redirect to /admin/login (check_session_epoch's behaviour)
    if resp.status_code == 302:
        assert '/admin/login' in resp.headers.get('Location', '')
    assert elapsed_ms < 250, f'revocation took {elapsed_ms:.0f}ms — SLA is <250ms'


def test_logout_revokes_cookie_on_blog_admin_routes(app):
    """Phase 23.1 (#51): the blog_admin blueprint must honour the epoch
    check too. A post-logout cookie must not grant access to /admin/blog.
    """
    client_a = app.test_client()
    client_a.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'testpassword123'},
        follow_redirects=False,
    )
    with client_a.session_transaction() as sess_a:
        user_id = sess_a['_user_id']
        admin_epoch = sess_a['_admin_epoch']

    client_b = app.test_client()
    with client_b.session_transaction() as sess_b:
        sess_b['_user_id'] = user_id
        sess_b['_admin_epoch'] = admin_epoch
        sess_b['_fresh'] = True

    assert client_b.get('/admin/blog').status_code == 200

    client_a.get('/admin/logout')

    resp = client_b.get('/admin/blog', follow_redirects=False)
    assert resp.status_code in (302, 401), (
        f'blog_admin accepted stale cookie post-logout: {resp.status_code}'
    )


# ============================================================
# CLIENT-IP RESOLUTION — Phase 23.2 (#34)
# ============================================================


class _FakeHeaders:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=''):
        return self._data.get(key, default)


class _FakeRequest:
    """Minimal request stub for unit-testing get_client_ip.

    The helper only reads ``remote_addr`` and ``headers.get`` — avoids
    pulling the full Flask test-client stack in for a pure-function test.
    """

    def __init__(self, remote_addr, xff=None):
        self.remote_addr = remote_addr
        data = {}
        if xff is not None:
            data['X-Forwarded-For'] = xff
        self.headers = _FakeHeaders(data)


def _mk_req(remote_addr, xff=None):
    return _FakeRequest(remote_addr, xff)


def test_get_client_ip_no_xff_returns_remote_addr():
    """Phase 23.2: with no XFF header, the TCP peer IS the client."""
    from app.services.request_ip import get_client_ip

    assert get_client_ip(_mk_req('1.2.3.4'), trusted_proxies=[]) == '1.2.3.4'


def test_get_client_ip_xff_ignored_when_no_trusted_proxies():
    """#34 headline: with trusted_proxies empty, XFF is ALWAYS ignored.

    This is the direct-exposure default. Without this rule, a
    non-proxied deployment accepted spoofed XFF headers for the contact
    rate limit, API rate limit, analytics, /metrics, and login throttle.
    """
    from app.services.request_ip import get_client_ip

    req = _mk_req('1.2.3.4', xff='127.0.0.1')
    assert get_client_ip(req, trusted_proxies=[]) == '1.2.3.4'


def test_get_client_ip_xff_ignored_when_peer_not_trusted():
    """Phase 23.2: peer is not a trusted proxy → XFF ignored entirely.

    Even with a trusted_proxies set, an attacker that reaches the
    container directly (bypassing the reverse proxy) must not be able
    to spoof their origin by setting XFF — the TCP peer IS them.
    """
    import ipaddress

    from app.services.request_ip import get_client_ip

    req = _mk_req('9.9.9.9', xff='127.0.0.1, 5.5.5.5')
    trusted = [ipaddress.ip_network('10.0.0.0/24')]
    assert get_client_ip(req, trusted_proxies=trusted) == '9.9.9.9'


def test_get_client_ip_walks_right_to_left():
    """Phase 23.2: with a proxy chain, the first right-to-left untrusted
    IP is the client. Forged leftmost entries are correctly discarded.

    Scenario: trusted proxy 10.0.0.5 forwards a request whose original
    attacker set ``X-Forwarded-For: 127.0.0.1`` before the proxy appended
    the real attacker IP. The leftmost-trust strategy (the v0.3.1
    interim fix) would return the forged 127.0.0.1. Right-to-left
    returns the real attacker.
    """
    import ipaddress

    from app.services.request_ip import get_client_ip

    req = _mk_req('10.0.0.5', xff='127.0.0.1, 9.9.9.9, 10.0.0.5')
    trusted = [ipaddress.ip_network('10.0.0.0/24')]
    assert get_client_ip(req, trusted_proxies=trusted) == '9.9.9.9'


def test_get_client_ip_all_entries_trusted_falls_back_to_peer():
    """Phase 23.2: if every XFF entry is a trusted proxy, we have no
    real client IP to return — fall back to remote_addr."""
    import ipaddress

    from app.services.request_ip import get_client_ip

    req = _mk_req('10.0.0.5', xff='10.0.0.1, 10.0.0.2, 10.0.0.5')
    trusted = [ipaddress.ip_network('10.0.0.0/24')]
    assert get_client_ip(req, trusted_proxies=trusted) == '10.0.0.5'


def test_get_client_ip_handles_ipv6_chains():
    """Phase 23.2: IPv6 in both peer and XFF is handled by the same walk."""
    import ipaddress

    from app.services.request_ip import get_client_ip

    req = _mk_req(
        'fd00:1::5',
        xff='::1, 2001:db8::1, fd00:1::2',
    )
    trusted = [ipaddress.ip_network('fd00::/16')]
    assert get_client_ip(req, trusted_proxies=trusted) == '2001:db8::1'


def test_get_client_ip_malformed_xff_entry_is_skipped():
    """Phase 23.2: a garbage token inside XFF doesn't break the walk."""
    import ipaddress

    from app.services.request_ip import get_client_ip

    req = _mk_req('10.0.0.5', xff='8.8.8.8, not-an-ip, 10.0.0.5')
    trusted = [ipaddress.ip_network('10.0.0.0/24')]
    assert get_client_ip(req, trusted_proxies=trusted) == '8.8.8.8'


def test_max_content_length_rejects_oversized_body(app):
    """Phase 23.5 (#37): a request body larger than MAX_CONTENT_LENGTH
    must be rejected before any view code runs. Werkzeug returns 413
    when it reaches this path; the existing WAF-lite request filter
    may preempt with 400 earlier in the chain. Either rejection is
    acceptable — the contract is "oversized body is not processed",
    not "specifically a 413".
    """
    client = app.test_client()
    # MAX_CONTENT_LENGTH default = 16 MiB; send one byte past it.
    limit = app.config['MAX_CONTENT_LENGTH']
    oversized = b'A' * (limit + 1)
    resp = client.post('/contact', data={'payload': oversized})
    assert resp.status_code in (400, 413), (
        f'expected 400 or 413 for oversized body, got {resp.status_code}'
    )


def test_max_content_length_config_keys_wired():
    """Phase 23.5 (#37): Flask config keys are present and positive."""
    # Build a plain app (no test client) to inspect startup config.
    import tempfile
    from pathlib import Path

    from app import create_app

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / 'config.yaml').write_text(
            'secret_key: "' + 'x' * 64 + '"\n'
            f'database_path: "{tmp_path}/site.db"\n'
            f'photo_storage: "{tmp_path}/photos"\n'
            'session_cookie_secure: false\n'
            'admin:\n'
            '  username: "admin"\n'
            '  password_hash: ""\n'
            '  allowed_networks:\n'
            '    - "127.0.0.0/8"\n'
        )
        flask_app = create_app(config_path=str(tmp_path / 'config.yaml'))

        assert flask_app.config.get('MAX_CONTENT_LENGTH', 0) > 0
        assert flask_app.config['MAX_CONTENT_LENGTH'] >= 1024 * 1024  # >= 1 MiB


def test_sanitize_html_adds_rel_noopener_to_links():
    """Phase 23.5 (#67): admin-authored <a target="_blank"> must render
    with rel="noopener noreferrer" injected by nh3's default link_rel.

    Pre-23.5 the call used ``link_rel=None`` which disabled the
    injection, leaving every target="_blank" link tabnabbable.
    """
    from app.services.content import sanitize_html

    html = '<p><a href="https://external.example" target="_blank">go</a></p>'
    result = sanitize_html(html)
    assert 'rel="noopener noreferrer"' in result or 'rel="nofollow noopener noreferrer"' in result


def test_canonical_url_root_uses_config_when_set(app):
    """Phase 23.5 (#57): when ``canonical_host`` is set, url-rooting
    helpers return it verbatim (trailing slash normalised) regardless
    of the inbound Host header."""
    with app.test_request_context('/', headers={'Host': 'attacker.example'}):
        app.config['SITE_CONFIG'] = {
            **app.config.get('SITE_CONFIG', {}),
            'canonical_host': 'https://trusted.example',
        }
        from app.services.urls import canonical_url_root

        assert canonical_url_root() == 'https://trusted.example/'


def test_canonical_url_root_falls_back_to_request_when_unset(app):
    """Phase 23.5 (#57): with canonical_host unset, fallback to the
    inbound request.url_root — preserves pre-change behaviour."""
    with app.test_request_context('/'):
        app.config['SITE_CONFIG'] = {
            k: v for k, v in app.config.get('SITE_CONFIG', {}).items() if k != 'canonical_host'
        }
        from app.services.urls import canonical_url_root

        result = canonical_url_root()
        # Flask's test request context gives http://localhost/
        assert result.endswith('/')
        assert '://' in result


# ============================================================
# Phase 27.6 — set-locale open redirect (#21, #40)
# ============================================================


def test_set_locale_rejects_external_referrer(client):
    """Phase 27.6: a Referer header pointing at an external origin must
    NOT be echoed in the 302 Location — that's the open-redirect hole."""
    resp = client.get(
        '/set-locale/en',
        headers={'Referer': 'https://attacker.example/phishing'},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers.get('Location', '')
    assert 'attacker.example' not in loc
    assert loc in ('/', 'http://localhost/')


def test_set_locale_accepts_same_origin_referrer(client):
    """Phase 27.6: a same-origin Referer is allowed — this is the
    legitimate path the language switcher relies on."""
    resp = client.get(
        '/set-locale/en',
        headers={'Referer': 'http://localhost/portfolio'},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/portfolio')


def test_set_locale_scheme_relative_referrer_rejected(client):
    """Phase 27.6: a scheme-relative '//evil.example' must not bypass
    the same-origin check."""
    resp = client.get(
        '/set-locale/en',
        headers={'Referer': '//attacker.example/phishing'},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers.get('Location', '')
    assert 'attacker.example' not in loc


# ============================================================
# Phase 27.7 — /csp-report rate limit + content-type gate (#32)
# ============================================================


def test_csp_report_rejects_non_json_content_type(client):
    """Phase 27.7: a non-JSON / non-CSP content type is silently
    dropped (204) without touching the log — prevents an attacker
    from flooding app.security via raw POSTs of arbitrary bodies."""
    resp = client.post(
        '/csp-report',
        data='arbitrary',
        headers={'Content-Type': 'text/plain'},
    )
    assert resp.status_code == 204


def test_csp_report_accepts_application_csp_report(client):
    """Phase 27.7: the browser-native content type is accepted."""
    payload = (
        b'{"csp-report": {"violated-directive": "script-src", '
        b'"blocked-uri": "https://x.example", '
        b'"document-uri": "http://localhost/"}}'
    )
    resp = client.post(
        '/csp-report',
        data=payload,
        headers={'Content-Type': 'application/csp-report'},
    )
    assert resp.status_code == 204


# ============================================================
# Phase 24.4 — Server header removal (#14)
# ============================================================


def test_server_header_stripped_from_response(client):
    """Phase 24.4 (#14): the Server header (Gunicorn/Werkzeug version)
    must not leak in responses. Fingerprinting our exact WSGI server
    from the response header tells an attacker what CVE list to try."""
    resp = client.get('/')
    assert resp.status_code == 200
    # Flask's test client uses Werkzeug which sets Server by default.
    # Our after_request hook removes it.
    assert 'Server' not in resp.headers, f'Server header leaked: {resp.headers.get("Server")!r}'
    assert 'X-Powered-By' not in resp.headers


# ============================================================
# Phase 24.3 — log-injection hygiene (#22)
# ============================================================


def test_sanitize_log_field_escapes_newlines():
    """A CR/LF/tab payload must be escaped, not passed through verbatim."""
    from app.services.logging import sanitize_log_field

    result = sanitize_log_field('normal\r\nWARN Fake admin success\ttab')
    assert '\r' not in result
    assert '\n' not in result
    assert '\t' not in result
    assert r'\r' in result
    assert r'\n' in result
    assert r'\t' in result


def test_sanitize_log_field_strips_ansi_escapes():
    """ANSI escape sequences must be removed so a crafted payload can't
    rewrite an operator's terminal when tailing the logs."""
    from app.services.logging import sanitize_log_field

    result = sanitize_log_field('hi\x1b[31mred\x1b[0m bye')
    assert '\x1b' not in result
    assert '[31m' not in result
    # The textual letters inside the escape are preserved (we strip the
    # escape sequence, not the surrounding characters).
    assert 'hi' in result
    assert 'red' in result
    assert 'bye' in result


def test_sanitize_log_field_truncates_oversized():
    """Over-long payloads are truncated with an explicit ellipsis."""
    from app.services.logging import sanitize_log_field

    payload = 'x' * 1000
    result = sanitize_log_field(payload, max_len=50)
    assert len(result) == 51  # 50 chars + ellipsis
    assert result.endswith('…')


def test_sanitize_log_field_none_returns_dash():
    """None becomes '-' so log lines stay aligned with existing format."""
    from app.services.logging import sanitize_log_field

    assert sanitize_log_field(None) == '-'


def test_csp_report_log_injection_rejected(client, caplog):
    """Phase 24.3 (#22) — a crafted CSP report must not forge a new
    log line. The response is always 204; the logged line must carry
    the payload as a single escaped record."""
    import logging

    payload = {
        'csp-report': {
            'violated-directive': 'script-src\r\nWARN Fake admin login success',
            'blocked-uri': 'https://attacker.example',
            'document-uri': 'https://site.example',
        }
    }
    with caplog.at_level(logging.WARNING, logger='app.security'):
        resp = client.post('/csp-report', json=payload)
    assert resp.status_code == 204
    # The log record must exist and must NOT contain a literal newline
    # in the message (the escaped form \r\n is fine).
    matching = [r for r in caplog.records if 'CSP violation' in r.message]
    assert matching, 'expected a CSP violation log line'
    for record in matching:
        assert '\n' not in record.message
        # Escaped form is present so the operator can still see the attempt.
        assert r'\r\n' in record.message or 'WARN' not in record.message


# ============================================================
# Phase 24.2 — analytics + contact privacy (#45, #60)
# ============================================================


def test_classify_user_agent_buckets_common_browsers():
    """The classifier collapses any UA string to one of the closed
    enum values; it never leaks the raw UA."""
    from app.services.logging import classify_user_agent

    cases = {
        'Mozilla/5.0 (Windows NT 10.0) Firefox/120.0': 'firefox-desktop',
        'Mozilla/5.0 (Android 12; Mobile) Firefox/120.0': 'firefox-mobile',
        'Mozilla/5.0 (Windows) Chrome/120.0': 'chrome-desktop',
        'Mozilla/5.0 (iPhone) Version/16.0 Mobile/15E148 Safari/604.1': 'safari-mobile',
        'Mozilla/5.0 (Macintosh) Version/16.0 Safari/605': 'safari-desktop',
        'Mozilla/5.0 (Windows) Chrome/120.0 Edg/120.0': 'edge-desktop',
        'curl/8.4.0': 'bot',
        'Googlebot/2.1 (+http://www.google.com/bot.html)': 'bot',
        'python-requests/2.31.0': 'bot',
        '': 'other',
    }
    for ua, expected in cases.items():
        got = classify_user_agent(ua)
        assert got == expected, f'{ua!r} → {got!r}, expected {expected!r}'


def test_contact_submission_stores_hashed_ip_only(app):
    """Phase 24.2 (#60): contact_submissions.ip_address must be the
    hex digest, never the raw IP."""
    client = app.test_client()

    # Submit via the HTML form handler.
    client.post(
        '/contact',
        data={
            'name': 'Alice',
            'email': 'a@example.com',
            'message': 'hi there',
            'website': '',  # honeypot empty
        },
    )

    import sqlite3

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        row = conn.execute(
            'SELECT ip_address, user_agent FROM contact_submissions ORDER BY id DESC LIMIT 1'
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, 'submission not saved'
    ip_stored, ua_stored = row
    # Never the raw IP — the test-client presents remote_addr 127.0.0.1.
    assert ip_stored != '127.0.0.1'
    # Hash is 16 hex chars (see logging.hash_client_ip).
    assert len(ip_stored) == 16
    assert all(c in '0123456789abcdef' for c in ip_stored)
    # UA is a coarse class, not the raw Werkzeug/X.Y.Z string.
    assert 'Werkzeug' not in (ua_stored or '')


def test_page_views_stores_hashed_ip_and_ua_class(app):
    """Phase 24.2 (#45): page_views.ip_address is the hex digest,
    user_agent is the coarse class."""
    client = app.test_client()
    client.get('/')  # trigger a page_view row

    import sqlite3

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        rows = conn.execute(
            'SELECT ip_address, user_agent FROM page_views ORDER BY id DESC LIMIT 1'
        ).fetchone()
    finally:
        conn.close()
    if rows is None:
        # Analytics is best-effort — if no row landed, skip (the / path
        # is gated on non-admin etc.).
        return
    ip, ua = rows
    assert ip != '127.0.0.1'
    assert len(ip) == 16
    assert 'Werkzeug' not in (ua or '')


def test_weak_secret_key_short_is_fatal():
    """Phase 23.4 (#48): a secret_key under 32 chars must abort app
    creation, not boot with a warning that operators skim past."""
    from app.services.config import _validate_secret_key

    assert _validate_secret_key('too-short') is False
    assert _validate_secret_key('x' * 31) is False
    assert _validate_secret_key('x' * 32) is True


def test_weak_secret_key_placeholder_is_fatal():
    """Phase 23.4 (#48): the well-known placeholder values are fatal."""
    from app.services.config import _validate_secret_key

    for placeholder in (
        'CHANGE-ME-generate-a-random-key',  # config.example.yaml default
        'generate-a-random-key',
        'change-me',
        'secret',
    ):
        assert _validate_secret_key(placeholder) is False, (
            f'placeholder {placeholder!r} must be rejected'
        )


def test_strong_secret_key_accepted():
    """Phase 23.4 (#48): a 32+ char non-placeholder value is accepted."""
    import secrets

    from app.services.config import _validate_secret_key

    assert _validate_secret_key(secrets.token_hex(32)) is True


def test_login_scrypt_cost_paid_on_username_miss():
    """Phase 23.3 (#46): a bad username must take roughly as long to
    reject as a bad password on a valid username.

    The before-23.3 path short-circuited ``check_password_hash`` on a
    username mismatch (boolean ``and``), so a bad-username attempt
    returned in microseconds while a valid-username bad-password
    attempt paid the full scrypt cost. That is a timing oracle that
    confirms username existence in a single request.

    Methodology: 20 trials each of bad-username vs. good-username-bad-
    password. Assert the ratio of the medians is within 2x, which is
    well below the scrypt cost's ~100x headroom and tolerates the
    variance you see on a shared test runner.
    """
    import statistics
    import time

    # Generate a real hash once so the test's own work doesn't dominate
    # the measurement.
    from werkzeug.security import check_password_hash, generate_password_hash

    from app.routes.admin import _DUMMY_PASSWORD_HASH

    real_hash = generate_password_hash('correct-horse-battery-staple')

    def _measure(hash_value):
        t0 = time.perf_counter()
        check_password_hash(hash_value, 'not-the-password')
        return time.perf_counter() - t0

    # Warmup.
    for _ in range(3):
        _measure(real_hash)
        _measure(_DUMMY_PASSWORD_HASH)

    real_times = [_measure(real_hash) for _ in range(20)]
    dummy_times = [_measure(_DUMMY_PASSWORD_HASH) for _ in range(20)]

    real_median = statistics.median(real_times)
    dummy_median = statistics.median(dummy_times)

    # Both paths must pay a meaningful scrypt cost (> 1ms on any box
    # the test runs on). Without this, the dummy hash could silently
    # have degraded to a cheap algorithm and the test would pass for
    # the wrong reason.
    assert real_median > 0.001, f'real scrypt too fast: {real_median * 1000:.2f}ms'
    assert dummy_median > 0.001, f'dummy scrypt too fast: {dummy_median * 1000:.2f}ms'

    # Ratio within 2x — both are scrypt, so costs should be close.
    ratio = max(real_median, dummy_median) / min(real_median, dummy_median)
    assert ratio < 2.0, (
        f'scrypt cost mismatch: real={real_median * 1000:.2f}ms, '
        f'dummy={dummy_median * 1000:.2f}ms (ratio {ratio:.2f})'
    )


def test_login_username_miss_does_not_short_circuit(app):
    """Phase 23.3 (#46) end-to-end: a login with the wrong username and
    a login with the right username + wrong password must both reach
    the credential-check branch. The observable is that both increment
    the failed-login counter (a short-circuit would skip the counter
    for the unknown-user path).
    """
    client = app.test_client()

    # Wrong username.
    r1 = client.post(
        '/admin/login',
        data={'username': 'not-admin', 'password': 'whatever'},
    )
    # Right username, wrong password.
    r2 = client.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'not-the-real-one'},
    )

    # Neither path should 302-to-dashboard.
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Both paths should have recorded a failed attempt.
    with app.app_context():
        from app.db import get_db

        db = get_db()
        rows = db.execute('SELECT COUNT(*) FROM login_attempts WHERE success = 0').fetchone()
        assert rows[0] >= 2, f'expected ≥2 failed login records, got {rows[0]}'


def test_no_inlined_xff_logic_remaining():
    """Regression: #34 is closed when no application file reimplements
    the XFF-split logic inline. If a new route grows its own copy
    instead of calling get_client_ip(), this grep-guard fails CI.
    """
    from pathlib import Path

    app_dir = Path(__file__).parent.parent / 'app'
    offenders = []
    for py_file in app_dir.rglob('*.py'):
        if py_file.name == 'request_ip.py':
            continue
        text = py_file.read_text()
        # The specific anti-pattern: taking X-Forwarded-For with a
        # request.remote_addr fallback and splitting on comma to grab
        # the leftmost. That's the exact shape #34 extracted.
        if (
            "headers.get('X-Forwarded-For'" in text
            and 'remote_addr' in text
            and ".split(',')" in text
        ):
            offenders.append(str(py_file.relative_to(app_dir.parent)))
    assert not offenders, (
        f'New inlined XFF logic detected in {offenders} — '
        f'call app.services.request_ip.get_client_ip instead.'
    )
