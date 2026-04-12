"""
resume-site Application Factory

This module implements the Flask application factory pattern, which centralizes
all app configuration, extension initialization, and blueprint registration in
a single `create_app()` function.

Architecture decisions:
- SQLite is accessed via Python's built-in `sqlite3` module (no ORM).
- Database connection lifecycle is managed in app/db.py (single source of truth).
- Configuration is split: infrastructure settings in YAML, content settings in SQLite.
- CSRF protection is enforced on all POST/PUT/DELETE routes via Flask-WTF.
- Security headers are added to every response via an after_request handler.
- A context processor injects all site settings into every template automatically.

Usage:
    from app import create_app
    app = create_app()                           # Uses default config path
    app = create_app('/path/to/config.yaml')     # Custom config path
"""

import os

from flask import Flask, request
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from app.db import get_db, close_db
from app.services.config import load_config

# CSRF protection instance (initialized in create_app)
csrf = CSRFProtect()


def create_app(config_path=None):
    """Create and configure the Flask application.

    This factory function:
    1. Loads infrastructure config from YAML (secret key, SMTP, admin credentials).
    2. Registers database teardown via app/db.py.
    3. Initializes Flask-Login for admin authentication.
    4. Enables CSRF protection on all POST/PUT/DELETE routes.
    5. Registers all route blueprints (public, admin, contact, review).
    6. Attaches the analytics middleware for page view tracking.
    7. Sets security response headers on every reply.
    8. Injects site settings into every template via a context processor.

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
    app.config['MAX_UPLOAD_SIZE'] = site_config.get('max_upload_size', 10 * 1024 * 1024)
    app.config['SESSION_TIMEOUT_MINUTES'] = site_config.get('session_timeout_minutes', 60)
    app.config['SITE_CONFIG'] = site_config  # Full config dict available to services
    app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # CSRF token expires after 1 hour

    # --- 2. Database connection lifecycle ---
    # close_db is defined in app/db.py and tears down the per-request connection.
    app.teardown_appcontext(close_db)

    # --- 3. Authentication (Flask-Login) ---
    # Single-user admin system — credentials stored in YAML, not in the database.
    login_manager = LoginManager()
    login_manager.login_view = 'admin.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        """Reload the admin user from the session cookie."""
        from app.models import AdminUser
        admin_username = site_config.get('admin', {}).get('username', 'admin')
        if user_id == admin_username:
            return AdminUser(user_id)
        return None

    # --- 4. CSRF protection ---
    # CSRFProtect validates the csrf_token field on all POST/PUT/DELETE requests.
    # The token is available in templates via {{ csrf_token() }}.
    # Endpoints can opt out with @csrf.exempt when needed (e.g., webhook receivers).
    csrf.init_app(app)

    # --- 5. Blueprint registration ---
    from app.routes.public import public_bp
    from app.routes.admin import admin_bp
    from app.routes.blog_admin import blog_admin_bp
    from app.routes.blog import blog_bp
    from app.routes.contact import contact_bp
    from app.routes.review import review_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(blog_admin_bp, url_prefix='/admin')
    app.register_blueprint(blog_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(review_bp)

    # --- 6. Analytics middleware ---
    from app.services.analytics import track_page_view
    app.before_request(track_page_view)

    # --- 7. Security response headers ---
    @app.after_request
    def set_security_headers(response):
        """Add security headers to every response.

        Headers applied:
        - X-Content-Type-Options: Prevents MIME-type sniffing attacks.
        - X-Frame-Options: Blocks clickjacking via iframes.
        - X-XSS-Protection: Disabled in favour of CSP (modern best practice).
        - Referrer-Policy: Limits referrer leakage on cross-origin navigation.
        - Permissions-Policy: Disables browser features this app doesn't use.
        - Cache-Control: Prevents admin pages from being cached by proxies/browsers.
        """
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '0'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

        # Prevent admin pages from being cached
        if request.path.startswith('/admin'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'

        return response

    # --- 8. Template context processor ---
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

    # --- 9. Ensure storage directories exist ---
    os.makedirs(os.path.dirname(app.config['DATABASE_PATH']) or '.', exist_ok=True)
    os.makedirs(app.config['PHOTO_STORAGE'], exist_ok=True)

    return app
