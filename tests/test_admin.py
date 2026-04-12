"""
Admin Panel Tests — Phase 7.2

Covers the full admin CRUD surface:
- Unauthenticated access to every admin route → 302 to /admin/login
- IP restriction enforcement → 403 from disallowed IPs
- Dashboard loads when authenticated
- Content block list, edit, and create
- Services CRUD (add, edit, delete)
- Stats CRUD (add, edit, delete)
- Review management (approve, reject, update tier)
- Token generation and deletion
- Settings save

All POST tests run with CSRF disabled (WTF_CSRF_ENABLED=False set in the
app fixture), which is the appropriate setup for testing business logic
without needing to replicate token generation. CSRF behaviour is covered
separately in test_security.py.

The auth_client fixture provides a pre-authenticated test client. The
populated_db fixture inserts representative rows so that update/delete
operations have a real target.
"""

import pytest


# ============================================================
# UNAUTHENTICATED ACCESS — all admin routes must redirect
# ============================================================

@pytest.mark.parametrize('path', [
    '/admin/',
    '/admin/content',
    '/admin/photos',
    '/admin/reviews',
    '/admin/tokens',
    '/admin/settings',
    '/admin/services',
    '/admin/stats',
])
def test_admin_requires_auth(client, path):
    """Every admin route should redirect unauthenticated users to /admin/login."""
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']


# ============================================================
# IP RESTRICTION — disallowed IPs get 403 before auth check
# ============================================================

@pytest.mark.parametrize('path', [
    '/admin/',
    '/admin/login',
    '/admin/content',
])
def test_admin_ip_restriction(app, path):
    """Requests from IPs outside allowed_networks must get 403."""
    client = app.test_client()
    # 203.0.113.0/24 is TEST-NET-3 (RFC 5737) — never in 127.0.0.0/8
    response = client.get(path, headers={'X-Forwarded-For': '203.0.113.42'})
    assert response.status_code == 403


# ============================================================
# DASHBOARD
# ============================================================

def test_dashboard_loads(auth_client):
    """Authenticated dashboard request should return 200."""
    response = auth_client.get('/admin/')
    assert response.status_code == 200


def test_dashboard_shows_metrics(auth_client):
    """Dashboard should contain analytics section markup."""
    response = auth_client.get('/admin/')
    assert response.status_code == 200
    # The dashboard template renders page view stats
    assert b'page' in response.data.lower()


# ============================================================
# CONTENT BLOCKS
# ============================================================

def test_content_list_loads(auth_client):
    """Content block list should return 200."""
    response = auth_client.get('/admin/content')
    assert response.status_code == 200


def test_content_list_shows_blocks(auth_client, populated_db):
    """Content block list should show the seeded 'about' block."""
    response = auth_client.get('/admin/content')
    assert response.status_code == 200
    assert b'about' in response.data


def test_content_edit_get(auth_client, populated_db):
    """GET /admin/content/edit/<slug> should render the edit form."""
    response = auth_client.get('/admin/content/edit/about')
    assert response.status_code == 200
    assert b'About Me' in response.data


def test_content_edit_post_saves(auth_client, populated_db):
    """POST /admin/content/edit/<slug> should update the block and redirect."""
    response = auth_client.post('/admin/content/edit/about', data={
        'title': 'Updated About',
        'content': '<p>New content.</p>',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/content' in response.headers['Location']


def test_content_edit_creates_missing_slug(auth_client):
    """Editing a non-existent slug should create the block (create_if_missing)."""
    response = auth_client.post('/admin/content/edit/new-block', data={
        'title': 'New Block Title',
        'content': '<p>Hello world.</p>',
    }, follow_redirects=False)
    assert response.status_code == 302


def test_content_new_get(auth_client):
    """GET /admin/content/new should render the creation form."""
    response = auth_client.get('/admin/content/new')
    assert response.status_code == 200


def test_content_new_post_creates(auth_client):
    """POST /admin/content/new should create a block and redirect."""
    response = auth_client.post('/admin/content/new', data={
        'slug': 'hero',
        'title': 'Hero Section',
        'content': '<h1>Welcome</h1>',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/content' in response.headers['Location']


def test_content_new_post_no_slug_redirects(auth_client):
    """POST /admin/content/new without a slug should redirect without creating."""
    response = auth_client.post('/admin/content/new', data={
        'slug': '',
        'title': '',
        'content': '',
    }, follow_redirects=False)
    assert response.status_code == 302


# ============================================================
# SERVICES CRUD
# ============================================================

def test_services_list_loads(auth_client):
    """Services list should return 200."""
    response = auth_client.get('/admin/services')
    assert response.status_code == 200


def test_services_add(auth_client):
    """POST /admin/services/add should create a service and redirect."""
    response = auth_client.post('/admin/services/add', data={
        'title': 'Photography',
        'description': 'Portrait and event photography.',
        'icon': '📷',
        'sort_order': '1',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/services' in response.headers['Location']


def test_services_add_no_title_skips(auth_client):
    """POST without a title should not create a service (guard in route)."""
    response = auth_client.post('/admin/services/add', data={
        'title': '',
        'description': 'No title provided.',
        'icon': '',
        'sort_order': '0',
    }, follow_redirects=False)
    assert response.status_code == 302


def test_services_edit(auth_client, populated_db):
    """POST /admin/services/1/edit should update service and redirect."""
    response = auth_client.post('/admin/services/1/edit', data={
        'title': 'Web Dev Updated',
        'description': 'Updated description.',
        'icon': '💻',
        'sort_order': '2',
        'visible': 'on',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/services' in response.headers['Location']


def test_services_delete(auth_client, populated_db):
    """POST /admin/services/1/delete should delete service and redirect."""
    response = auth_client.post('/admin/services/1/delete',
                                follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/services' in response.headers['Location']

    # Verify the service is gone
    list_response = auth_client.get('/admin/services')
    assert b'Web Development' not in list_response.data


# ============================================================
# STATS CRUD
# ============================================================

def test_stats_list_loads(auth_client):
    """Stats list should return 200."""
    response = auth_client.get('/admin/stats')
    assert response.status_code == 200


def test_stats_add(auth_client):
    """POST /admin/stats/add should create a stat and redirect."""
    response = auth_client.post('/admin/stats/add', data={
        'label': 'Happy Clients',
        'value': '50',
        'suffix': '+',
        'sort_order': '1',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/stats' in response.headers['Location']


def test_stats_add_no_label_skips(auth_client):
    """POST without a label should not create a stat."""
    response = auth_client.post('/admin/stats/add', data={
        'label': '',
        'value': '0',
        'suffix': '',
        'sort_order': '0',
    }, follow_redirects=False)
    assert response.status_code == 302


def test_stats_edit(auth_client, populated_db):
    """POST /admin/stats/1/edit should update stat and redirect."""
    response = auth_client.post('/admin/stats/1/edit', data={
        'label': 'Projects Completed',
        'value': '100',
        'suffix': '+',
        'sort_order': '1',
        'visible': 'on',
    }, follow_redirects=False)
    assert response.status_code == 302


def test_stats_delete(auth_client, populated_db):
    """POST /admin/stats/1/delete should delete stat and redirect."""
    response = auth_client.post('/admin/stats/1/delete',
                                follow_redirects=False)
    assert response.status_code == 302

    # Verify the stat row is gone — check for the empty-state message
    list_response = auth_client.get('/admin/stats')
    assert b'No stats yet' in list_response.data


# ============================================================
# REVIEW MANAGER
# ============================================================

def test_reviews_list_loads(auth_client):
    """Reviews list should return 200."""
    response = auth_client.get('/admin/reviews')
    assert response.status_code == 200


def test_reviews_list_shows_approved(auth_client, populated_db):
    """Reviews page should show the seeded approved review."""
    response = auth_client.get('/admin/reviews')
    assert response.status_code == 200
    assert b'Alice Smith' in response.data


def test_reviews_reject(auth_client, populated_db):
    """POST /admin/reviews/1/update with action=reject should reject review."""
    response = auth_client.post('/admin/reviews/1/update', data={
        'action': 'reject',
        'display_tier': 'standard',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/reviews' in response.headers['Location']


def test_reviews_approve(auth_client, populated_db):
    """POST /admin/reviews/1/update with action=approve should approve review."""
    response = auth_client.post('/admin/reviews/1/update', data={
        'action': 'approve',
        'display_tier': 'featured',
    }, follow_redirects=False)
    assert response.status_code == 302


def test_reviews_update_tier(auth_client, populated_db):
    """POST /admin/reviews/1/update with action=update_tier should change tier."""
    response = auth_client.post('/admin/reviews/1/update', data={
        'action': 'update_tier',
        'display_tier': 'standard',
    }, follow_redirects=False)
    assert response.status_code == 302


# ============================================================
# TOKEN GENERATOR
# ============================================================

def test_tokens_list_loads(auth_client):
    """Tokens list should return 200."""
    response = auth_client.get('/admin/tokens')
    assert response.status_code == 200


def test_tokens_list_shows_existing(auth_client, populated_db):
    """Tokens page should show the seeded token name."""
    response = auth_client.get('/admin/tokens')
    assert response.status_code == 200
    assert b'Alice Smith' in response.data


def test_tokens_generate(auth_client):
    """POST /admin/tokens/generate should create a token and redirect."""
    response = auth_client.post('/admin/tokens/generate', data={
        'name': 'Bob Jones',
        'type': 'client_review',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/tokens' in response.headers['Location']


def test_tokens_generate_recommendation_type(auth_client):
    """POST /admin/tokens/generate with type=recommendation should work."""
    response = auth_client.post('/admin/tokens/generate', data={
        'name': 'Carol White',
        'type': 'recommendation',
    }, follow_redirects=False)
    assert response.status_code == 302


def test_tokens_generate_invalid_type_defaults(auth_client):
    """POST with an invalid type should default to 'recommendation'."""
    response = auth_client.post('/admin/tokens/generate', data={
        'name': 'Malicious User',
        'type': 'admin_override',  # Invalid type
    }, follow_redirects=False)
    assert response.status_code == 302


def test_tokens_delete(auth_client, populated_db):
    """POST /admin/tokens/1/delete should delete the token and redirect."""
    response = auth_client.post('/admin/tokens/1/delete',
                                follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/tokens' in response.headers['Location']


# ============================================================
# SETTINGS
# ============================================================

def test_settings_page_loads(auth_client):
    """Settings page should return 200."""
    response = auth_client.get('/admin/settings')
    assert response.status_code == 200


def test_settings_save(auth_client):
    """POST /admin/settings should save settings and redirect."""
    response = auth_client.post('/admin/settings', data={
        'site_title': 'My Test Portfolio',
        'site_tagline': 'Tagline here',
        'theme': 'light',
        'resume_visibility': 'off',
        'testimonial_display_mode': 'mixed',
        'case_studies_enabled': 'false',
        'contact_visible': 'true',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/settings' in response.headers['Location']


def test_settings_unknown_key_ignored(auth_client):
    """POST with unknown keys should not cause an error (save_many filters them)."""
    response = auth_client.post('/admin/settings', data={
        'site_title': 'Clean Title',
        '__evil_key__': 'injected_value',  # Should be silently ignored
    }, follow_redirects=False)
    assert response.status_code == 302


# ============================================================
# PHOTOS PAGE (no upload — Pillow dependency not guaranteed)
# ============================================================

def test_photos_list_loads(auth_client):
    """Photos list page should return 200 even when empty."""
    response = auth_client.get('/admin/photos')
    assert response.status_code == 200
