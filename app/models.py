"""
Database Models and Query Helpers

This module provides the data access layer for the resume-site application.
Instead of an ORM, it uses raw SQL with Python's built-in `sqlite3` module.
All query functions accept a `db` connection as the first argument and return
`sqlite3.Row` objects (which support dict-like access: row['column_name']).

Architecture:
- AdminUser: A minimal Flask-Login compatible user class backed by YAML config.
- Settings: Key-value store for site configuration managed through the admin panel.
- Content/photos/reviews/etc.: Thin query wrappers organized by domain.

All write operations call db.commit() to ensure changes are persisted.
"""

from flask_login import UserMixin


class AdminUser(UserMixin):
    """Single admin user backed by YAML config (not database).

    Flask-Login requires a user class with four properties/methods:
    is_authenticated, is_active, is_anonymous, and get_id().
    UserMixin provides sensible defaults; we only need to set the id.

    The admin credentials (username, password_hash) live in config.yaml
    because this is a single-user system, avoiding a chicken-and-egg
    problem with database-stored credentials.
    """

    def __init__(self, username):
        self.id = username
        self.username = username


def get_db():
    """Get the database connection from Flask's g object.

    This is a convenience re-export for modules that don't want to import
    from app.__init__ directly. The actual connection is managed by the
    app factory's before_request/teardown_appcontext hooks.
    """
    from flask import g
    return g.db


# ============================================================
# SETTINGS (key-value store in SQLite)
# ============================================================

def get_all_settings(db):
    """Return all settings as a {key: value} dict.

    Used by the context processor to inject settings into every template,
    and by the admin settings page to populate form fields.
    """
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return {row['key']: row['value'] for row in rows}


def get_setting(db, key, default=''):
    """Return a single setting value by key, or default if not found.

    All values are stored as text strings; callers must cast to
    int/bool as needed (e.g., `get_setting(db, 'contact_form_enabled') == 'true'`).
    """
    row = db.execute(
        'SELECT value FROM settings WHERE key = ?', (key,)
    ).fetchone()
    return row['value'] if row else default


def set_setting(db, key, value):
    """Insert or update a setting using SQLite's UPSERT syntax.

    The updated_at timestamp is set automatically to the current UTC time.
    """
    db.execute(
        'INSERT INTO settings (key, value, updated_at) VALUES (?, ?, strftime(\'%Y-%m-%dT%H:%M:%SZ\', \'now\')) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
        (key, str(value)),
    )
    db.commit()


# ============================================================
# CONTENT BLOCKS (rich text sections managed via admin)
# ============================================================

def get_content_block(db, slug):
    """Return a single content block by its slug identifier, or None.

    Content blocks store HTML from the Quill.js editor. Templates reference
    them by slug (e.g., 'about', 'hero_description') to render editable
    sections without changing template code.
    """
    return db.execute(
        'SELECT * FROM content_blocks WHERE slug = ?', (slug,)
    ).fetchone()


# ============================================================
# STATS (animated counters on the landing page)
# ============================================================

def get_visible_stats(db):
    """Return all visible stat counters ordered by sort_order.

    Stats display as animated number counters on the landing page
    (e.g., "42+ Projects", "99% Uptime").
    """
    return db.execute(
        'SELECT * FROM stats WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


# ============================================================
# SERVICES
# ============================================================

def get_visible_services(db):
    """Return all visible services ordered by sort_order."""
    return db.execute(
        'SELECT * FROM services WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


# ============================================================
# SKILLS (grouped by domain for expandable accordion display)
# ============================================================

def get_skill_domains_with_skills(db):
    """Return visible skill domains, each with a nested list of skills.

    Returns a list of dicts: [{'domain': Row, 'skills': [Row, ...]}, ...]
    This two-query approach (domains, then skills per domain) avoids
    complex joins and keeps the data structure template-friendly.
    """
    domains = db.execute(
        'SELECT * FROM skill_domains WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()
    result = []
    for domain in domains:
        skills = db.execute(
            'SELECT * FROM skills WHERE domain_id = ? AND visible = 1 ORDER BY sort_order',
            (domain['id'],),
        ).fetchall()
        result.append({'domain': domain, 'skills': skills})
    return result


# ============================================================
# PHOTOS (portfolio gallery)
# ============================================================

def get_photos_by_tier(db, tier='grid'):
    """Return photos filtered by display tier ('featured', 'grid', or 'hidden').

    The three-tier system controls how photos appear:
    - featured: Displayed large at the top of the portfolio page and on the landing page.
    - grid: Standard masonry grid display.
    - hidden: Not shown publicly (admin-only visibility).
    """
    return db.execute(
        'SELECT * FROM photos WHERE display_tier = ? ORDER BY sort_order',
        (tier,),
    ).fetchall()


def get_all_visible_photos(db):
    """Return all non-hidden photos for the portfolio grid."""
    return db.execute(
        "SELECT * FROM photos WHERE display_tier != 'hidden' ORDER BY sort_order"
    ).fetchall()


def get_photo_categories(db):
    """Return distinct category names for the portfolio filter bar.

    Only includes categories from visible (non-hidden) photos that have
    a category assigned. Returns a plain list of strings.
    """
    rows = db.execute(
        "SELECT DISTINCT category FROM photos WHERE display_tier != 'hidden' AND category != '' ORDER BY category"
    ).fetchall()
    return [row['category'] for row in rows]


# ============================================================
# CASE STUDIES (detailed portfolio write-ups)
# ============================================================

def get_case_study_by_slug(db, slug):
    """Return a published case study by its URL slug, or None.

    Only published case studies are returned to prevent draft leakage.
    The case_studies_enabled setting must also be checked by the route handler.
    """
    return db.execute(
        'SELECT * FROM case_studies WHERE slug = ? AND published = 1', (slug,)
    ).fetchone()


# ============================================================
# REVIEWS / TESTIMONIALS
# ============================================================

def get_approved_reviews_by_tier(db, tier='featured'):
    """Return approved reviews filtered by display tier.

    Display tiers control visibility on the testimonials page:
    - featured: Large quote cards at the top.
    - standard: Regular grid cards.
    - hidden: Not shown publicly.
    """
    return db.execute(
        "SELECT * FROM reviews WHERE status = 'approved' AND display_tier = ? ORDER BY created_at DESC",
        (tier,),
    ).fetchall()


def get_all_approved_reviews(db):
    """Return all approved reviews, with featured reviews sorted first.

    Uses a CASE expression to sort by tier priority (featured=0, standard=1),
    then by creation date descending within each tier.
    """
    return db.execute(
        "SELECT * FROM reviews WHERE status = 'approved' "
        "ORDER BY CASE display_tier WHEN 'featured' THEN 0 WHEN 'standard' THEN 1 ELSE 2 END, "
        "created_at DESC"
    ).fetchall()


# ============================================================
# PROJECTS
# ============================================================

def get_visible_projects(db):
    """Return all visible projects ordered by sort_order."""
    return db.execute(
        'SELECT * FROM projects WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


def get_project_by_slug(db, slug):
    """Return a visible project with a detail page enabled, or None.

    Projects can optionally have a dedicated detail page (has_detail_page=1).
    If disabled, the project card links directly to its GitHub URL instead.
    """
    return db.execute(
        'SELECT * FROM projects WHERE slug = ? AND visible = 1 AND has_detail_page = 1',
        (slug,),
    ).fetchone()


# ============================================================
# CERTIFICATIONS
# ============================================================

def get_visible_certifications(db):
    """Return all visible certifications ordered by sort_order."""
    return db.execute(
        'SELECT * FROM certifications WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


# ============================================================
# CONTACT SUBMISSIONS
# ============================================================

def save_contact_submission(db, name, email, message, ip_address, user_agent, is_spam=False):
    """Insert a contact form submission into the database.

    All submissions are saved regardless of spam status — honeypot-flagged
    entries are marked is_spam=1 but still recorded for audit purposes.

    Returns:
        int: The auto-incremented row ID of the new submission.
    """
    cursor = db.execute(
        'INSERT INTO contact_submissions (name, email, message, ip_address, user_agent, is_spam) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (name, email, message, ip_address, user_agent, 1 if is_spam else 0),
    )
    db.commit()
    return cursor.lastrowid


def count_recent_submissions(db, ip_address, minutes=60):
    """Count contact submissions from a specific IP in the last N minutes.

    Used for rate limiting — the contact route rejects submissions if
    the count exceeds 5 per hour from the same IP address.
    """
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM contact_submissions "
        "WHERE ip_address = ? AND created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        (ip_address, f'-{minutes} minutes'),
    ).fetchone()
    return row['cnt'] if row else 0


# ============================================================
# REVIEW TOKENS (invite-only review system)
# ============================================================

def get_review_token(db, token_string):
    """Return a review token row by its URL-safe token string, or None."""
    return db.execute(
        'SELECT * FROM review_tokens WHERE token = ?', (token_string,)
    ).fetchone()


def mark_token_used(db, token_id):
    """Mark a review token as used after a review is submitted.

    Each token is single-use — once a review is submitted through it,
    the token cannot be reused. The used_at timestamp is recorded for audit.
    """
    db.execute(
        "UPDATE review_tokens SET used = 1, used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (token_id,),
    )
    db.commit()


# ============================================================
# REVIEWS (submission/creation)
# ============================================================

def create_review(db, token_id, reviewer_name, reviewer_title, relationship, message, rating, review_type):
    """Insert a new review with 'pending' status for admin approval.

    Reviews submitted via invite tokens are always pending — the admin
    must approve them before they appear on the public testimonials page.

    Args:
        token_id: The review_tokens.id that generated this review.
        reviewer_name: The reviewer's display name.
        reviewer_title: Optional job title or role.
        relationship: How the reviewer knows the site owner.
        message: The review text.
        rating: Integer 1-5, or None if not provided.
        review_type: 'recommendation' or 'client_review' (inherited from the token).

    Returns:
        int: The auto-incremented row ID of the new review.
    """
    cursor = db.execute(
        'INSERT INTO reviews (token_id, reviewer_name, reviewer_title, relationship, message, rating, type, status) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (token_id, reviewer_name, reviewer_title, relationship, message, rating, review_type, 'pending'),
    )
    db.commit()
    return cursor.lastrowid
