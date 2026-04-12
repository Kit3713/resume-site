"""
resume-site Application Factory

This module implements the Flask application factory pattern, which centralizes
all app configuration, extension initialization, and blueprint registration in
a single `create_app()` function.

Architecture decisions:
- SQLite is accessed via Python's built-in `sqlite3` module (no ORM) for simplicity.
- Database connections are managed per-request using Flask's `g` object.
- Configuration is split: infrastructure settings in YAML, content settings in SQLite.
- A context processor injects all site settings into every template automatically.

Usage:
    from app import create_app
    app = create_app()                           # Uses default config path
    app = create_app('/path/to/config.yaml')     # Custom config path
"""

import os
import sqlite3

from flask import Flask, g
from flask_login import LoginManager

from app.services.config import load_config


def get_db():
    """Get or create a SQLite database connection for the current request.

    Connections are stored in Flask's `g` object and reused within a single
    request. The connection is configured with:
    - Row factory: sqlite3.Row for dict-like column access (row['column_name']).
    - Foreign keys: Enforced via PRAGMA to maintain referential integrity.
    - Busy timeout: 5 seconds to handle concurrent writes from Gunicorn workers.

    Returns:
        sqlite3.Connection: The active database connection.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(g.db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA busy_timeout=5000')
    return g.db


def create_app(config_path=None):
    """Create and configure the Flask application.

    This factory function:
    1. Loads infrastructure config from YAML (secret key, SMTP, admin credentials).
    2. Sets up per-request SQLite connection management.
    3. Initializes Flask-Login for admin authentication.
    4. Registers all route blueprints (public, admin, contact, review).
    5. Attaches the analytics middleware for page view tracking.
    6. Injects site settings into every template via a context processor.

    Args:
        config_path: Optional path to config.yaml. Defaults to the project root,
                     or the RESUME_SITE_CONFIG environment variable if set.

    Returns:
        Flask: The configured application instance.
    """
    app = Flask(__name__)

    # --- 1. Load YAML configuration ---
    # Config path resolution order: explicit arg > env var > default (project root)
    if config_path is None:
        config_path = os.environ.get(
            'RESUME_SITE_CONFIG',
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml'),
        )
    site_config = load_config(config_path)

    # Map YAML config values to Flask config for use across the app
    app.secret_key = site_config['secret_key']
    app.config['DATABASE_PATH'] = site_config.get('database_path', 'data/site.db')
    app.config['PHOTO_STORAGE'] = site_config.get('photo_storage', 'photos')
    app.config['SITE_CONFIG'] = site_config  # Full config dict available to services

    # --- 2. Database connection lifecycle ---
    @app.before_request
    def before_request():
        """Store the database path in g so get_db() can access it."""
        g.db_path = app.config['DATABASE_PATH']

    @app.teardown_appcontext
    def close_db(exception):
        """Close the database connection at the end of each request."""
        db = g.pop('db', None)
        if db is not None:
            db.close()

    # --- 3. Authentication (Flask-Login) ---
    # Single-user admin system — credentials stored in YAML, not in the database.
    login_manager = LoginManager()
    login_manager.login_view = 'admin.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        """Reload the admin user from the session cookie.

        Since this is a single-user system, we only validate that the
        session's user_id matches the configured admin username.
        """
        from app.models import AdminUser
        admin_username = site_config.get('admin', {}).get('username', 'admin')
        if user_id == admin_username:
            return AdminUser(user_id)
        return None

    # --- 4. Blueprint registration ---
    from app.routes.public import public_bp
    from app.routes.admin import admin_bp
    from app.routes.contact import contact_bp
    from app.routes.review import review_bp

    app.register_blueprint(public_bp)                     # Public pages (/, /portfolio, etc.)
    app.register_blueprint(admin_bp, url_prefix='/admin')  # Admin panel (IP-restricted)
    app.register_blueprint(contact_bp)                     # Contact form (/contact)
    app.register_blueprint(review_bp)                      # Review submission (/review/<token>)

    # --- 5. Analytics middleware ---
    # Tracks page views on every public GET request (skips static/admin/photos)
    from app.services.analytics import track_page_view
    app.before_request(track_page_view)

    # --- 6. Template context processor ---
    @app.context_processor
    def inject_settings():
        """Make site settings and config available in all templates.

        Templates access these as:
        - {{ site_settings.site_title }}  — from the SQLite settings table
        - {{ site_config.smtp.host }}     — from config.yaml (infrastructure)

        Wrapped in try/except to handle first-run when the DB doesn't exist yet.
        """
        try:
            db = get_db()
            rows = db.execute('SELECT key, value FROM settings').fetchall()
            settings = {row['key']: row['value'] for row in rows}
        except Exception:
            settings = {}
        return dict(site_settings=settings, site_config=site_config)

    # --- 7. Ensure storage directories exist ---
    os.makedirs(os.path.dirname(app.config['DATABASE_PATH']) or '.', exist_ok=True)
    os.makedirs(app.config['PHOTO_STORAGE'], exist_ok=True)

    return app
