"""Tests for Phase 1 foundation: routes, auth, IP restriction, config."""


def test_index_page_loads(client):
    """Landing page should return 200."""
    response = client.get('/')
    assert response.status_code == 200
    assert b'hero' in response.data


def test_index_contains_site_title(client):
    """Landing page should include the default site title."""
    response = client.get('/')
    assert b'My Portfolio' in response.data


def test_dark_theme_default(client):
    """Default theme should be dark."""
    response = client.get('/')
    assert b'data-theme="dark"' in response.data


def test_admin_requires_login(client):
    """Admin dashboard should redirect to login when not authenticated."""
    response = client.get('/admin/', follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']


def test_admin_login_page_loads(client):
    """Admin login page should return 200."""
    response = client.get('/admin/login')
    assert response.status_code == 200
    assert b'Admin Login' in response.data


def test_admin_login_invalid_credentials(client):
    """Invalid login should show error and stay on login page."""
    response = client.post('/admin/login', data={
        'username': 'admin',
        'password': 'wrongpassword',
    }, follow_redirects=True)
    assert b'Invalid credentials' in response.data


def test_admin_ip_restriction(app):
    """Requests from disallowed IPs should get 403."""
    client = app.test_client()
    # Simulate a request from an external IP via X-Forwarded-For
    response = client.get('/admin/login', headers={
        'X-Forwarded-For': '203.0.113.1'
    })
    assert response.status_code == 403


def test_admin_ip_allowed(client):
    """Requests from 127.0.0.1 (test client default) should be allowed."""
    response = client.get('/admin/login')
    assert response.status_code == 200


def test_static_css_loads(client):
    """CSS file should be accessible."""
    response = client.get('/static/css/style.css')
    assert response.status_code == 200
    assert b'--color-bg' in response.data


def test_static_js_loads(client):
    """JavaScript file should be accessible."""
    response = client.get('/static/js/main.js')
    assert response.status_code == 200
    assert b'themeToggle' in response.data
