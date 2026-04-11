"""Tests for Phase 1 & 2: routes, auth, IP restriction, public pages, contact, review."""


# --- Phase 1: Foundation ---

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


# --- Phase 2: Public Pages ---

def test_portfolio_page(client):
    """Portfolio page should load with empty state."""
    response = client.get('/portfolio')
    assert response.status_code == 200
    assert b'Portfolio' in response.data


def test_services_page(client):
    """Services page should load."""
    response = client.get('/services')
    assert response.status_code == 200
    assert b'Services' in response.data


def test_testimonials_page(client):
    """Testimonials page should load with empty state."""
    response = client.get('/testimonials')
    assert response.status_code == 200
    assert b'Testimonials' in response.data


def test_projects_page(client):
    """Projects page should load with empty state."""
    response = client.get('/projects')
    assert response.status_code == 200
    assert b'Projects' in response.data


def test_certifications_page(client):
    """Certifications page should load with empty state."""
    response = client.get('/certifications')
    assert response.status_code == 200
    assert b'Certifications' in response.data


def test_contact_page(client):
    """Contact page should load with form."""
    response = client.get('/contact')
    assert response.status_code == 200
    assert b'Get in Touch' in response.data
    assert b'name="name"' in response.data
    assert b'name="email"' in response.data
    assert b'name="message"' in response.data


def test_contact_form_submit(client, app):
    """Contact form should accept submissions."""
    response = client.post('/contact', data={
        'name': 'Test User',
        'email': 'test@example.com',
        'message': 'Hello, this is a test message.',
        'website': '',  # honeypot empty
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Message sent successfully' in response.data


def test_contact_form_honeypot(client, app):
    """Honeypot-filled submissions should be saved as spam but show success."""
    response = client.post('/contact', data={
        'name': 'Bot',
        'email': 'bot@spam.com',
        'message': 'Buy stuff now!',
        'website': 'http://spam.com',  # honeypot filled
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Message sent successfully' in response.data


def test_contact_form_validation(client):
    """Missing fields should show error."""
    response = client.post('/contact', data={
        'name': '',
        'email': '',
        'message': '',
    }, follow_redirects=True)
    assert b'Please fill in all required fields' in response.data


def test_case_study_404(client):
    """Nonexistent case study should return 404."""
    response = client.get('/portfolio/nonexistent')
    assert response.status_code == 404


def test_project_detail_404(client):
    """Nonexistent project detail should return 404."""
    response = client.get('/projects/nonexistent')
    assert response.status_code == 404


def test_resume_off_by_default(client):
    """Resume download should be off by default."""
    response = client.get('/resume')
    assert response.status_code == 404


def test_review_invalid_token(client):
    """Invalid review token should show error."""
    response = client.get('/review/invalid-token-123')
    assert response.status_code == 200
    assert b'Invalid Link' in response.data


def test_navbar_has_all_links(client):
    """Navbar should have links to all public pages."""
    response = client.get('/')
    assert b'/services' in response.data
    assert b'/portfolio' in response.data
    assert b'/projects' in response.data
    assert b'/testimonials' in response.data
    assert b'/contact' in response.data


def test_index_has_sections(client):
    """Landing page should have hero and section content."""
    response = client.get('/')
    assert b'hero__heading' in response.data
    assert b'id="about"' in response.data
    assert b'id="contact"' in response.data
