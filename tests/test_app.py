"""
Test Suite for resume-site

Covers the core functionality across all phases:
- Phase 1: Foundation (routing, auth, IP restriction, static assets)
- Phase 2: Public pages (all visitor-facing pages, contact form, review system)

Tests use the fixtures defined in conftest.py, which provide an isolated
test app with a temporary database for each test function.

Note: Admin panel CRUD operations are tested via manual smoke tests
(see the project README). These automated tests focus on the public-facing
routes and security boundaries.
"""


# ============================================================
# PHASE 1: FOUNDATION
# ============================================================


def test_index_page_loads(client):
    """Landing page should return 200 and contain the hero section."""
    response = client.get('/')
    assert response.status_code == 200
    assert b'hero' in response.data


def test_index_contains_site_title(client):
    """Landing page should include the default site title from seeded settings."""
    response = client.get('/')
    assert b'My Portfolio' in response.data


def test_dark_theme_default(client):
    """The default theme should be dark (set in the seeded settings)."""
    response = client.get('/')
    assert b'data-theme="dark"' in response.data


def test_admin_requires_login(client):
    """Admin dashboard should redirect unauthenticated users to login."""
    response = client.get('/admin/', follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']


def test_admin_login_page_loads(client):
    """Admin login page should be accessible (after passing IP check)."""
    response = client.get('/admin/login')
    assert response.status_code == 200
    assert b'Admin Login' in response.data


def test_admin_login_invalid_credentials(client):
    """Invalid login attempts should show an error message."""
    response = client.post(
        '/admin/login',
        data={
            'username': 'admin',
            'password': 'wrongpassword',
        },
        follow_redirects=True,
    )
    assert b'Invalid credentials' in response.data


def test_admin_ip_restriction(app):
    """Requests from IPs outside allowed_networks should get 403."""
    client = app.test_client()
    # Simulate a request from an external IP via the X-Forwarded-For header
    response = client.get(
        '/admin/login',
        headers={
            'X-Forwarded-For': '203.0.113.1'  # TEST-NET-3 (RFC 5737) — not in allowed_networks
        },
    )
    assert response.status_code == 403


def test_admin_ip_allowed(client):
    """Requests from 127.0.0.1 (test client default) should be allowed."""
    response = client.get('/admin/login')
    assert response.status_code == 200


def test_static_css_loads(client):
    """The CSS stylesheet should be accessible and contain theme variables."""
    response = client.get('/static/css/style.css')
    assert response.status_code == 200
    assert b'--color-bg' in response.data


def test_static_js_loads(client):
    """The JavaScript file should be accessible and contain core functionality."""
    response = client.get('/static/js/main.js')
    assert response.status_code == 200
    assert b'themeToggle' in response.data


# ============================================================
# PHASE 2: PUBLIC PAGES
# ============================================================


def test_portfolio_page(client):
    """Portfolio page should load with empty state when no photos exist."""
    response = client.get('/portfolio')
    assert response.status_code == 200
    assert b'Portfolio' in response.data


def test_services_page(client):
    """Services page should load with empty state when no services exist."""
    response = client.get('/services')
    assert response.status_code == 200
    assert b'Services' in response.data


def test_testimonials_page(client):
    """Testimonials page should load with empty state when no reviews exist."""
    response = client.get('/testimonials')
    assert response.status_code == 200
    assert b'Testimonials' in response.data


def test_projects_page(client):
    """Projects page should load with empty state when no projects exist."""
    response = client.get('/projects')
    assert response.status_code == 200
    assert b'Projects' in response.data


def test_certifications_page(client):
    """Certifications page should load with empty state."""
    response = client.get('/certifications')
    assert response.status_code == 200
    assert b'Certifications' in response.data


def test_contact_page(client):
    """Contact page should display the form with all required fields."""
    response = client.get('/contact')
    assert response.status_code == 200
    assert b'Get in Touch' in response.data
    assert b'name="name"' in response.data
    assert b'name="email"' in response.data
    assert b'name="message"' in response.data


def test_contact_form_submit(client, app, smtp_mock):
    """Valid contact form submissions should save and show a success message."""
    response = client.post(
        '/contact',
        data={
            'name': 'Test User',
            'email': 'test@example.com',
            'message': 'Hello, this is a test message.',
            'website': '',  # Honeypot field — empty for legitimate submissions
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'Message sent successfully' in response.data


def test_contact_form_honeypot(client, app):
    """Honeypot-filled submissions should be silently accepted (flagged as spam)."""
    response = client.post(
        '/contact',
        data={
            'name': 'Bot',
            'email': 'bot@spam.com',
            'message': 'Buy stuff now!',
            'website': 'http://spam.com',  # Honeypot filled — indicates a bot
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    # Same success message shown to avoid revealing the honeypot to bots
    # (spam submissions skip the SMTP relay, so no smtp_mock needed)
    assert b'Message sent successfully' in response.data


def test_contact_form_validation(client):
    """Submissions with missing required fields should show a validation error."""
    response = client.post(
        '/contact',
        data={
            'name': '',
            'email': '',
            'message': '',
        },
        follow_redirects=True,
    )
    assert b'Please fill in all required fields' in response.data


def test_contact_smtp_failure_flashes_sorry(client, app, monkeypatch):
    """Issue #80 — when SMTP relay fails, the visitor sees a sorry flash, not success."""
    monkeypatch.setattr(
        'app.services.mail.send_contact_email',
        lambda name, email, message: False,
    )
    response = client.post(
        '/contact',
        data={
            'name': 'Test User',
            'email': 'test@example.com',
            'message': 'Hello, this is a test message.',
            'website': '',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as sess:
        flashes = sess.get('_flashes', [])
    categories_messages = [(cat, msg) for cat, msg in flashes]
    assert any('Sorry' in msg and cat == 'error' for cat, msg in categories_messages), (
        f"Expected a sorry-couldn't-send error flash, got: {categories_messages}"
    )
    assert not any('successfully' in msg for _cat, msg in categories_messages), (
        f'Did not expect a success flash on SMTP failure, got: {categories_messages}'
    )


def test_contact_validation_failure_preserves_input(client):
    """Issue #81 — validation errors re-render the form with the visitor's typed values."""
    response = client.post(
        '/contact',
        data={
            'name': 'Jane Doe',
            'email': 'not-a-valid-email',
            'message': 'A message I do not want to retype, please preserve me.',
            'website': '',
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'value="Jane Doe"' in body
    assert 'value="not-a-valid-email"' in body
    assert 'A message I do not want to retype, please preserve me.' in body


def test_case_study_404(client):
    """Nonexistent case study slugs should return 404."""
    response = client.get('/portfolio/nonexistent')
    assert response.status_code == 404


def test_project_detail_404(client):
    """Nonexistent project slugs should return 404."""
    response = client.get('/projects/nonexistent')
    assert response.status_code == 404


def test_resume_off_by_default(client):
    """Resume download should return 404 when visibility is set to 'off' (default)."""
    response = client.get('/resume')
    assert response.status_code == 404


def test_review_invalid_token(client):
    """Invalid review tokens should show an error message (not a 404)."""
    response = client.get('/review/invalid-token-123')
    assert response.status_code == 200
    assert b'Invalid Link' in response.data


def test_navbar_has_all_links(client):
    """The navigation bar should include links to all main public pages."""
    response = client.get('/')
    assert b'/services' in response.data
    assert b'/portfolio' in response.data
    assert b'/projects' in response.data
    assert b'/testimonials' in response.data
    assert b'/contact' in response.data


def test_index_has_sections(client):
    """Landing page should contain the hero and key scroll sections."""
    response = client.get('/')
    assert b'hero__heading' in response.data
    assert b'id="about"' in response.data
    assert b'id="contact"' in response.data
