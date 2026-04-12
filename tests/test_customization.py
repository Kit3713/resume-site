"""
Admin Panel Customization Tests -- Phase 9

Covers:
- Settings registry: categories, labels, grouped rendering
- Custom CSS injection into public pages
- Font pairing selection reflected in page output
- Color preset selection updates accent color
- Nav visibility toggles hide/show nav items
- Activity log recording and dashboard display
- Settings form saves all registry keys correctly
"""


# ============================================================
# SETTINGS REGISTRY
# ============================================================

def test_settings_registry_has_categories():
    """Every setting in the registry must have a category."""
    from app.services.settings_svc import SETTINGS_REGISTRY
    for key, meta in SETTINGS_REGISTRY.items():
        assert 'category' in meta, f"Setting '{key}' missing 'category'"
        assert 'label' in meta, f"Setting '{key}' missing 'label'"
        assert 'type' in meta, f"Setting '{key}' missing 'type'"


def test_settings_registry_select_has_options():
    """Select-type settings must define options."""
    from app.services.settings_svc import SETTINGS_REGISTRY
    for key, meta in SETTINGS_REGISTRY.items():
        if meta['type'] == 'select':
            assert 'options' in meta, f"Select setting '{key}' missing 'options'"
            assert len(meta['options']) > 0, f"Select setting '{key}' has empty options"


def test_grouped_settings_returns_all_categories(app):
    """get_grouped_settings should return all defined categories."""
    from app.services.settings_svc import get_grouped_settings, SETTINGS_CATEGORIES
    with app.app_context():
        from app.db import get_db
        db = get_db()
        grouped = get_grouped_settings(db)
        categories = [cat for cat, _ in grouped]
        for cat in SETTINGS_CATEGORIES:
            assert cat in categories, f"Category '{cat}' missing from grouped settings"


def test_grouped_settings_includes_values(app):
    """Grouped settings should include current values from the database."""
    from app.services.settings_svc import get_grouped_settings
    with app.app_context():
        from app.db import get_db
        db = get_db()
        grouped = get_grouped_settings(db)
        # Find the Site Identity category and check site_title
        for cat, items in grouped:
            if cat == 'Site Identity':
                keys = [item['key'] for item in items]
                assert 'site_title' in keys
                title_item = next(i for i in items if i['key'] == 'site_title')
                assert title_item['value'] == 'My Portfolio'
                break


# ============================================================
# SETTINGS PAGE (auto-rendered)
# ============================================================

def test_settings_page_renders_categories(auth_client):
    """Settings page should render category headings from the registry."""
    response = auth_client.get('/admin/settings')
    assert response.status_code == 200
    assert b'Site Identity' in response.data
    assert b'Appearance' in response.data
    assert b'Navigation' in response.data
    assert b'Blog' in response.data


def test_settings_page_renders_color_presets(auth_client):
    """Settings page should show color preset buttons."""
    response = auth_client.get('/admin/settings')
    assert b'preset-btn' in response.data
    assert b'Ocean' in response.data
    assert b'Forest' in response.data


def test_settings_page_renders_font_options(auth_client):
    """Settings page should show font pairing options."""
    response = auth_client.get('/admin/settings')
    assert b'font_pairing' in response.data
    assert b'Space Grotesk' in response.data


def test_settings_page_renders_custom_css_textarea(auth_client):
    """Settings page should include a textarea for custom CSS."""
    response = auth_client.get('/admin/settings')
    assert b'custom_css' in response.data
    assert b'settings-textarea' in response.data


# ============================================================
# CUSTOM CSS INJECTION
# ============================================================

def test_custom_css_injected_into_public_page(auth_client, app):
    """Custom CSS from settings should appear in the public page <head>."""
    auth_client.post('/admin/settings', data={
        'custom_css': '.my-custom-class { color: red; }',
        'site_title': 'Test Site',
    })

    public_client = app.test_client()
    response = public_client.get('/')
    assert b'.my-custom-class { color: red; }' in response.data


def test_empty_custom_css_no_extra_style_tag(client):
    """When custom_css is empty, no extra <style> block should be injected."""
    response = client.get('/')
    # The page should not contain an empty style block for custom CSS
    # (there will be the theme override style block, but not an extra one)
    data = response.data.decode()
    # Count style tags — should have the theme override and possibly the
    # anti-FOUC script, but not an empty custom CSS block
    assert data.count('custom_css') == 0  # No reference to the setting key in output


# ============================================================
# FONT PAIRING
# ============================================================

def test_font_pairing_reflected_in_google_fonts_link(auth_client, app):
    """Changing font_pairing should load the correct Google Fonts family."""
    auth_client.post('/admin/settings', data={
        'font_pairing': 'space-grotesk',
        'site_title': 'Test Site',
    })

    public_client = app.test_client()
    response = public_client.get('/')
    assert b'Space+Grotesk' in response.data


def test_default_font_loads_inter(client):
    """Default font pairing should load Inter."""
    response = client.get('/')
    assert b'Inter' in response.data


# ============================================================
# ACCENT COLOR
# ============================================================

def test_accent_color_in_css_variables(auth_client, app):
    """The accent color setting should be reflected in CSS custom properties."""
    auth_client.post('/admin/settings', data={
        'accent_color': '#E65100',
        'site_title': 'Test Site',
    })

    public_client = app.test_client()
    response = public_client.get('/')
    assert b'#E65100' in response.data


# ============================================================
# NAV VISIBILITY
# ============================================================

def test_nav_hide_services_removes_link(auth_client, app):
    """Setting nav_hide_services=true should remove Services from the nav."""
    auth_client.post('/admin/settings', data={
        'nav_hide_services': 'true',
        'site_title': 'Test Site',
    })

    public_client = app.test_client()
    response = public_client.get('/')
    assert b'>Services</a>' not in response.data


def test_nav_visible_by_default(client):
    """All nav items should be visible by default."""
    response = client.get('/')
    assert b'/services' in response.data
    assert b'/portfolio' in response.data
    assert b'/projects' in response.data
    assert b'/testimonials' in response.data
    assert b'/contact' in response.data


def test_nav_hide_multiple_items(auth_client, app):
    """Multiple nav items can be hidden simultaneously."""
    auth_client.post('/admin/settings', data={
        'nav_hide_portfolio': 'true',
        'nav_hide_testimonials': 'true',
        'site_title': 'Test Site',
    })

    public_client = app.test_client()
    response = public_client.get('/')
    assert b'>Portfolio</a>' not in response.data
    assert b'>Testimonials</a>' not in response.data
    # Others should still be visible
    assert b'>Services</a>' in response.data


# ============================================================
# ACTIVITY LOG
# ============================================================

def test_activity_log_records_action(app):
    """log_action should insert an entry into admin_activity_log."""
    with app.app_context():
        from app.db import get_db
        db = get_db()
        # Ensure the table exists by running migration
        db.executescript(open('migrations/003_admin_customization.sql').read())

        from app.services.activity_log import log_action, get_recent_activity
        log_action(db, 'Test action', 'test', 'detail here')

        entries = get_recent_activity(db, limit=5)
        assert len(entries) >= 1
        assert entries[0]['action'] == 'Test action'
        assert entries[0]['category'] == 'test'
        assert entries[0]['detail'] == 'detail here'


def test_activity_log_on_settings_save(auth_client, app):
    """Saving settings should record an activity log entry."""
    # Apply migration first
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.executescript(open('migrations/003_admin_customization.sql').read())

    auth_client.post('/admin/settings', data={
        'site_title': 'Activity Test',
    })

    with app.app_context():
        from app.db import get_db
        db = get_db()
        from app.services.activity_log import get_recent_activity
        entries = get_recent_activity(db, limit=5)
        actions = [e['action'] for e in entries]
        assert 'Updated settings' in actions


def test_dashboard_shows_activity_log(auth_client, app):
    """Dashboard should display the activity log section when entries exist."""
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.executescript(open('migrations/003_admin_customization.sql').read())
        from app.services.activity_log import log_action
        log_action(db, 'Test dashboard action', 'test', 'visible on dashboard')

    response = auth_client.get('/admin/')
    assert response.status_code == 200
    assert b'Recent Activity' in response.data
    assert b'Test dashboard action' in response.data


# ============================================================
# SETTINGS SAVE WITH NEW KEYS
# ============================================================

def test_settings_save_custom_css(auth_client, app):
    """Custom CSS should be persisted through the settings form."""
    auth_client.post('/admin/settings', data={
        'custom_css': 'body { background: pink; }',
        'site_title': 'CSS Test',
    })

    with app.app_context():
        from app.db import get_db
        from app.models import get_setting
        db = get_db()
        assert get_setting(db, 'custom_css') == 'body { background: pink; }'


def test_settings_save_font_pairing(auth_client, app):
    """Font pairing should be persisted through the settings form."""
    auth_client.post('/admin/settings', data={
        'font_pairing': 'dm-sans',
        'site_title': 'Font Test',
    })

    with app.app_context():
        from app.db import get_db
        from app.models import get_setting
        db = get_db()
        assert get_setting(db, 'font_pairing') == 'dm-sans'


def test_settings_save_nav_visibility(auth_client, app):
    """Nav hide toggles should be persisted through the settings form."""
    auth_client.post('/admin/settings', data={
        'nav_hide_projects': 'true',
        'site_title': 'Nav Test',
    })

    with app.app_context():
        from app.db import get_db
        from app.models import get_setting
        db = get_db()
        assert get_setting(db, 'nav_hide_projects') == 'true'
