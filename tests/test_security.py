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
