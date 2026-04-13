"""
Settings Service (app/services/settings_svc.py)

Wraps the raw key-value settings table with type validation, a centralized
registry of known setting keys, and category-based grouping for the admin UI.

The settings table is the source of truth for everything the admin panel
controls (display preferences, feature toggles, contact info, etc.).
Infrastructure settings (secret_key, SMTP, database path) live in
config.yaml and are never stored here.

The registry drives the admin settings page: each entry defines the key,
type, default value, human-readable label, category for grouping, and
options for select/preset widgets. Adding a new setting to any feature
requires only a registry entry and a migration for the default value.

Caching (Phase 12.1):
    Every HTTP request runs `inject_settings()` which used to issue a
    `SELECT key, value FROM settings` against SQLite. That's the highest-
    frequency query in the app. `get_all_cached()` serves it from a
    process-local TTL cache keyed by database path. Writes (`save_many`,
    `set_one`) call `invalidate_cache()` so admin edits are visible
    immediately. The cache is bounded in size to one entry per app
    instance, so memory pressure is negligible.
"""

import threading
import time

# Registry of known setting keys with metadata for the admin UI.
#
# Fields:
#   type     — Widget type: 'str', 'bool', 'int', 'color', 'select', 'textarea'
#   default  — Default value (always a string, cast on read)
#   label    — Human-readable label for the form
#   category — Groups settings into collapsible sections
#   options  — For 'select' type: list of (value, label) tuples
SETTINGS_REGISTRY = {
    # --- Site Identity ---
    'site_title': {
        'type': 'str',
        'default': 'My Portfolio',
        'label': 'Site Title',
        'category': 'Site Identity',
    },
    'site_tagline': {
        'type': 'str',
        'default': 'Welcome to my portfolio',
        'label': 'Site Tagline',
        'category': 'Site Identity',
    },
    'footer_text': {
        'type': 'str',
        'default': '',
        'label': 'Footer Text',
        'category': 'Site Identity',
    },
    'logo_mode': {
        'type': 'select',
        'default': 'title',
        'label': 'Logo Mode',
        'category': 'Site Identity',
        'options': [('title', 'Site Title'), ('initials', 'Initials')],
    },
    # --- Hero Section ---
    'hero_heading': {
        'type': 'str',
        'default': '',
        'label': 'Heading (your name)',
        'category': 'Hero Section',
    },
    'hero_subheading': {
        'type': 'str',
        'default': '',
        'label': 'Subheading (your title)',
        'category': 'Hero Section',
    },
    'hero_tagline': {
        'type': 'str',
        'default': '',
        'label': 'Tagline (value proposition)',
        'category': 'Hero Section',
    },
    'availability_status': {
        'type': 'select',
        'default': 'available',
        'label': 'Availability Status',
        'category': 'Hero Section',
        'options': [
            ('available', 'Available'),
            ('open', 'Open to Opportunities'),
            ('unavailable', 'Not Available'),
            ('off', 'Hidden'),
        ],
    },
    # --- Appearance ---
    'dark_mode_default': {
        'type': 'bool',
        'default': 'true',
        'label': 'Default Theme',
        'category': 'Appearance',
    },
    'accent_color': {
        'type': 'color',
        'default': '#0071e3',
        'label': 'Accent Color',
        'category': 'Appearance',
    },
    'color_preset': {
        'type': 'select',
        'default': 'default',
        'label': 'Color Preset',
        'category': 'Appearance',
        'options': [
            ('default', 'Default Blue (#0071e3)'),
            ('ocean', 'Ocean Teal (#00897B)'),
            ('forest', 'Forest Green (#2E7D32)'),
            ('sunset', 'Warm Sunset (#E65100)'),
            ('minimal', 'Minimal Gray (#616161)'),
            ('royal', 'Royal Purple (#6200EA)'),
        ],
    },
    'font_pairing': {
        'type': 'select',
        'default': 'inter',
        'label': 'Font Pairing',
        'category': 'Appearance',
        'options': [
            ('inter', 'Inter (default)'),
            ('space-grotesk', 'Space Grotesk + Inter'),
            ('plus-jakarta', 'Plus Jakarta Sans + Inter'),
            ('dm-sans', 'DM Sans'),
            ('outfit', 'Outfit + Inter'),
        ],
    },
    'custom_css': {
        'type': 'textarea',
        'default': '',
        'label': 'Custom CSS',
        'category': 'Appearance',
        'description': 'Injected after the main stylesheet. Override any CSS variable or add custom rules.',
    },
    # --- Navigation Visibility ---
    'nav_hide_about': {
        'type': 'bool',
        'default': 'false',
        'label': 'Hide About',
        'category': 'Navigation',
    },
    'nav_hide_services': {
        'type': 'bool',
        'default': 'false',
        'label': 'Hide Services',
        'category': 'Navigation',
    },
    'nav_hide_portfolio': {
        'type': 'bool',
        'default': 'false',
        'label': 'Hide Portfolio',
        'category': 'Navigation',
    },
    'nav_hide_projects': {
        'type': 'bool',
        'default': 'false',
        'label': 'Hide Projects',
        'category': 'Navigation',
    },
    'nav_hide_testimonials': {
        'type': 'bool',
        'default': 'false',
        'label': 'Hide Testimonials',
        'category': 'Navigation',
    },
    'nav_hide_contact': {
        'type': 'bool',
        'default': 'false',
        'label': 'Hide Contact',
        'category': 'Navigation',
    },
    'nav_hide_certifications': {
        'type': 'bool',
        'default': 'false',
        'label': 'Hide Certifications',
        'category': 'Navigation',
    },
    # --- Content ---
    'case_studies_enabled': {
        'type': 'bool',
        'default': 'false',
        'label': 'Case Studies',
        'category': 'Content',
    },
    'testimonial_display_mode': {
        'type': 'select',
        'default': 'mixed',
        'label': 'Testimonial Display',
        'category': 'Content',
        'options': [
            ('mixed', 'Mixed with Labels'),
            ('separate', 'Separate Sections'),
            ('all', 'All Together'),
        ],
    },
    # --- Contact & Social ---
    'contact_form_enabled': {
        'type': 'bool',
        'default': 'true',
        'label': 'Contact Form',
        'category': 'Contact & Social',
    },
    'contact_email_visible': {
        'type': 'bool',
        'default': 'false',
        'label': 'Show Email',
        'category': 'Contact & Social',
    },
    'contact_phone_visible': {
        'type': 'bool',
        'default': 'false',
        'label': 'Show Phone',
        'category': 'Contact & Social',
    },
    'resume_visibility': {
        'type': 'select',
        'default': 'off',
        'label': 'Resume Visibility',
        'category': 'Contact & Social',
        'options': [
            ('public', 'Public'),
            ('private', 'Private Link'),
            ('off', 'Disabled'),
        ],
    },
    'contact_github_url': {
        'type': 'str',
        'default': '',
        'label': 'GitHub URL',
        'category': 'Contact & Social',
    },
    'contact_linkedin_url': {
        'type': 'str',
        'default': '',
        'label': 'LinkedIn URL',
        'category': 'Contact & Social',
    },
    # --- Blog ---
    'blog_enabled': {
        'type': 'bool',
        'default': 'false',
        'label': 'Blog Enabled',
        'category': 'Blog',
    },
    'blog_title': {
        'type': 'str',
        'default': 'Blog',
        'label': 'Blog Title',
        'category': 'Blog',
    },
    'posts_per_page': {
        'type': 'int',
        'default': '10',
        'label': 'Posts Per Page',
        'category': 'Blog',
    },
    'show_reading_time': {
        'type': 'bool',
        'default': 'true',
        'label': 'Show Reading Time',
        'category': 'Blog',
    },
    'enable_rss': {
        'type': 'bool',
        'default': 'true',
        'label': 'Enable RSS Feed',
        'category': 'Blog',
    },
    # --- Internationalization ---
    'default_locale': {
        'type': 'str',
        'default': 'en',
        'label': 'Default Locale',
        'category': 'Internationalization',
    },
    'available_locales': {
        'type': 'str',
        'default': 'en',
        'label': 'Available Locales (comma-separated)',
        'category': 'Internationalization',
    },
    # --- Analytics ---
    'analytics_retention_days': {
        'type': 'int',
        'default': '90',
        'label': 'Retention Days',
        'category': 'Analytics',
    },
}

# Ordered list of categories for the admin settings page.
SETTINGS_CATEGORIES = [
    'Site Identity',
    'Hero Section',
    'Appearance',
    'Navigation',
    'Content',
    'Contact & Social',
    'Blog',
    'Internationalization',
    'Analytics',
]

# Color preset definitions: preset name → accent color hex value.
COLOR_PRESETS = {
    'default': '#0071e3',
    'ocean': '#00897B',
    'forest': '#2E7D32',
    'sunset': '#E65100',
    'minimal': '#616161',
    'royal': '#6200EA',
}

# Font pairing definitions: key → (display_font, body_font, google_fonts_families).
FONT_PAIRINGS = {
    'inter': {
        'display': "'Inter'",
        'body': "'Inter'",
        'google_families': 'Inter:wght@400;500;600;700;800;900',
    },
    'space-grotesk': {
        'display': "'Space Grotesk'",
        'body': "'Inter'",
        'google_families': 'Inter:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700',
    },
    'plus-jakarta': {
        'display': "'Plus Jakarta Sans'",
        'body': "'Plus Jakarta Sans'",
        'google_families': 'Plus+Jakarta+Sans:wght@400;500;600;700;800',
    },
    'dm-sans': {
        'display': "'DM Sans'",
        'body': "'DM Sans'",
        'google_families': 'DM+Sans:wght@400;500;600;700',
    },
    'outfit': {
        'display': "'Outfit'",
        'body': "'Inter'",
        'google_families': 'Inter:wght@400;500;600;700&family=Outfit:wght@400;500;600;700;800',
    },
}

# Keys that the settings form processes (ordered).
SETTINGS_FORM_KEYS = list(SETTINGS_REGISTRY.keys())


# --- Settings cache (Phase 12.1) -------------------------------------------
# Keyed by database path so multiple app instances in the same process (e.g.,
# parallel test apps) don't collide. Value is (expires_at_monotonic, mapping).
# 30s default TTL is short enough that admin reads after a save look fresh
# even if invalidation is somehow skipped, but long enough to absorb the
# burst-traffic case where a single page load triggers many template renders.
_settings_cache: dict[str, tuple[float, dict[str, str]]] = {}
_settings_cache_lock = threading.Lock()
DEFAULT_SETTINGS_TTL = 30.0  # seconds


def get_all(db):
    """Return all settings as a {key: value} dict."""
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return {row['key']: row['value'] for row in rows}


def get_all_cached(db, db_path, ttl=DEFAULT_SETTINGS_TTL):
    """Return all settings, served from a process-local TTL cache.

    Use this from hot paths (the request-time context processor) instead of
    `get_all`. The cache is keyed by `db_path` so each app instance has an
    isolated entry. Mutating callers MUST call `invalidate_cache()` after
    committing — `save_many` and `set_one` already do.

    The returned dict is a fresh copy; callers may mutate it without
    affecting cached state.
    """
    now = time.monotonic()
    with _settings_cache_lock:
        cached = _settings_cache.get(db_path)
        if cached and cached[0] > now:
            return dict(cached[1])
    # Miss: query outside the lock so a slow SQLite read doesn't block readers
    settings = get_all(db)
    with _settings_cache_lock:
        _settings_cache[db_path] = (now + ttl, settings)
    return dict(settings)


def invalidate_cache(db_path=None):
    """Drop cached settings.

    Pass a `db_path` to clear one app's cache; pass `None` to clear every
    cache entry (handy in tests). Called automatically from `save_many` and
    `set_one`, which clear all entries since they don't know which app's
    DB they're writing to.
    """
    with _settings_cache_lock:
        if db_path is None:
            _settings_cache.clear()
        else:
            _settings_cache.pop(db_path, None)


def get(db, key, default=''):
    """Return a single setting value by key, or default if not found."""
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def save_many(db, form_data):
    """Save multiple settings from a form submission dict.

    Only keys present in SETTINGS_REGISTRY are accepted. Unknown keys
    are silently ignored to prevent injection of arbitrary settings.
    Boolean settings use checkbox behavior: present in form_data means
    'true', absent means 'false'.
    """
    for key, meta in SETTINGS_REGISTRY.items():
        if meta['type'] == 'bool':
            # Checkboxes: present = true, absent = false
            # But select-based bools submit their value directly
            if key in form_data:
                value = form_data[key]
                if value not in ('true', 'false'):
                    value = 'true'
            else:
                value = 'false'
            _upsert(db, key, value)
        elif key in form_data:
            _upsert(db, key, str(form_data[key]))
    db.commit()
    invalidate_cache()


def set_one(db, key, value):
    """Set a single setting and commit immediately.

    Raises:
        KeyError: If the key is not in the registry.
    """
    if key not in SETTINGS_REGISTRY:
        raise KeyError(f'Unknown setting key: {key!r}')
    _upsert(db, key, str(value))
    db.commit()
    invalidate_cache()


def get_grouped_settings(db):
    """Return settings grouped by category for the admin UI.

    Returns a list of (category_name, settings_list) tuples where each
    setting includes its current value merged with its registry metadata.
    """
    current = get_all(db)
    grouped = []

    for category in SETTINGS_CATEGORIES:
        items = []
        for key, meta in SETTINGS_REGISTRY.items():
            if meta['category'] == category:
                items.append(
                    {
                        'key': key,
                        'value': current.get(key, meta['default']),
                        **meta,
                    }
                )
        if items:
            grouped.append((category, items))

    return grouped


def _upsert(db, key, value):
    """INSERT OR UPDATE a single setting row (no commit -- caller commits)."""
    db.execute(
        'INSERT INTO settings (key, value, updated_at) '
        "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) "
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
        (key, value),
    )
