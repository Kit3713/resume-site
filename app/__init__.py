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

import contextlib
import os

from flask import Flask, g, request
from flask_babel import Babel
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from app.db import close_db, get_db
from app.services.config import load_config
from app.services.settings_svc import get_all_cached

# Extension instances (initialized in create_app)
csrf = CSRFProtect()
babel = Babel()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # No global limit — applied per-route
    storage_uri='memory://',  # In-memory for single-process deployments
)


def _get_available_locales(app):
    """Return the list of available locale codes from site settings.

    Falls back to ['en'] if no locales are configured or the database
    is not yet initialized. Reads through the settings cache so the
    locale selector (called on every request) doesn't re-query SQLite.
    """
    # Best-effort: the DB may not yet exist (first boot, migrations pending).
    # Any failure falls through to the ['en'] default.
    with contextlib.suppress(Exception), app.app_context():
        db = get_db()
        settings = get_all_cached(db, app.config['DATABASE_PATH'])
        raw = settings.get('available_locales', '')
        if raw:
            return [loc.strip() for loc in raw.split(',') if loc.strip()]
    return ['en']


def create_app(config_path=None):
    """Create and configure the Flask application.

    This factory function:
    1. Loads infrastructure config from YAML (secret key, SMTP, admin credentials).
    2. Registers database teardown via app/db.py.
    3. Initializes Flask-Login for admin authentication.
    4. Enables CSRF protection on all POST/PUT/DELETE routes.
    5. Configures Flask-Babel for internationalization.
    6. Registers all route blueprints (public, admin, blog, contact, review, locale).
    7. Attaches the analytics middleware for page view tracking.
    8. Sets security response headers on every reply.
    9. Injects site settings and locale info into every template.
    10. Ensures storage directories exist.

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

    # Session cookie hardening. SECURE defaults to True in production; a
    # `session_cookie_secure: false` entry in config.yaml disables it for
    # plain-HTTP local development. SAMESITE=Lax blocks CSRF-style cross-site
    # cookie leaks while still allowing top-level navigation (e.g., clicking a
    # link from email).
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = bool(site_config.get('session_cookie_secure', True))

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

    # --- 4b. Rate limiting ---
    # Applied per-route on public POST endpoints (contact, review, admin login).
    limiter.init_app(app)

    # --- 5. Internationalization (Flask-Babel) ---
    app.config['BABEL_DEFAULT_LOCALE'] = 'en'
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'translations'
    )

    def get_locale():
        """Select the locale for the current request.

        Priority: URL prefix > session > Accept-Language header > default.
        The URL-based locale is set by the locale routing blueprint.
        """
        # URL-based locale (set by locale blueprint's url_value_preprocessor)
        locale = g.get('lang')
        if locale:
            return locale
        # Session-based persistence
        from flask import session

        locale = session.get('locale')
        if locale and locale in _get_available_locales(app):
            return locale
        # Browser preference
        available = _get_available_locales(app)
        return request.accept_languages.best_match(available, default='en')

    babel.init_app(app, locale_selector=get_locale)

    # --- 6. Blueprints ---
    from app.routes.admin import admin_bp
    from app.routes.blog import blog_bp
    from app.routes.blog_admin import blog_admin_bp
    from app.routes.contact import contact_bp
    from app.routes.locale import locale_bp
    from app.routes.public import public_bp
    from app.routes.review import review_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(blog_admin_bp, url_prefix='/admin')
    app.register_blueprint(blog_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(locale_bp)

    # --- 7. Analytics middleware ---
    from app.services.analytics import track_page_view

    app.before_request(track_page_view)

    # --- 8. Security response headers ---
    @app.after_request
    def set_security_headers(response):
        """Add security headers to every response.

        Headers applied:
        - X-Content-Type-Options: Prevents MIME-type sniffing attacks.
        - X-Frame-Options: Blocks clickjacking via iframes.
        - X-XSS-Protection: Disabled in favour of CSP (modern best practice).
        - Referrer-Policy: Limits referrer leakage on cross-origin navigation.
        - Permissions-Policy: Disables browser features this app doesn't use.
        - Content-Security-Policy-Report-Only: CSP in report-only mode while
          tuning the policy. Allows GSAP CDN, Google Fonts, Quill.js, and
          inline styles (needed for custom CSS injection and Quill editor).
        - Cache-Control: Prevents admin pages from being cached; enables
          long caching for static assets (CSS/JS/images).
        """
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '0'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

        # Content Security Policy (report-only to avoid breaking pages while tuning)
        csp = (
            "default-src 'self'; "
            "script-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'"
        )
        response.headers['Content-Security-Policy-Report-Only'] = csp

        # Cache-Control per route type
        if request.path.startswith('/admin'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        elif request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'

        return response

    # --- 9. Template context processor ---
    @app.context_processor
    def inject_settings():
        """Make site settings and config available in all templates.

        Templates access these as:
        - {{ site_settings.site_title }}  — from the SQLite settings table
        - {{ site_config.smtp.host }}     — from config.yaml (infrastructure)

        Reads via the settings cache (Phase 12.1) so each request hits
        SQLite at most once per cache TTL window. Wrapped in suppress() to
        handle first-run when the DB doesn't exist yet.
        """
        settings = {}
        with contextlib.suppress(Exception):
            db = get_db()
            settings = get_all_cached(db, app.config['DATABASE_PATH'])
        # Locale information for language switcher and hreflang tags
        available_locales = [
            loc.strip() for loc in settings.get('available_locales', 'en').split(',') if loc.strip()
        ]
        current_locale = str(get_locale())
        return {
            'site_settings': settings,
            'site_config': site_config,
            'available_locales': available_locales,
            'current_locale': current_locale,
        }

    # --- 10. Ensure storage directories exist ---
    os.makedirs(os.path.dirname(app.config['DATABASE_PATH']) or '.', exist_ok=True)
    os.makedirs(app.config['PHOTO_STORAGE'], exist_ok=True)

    return app
