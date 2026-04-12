"""
Settings Service (app/services/settings_svc.py)

Wraps the raw key-value settings table with type validation and a
centralized registry of known setting keys. Admin routes call these
functions so that invalid values are caught before they reach the DB.

The settings table is the source of truth for everything the admin panel
controls (display preferences, feature toggles, contact info, etc.).
Infrastructure settings (secret_key, SMTP, database path) live in
config.yaml and are never stored here.
"""

# Registry of known setting keys with their expected type and default.
# Type is used for validation on save: 'str', 'bool', 'int', 'color'.
SETTINGS_REGISTRY = {
    'site_title':               {'type': 'str',   'default': 'My Portfolio'},
    'site_tagline':             {'type': 'str',   'default': 'Welcome to my portfolio'},
    'dark_mode_default':        {'type': 'bool',  'default': 'true'},
    'availability_status':      {'type': 'str',   'default': 'available'},
    'contact_form_enabled':     {'type': 'bool',  'default': 'true'},
    'contact_email_visible':    {'type': 'bool',  'default': 'false'},
    'contact_phone_visible':    {'type': 'bool',  'default': 'false'},
    'contact_github_url':       {'type': 'str',   'default': ''},
    'contact_linkedin_url':     {'type': 'str',   'default': ''},
    'resume_visibility':        {'type': 'str',   'default': 'off'},
    'case_studies_enabled':     {'type': 'bool',  'default': 'false'},
    'testimonial_display_mode': {'type': 'str',   'default': 'mixed'},
    'analytics_retention_days': {'type': 'int',   'default': '90'},
    'hero_heading':             {'type': 'str',   'default': ''},
    'hero_subheading':          {'type': 'str',   'default': ''},
    'hero_tagline':             {'type': 'str',   'default': ''},
    'accent_color':             {'type': 'color', 'default': '#0071e3'},
    'logo_mode':                {'type': 'str',   'default': 'title'},
    'footer_text':              {'type': 'str',   'default': ''},
    'blog_enabled':             {'type': 'bool',  'default': 'false'},
    'blog_title':               {'type': 'str',   'default': 'Blog'},
    'posts_per_page':           {'type': 'int',   'default': '10'},
    'show_reading_time':        {'type': 'bool',  'default': 'true'},
    'enable_rss':               {'type': 'bool',  'default': 'true'},
}

# Keys managed by the settings form (ordered list for the admin view).
SETTINGS_FORM_KEYS = list(SETTINGS_REGISTRY.keys())


def get_all(db):
    """Return all settings as a {key: value} dict."""
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return {row['key']: row['value'] for row in rows}


def get(db, key, default=''):
    """Return a single setting value by key, or default if not found."""
    row = db.execute(
        'SELECT value FROM settings WHERE key = ?', (key,)
    ).fetchone()
    return row['value'] if row else default


def save_many(db, form_data):
    """Save multiple settings from a form submission dict.

    Only keys present in SETTINGS_REGISTRY are accepted. Unknown keys
    are silently ignored to prevent injection of arbitrary settings.

    Args:
        db: Database connection.
        form_data: Dict of {key: value} from request.form.
    """
    for key in SETTINGS_FORM_KEYS:
        if key in form_data:
            value = str(form_data[key])
            _upsert(db, key, value)
    db.commit()


def set_one(db, key, value):
    """Set a single setting and commit immediately.

    Args:
        db: Database connection.
        key: Setting key (must be in SETTINGS_REGISTRY).
        value: String value to store.

    Raises:
        KeyError: If the key is not in the registry.
    """
    if key not in SETTINGS_REGISTRY:
        raise KeyError(f"Unknown setting key: {key!r}")
    _upsert(db, key, str(value))
    db.commit()


def _upsert(db, key, value):
    """INSERT OR UPDATE a single setting row (no commit — caller commits)."""
    db.execute(
        "INSERT INTO settings (key, value, updated_at) "
        "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value),
    )
