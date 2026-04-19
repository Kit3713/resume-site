"""
Edge-case / regression tests for admin session lifecycle — post-pentest.

Covers the High finding from the 2026-04-18 pentest: without
``session.clear()`` on logout, Flask's default itsdangerous-signed cookie
sessions cannot be revoked server-side, and a captured pre-logout cookie
value keeps granting read access to every admin page indefinitely.

The fix (``app/routes/admin.py::logout``) adds ``session.clear()`` so the
response reissues a fresh signed cookie; the old cookie value no longer
deserialises to an authenticated user.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def no_rate_limits(app):
    app.config['RATELIMIT_ENABLED'] = False
    yield
    app.config['RATELIMIT_ENABLED'] = True


def _snapshot_session_cookie(client):
    """Extract the current ``resume_session`` cookie value from the test
    client's jar. Werkzeug's jar keys are ``(domain, path, name)`` tuples
    and the stored object exposes ``.key`` / ``.value`` attributes.
    """
    for cookie in client._cookies.values():
        if cookie.key == 'resume_session':
            return cookie.value
    return None


def test_logout_invalidates_pre_logout_cookie(auth_client, no_rate_limits):
    """Regression for pentest High finding: the pre-logout cookie must not
    grant read access after the admin has hit ``/admin/logout``.
    """
    pre = auth_client.get('/admin/')
    assert pre.status_code == 200, 'baseline: dashboard should render when logged in'

    snapshot = _snapshot_session_cookie(auth_client)
    assert snapshot, 'could not snapshot pre-logout cookie'

    assert auth_client.get('/admin/logout', follow_redirects=False).status_code == 302

    # Same client jar (updated by logout) should redirect to login
    post = auth_client.get('/admin/', follow_redirects=False)
    assert post.status_code == 302
    assert '/admin/login' in post.headers.get('Location', '')

    # Replay the pre-logout cookie value in a fresh client — this is the
    # attack we're guarding against. ``session.clear()`` in the logout
    # handler makes the old signed cookie invalid.
    replay = auth_client.application.test_client()
    replay.set_cookie('resume_session', snapshot, domain='localhost')
    response = replay.get('/admin/', follow_redirects=False)
    assert response.status_code == 302, (
        f'stale-cookie replay granted admin access (status {response.status_code}); '
        'session.clear() is missing from the logout handler'
    )
    assert '/admin/login' in response.headers.get('Location', '')


def test_logout_clears_every_admin_page(auth_client, no_rate_limits):
    """After logout, every admin blueprint route must redirect to login
    when the client replays its previous cookie.
    """
    for path in ('/admin/', '/admin/settings', '/admin/blog', '/admin/photos'):
        assert auth_client.get(path).status_code == 200, (
            f'baseline: {path} should render when logged in'
        )

    snapshot = _snapshot_session_cookie(auth_client)
    assert snapshot

    auth_client.get('/admin/logout')

    replay = auth_client.application.test_client()
    replay.set_cookie('resume_session', snapshot, domain='localhost')
    for path in ('/admin/', '/admin/settings', '/admin/blog', '/admin/photos'):
        response = replay.get(path, follow_redirects=False)
        assert response.status_code in (302, 401), (
            f'stale cookie granted read access to {path} (status {response.status_code})'
        )
