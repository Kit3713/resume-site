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
import logging
import os
import re
import secrets
import time
import uuid

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


# Format of an inbound X-Request-ID we're willing to echo back. Anchored
# match of 8–128 characters from a restricted alphabet (alphanumerics plus
# `.`, `_`, `-`) rules out log-injection payloads (CRLF, quotes, spaces,
# control characters) while still accepting UUIDs, ULIDs, short hashes, and
# the `trace-id` values common reverse proxies emit.
_REQUEST_ID_PATTERN = re.compile(r'^[A-Za-z0-9._-]{8,128}$')


def _assign_request_id():
    """Populate ``flask.g.request_id`` for the current request.

    If the incoming request carries an ``X-Request-ID`` header that matches
    ``_REQUEST_ID_PATTERN``, propagate it verbatim so correlation with an
    upstream reverse proxy works. Otherwise generate a fresh UUID4 hex so
    every request is uniquely identifiable.

    This runs before analytics (and future structured logging) so downstream
    handlers can tag their records with the same ID.
    """
    incoming = request.headers.get('X-Request-ID', '')
    if incoming and _REQUEST_ID_PATTERN.match(incoming):
        g.request_id = incoming
    else:
        g.request_id = uuid.uuid4().hex


def _assign_csp_nonce():
    """Generate a per-request CSP nonce for inline script/style tags.

    Stored in ``flask.g.csp_nonce`` and made available to templates via
    the context processor. The nonce is a 16-byte URL-safe base64 token.
    """
    g.csp_nonce = secrets.token_urlsafe(16)


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


def create_app(config_path=None):  # noqa: C901 — app factory is inherently sequential setup; splitting into sub-functions would scatter related init logic without reducing real complexity
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

    # --- 1b. Structured logging (Phase 18.1) ---
    # Must run after config load so the secret_key is available as a salt
    # for client-IP hashing. Idempotent — safe in test fixtures that
    # build multiple apps in one process.
    from app.services.logging import configure_logging, get_logger

    configure_logging(app)
    request_logger = get_logger('app.request')

    # Session cookie hardening (Phase 13.6). SECURE defaults to True in
    # production; `session_cookie_secure: false` in config.yaml disables it for
    # plain-HTTP local development. SAMESITE=Lax blocks CSRF-style cross-site
    # cookie leaks while still allowing top-level navigation (e.g., clicking a
    # link from email). NAME is set explicitly so the cookie is identifiable
    # in browser devtools and cookie audits.
    app.config['SESSION_COOKIE_NAME'] = 'resume_session'
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
    from app.routes.api import api_bp
    from app.routes.blog import blog_bp
    from app.routes.blog_admin import blog_admin_bp
    from app.routes.contact import contact_bp
    from app.routes.locale import locale_bp
    from app.routes.metrics import metrics_bp
    from app.routes.public import public_bp
    from app.routes.review import review_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(blog_admin_bp, url_prefix='/admin')
    app.register_blueprint(blog_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(locale_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(api_bp)
    # The REST API uses Bearer-token auth (Phase 13.4) on write/admin
    # routes and public access on reads. CSRF is a browser-form
    # mitigation that doesn't apply to a JSON API consumed by
    # non-browser clients, so the entire blueprint opts out.
    csrf.exempt(api_bp)

    # --- 7. Request ID propagation (Phase 18.1) ---
    # Assigned before analytics so any future request-scoped logging can
    # correlate with the ID a reverse proxy already sent us.
    app.before_request(_assign_request_id)
    app.before_request(_assign_csp_nonce)

    # --- 7b. Request-timing + client-IP hashing (Phase 18.1) ---
    # Runs after _assign_request_id so g.request_id is available to the
    # _RequestContextFilter installed by configure_logging().
    from app.services.logging import hash_client_ip

    def _start_request_timer():
        g.request_start = time.monotonic()
        g.client_ip_hash = hash_client_ip(request.remote_addr or '', app.secret_key or '')

    app.before_request(_start_request_timer)

    # --- 7c. WAF-lite request filter (Phase 13.3) ---
    from app.services.request_filter import check_request as _check_request

    def _run_request_filter():
        settings = {}
        with contextlib.suppress(Exception):
            db = get_db()
            settings = get_all_cached(db, app.config['DATABASE_PATH'])
        _check_request(settings)

    app.before_request(_run_request_filter)

    # --- 8. Analytics middleware ---
    from app.services.analytics import track_page_view

    app.before_request(track_page_view)

    # --- 8b. Structured request log + Prometheus metrics (Phase 18.1/18.2/18.9) ---
    # Registered BEFORE set_security_headers below. Flask invokes
    # after_request callbacks in reverse registration order, so this
    # hook runs LAST — it sees the finalised status code and headers.
    from app.errors import categorize_exception, categorize_status
    from app.services.metrics import errors_total, record_request

    @app.after_request
    def _log_request(response):
        start = g.get('request_start')
        duration_s = (time.monotonic() - start) if start is not None else 0.0
        duration_ms = int(duration_s * 1000)
        status = response.status_code
        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        else:
            level = logging.INFO

        # url_rule.rule is the bound template (e.g. "/blog/<slug>") — use
        # it as the metric "path" label to keep cardinality bounded.
        # Unmatched requests (404 probes) produce url_rule=None which
        # record_request() normalises to a constant sentinel.
        rule = request.url_rule.rule if request.url_rule else None

        # Never double-count /metrics scrapes — a high scrape rate would
        # otherwise drown out real traffic in rate() queries.
        if rule != '/metrics':
            record_request(request.method, rule, status, duration_s)

        # Error categorisation (Phase 18.9). The 500 errorhandler below
        # sets g.error_category after seeing the exception directly —
        # trust that over the status-only classifier because only the
        # handler can distinguish DataError from a generic 500.
        error_category = g.get('error_category') or categorize_status(status)

        if error_category is not None and rule != '/metrics':
            errors_total.inc(label_values=(error_category, str(status)))

        db_queries = g.get('db_query_count', 0)
        db_time_ms = g.get('db_query_time_ms', 0.0)

        extra = {
            'method': request.method,
            'path': request.path,
            'status_code': status,
            'duration_ms': duration_ms,
            'db_queries': db_queries,
            'db_time_ms': round(db_time_ms, 1),
            'user_agent': (request.headers.get('User-Agent', '') or '')[:200],
        }
        if error_category is not None:
            extra['error_category'] = error_category

        request_logger.log(
            level,
            '%s %s %d %dms (db: %d queries, %.1fms)',
            request.method,
            request.path,
            status,
            duration_ms,
            db_queries,
            db_time_ms,
            extra=extra,
        )

        if duration_ms > 500:
            request_logger.warning(
                'slow request: %s %s %dms (db: %d queries, %.1fms)',
                request.method,
                request.path,
                duration_ms,
                db_queries,
                db_time_ms,
            )

        return response

    # --- 8c. Unhandled-exception handler (Phase 18.9) ---
    # Flask's default 500 handler logs the traceback via ``app.logger``
    # but does not touch our structured logger or counter. Wire both up
    # so every 500 fires an ERROR record with exc_info and a category.
    # The response body is deliberately minimal — no traceback, no
    # internal paths, no SQL or config hints ever leak to the client.
    @app.errorhandler(Exception)
    def _handle_uncaught(exc):
        # HTTPException subclasses (404, 403, 429, ...) are NOT "bugs" —
        # let Flask render them with its default handler and the status
        # gets picked up by _log_request below for categorisation.
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc

        category = categorize_exception(exc, status_code=500)
        g.error_category = category  # picked up by _log_request

        request_logger.error(
            'Unhandled %s at %s %s',
            type(exc).__name__,
            request.method,
            request.path,
            exc_info=exc,
            extra={
                'method': request.method,
                'path': request.path,
                'status_code': 500,
                'error_category': category,
                'exception_type': type(exc).__name__,
            },
        )

        # Phase 19.1 event bus — fire security.internal_error so future
        # webhook / notification subscribers can react. Kept deliberately
        # lean (no exception message or traceback in the payload) so the
        # bus can't leak internals into third-party destinations.
        from app.events import Events as _Events
        from app.events import emit as _emit

        _emit(
            _Events.SECURITY_INTERNAL_ERROR,
            request_id=g.get('request_id', '-'),
            method=request.method,
            path=request.path,
            exception_type=type(exc).__name__,
            category=category,
        )

        accept = (request.headers.get('Accept') or '').lower()
        request_id = g.get('request_id', '-')
        if 'application/json' in accept:
            return (
                {
                    'error': 'internal server error',
                    'code': category,
                    'request_id': request_id,
                },
                500,
            )
        # HTML fallback — text/plain body keeps us independent of any
        # Jinja template the app may or may not have.
        body = (
            'Internal Server Error\n\n'
            f'Request ID: {request_id}\n'
            'Please quote the request ID when reporting this problem.\n'
        )
        return body, 500, {'Content-Type': 'text/plain; charset=utf-8'}

    # --- 8d. JSON 404/405 for /api/ paths (Phase 16.1) ---
    # Flask's routing dispatches unmatched URLs to an app-level 404
    # BEFORE any blueprint gets a chance to handle it, so the API
    # blueprint's own errorhandler(404) cannot fire for an unknown
    # /api/v1/... path. Register app-level handlers that return the
    # uniform JSON envelope for every /api/ request and fall back to
    # Flask's default HTML for non-API paths.

    @app.errorhandler(404)
    def _handle_404(exc):
        if request.path.startswith('/api/'):
            return (
                {'error': 'Not found', 'code': 'NOT_FOUND'},
                404,
                {'Content-Type': 'application/json'},
            )
        return exc

    @app.errorhandler(405)
    def _handle_405(exc):
        if request.path.startswith('/api/'):
            return (
                {'error': 'Method not allowed', 'code': 'METHOD_NOT_ALLOWED'},
                405,
                {'Content-Type': 'application/json'},
            )
        return exc

    # --- 8e. security.rate_limited event emission (Phase 19.1) ---
    # Catches every 429 response (Flask-Limiter and any other source)
    # and emits the canonical event so subscribers (alerts, abuse
    # dashboards, future webhook delivery) see a uniform shape. We
    # deliberately re-raise the original exception so Flask still uses
    # its normal 429 response — this handler is observability only,
    # not control flow.
    @app.errorhandler(429)
    def _handle_429(exc):
        from app.events import Events as _Events
        from app.events import emit as _emit

        # Best-effort payload: the URL rule (template, not the rendered
        # path with values) keeps cardinality bounded for any subscriber
        # that aggregates by endpoint. Fallback to '<unmatched>' for
        # paths that didn't match any route (rate-limited at a global
        # limit before dispatch).
        try:
            endpoint = request.url_rule.rule if request.url_rule else '<unmatched>'
        except RuntimeError:
            # No request context — exotic, but defend against it so the
            # observability hook can never blow up the response.
            endpoint = '<no-request-context>'

        with contextlib.suppress(Exception):
            _emit(
                _Events.SECURITY_RATE_LIMITED,
                request_id=g.get('request_id', '-') if g else '-',
                ip_hash=g.get('client_ip_hash', '-') if g else '-',
                method=request.method if request else '-',
                endpoint=endpoint,
                # Flask-Limiter sets exc.description to the limit string
                # (e.g. '5 per 1 minute'); other sources may not.
                limit=getattr(exc, 'description', None) or '',
            )
        # Hand the exception back to Flask so the original 429 response
        # (and any Retry-After header set by Flask-Limiter) is still served.
        return exc

    # --- 9. Security response headers ---
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

        # Content Security Policy (Phase 13.2). Nonce-based: inline scripts
        # and styles must carry the per-request nonce generated by
        # _assign_csp_nonce(). Enforced — violations are blocked and reported
        # to the /csp-report endpoint.
        nonce = g.get('csp_nonce', '')
        csp = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
            f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "report-uri /csp-report"
        )
        response.headers['Content-Security-Policy'] = csp

        # Cache-Control per route type
        if request.path.startswith('/admin'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        elif request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'

        # Request ID correlation header — echo the ID assigned in _assign_request_id
        # so clients and downstream log aggregators can match response to request.
        request_id = g.get('request_id')
        if request_id:
            response.headers['X-Request-ID'] = request_id

        return response

    # --- 9b. Asset fingerprinting (Phase 12.3) ---
    from app.assets import init_app as init_assets

    init_assets(app)

    # --- 10. Template context processor ---
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

        # Parse JSON layout settings (Phase 14.1)
        import json as _json

        nav_order = []
        with contextlib.suppress(Exception):
            raw = settings.get('nav_order', '')
            if raw:
                nav_order = _json.loads(raw)

        homepage_layout = []
        with contextlib.suppress(Exception):
            raw = settings.get('homepage_layout', '')
            if raw:
                homepage_layout = _json.loads(raw)

        custom_nav_links = []
        with contextlib.suppress(Exception):
            raw = settings.get('custom_nav_links', '')
            if raw:
                parsed = _json.loads(raw)
                if isinstance(parsed, list):
                    custom_nav_links = parsed[:10]

        return {
            'site_settings': settings,
            'site_config': site_config,
            'available_locales': available_locales,
            'current_locale': current_locale,
            'csp_nonce': g.get('csp_nonce', ''),
            'nav_order': nav_order,
            'homepage_layout': homepage_layout,
            'custom_nav_links': custom_nav_links,
        }

    # --- 11. Template filters (Phase 17.2) ---
    # ``time_ago`` renders ISO-8601 timestamps as "5 minutes ago" /
    # "yesterday" / etc. First consumer is the admin dashboard backup
    # health card; deliberately generic so future widgets can reuse it.
    from app.services.time_helpers import time_ago

    app.jinja_env.filters['time_ago'] = time_ago

    # --- 12. Webhook bus subscribers (Phase 19.2) ---
    # Subscribes one handler per Events.* constant so every emission
    # fans out to enabled webhooks. The handler closure captures the
    # database path so it works even when there's no Flask request
    # context (e.g. CLI invocations of `manage.py backup` that fire
    # backup.completed). The master `webhooks_enabled` toggle is read
    # at dispatch time, so admin edits propagate within the settings
    # cache TTL — no restart required.
    #
    # ``register_bus_handlers`` is idempotent: re-registering against
    # the same db_path drops the previous closures first. This keeps
    # the test suite from accumulating duplicates across the autouse
    # ``clear()`` fixture in tests/test_events.py and tests/test_webhooks.py.
    from app.services.webhooks import register_bus_handlers

    register_bus_handlers(app.config['DATABASE_PATH'])

    # --- 12b. Metrics event handlers (Phase 18.2 deferred counters) ---
    # Increment domain-specific counters on the event bus so the /metrics
    # route picks them up at scrape time.
    from app.events import Events
    from app.events import register as register_event
    from app.services.metrics import contact_submissions_total, photo_uploads_total

    register_event(
        Events.PHOTO_UPLOADED,
        lambda **_kw: photo_uploads_total.inc(),
    )
    register_event(
        Events.CONTACT_SUBMITTED,
        lambda **kw: contact_submissions_total.inc(
            label_values=(str(kw.get('is_spam', False)).lower(),)
        ),
    )

    # --- 13. Ensure storage directories exist ---
    os.makedirs(os.path.dirname(app.config['DATABASE_PATH']) or '.', exist_ok=True)
    os.makedirs(app.config['PHOTO_STORAGE'], exist_ok=True)

    return app
