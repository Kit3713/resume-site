from flask_login import UserMixin


class AdminUser(UserMixin):
    """Single admin user backed by YAML config (not database)."""

    def __init__(self, username):
        self.id = username
        self.username = username


def get_db():
    """Get the database connection from Flask's g object."""
    from flask import g
    return g.db


def get_all_settings(db):
    """Return all settings as a dict."""
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return {row['key']: row['value'] for row in rows}


def get_setting(db, key, default=''):
    """Return a single setting value, or default if not found."""
    row = db.execute(
        'SELECT value FROM settings WHERE key = ?', (key,)
    ).fetchone()
    return row['value'] if row else default


def set_setting(db, key, value):
    """Insert or update a setting."""
    db.execute(
        'INSERT INTO settings (key, value, updated_at) VALUES (?, ?, strftime(\'%Y-%m-%dT%H:%M:%SZ\', \'now\')) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
        (key, str(value)),
    )
    db.commit()


# --- Content Blocks ---

def get_content_block(db, slug):
    """Return a single content block by slug, or None."""
    return db.execute(
        'SELECT * FROM content_blocks WHERE slug = ?', (slug,)
    ).fetchone()


# --- Stats ---

def get_visible_stats(db):
    """Return all visible stats ordered by sort_order."""
    return db.execute(
        'SELECT * FROM stats WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


# --- Services ---

def get_visible_services(db):
    """Return all visible services ordered by sort_order."""
    return db.execute(
        'SELECT * FROM services WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


# --- Skills ---

def get_skill_domains_with_skills(db):
    """Return visible skill domains, each with a 'skills' list."""
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


# --- Photos ---

def get_photos_by_tier(db, tier='grid'):
    """Return photos of a given display_tier, ordered by sort_order."""
    return db.execute(
        'SELECT * FROM photos WHERE display_tier = ? ORDER BY sort_order',
        (tier,),
    ).fetchall()


def get_all_visible_photos(db):
    """Return all non-hidden photos ordered by sort_order."""
    return db.execute(
        "SELECT * FROM photos WHERE display_tier != 'hidden' ORDER BY sort_order"
    ).fetchall()


def get_photo_categories(db):
    """Return distinct non-empty category values from visible photos."""
    rows = db.execute(
        "SELECT DISTINCT category FROM photos WHERE display_tier != 'hidden' AND category != '' ORDER BY category"
    ).fetchall()
    return [row['category'] for row in rows]


# --- Case Studies ---

def get_case_study_by_slug(db, slug):
    """Return a published case study by slug, or None."""
    return db.execute(
        'SELECT * FROM case_studies WHERE slug = ? AND published = 1', (slug,)
    ).fetchone()


# --- Reviews ---

def get_approved_reviews_by_tier(db, tier='featured'):
    """Return approved reviews of a given display_tier."""
    return db.execute(
        "SELECT * FROM reviews WHERE status = 'approved' AND display_tier = ? ORDER BY created_at DESC",
        (tier,),
    ).fetchall()


def get_all_approved_reviews(db):
    """Return all approved reviews, featured first, then by date."""
    return db.execute(
        "SELECT * FROM reviews WHERE status = 'approved' "
        "ORDER BY CASE display_tier WHEN 'featured' THEN 0 WHEN 'standard' THEN 1 ELSE 2 END, "
        "created_at DESC"
    ).fetchall()


# --- Projects ---

def get_visible_projects(db):
    """Return all visible projects ordered by sort_order."""
    return db.execute(
        'SELECT * FROM projects WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


def get_project_by_slug(db, slug):
    """Return a visible project with detail page by slug, or None."""
    return db.execute(
        'SELECT * FROM projects WHERE slug = ? AND visible = 1 AND has_detail_page = 1',
        (slug,),
    ).fetchone()


# --- Certifications ---

def get_visible_certifications(db):
    """Return all visible certifications ordered by sort_order."""
    return db.execute(
        'SELECT * FROM certifications WHERE visible = 1 ORDER BY sort_order'
    ).fetchall()


# --- Contact ---

def save_contact_submission(db, name, email, message, ip_address, user_agent, is_spam=False):
    """Insert a contact form submission. Returns the new row id."""
    cursor = db.execute(
        'INSERT INTO contact_submissions (name, email, message, ip_address, user_agent, is_spam) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (name, email, message, ip_address, user_agent, 1 if is_spam else 0),
    )
    db.commit()
    return cursor.lastrowid


def count_recent_submissions(db, ip_address, minutes=60):
    """Count contact submissions from an IP in the last N minutes."""
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM contact_submissions "
        "WHERE ip_address = ? AND created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        (ip_address, f'-{minutes} minutes'),
    ).fetchone()
    return row['cnt'] if row else 0


# --- Review Tokens ---

def get_review_token(db, token_string):
    """Return a review token row by token string, or None."""
    return db.execute(
        'SELECT * FROM review_tokens WHERE token = ?', (token_string,)
    ).fetchone()


def mark_token_used(db, token_id):
    """Mark a review token as used."""
    db.execute(
        "UPDATE review_tokens SET used = 1, used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (token_id,),
    )
    db.commit()


# --- Reviews (insert) ---

def create_review(db, token_id, reviewer_name, reviewer_title, relationship, message, rating, review_type):
    """Insert a new review with status='pending'. Returns the new row id."""
    cursor = db.execute(
        'INSERT INTO reviews (token_id, reviewer_name, reviewer_title, relationship, message, rating, type, status) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (token_id, reviewer_name, reviewer_title, relationship, message, rating, review_type, 'pending'),
    )
    db.commit()
    return cursor.lastrowid
