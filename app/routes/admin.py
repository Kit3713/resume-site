"""
Admin Panel Routes

Provides a full content management interface for the site owner, protected
by both IP restriction and password authentication.

Security model:
1. IP restriction: before_request hook checks the client IP against the
   allowed_networks list in config.yaml. Requests from disallowed IPs
   get a 403 before any route handler runs.
2. Authentication: Flask-Login session-based auth with password hashed
   via Werkzeug's pbkdf2:sha256. Every route (except /login) requires
   @login_required.

Admin features:
- Dashboard: Analytics overview (page views, popular pages, pending reviews)
- Content: Rich text editor (Quill.js) for managing content blocks
- Photos: Upload with Pillow processing, edit metadata, manage display tiers
- Reviews: Approve/reject pending reviews, set display tiers
- Tokens: Generate invite-only review URLs
- Services: CRUD for service cards shown on the public pages
- Stats: CRUD for animated counter stats on the landing page
- Settings: All site toggles, identity, hero section, contact visibility
"""

import contextlib
import hmac
import ipaddress
import os
import re
import secrets
from datetime import UTC, datetime
from urllib.parse import urlparse

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app import limiter
from app.db import get_db
from app.models import AdminUser
from app.services.activity_log import get_recent_activity, log_action
from app.services.content import create_block, delete_block, get_all_blocks, save_block
from app.services.form import get_stripped
from app.services.reviews import (
    approve_review,
    get_reviews_by_status,
    reject_review,
    update_review_tier,
)
from app.services.service_items import (
    add_service,
    delete_service,
    get_all_services,
    update_service,
)
from app.services.settings_svc import get_all as get_all_settings_svc
from app.services.settings_svc import get_grouped_settings
from app.services.settings_svc import save_many as save_settings
from app.services.stats import add_stat, delete_stat, get_all_stats, update_stat

admin_bp = Blueprint('admin', __name__, template_folder='../templates')


# Phase 23.3 (#46) — dummy password hash used when the submitted
# username does not match the configured admin username, so the scrypt
# cost is paid on every login attempt regardless of whether the name
# was valid. The hash is generated once per process from a fresh random
# password; the plaintext is discarded immediately so nothing can match
# it. Same algorithm defaults as ``generate_password_hash`` produces for
# an operator who runs ``manage.py hash-password``, so the verification
# cost matches the real hash within the scrypt noise floor.
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))

# Phase 23.6 (#50) — photo display_tier is a closed enum. Every write
# path (this HTML handler and the service-layer API path) validates
# against this set before inserting, so the column can never carry an
# unknown value that would silently bypass the public visibility
# filter in ``get_visible_photos``.
_VALID_PHOTO_DISPLAY_TIERS = {'featured', 'grid', 'hidden'}


# ============================================================
# IP RESTRICTION MIDDLEWARE
# ============================================================


@admin_bp.before_request
def restrict_to_allowed_networks():
    """Block admin access from IPs outside the configured allowed networks.

    Runs before every admin route. Reads the effective client IP via
    :func:`app.services.request_ip.get_client_ip` (the one helper that
    correctly walks ``X-Forwarded-For`` right-to-left against the
    ``trusted_proxies`` set — see that module's docstring for the
    algorithm and the Phase 22.6 → 23.2 history) and checks it against
    the CIDR ranges in ``config.yaml`` ``admin.allowed_networks``.

    Fails closed: unparseable IP → 403; malformed allowlist entry is
    skipped. An empty allowlist is permissive (the explicit "no gate"
    opt-in for trusted-LAN deployments).
    """
    from app.services.request_ip import get_client_ip, parse_cidr_list

    config = current_app.config['SITE_CONFIG']
    allowed = config.get('admin', {}).get('allowed_networks', [])

    # If no networks are configured, allow all (not recommended for production)
    if not allowed:
        return

    trusted_proxies = parse_cidr_list(config.get('trusted_proxies'))
    client_ip_str = get_client_ip(request, trusted_proxies)

    # Parse the client IP (fail closed on invalid values)
    try:
        client_ip = ipaddress.ip_address(client_ip_str)
    except (ValueError, TypeError):
        abort(403)

    # Check if the client IP falls within any allowed network
    for network_str in allowed:
        try:
            network = ipaddress.ip_network(network_str, strict=False)
            if client_ip in network:
                return  # IP is allowed — proceed to the route handler
        except ValueError:
            continue  # Skip malformed network entries

    # No matching network found — block the request
    abort(403)


@admin_bp.after_request
def update_last_activity(response):
    """Record the timestamp of each admin request for session timeout tracking."""
    if current_user.is_authenticated:
        session['_last_activity'] = datetime.now(UTC).isoformat()
    return response


@admin_bp.before_request
def check_session_timeout():
    """Expire admin sessions after a period of inactivity.

    The timeout is configurable via session_timeout_minutes in config.yaml
    (default 60 minutes). Only applies to authenticated users — the login
    page is always accessible. On timeout, the user is logged out and
    redirected to the login page with a flash message.
    """
    if not current_user.is_authenticated:
        return

    last_activity = session.get('_last_activity')
    if last_activity:
        try:
            last_dt = datetime.fromisoformat(last_activity)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
            timeout_minutes = current_app.config.get('SESSION_TIMEOUT_MINUTES', 60)
            elapsed = (datetime.now(UTC) - last_dt).total_seconds() / 60
            if elapsed > timeout_minutes:
                logout_user()
                session.clear()
                flash(_('Session expired due to inactivity. Please log in again.'), 'error')
                return redirect(url_for('admin.login'))
        except (ValueError, TypeError):
            pass  # Malformed timestamp — let the request proceed


def check_session_epoch():
    """Reject sessions older than the current admin session epoch.

    Flask's default itsdangerous-signed cookie sessions cannot be revoked
    server-side — a captured pre-logout cookie keeps deserialising to the
    original ``{_user_id: 'admin'}`` dict forever. To fix the cookie-replay
    finding documented in the 2026-04-18 pentest we compare a per-session
    epoch stamp (written at login) against the current epoch stored in the
    ``settings`` table; logout bumps the stored epoch so every
    previously-issued cookie becomes invalid.

    Phase 23.1 (#33) — read via ``get_uncached`` rather than the shared
    settings cache. Another worker that just bumped the epoch on logout
    would otherwise stay invisible here for up to ``DEFAULT_SETTINGS_TTL``
    (30 s), during which a captured cookie still authenticates. The
    per-request SELECT is acceptable on the admin hot path (one integer
    lookup; orders of magnitude below login's scrypt cost).

    Phase 23.1 (#51) — this function is shared between ``admin_bp`` and
    ``blog_admin_bp`` (registered as ``before_request`` on both). Do not
    decorate it here; the blueprints register the callable directly so a
    new admin-prefixed blueprint can opt into the same guard without
    having to import a decorator that's already attached to another
    blueprint.

    Runs after ``check_session_timeout`` so the activity-timeout path still
    applies. No-ops for unauthenticated requests.
    """
    if not current_user.is_authenticated:
        return

    from app.services.settings_svc import get_uncached

    db = get_db()
    try:
        current_epoch = int(get_uncached(db, '_admin_session_epoch', '0'))
    except (TypeError, ValueError):
        current_epoch = 0

    session_epoch = session.get('_admin_epoch')
    if session_epoch != current_epoch:
        logout_user()
        session.clear()
        return redirect(url_for('admin.login'))


admin_bp.before_request(check_session_epoch)


# ============================================================
# AUTHENTICATION
# ============================================================


@admin_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
def login():
    """Handle admin login form display and credential validation.

    Validates the username and password against the values stored in
    config.yaml (not in the database). Uses Werkzeug's secure password
    hash comparison to prevent timing attacks.

    Defence-in-depth (Phase 13.6): on top of Flask-Limiter's per-minute
    rate, an application-level lockout persists failed attempts in the
    ``login_attempts`` table and rejects new attempts from an IP that has
    crossed the configured threshold. Both mechanisms run together — the
    rate-limit refuses bursts, the lockout refuses slow sustained
    probing across rate-limit windows.
    """
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        from app.db import get_db
        from app.events import Events as _Events
        from app.events import emit as _emit
        from app.services.login_throttle import (
            check_lockout,
            record_failed_login,
            record_successful_login,
        )
        from app.services.settings_svc import get_all_cached

        config = current_app.config['SITE_CONFIG']
        admin_config = config.get('admin', {})
        username = request.form.get('username', '')
        password = request.form.get('password', '')

        ip_hash = g.get('client_ip_hash', '-') or '-'
        db = get_db()
        settings = get_all_cached(db, current_app.config['DATABASE_PATH'])

        def _setting_int(key, default):
            raw = settings.get(key, default)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return int(default)

        status = check_lockout(
            db,
            ip_hash,
            threshold=_setting_int('login_lockout_threshold', '10'),
            window_minutes=_setting_int('login_lockout_window_minutes', '15'),
            lockout_minutes=_setting_int('login_lockout_duration_minutes', '15'),
        )

        if status.locked:
            _emit(
                _Events.SECURITY_LOGIN_FAILED,
                request_id=g.get('request_id', '-'),
                ip_hash=ip_hash,
                reason='locked',
                failures_in_window=status.failures_in_window,
                seconds_remaining=status.seconds_remaining,
            )
            flash(
                _(
                    'Too many failed login attempts from your IP. '
                    'Please try again in a few minutes.'
                ),
                'error',
            )
            return (
                render_template('admin/login.html'),
                429,
                {'Retry-After': str(status.seconds_remaining)},
            )

        # Verify credentials against YAML config.
        #
        # Phase 23.3 (#38) — ``hmac.compare_digest`` makes the username
        # compare take a wall-clock time that's independent of how many
        # leading bytes match, so the attacker can't brute-force the
        # admin username one character at a time off the timing signal.
        #
        # Phase 23.3 (#46) — ``check_password_hash`` is ALWAYS run, even
        # on a username miss or when no ``password_hash`` is set. The
        # dummy hash below has the same scrypt cost as the real hash, so
        # the wall-clock delta between "valid user / bad password" and
        # "unknown user" is below the noise floor. Without this the
        # scrypt work only ran on a username hit, exposing a timing
        # oracle that confirmed username existence in a single request
        # (useful to an attacker who guessed the wrong name).
        expected_username = admin_config.get('username', 'admin') or ''
        real_hash = admin_config.get('password_hash', '') or ''
        username_match = hmac.compare_digest(
            username.encode('utf-8'),
            expected_username.encode('utf-8'),
        )
        hash_to_check = real_hash if (username_match and real_hash) else _DUMMY_PASSWORD_HASH
        password_ok = check_password_hash(hash_to_check, password)
        if username_match and real_hash and password_ok:
            record_successful_login(db, ip_hash)
            user = AdminUser(username)
            login_user(user)
            # Stamp the session with the current epoch so the
            # ``check_session_epoch`` guard accepts it. ``_admin_session_epoch``
            # is an integer counter in the settings table; it's bumped on
            # logout (see ``logout``) so every pre-logout cookie becomes
            # invalid. Fresh logins always adopt the current value.
            #
            # Phase 23.1 (#33) — read via ``get_uncached`` to match the
            # check-path read strategy. A logout that landed on another
            # worker must be reflected in this session's stamp within
            # one request, not after the cache TTL expires.
            from app.services.settings_svc import get_uncached

            try:
                current_epoch = int(get_uncached(db, '_admin_session_epoch', '0'))
            except (TypeError, ValueError):
                current_epoch = 0
            session['_admin_epoch'] = current_epoch
            # Redirect to the page they were trying to access, or the dashboard.
            # Only accept same-origin relative paths — reject absolute URLs,
            # scheme-relative URLs (//evil.com), and anything with a netloc to
            # prevent open-redirect abuse of the ?next= query parameter.
            next_page = request.args.get('next', '')
            parsed = urlparse(next_page)
            if next_page and not parsed.scheme and not parsed.netloc and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('admin.dashboard'))

        # Credentials rejected — persist the failure + emit the event.
        record_failed_login(db, ip_hash)
        _emit(
            _Events.SECURITY_LOGIN_FAILED,
            request_id=g.get('request_id', '-'),
            ip_hash=ip_hash,
            reason='invalid_credentials',
            username=username[:64] if username else '',
        )
        flash(_('Invalid credentials.'), 'error')

    return render_template('admin/login.html')


@admin_bp.route('/logout')
@login_required
def logout():
    """Log out the admin and redirect to the public landing page.

    Bumps ``_admin_session_epoch`` in the settings table so every
    previously-issued admin session cookie (including any captured
    before this logout) fails the ``check_session_epoch`` guard on
    its next request. This is the server-side revocation needed
    because Flask's default cookie sessions can't be invalidated
    just by clearing the jar.
    """
    from app.services.settings_svc import get_uncached, invalidate_cache

    db = get_db()
    # Phase 23.1 (#33) — uncached read so a near-simultaneous logout on
    # another worker is not double-counted or lost in an increment race.
    try:
        current_epoch = int(get_uncached(db, '_admin_session_epoch', '0'))
    except (TypeError, ValueError):
        current_epoch = 0
    # ``set_one`` refuses keys outside SETTINGS_REGISTRY; write directly.
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        ('_admin_session_epoch', str(current_epoch + 1)),
    )
    db.commit()
    invalidate_cache()

    logout_user()
    session.clear()
    return redirect(url_for('public.index'))


# ============================================================
# DASHBOARD
# ============================================================


@admin_bp.route('/')
@login_required
def dashboard():
    """Render the admin dashboard with analytics overview.

    Displays key metrics: total/recent page views, most popular pages,
    pending review count, and recent contact form submissions.
    """
    db = get_db()

    # Page view analytics
    total_views = db.execute('SELECT COUNT(*) as cnt FROM page_views').fetchone()['cnt']
    recent_views = db.execute(
        "SELECT COUNT(*) as cnt FROM page_views WHERE created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')"
    ).fetchone()['cnt']
    popular_pages = db.execute(
        'SELECT path, COUNT(*) as cnt FROM page_views GROUP BY path ORDER BY cnt DESC LIMIT 5'
    ).fetchall()

    # Review and contact metrics
    pending_reviews = db.execute(
        "SELECT COUNT(*) as cnt FROM reviews WHERE status = 'pending'"
    ).fetchone()['cnt']
    recent_contacts = db.execute(
        'SELECT * FROM contact_submissions WHERE is_spam = 0 ORDER BY created_at DESC LIMIT 5'
    ).fetchall()
    unread_contacts = db.execute(
        'SELECT COUNT(*) as cnt FROM contact_submissions WHERE is_spam = 0 AND read = 0'
    ).fetchone()['cnt']

    # Activity log
    try:
        activity = get_recent_activity(db, limit=10)
    except Exception:
        activity = []  # Table may not exist until migration 003 is applied

    # Backup health (Phase 17.2). `backup_last_success` is written by
    # `app.services.backups.create_backup` on every successful run and
    # is intentionally not in SETTINGS_REGISTRY (diagnostic, not user-
    # editable), so we read it directly. Empty string means "no backup
    # has ever run on this deployment" — the template renders 'never'.
    last_row = db.execute("SELECT value FROM settings WHERE key = 'backup_last_success'").fetchone()
    backup_last_success = last_row['value'] if last_row else None

    backup_dir = os.path.abspath(
        os.environ.get('RESUME_SITE_BACKUP_DIR')
        or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'backups')
    )
    try:
        from app.services.backups import list_backups

        backup_entries = list_backups(backup_dir)
    except Exception:  # noqa: BLE001 — diagnostic widget, never break the dashboard
        backup_entries = []

    backup_count = len(backup_entries)
    backup_total_bytes = sum(entry.size_bytes for entry in backup_entries)
    # Show the five newest in the dashboard table; the full list lives
    # behind the future /admin/backups page.
    recent_backups = backup_entries[:5]

    # Error summary (Phase 18.9). Read from the in-memory metrics
    # counters — these reset on process restart, which is fine for a
    # dashboard overview. The full history lives in structured logs.
    error_summary = {}
    try:
        from app.services.metrics import errors_total

        for label_key, count in errors_total._values.items():
            category = label_key[0]  # (category, status) tuple
            error_summary[category] = error_summary.get(category, 0) + int(count)
    except Exception:  # noqa: BLE001, S110 — diagnostic widget, never break the dashboard
        pass
    total_errors = sum(error_summary.values())

    # Translation completeness matrix (Phase 36.3). Reads the
    # ``available_locales`` setting and produces one aggregate query per
    # translatable content type. Wrapped defensively so a missing junction
    # table (pre-migration-011 deployments) never breaks the dashboard.
    translation_matrix: list = []
    translation_locales: list[str] = []
    try:
        from app.services.settings_svc import get_all_cached as _get_settings_cached
        from app.services.translations import get_coverage_matrix

        settings_row = _get_settings_cached(db, current_app.config['DATABASE_PATH'])
        default_locale = (settings_row.get('default_locale') or 'en').strip() or 'en'
        raw_locales = settings_row.get('available_locales') or default_locale
        translation_locales = [loc.strip() for loc in raw_locales.split(',') if loc.strip()]
        if default_locale not in translation_locales:
            translation_locales.insert(0, default_locale)
        translation_matrix = get_coverage_matrix(db, translation_locales, default_locale)
    except Exception:  # noqa: BLE001 — diagnostic widget, never break the dashboard
        translation_matrix = []
        translation_locales = []

    # In-app alerting widget (Phase 36.5). Reads the parsed alerting-rules.yaml
    # thresholds (cached at app startup) and compares them against the same
    # in-memory counters the "Errors (since restart)" card already consumes.
    try:
        from app.services.alerting import get_active_alerts

        active_alerts = get_active_alerts(error_summary)
    except Exception:  # noqa: BLE001 — never break the dashboard
        active_alerts = []

    return render_template(
        'admin/dashboard.html',
        total_views=total_views,
        recent_views=recent_views,
        popular_pages=popular_pages,
        pending_reviews=pending_reviews,
        recent_contacts=recent_contacts,
        unread_contacts=unread_contacts,
        activity=activity,
        backup_last_success=backup_last_success,
        backup_count=backup_count,
        backup_total_bytes=backup_total_bytes,
        backup_dir=backup_dir,
        recent_backups=recent_backups,
        total_errors=total_errors,
        error_summary=error_summary,
        translation_matrix=translation_matrix,
        translation_locales=translation_locales,
        active_alerts=active_alerts,
    )


# ============================================================
# CONTENT EDITOR (Quill.js rich text blocks)
# ============================================================


@admin_bp.route('/content')
@login_required
def content():
    """List all content blocks for editing."""
    db = get_db()
    blocks = get_all_blocks(db)
    return render_template('admin/content.html', blocks=blocks)


@admin_bp.route('/content/edit/<slug>', methods=['GET', 'POST'])
@login_required
def content_edit(slug):
    """Edit an existing content block or create one if the slug is new.

    The Quill.js editor on the frontend submits HTML content via a hidden
    input field. Content is sanitized via nh3 before storage.
    """
    db = get_db()
    from app.services.content import get_block_by_slug

    block = get_block_by_slug(db, slug)

    if request.method == 'POST':
        title = request.form.get('title', '')
        content_html = request.form.get('content', '')
        save_block(db, slug, title, content_html, create_if_missing=True)
        flash(_('Content saved.'), 'success')
        return redirect(url_for('admin.content'))

    return render_template('admin/content_edit.html', block=block, slug=slug)


@admin_bp.route('/content/new', methods=['GET', 'POST'])
@login_required
def content_new():
    """Create a new content block with a unique slug identifier."""
    if request.method == 'POST':
        db = get_db()
        from app.services.content import get_block_by_slug

        raw_slug = get_stripped(request.form, 'slug')
        normalized = raw_slug.lower().replace(' ', '_')
        title = get_stripped(request.form, 'title')
        content_html = request.form.get('content', '')
        if not normalized:
            flash(_('Slug is required.'), 'error')
            return redirect(url_for('admin.content'))
        if get_block_by_slug(db, normalized):
            flash(
                _(
                    'A content block with slug "%(slug)s" already exists. Edit it instead.',
                    slug=normalized,
                ),
                'error',
            )
            return redirect(url_for('admin.content_edit', slug=normalized))
        create_block(db, raw_slug, title, content_html)
        flash(_('Content block created.'), 'success')
        return redirect(url_for('admin.content_edit', slug=normalized))
    return render_template('admin/content_edit.html', block=None, slug='')


@admin_bp.route('/content/delete/<slug>', methods=['POST'])
@login_required
def content_delete(slug):
    """Delete a content block by slug."""
    db = get_db()
    if delete_block(db, slug):
        flash(_('Content block "%(slug)s" deleted.', slug=slug), 'success')
    else:
        flash(_('Content block "%(slug)s" not found.', slug=slug), 'error')
    return redirect(url_for('admin.content'))


# ============================================================
# PHOTO MANAGER
# ============================================================


@admin_bp.route('/photos')
@login_required
def photos():
    """List all uploaded photos with inline edit forms."""
    db = get_db()
    photo_list = db.execute('SELECT * FROM photos ORDER BY sort_order, created_at DESC').fetchall()
    return render_template('admin/photos.html', photos=photo_list)


@admin_bp.route('/photos/upload', methods=['POST'])
@login_required
def photos_upload():
    """Handle photo file upload with Pillow processing.

    The upload workflow:
    1. Validate that a file was provided.
    2. Process through Pillow (resize if > 2000px, optimize quality).
    3. Save metadata to the database (filename, dimensions, MIME type).
    4. Redirect back to the photo manager with a success message.
    """
    db = get_db()
    file = request.files.get('photo')
    if not file or not file.filename:
        flash(_('No file selected.'), 'error')
        return redirect(url_for('admin.photos'))

    from app.services.photos import process_upload

    result = process_upload(file)
    if result is None:
        flash(_('Invalid file type. Allowed: jpg, png, gif, webp.'), 'error')
        return redirect(url_for('admin.photos'))
    if isinstance(result, str):
        flash(result, 'error')
        return redirect(url_for('admin.photos'))

    # Read optional metadata from the upload form
    title = request.form.get('title', '')
    description = request.form.get('description', '')
    category = request.form.get('category', '')
    # Phase 23.6 (#50) — validate display_tier against the allowed set
    # before inserting. The API path already rejected unknown tiers in
    # the service layer; the HTML admin form accepted the value
    # verbatim, so a manually-crafted form could stuff any string into
    # the column (which then broke the public visibility filter that
    # relied on the enum being one of the three known values).
    display_tier = request.form.get('display_tier', 'grid')
    if display_tier not in _VALID_PHOTO_DISPLAY_TIERS:
        flash(_('Invalid display tier.'), 'error')
        # Clean up the quarantine file so a rejected upload doesn't leak disk.
        with contextlib.suppress(Exception):
            os.remove(os.path.join(current_app.config['PHOTO_STORAGE'], result['storage_name']))
        return redirect(url_for('admin.photos'))

    cursor = db.execute(
        'INSERT INTO photos '
        '(filename, storage_name, mime_type, width, height, file_size, title, description, category, display_tier) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            result['filename'],
            result['storage_name'],
            result['mime_type'],
            result['width'],
            result['height'],
            result['file_size'],
            title,
            description,
            category,
            display_tier,
        ),
    )
    db.commit()

    # Phase 19.1 event bus + Phase 36.7 subscribers — emitting
    # ``photo.uploaded`` now drives both the activity-log entry and the
    # Prometheus-style counter via ``app.services.event_subscribers``.
    # Mirrors the API-side emission in ``app.routes.api.portfolio_create``.
    from app.events import Events as _Events
    from app.events import emit as _emit

    _emit(
        _Events.PHOTO_UPLOADED,
        photo_id=cursor.lastrowid,
        title=title,
        category=category,
        display_tier=display_tier,
        storage_name=result['storage_name'],
        file_size=result['file_size'],
        source='admin_ui',
    )

    flash(_('Photo uploaded successfully.'), 'success')
    return redirect(url_for('admin.photos'))


@admin_bp.route('/photos/<int:photo_id>/edit', methods=['POST'])
@login_required
def photos_edit(photo_id):
    """Update photo metadata (title, description, category, display tier, sort order)."""
    db = get_db()
    title = request.form.get('title', '')
    description = request.form.get('description', '')
    tech_used = request.form.get('tech_used', '')
    category = request.form.get('category', '')
    display_tier = request.form.get('display_tier', 'grid')
    sort_order = request.form.get('sort_order', '0')

    db.execute(
        'UPDATE photos SET title=?, description=?, tech_used=?, category=?, display_tier=?, sort_order=?, '
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (title, description, tech_used, category, display_tier, int(sort_order), photo_id),
    )
    db.commit()
    flash(_('Photo updated.'), 'success')
    return redirect(url_for('admin.photos'))


@admin_bp.route('/photos/<int:photo_id>/delete', methods=['POST'])
@login_required
def photos_delete(photo_id):
    """Delete a photo: remove the file from disk and the record from the database."""
    db = get_db()
    photo = db.execute('SELECT storage_name FROM photos WHERE id = ?', (photo_id,)).fetchone()
    if photo:
        from app.services.photos import delete_photo_file

        delete_photo_file(photo['storage_name'])
        db.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
        db.commit()
        flash(_('Photo deleted.'), 'success')
    return redirect(url_for('admin.photos'))


# ============================================================
# REVIEW MANAGER
# ============================================================


@admin_bp.route('/reviews')
@login_required
def reviews():
    """List all reviews grouped by status (pending, approved, rejected)."""
    db = get_db()
    pending = get_reviews_by_status(db, 'pending')
    approved = get_reviews_by_status(db, 'approved')
    rejected = get_reviews_by_status(db, 'rejected')
    return render_template(
        'admin/reviews.html', pending=pending, approved=approved, rejected=rejected
    )


@admin_bp.route('/reviews/<int:review_id>/update', methods=['POST'])
@login_required
def reviews_update(review_id):
    """Update a review's status or display tier."""
    from app.events import Events as _Events
    from app.events import emit as _emit

    db = get_db()
    action = request.form.get('action', '')
    display_tier = request.form.get('display_tier', 'standard')

    if action == 'approve':
        approve_review(db, review_id, display_tier)
    elif action == 'reject':
        reject_review(db, review_id)
    elif action == 'update_tier':
        update_review_tier(db, review_id, display_tier)

    with contextlib.suppress(Exception):
        log_action(db, f'{action.capitalize()}d review', 'reviews', f'ID {review_id}')

    # Phase 19.1 event bus — only the approve action fires
    # `review.approved` (mirrors the API-side emission). reject /
    # update_tier are admin housekeeping that webhook subscribers don't
    # typically care about; they remain visible via the activity log.
    if action == 'approve':
        _emit(
            _Events.REVIEW_APPROVED,
            review_id=review_id,
            display_tier=display_tier,
            source='admin_ui',
        )

    flash(_('Review updated.'), 'success')
    return redirect(url_for('admin.reviews'))


# ============================================================
# TOKEN GENERATOR (review invite system)
# ============================================================


@admin_bp.route('/tokens')
@login_required
def tokens():
    """List all generated review tokens with their status."""
    db = get_db()
    token_list = db.execute('SELECT * FROM review_tokens ORDER BY created_at DESC').fetchall()
    return render_template('admin/tokens.html', tokens=token_list)


@admin_bp.route('/tokens/generate', methods=['POST'])
@login_required
def tokens_generate():
    """Generate a new review invitation token.

    Creates a cryptographically secure URL-safe token using Python's
    secrets module. The token is tagged with a type ('recommendation'
    or 'client_review') which is inherited by the submitted review.
    """
    db = get_db()
    name = get_stripped(request.form, 'name')
    token_type = request.form.get('type', 'recommendation')
    if token_type not in ('recommendation', 'client_review'):
        token_type = 'recommendation'  # noqa: S105 — enum label, not a credential

    # Generate a 32-byte URL-safe token (43 characters)
    token_string = secrets.token_urlsafe(32)
    db.execute(
        'INSERT INTO review_tokens (token, name, type) VALUES (?, ?, ?)',
        (token_string, name, token_type),
    )
    db.commit()
    flash(_('Token generated for %(name)s.', name=name or _('anonymous')), 'success')
    return redirect(url_for('admin.tokens'))


@admin_bp.route('/tokens/<int:token_id>/delete', methods=['POST'])
@login_required
def tokens_delete(token_id):
    """Delete a review token (revokes the invitation)."""
    db = get_db()
    db.execute('DELETE FROM review_tokens WHERE id = ?', (token_id,))
    db.commit()
    flash(_('Token deleted.'), 'success')
    return redirect(url_for('admin.tokens'))


# ============================================================
# API TOKENS (Phase 13.4 — programmatic access for the REST API)
# ============================================================


@admin_bp.route('/api-tokens')
@login_required
def api_tokens():
    """List all API tokens with their status and metadata."""
    from app.services.api_tokens import list_tokens

    db = get_db()
    records = list_tokens(db, include_revoked=True)
    return render_template('admin/api_tokens.html', tokens=records)


@admin_bp.route('/api-tokens/generate', methods=['POST'])
@login_required
def api_tokens_generate():
    """Generate a new API token.

    The raw value is stashed in the session and displayed exactly once
    via :func:`api_tokens_reveal`; after that GET the session slot is
    popped, so refreshing the reveal page or coming back later yields
    no token.
    """
    from app.events import Events as _Events
    from app.events import emit as _emit
    from app.services.api_tokens import (
        InvalidScopeError,
        generate_token,
        parse_expires,
    )

    db = get_db()
    name = get_stripped(request.form, 'name')
    scope_items = request.form.getlist('scope')
    expires_raw = get_stripped(request.form, 'expires')

    if not name:
        flash(_('Name is required.'), 'error')
        return redirect(url_for('admin.api_tokens'))
    if not scope_items:
        flash(_('Select at least one scope.'), 'error')
        return redirect(url_for('admin.api_tokens'))
    scope_str = ','.join(scope_items)

    try:
        expires_at = parse_expires(expires_raw)
    except ValueError as e:
        flash(_('Invalid expiry: %(err)s', err=str(e)), 'error')
        return redirect(url_for('admin.api_tokens'))

    try:
        result = generate_token(
            db,
            name=name,
            scope=scope_str,
            expires_at=expires_at,
            created_by=current_user.id if current_user.is_authenticated else 'admin',
        )
    except InvalidScopeError as e:
        flash(_('Invalid scope: %(err)s', err=str(e)), 'error')
        return redirect(url_for('admin.api_tokens'))

    log_action(
        db,
        action='Generated API token',
        category='api_tokens',
        detail=f'{name} ({result.scope})',
    )
    _emit(
        _Events.API_TOKEN_CREATED,
        name=result.name,
        scope=result.scope,
        created_by=current_user.id if current_user.is_authenticated else 'admin',
        expires_at=result.expires_at or '',
        token_id=result.id,
    )

    # Phase 22.4 — the raw token never lands in the Flask session cookie
    # (signed, not encrypted). Stash it in ``api_token_reveals`` keyed by
    # a random reveal_id; put only the reveal_id in the session.
    from app.services.api_token_reveals import create_reveal, prune_expired_reveals

    prune_expired_reveals(db)
    reveal_id = create_reveal(
        db,
        token_id=result.id,
        raw=result.raw,
        name=result.name,
        scope=result.scope,
        token_expires_at=result.expires_at or '',
    )
    # Drop any legacy cookie slot carried over from a pre-22.4 session.
    session.pop('_api_token_reveal', None)
    session['_api_token_reveal_id'] = reveal_id
    return redirect(url_for('admin.api_tokens_reveal'))


@admin_bp.route('/api-tokens/reveal')
@login_required
def api_tokens_reveal():
    """Display a freshly-generated token exactly once.

    Phase 22.4: the raw token lives in a server-side
    ``api_token_reveals`` row keyed by the ``reveal_id`` stashed in the
    session. Look-up / delete / expiry-check are atomic in
    :func:`consume_reveal` — a second GET, browser refresh, or back
    button lands on the missing-row branch.
    """
    from app.services.api_token_reveals import consume_reveal, prune_expired_reveals

    db = get_db()
    reveal_id = session.pop('_api_token_reveal_id', None)
    # Also drop any pre-22.4 session slot that a long-lived session
    # might still carry (the plaintext-in-cookie shape).
    session.pop('_api_token_reveal', None)
    # Consume must run *before* the prune so the status/expiry check
    # for the caller's own reveal row isn't pre-empted by the bulk
    # DELETE. Prune runs afterwards as opportunistic cleanup of any
    # other rows whose TTL lapsed while the admin was idle.
    status, payload = consume_reveal(db, reveal_id or '')
    prune_expired_reveals(db)
    if status == 'expired':
        # 410 Gone is the semantically correct status for a resource
        # that existed but is now permanently unavailable — matches the
        # contract the #58 audit finding documented.
        return (
            render_template(
                'admin/api_tokens_reveal.html',
                token=None,
                expired=True,
            ),
            410,
        )
    if status != 'ok' or payload is None:
        flash(
            _('No token to reveal. Generate a new one from the API Tokens page.'),
            'info',
        )
        return redirect(url_for('admin.api_tokens'))
    token_ctx = {
        'id': payload.token_id,
        'raw': payload.raw,
        'name': payload.name,
        'scope': payload.scope,
        'expires_at': payload.token_expires_at or '',
    }
    return render_template('admin/api_tokens_reveal.html', token=token_ctx, expired=False)


@admin_bp.route('/api-tokens/<int:token_id>/revoke', methods=['POST'])
@login_required
def api_tokens_revoke(token_id):
    """Revoke an API token (soft delete — row retained for audit)."""
    from app.services.api_tokens import revoke_token

    db = get_db()
    changed = revoke_token(db, token_id)
    if changed:
        log_action(
            db,
            action='Revoked API token',
            category='api_tokens',
            detail=f'id={token_id}',
        )
        flash(_('API token revoked.'), 'success')
    else:
        flash(_('Token was already revoked or does not exist.'), 'info')
    return redirect(url_for('admin.api_tokens'))


# ============================================================
# WEBHOOKS (Phase 19.2 — outbound HMAC-signed event delivery)
# ============================================================
#
# Operator surface for the Phase 19.2 dispatcher in
# ``app/services/webhooks.py``. The service layer owns CRUD, signing,
# and delivery; these routes are pure adapters that translate form
# submissions into service calls and render the templates.
#
# The master ``webhooks_enabled`` toggle still lives on the Settings
# page (Webhooks category). Disabling it short-circuits dispatch
# without affecting anything visible here — operators can still create,
# edit, and test rows while the master switch is off.


def _normalise_webhook_events_form(raw):
    """Translate the textarea/list form submission into a clean event list.

    Accepts either a JSON-array string ("Use raw JSON" power-user mode)
    or a comma / whitespace separated string of event names. Empty input
    becomes ``["*"]`` so a row created without explicit events still
    receives every event (matches the schema default).
    """
    text = (raw or '').strip()
    if not text:
        return ['*']
    # JSON array form — preserve exact ordering.
    if text.startswith('['):
        try:
            import json as _json

            parsed = _json.loads(text)
            if isinstance(parsed, list):
                return [str(e).strip() for e in parsed if str(e).strip()] or ['*']
        except ValueError:
            pass
    # Otherwise treat commas, semicolons, and whitespace as separators.
    parts = re.split(r'[\s,;]+', text)
    cleaned = [p.strip() for p in parts if p.strip()]
    return cleaned or ['*']


def _generate_webhook_secret():
    """32-byte URL-safe random string for the HMAC secret default."""
    return secrets.token_urlsafe(32)


@admin_bp.route('/webhooks')
@login_required
def webhooks():
    """List every webhook with delivery counts + last-status hints."""
    from app.events import Events
    from app.services.webhooks import list_recent_deliveries, list_webhooks

    db = get_db()
    rows = list_webhooks(db)
    recent = list_recent_deliveries(db, limit=20)
    return render_template(
        'admin/webhooks.html',
        webhooks=rows,
        recent_deliveries=recent,
        event_choices=sorted(Events.ALL),
        new_secret=_generate_webhook_secret(),
    )


@admin_bp.route('/webhooks/create', methods=['POST'])
@login_required
def webhooks_create():
    """Create a new webhook subscription."""
    from app.services.settings_svc import get as get_setting
    from app.services.webhooks import create_webhook, validate_webhook_target

    db = get_db()
    name = get_stripped(request.form, 'name')
    url = get_stripped(request.form, 'url')
    secret = get_stripped(request.form, 'secret') or _generate_webhook_secret()
    enabled = bool(request.form.get('enabled'))
    events_list = _normalise_webhook_events_form(request.form.get('events'))

    if not name:
        flash(_('Name is required.'), 'error')
        return redirect(url_for('admin.webhooks'))
    if not url:
        flash(_('URL is required.'), 'error')
        return redirect(url_for('admin.webhooks'))
    # Phase 22.3 — SSRF gate. Reject loopback / private / link-local /
    # CGNAT / ULA targets unless the operator has opted in via the
    # `webhook_allow_private_targets` setting. DNS-resolves the host
    # now; delivery-time code re-resolves to defeat DNS rebinding.
    allow_private = get_setting(db, 'webhook_allow_private_targets', 'false').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }
    ok, msg = validate_webhook_target(url, allow_private=allow_private)
    if not ok:
        flash(msg, 'error')
        return redirect(url_for('admin.webhooks'))

    wh_id = create_webhook(
        db,
        name=name,
        url=url,
        secret=secret,
        events=events_list,
        enabled=enabled,
    )
    log_action(
        db,
        action='Created webhook',
        category='webhooks',
        detail=f'id={wh_id} name={name} events={",".join(events_list)}',
    )
    flash(_('Webhook created.'), 'success')
    return redirect(url_for('admin.webhooks'))


@admin_bp.route('/webhooks/<int:webhook_id>/update', methods=['POST'])
@login_required
def webhooks_update(webhook_id):
    """Edit fields on an existing webhook row.

    Empty ``secret`` keeps the current value (the form deliberately
    masks the existing secret so an operator can rotate it without
    being able to read it back).
    """
    from app.services.settings_svc import get as get_setting
    from app.services.webhooks import get_webhook, update_webhook, validate_webhook_target

    db = get_db()
    existing = get_webhook(db, webhook_id)
    if existing is None:
        flash(_('Webhook not found.'), 'error')
        return redirect(url_for('admin.webhooks'))

    fields = {}
    if 'name' in request.form:
        fields['name'] = get_stripped(request.form, 'name') or existing.name
    if 'url' in request.form:
        url = get_stripped(request.form, 'url')
        if url:
            allow_private = get_setting(
                db, 'webhook_allow_private_targets', 'false'
            ).strip().lower() in {'1', 'true', 'yes', 'on'}
            ok, msg = validate_webhook_target(url, allow_private=allow_private)
            if not ok:
                flash(msg, 'error')
                return redirect(url_for('admin.webhooks'))
            fields['url'] = url
    if 'events' in request.form:
        fields['events'] = _normalise_webhook_events_form(request.form.get('events'))
    if 'enabled' in request.form:
        fields['enabled'] = bool(request.form.get('enabled'))
    new_secret = get_stripped(request.form, 'secret')
    if new_secret:
        fields['secret'] = new_secret
    # Manual reset of the auto-disable counter — handy after fixing a
    # downstream and re-enabling the row.
    if request.form.get('reset_failures'):
        fields['failure_count'] = 0

    update_webhook(db, webhook_id, **fields)
    log_action(
        db,
        action='Updated webhook',
        category='webhooks',
        detail=f'id={webhook_id} fields={",".join(sorted(fields))}',
    )
    flash(_('Webhook updated.'), 'success')
    return redirect(url_for('admin.webhooks'))


@admin_bp.route('/webhooks/<int:webhook_id>/delete', methods=['POST'])
@login_required
def webhooks_delete(webhook_id):
    """Hard-delete a webhook row (cascades its delivery log)."""
    from app.services.webhooks import delete_webhook, get_webhook

    db = get_db()
    existing = get_webhook(db, webhook_id)
    if existing is None:
        flash(_('Webhook not found.'), 'error')
        return redirect(url_for('admin.webhooks'))

    delete_webhook(db, webhook_id)
    log_action(
        db,
        action='Deleted webhook',
        category='webhooks',
        detail=f'id={webhook_id} name={existing.name}',
    )
    flash(_('Webhook deleted.'), 'success')
    return redirect(url_for('admin.webhooks'))


@admin_bp.route('/webhooks/<int:webhook_id>/test', methods=['POST'])
@login_required
def webhooks_test(webhook_id):
    """Fire a synthetic test delivery so operators can verify endpoint wiring.

    Uses :func:`deliver_now` synchronously — the operator sees the
    response inline as a flash message rather than having to refresh
    the deliveries panel after an async fan-out.
    """
    from app.services.webhooks import (
        deliver_now,
        get_webhook,
        increment_failures,
        record_delivery,
        reset_failures,
    )

    db = get_db()
    webhook = get_webhook(db, webhook_id)
    if webhook is None:
        flash(_('Webhook not found.'), 'error')
        return redirect(url_for('admin.webhooks'))

    payload = {
        'test': True,
        'message': 'resume-site test delivery',
        'sent_at': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    result = deliver_now(webhook, 'webhook.test', payload, timeout=5)
    record_delivery(db, result)
    if 200 <= result.status_code < 300:
        reset_failures(db, webhook_id)
        flash(
            _('Test delivery succeeded — HTTP %(status)d in %(ms)d ms.')
            % {'status': result.status_code, 'ms': result.response_time_ms},
            'success',
        )
    else:
        # Read the configured threshold so the auto-disable contract
        # mirrors the bus dispatcher.
        from app.models import get_setting

        try:
            threshold = max(0, int(get_setting(db, 'webhook_failure_threshold', '10') or 10))
        except (TypeError, ValueError):
            threshold = 10
        increment_failures(db, webhook_id, threshold=threshold)
        flash(
            _('Test delivery failed — HTTP %(status)d. %(error)s')
            % {'status': result.status_code, 'error': result.error},
            'error',
        )
    log_action(
        db,
        action='Tested webhook',
        category='webhooks',
        detail=f'id={webhook_id} status={result.status_code}',
    )
    return redirect(url_for('admin.webhooks'))


@admin_bp.route('/webhooks/<int:webhook_id>/deliveries')
@login_required
def webhooks_deliveries(webhook_id):
    """Per-webhook delivery log — last 100 attempts in newest-first order."""
    from app.services.webhooks import get_webhook, list_recent_deliveries

    db = get_db()
    webhook = get_webhook(db, webhook_id)
    if webhook is None:
        flash(_('Webhook not found.'), 'error')
        return redirect(url_for('admin.webhooks'))
    deliveries = list_recent_deliveries(db, webhook_id=webhook_id, limit=100)
    return render_template(
        'admin/webhook_deliveries.html',
        webhook=webhook,
        deliveries=deliveries,
    )


# ============================================================
# SETTINGS (all site-wide toggles and configuration)
# ============================================================


# ============================================================
# CONTENT TRANSLATIONS (Phase 15.3)
# ============================================================

_TRANSLATABLE_TABLES = {
    'content_blocks': 'Content Block',
    'blog_posts': 'Blog Post',
    'services': 'Service',
    'stats': 'Stat',
    'projects': 'Project',
    'certifications': 'Certification',
}


@admin_bp.route('/translations/<table>/<int:item_id>', methods=['GET', 'POST'])
@login_required
def translations(table, item_id):
    """View and save translations for a content item (Phase 15.3)."""
    if table not in _TRANSLATABLE_TABLES:
        abort(404)

    db = get_db()
    from app.services.settings_svc import get_all_cached
    from app.services.translations import (
        get_available_translations,
        get_translated,
        save_translation,
    )

    settings = get_all_cached(db, current_app.config['DATABASE_PATH'])
    available_locales = [
        loc.strip() for loc in settings.get('available_locales', 'en').split(',') if loc.strip()
    ]

    original = get_translated(db, table, item_id, 'en')
    if not original:
        abort(404)

    if request.method == 'POST':
        locale = get_stripped(request.form, 'locale')
        if locale and locale in available_locales:
            from app.services.translations import _TRANSLATION_TABLES

            config = _TRANSLATION_TABLES.get(table, {})
            fields = {}
            for field in config.get('fields', ()):
                val = get_stripped(request.form, field)
                if val:
                    fields[field] = val
            if fields:
                save_translation(db, table, item_id, locale, **fields)
                db.commit()
                flash(_('Translation saved.'), 'success')
        return redirect(url_for('admin.translations', table=table, item_id=item_id))

    existing_locales = get_available_translations(db, table, item_id)
    locale_translations = {}
    for loc in available_locales:
        if loc == 'en':
            continue
        trans = get_translated(db, table, item_id, loc, fallback_locale='')
        locale_translations[loc] = trans

    from app.services.translations import _TRANSLATION_TABLES

    fields = _TRANSLATION_TABLES.get(table, {}).get('fields', ())

    return render_template(
        'admin/translations.html',
        table=table,
        table_label=_TRANSLATABLE_TABLES[table],
        item_id=item_id,
        original=original,
        available_locales=[loc for loc in available_locales if loc != 'en'],
        existing_locales=existing_locales,
        locale_translations=locale_translations,
        fields=fields,
    )


_THEME_PRESETS = {
    'default': {'accent': '#0071e3', 'label': 'Default Blue'},
    'ocean': {'accent': '#00897B', 'label': 'Ocean Teal'},
    'forest': {'accent': '#2E7D32', 'label': 'Forest Green'},
    'sunset': {'accent': '#E65100', 'label': 'Warm Sunset'},
    'minimal': {'accent': '#616161', 'label': 'Minimal Gray'},
    'royal': {'accent': '#6200EA', 'label': 'Royal Purple'},
    'coral': {'accent': '#FF6B6B', 'label': 'Coral Pink'},
    'amber': {'accent': '#FF8F00', 'label': 'Amber Gold'},
    'indigo': {'accent': '#3F51B5', 'label': 'Indigo'},
    'teal': {'accent': '#009688', 'label': 'Teal'},
    'crimson': {'accent': '#D32F2F', 'label': 'Crimson Red'},
    'slate': {'accent': '#455A64', 'label': 'Slate Blue-Gray'},
}

_DANGEROUS_CSS_PATTERNS = [
    '@import',
    'expression(',
    '-moz-binding',
    'javascript:',
    'behavior:',
]


def _sanitize_custom_css(css):
    """Strip dangerous patterns from custom CSS."""
    import re

    for pattern in _DANGEROUS_CSS_PATTERNS:
        css = re.sub(re.escape(pattern), '', css, flags=re.IGNORECASE)
    css = re.sub(r'url\s*\(\s*["\']?(?!https?://)', 'url(/* blocked */', css, flags=re.IGNORECASE)
    return css


@admin_bp.route('/theme', methods=['GET', 'POST'])
@login_required
def theme():
    """Visual theme editor with live preview (Phase 14.6)."""
    db = get_db()

    if request.method == 'POST':
        from app.services.settings_svc import set_one

        data = request.form
        set_one(db, 'accent_color', data.get('accent_color', '#0071e3'))
        set_one(db, 'color_preset', data.get('color_preset', 'default'))
        set_one(db, 'font_pairing', data.get('font_pairing', 'inter'))
        custom_css = _sanitize_custom_css(data.get('custom_css', ''))
        set_one(db, 'custom_css', custom_css)
        flash(_('Theme saved.'), 'success')
        return redirect(url_for('admin.theme'))

    from app.services.settings_svc import get_all_cached

    settings = get_all_cached(db, current_app.config['DATABASE_PATH'])
    return render_template(
        'admin/theme.html',
        current_settings=settings,
        presets=_THEME_PRESETS,
    )


def _validate_json_list_of_strings(raw, field_name, max_len=20):
    """Phase 23.6 (#25) — validate ``raw`` is a JSON array of strings.

    Used for ``nav_order`` in the settings form. Empty is handled by
    the caller (treated as "use defaults"), so this function is only
    called on non-empty input.
    """
    import json as _json

    try:
        parsed = _json.loads(raw)
    except ValueError as exc:
        return False, f'{field_name} is not valid JSON: {exc}'
    if not isinstance(parsed, list):
        return False, f'{field_name} must be a JSON array, got {type(parsed).__name__}'
    if len(parsed) > max_len:
        return False, f'{field_name} has {len(parsed)} entries; max {max_len}'
    for i, entry in enumerate(parsed):
        if not isinstance(entry, str):
            return False, f'{field_name}[{i}] must be a string, got {type(entry).__name__}'
        if len(entry) > 64:
            return False, f'{field_name}[{i}] too long (>64 chars)'
    return True, ''


def _validate_homepage_layout(raw, max_len=20):
    """Phase 23.6 (#25) — validate ``homepage_layout`` is a JSON array
    of ``{section: str, visible: bool}`` dicts.
    """
    import json as _json

    try:
        parsed = _json.loads(raw)
    except ValueError as exc:
        return False, f'homepage_layout is not valid JSON: {exc}'
    if not isinstance(parsed, list):
        return False, 'homepage_layout must be a JSON array'
    if len(parsed) > max_len:
        return False, f'homepage_layout has {len(parsed)} entries; max {max_len}'
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            return False, f'homepage_layout[{i}] must be an object'
        section = entry.get('section')
        if not isinstance(section, str) or not section:
            return False, f'homepage_layout[{i}].section must be a non-empty string'
        if 'visible' in entry and not isinstance(entry['visible'], bool):
            return False, f'homepage_layout[{i}].visible must be true or false'
    return True, ''


def _validate_custom_nav_links(raw):
    """Parse + validate the ``custom_nav_links`` JSON field (Phase 22.2 #17).

    Returns ``(ok, cleaned_json, error_message)``:

    * ``ok=True``  — the JSON parses, every entry's ``url`` matches
      :func:`app.services.content.validate_safe_url`, and the result
      has at most 10 entries. ``cleaned_json`` is the serialised form
      (with stripped whitespace / reordered keys) that should be
      written to the settings table.
    * ``ok=False`` — JSON is malformed or at least one entry carries
      an unsafe URL. ``error_message`` is the user-visible flash.

    Empty string is treated as "no custom links" and accepted — the
    default-locale nav shows the built-in items.
    """
    import json as _json

    from app.services.content import validate_safe_url

    if raw is None:
        return True, '', ''
    text = (raw or '').strip()
    if not text:
        return True, '', ''
    try:
        parsed = _json.loads(text)
    except ValueError as exc:
        return False, text, f'custom_nav_links is not valid JSON: {exc}'
    if not isinstance(parsed, list):
        return False, text, 'custom_nav_links must be a JSON array.'
    if len(parsed) > 10:
        return False, text, 'custom_nav_links accepts at most 10 entries.'
    cleaned = []
    for idx, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            return False, text, f'custom_nav_links entry #{idx + 1} is not an object.'
        url = str(entry.get('url', '')).strip()
        if not validate_safe_url(url):
            return (
                False,
                text,
                (
                    f'custom_nav_links entry #{idx + 1} has an unsafe URL '
                    f'({url!r}). Only http(s):// , mailto: , and /-relative '
                    f'paths are accepted — scheme-relative // and '
                    f'javascript: / data: / vbscript: are rejected.'
                ),
            )
        label = str(entry.get('label', '')).strip()[:120]
        new_tab = bool(entry.get('new_tab'))
        cleaned.append({'label': label, 'url': url, 'new_tab': new_tab})
    return True, _json.dumps(cleaned), ''


@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Display and save site-wide settings."""
    db = get_db()

    if request.method == 'POST':
        # Phase 22.2 (#17) — validate custom_nav_links BEFORE the
        # generic save_settings call so a bad entry is rejected with a
        # 400 and a user-visible error, not silently written into the
        # DB. A valid payload is re-serialised canonically so later
        # read paths see the cleaned-up shape.
        nav_raw = request.form.get('custom_nav_links')
        if nav_raw is not None:
            ok, cleaned, err = _validate_custom_nav_links(nav_raw)
            if not ok:
                flash(err, 'error')
                grouped = get_grouped_settings(db)
                all_settings = get_all_settings_svc(db)
                return (
                    render_template(
                        'admin/settings.html',
                        settings=all_settings,
                        grouped=grouped,
                    ),
                    400,
                )
            form_data = request.form.copy()
            form_data.setlist('custom_nav_links', [cleaned])
        else:
            form_data = request.form

        # Phase 23.6 (#25) — validate the JSON layout fields BEFORE
        # saving so a malformed value doesn't silently land in the DB
        # (where the template-side `contextlib.suppress` would eat the
        # error and render the defaults, masking the bug). Accepts
        # empty / unset as "use defaults".
        nav_order_raw = form_data.get('nav_order')
        if nav_order_raw:
            ok, err = _validate_json_list_of_strings(nav_order_raw, 'nav_order', max_len=20)
            if not ok:
                flash(err, 'error')
                grouped = get_grouped_settings(db)
                all_settings = get_all_settings_svc(db)
                return render_template(
                    'admin/settings.html', settings=all_settings, grouped=grouped
                ), 400

        homepage_raw = form_data.get('homepage_layout')
        if homepage_raw:
            ok, err = _validate_homepage_layout(homepage_raw)
            if not ok:
                flash(err, 'error')
                grouped = get_grouped_settings(db)
                all_settings = get_all_settings_svc(db)
                return render_template(
                    'admin/settings.html', settings=all_settings, grouped=grouped
                ), 400

        save_settings(db, form_data)
        with contextlib.suppress(Exception):
            log_action(db, 'Updated settings', 'settings')

        # Phase 19.1 event bus — fire `settings.changed` with the sorted
        # list of submitted form keys (excluding csrf_token). Mirrors
        # the API-side emission in app.routes.api.admin_settings_update;
        # subscribers see one event per save, payload size bounded by
        # the registry size (~30 keys).
        from app.events import Events as _Events
        from app.events import emit as _emit

        _emit(
            _Events.SETTINGS_CHANGED,
            keys=sorted(k for k in request.form if k != 'csrf_token'),
            source='admin_ui',
        )

        flash(_('Settings saved.'), 'success')
        return redirect(url_for('admin.settings'))

    grouped = get_grouped_settings(db)
    all_settings = get_all_settings_svc(db)
    return render_template('admin/settings.html', settings=all_settings, grouped=grouped)


# ============================================================
# SERVICES MANAGER (CRUD)
# ============================================================


# ============================================================
# GENERIC REORDER (Phase 14.1 — Drag-and-Drop)
# ============================================================

_REORDER_ALLOWLIST = {
    'services': ('services', 'id'),
    'stats': ('stats', 'id'),
    'photos': ('photos', 'id'),
    'projects': ('projects', 'id'),
}


@admin_bp.route('/reorder', methods=['POST'])
@login_required
def reorder():
    """Update sort_order for items in a table based on drag-and-drop order.

    Expects JSON body: {"table": "<name>", "id_order": [1, 3, 2, ...]}
    Table name is validated against an allowlist to prevent SQL injection.
    """
    data = request.get_json(silent=True) or {}
    table_key = data.get('table', '')
    id_order = data.get('id_order', [])

    if table_key not in _REORDER_ALLOWLIST:
        return jsonify({'error': 'Invalid table'}), 400

    if not isinstance(id_order, list) or not all(isinstance(i, int) for i in id_order):
        return jsonify({'error': 'id_order must be a list of integers'}), 400

    table_name, id_col = _REORDER_ALLOWLIST[table_key]
    db = get_db()
    for position, item_id in enumerate(id_order):
        db.execute(
            f'UPDATE {table_name} SET sort_order = ? WHERE {id_col} = ?',  # noqa: S608  # nosec B608 — table/col from allowlist, not user input
            (position, item_id),
        )
    db.commit()

    from app.services.activity_log import log_activity

    log_activity(db, f'Reordered {table_key}', category=table_key, detail=f'{len(id_order)} items')

    return jsonify({'ok': True})


_SEARCH_TYPE_URLS = {
    'content_block': 'admin.content_edit',
    'blog_post': 'admin.blog_edit',
    'review': 'admin.reviews',
    'photo': 'admin.photos',
    'service': 'admin.services',
}


#: Phase 22.2 (#44) — unique sentinels fed to SQLite's snippet() so we can
#: tell the FTS-supplied highlight marks apart from attacker-controlled
#: ``<mark>`` bytes that might live inside the indexed content. The
#: sentinels contain characters that can never appear in either HTML or
#: an attacker's search hit (the `^` + random token bytes are chosen so
#: no legitimate text contains them).
_SEARCH_SNIPPET_START = '\x02mark_start_2cc2\x02'
_SEARCH_SNIPPET_END = '\x02mark_end_2cc2\x02'


def _render_search_snippet(raw_snippet):
    """Escape a FTS5 snippet and then re-inject the highlight marks.

    SQLite's ``snippet()`` returns a string that interleaves attacker-
    controlled text (indexed review / blog body) with our sentinel
    delimiters. Passing that verbatim through ``| safe`` was the
    audit-called-out XSS (#44): a review containing ``<script>...``
    would render as live script. Autoescape alone would defang the
    XSS but also destroy the highlight markup.

    Strategy: escape the whole string (defangs everything attacker-
    controlled), then replace the sentinels with real ``<mark>`` /
    ``</mark>`` markup, and wrap in ``Markup`` so Jinja renders the
    final HTML without re-escaping.
    """
    from markupsafe import Markup, escape

    escaped = str(escape(raw_snippet))
    escaped = escaped.replace(escape(_SEARCH_SNIPPET_START), '<mark>')
    escaped = escaped.replace(escape(_SEARCH_SNIPPET_END), '</mark>')
    # Attacker-controlled bytes were already escaped above; the only
    # unescaped HTML substrings are the server-controlled ``<mark>``
    # markers. Marking the result safe is therefore the intentional
    # result — the alternative is losing highlight markup entirely.
    return Markup(escaped)  # noqa: S704  # nosec B704 — pre-escaped, delimiters server-constant


@admin_bp.route('/search')
@login_required
def search():
    """Full-text search across all admin content types (Phase 14.5)."""
    q = request.args.get('q', '').strip()
    results = []
    if q:
        db = get_db()
        try:
            rows = db.execute(
                'SELECT content_type, content_id, title, '
                'snippet(search_index, 3, ?, ?, "…", 32) AS snippet '
                'FROM search_index WHERE search_index MATCH ? ORDER BY rank LIMIT 50',
                (_SEARCH_SNIPPET_START, _SEARCH_SNIPPET_END, q),
            ).fetchall()
            for row in rows:
                results.append(
                    {
                        'type': row['content_type'],
                        'id': row['content_id'],
                        'title': row['title'],
                        'snippet': _render_search_snippet(row['snippet']),
                    }
                )
        except Exception:  # noqa: BLE001, S110 — FTS5 table may not exist yet
            pass
    return render_template(
        'admin/search.html', query=q, results=results, type_urls=_SEARCH_TYPE_URLS
    )


def _in_clause(ids):
    """Build a safe IN clause with ? placeholders from a validated int list."""
    return ','.join('?' * len(ids))


def _bulk_exec(db, sql_template, ids, extra_params=None):
    """Execute a bulk SQL statement with safe IN clause. Table/column names are
    hardcoded in the templates below — only the id count is dynamic."""
    placeholders = _in_clause(ids)
    sql = sql_template.format(placeholders=placeholders)  # noqa: S608 — template is hardcoded, not user input
    params = list(extra_params or []) + list(ids)
    db.execute(sql, params)


_BULK_ACTIONS = {
    'photos': {
        'delete': lambda db, ids: _bulk_delete_photos(db, ids),
        'set_tier': lambda db, ids, tier='grid': _bulk_set_photo_tier(db, ids, tier),
    },
    'reviews': {
        'approve': lambda db, ids: _bulk_exec(
            db, "UPDATE reviews SET status='approved' WHERE id IN ({placeholders})", ids
        ),
        'reject': lambda db, ids: _bulk_exec(
            db, "UPDATE reviews SET status='rejected' WHERE id IN ({placeholders})", ids
        ),
        'delete': lambda db, ids: _bulk_exec(
            db, 'DELETE FROM reviews WHERE id IN ({placeholders})', ids
        ),
    },
    'blog_posts': {
        'publish': lambda db, ids: _bulk_exec(
            db,
            "UPDATE blog_posts SET status='published', published_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id IN ({placeholders})",
            ids,
        ),
        'unpublish': lambda db, ids: _bulk_exec(
            db, "UPDATE blog_posts SET status='draft' WHERE id IN ({placeholders})", ids
        ),
        'delete': lambda db, ids: _bulk_exec(
            db, 'DELETE FROM blog_posts WHERE id IN ({placeholders})', ids
        ),
    },
    'contact_submissions': {
        'delete': lambda db, ids: _bulk_exec(
            db, 'DELETE FROM contact_submissions WHERE id IN ({placeholders})', ids
        ),
        'mark_spam': lambda db, ids: _bulk_exec(
            db, 'UPDATE contact_submissions SET is_spam=1 WHERE id IN ({placeholders})', ids
        ),
    },
}


def _bulk_delete_photos(db, ids):
    """Delete photo records and their files from disk."""
    from app.services.photos import delete_photo_file

    placeholders = _in_clause(ids)
    rows = db.execute(
        f'SELECT id, storage_name FROM photos WHERE id IN ({placeholders})',  # noqa: S608  # nosec B608 — placeholders are ? only
        ids,
    ).fetchall()
    for row in rows:
        delete_photo_file(row['storage_name'])
    _bulk_exec(db, 'DELETE FROM photos WHERE id IN ({placeholders})', ids)


def _bulk_set_photo_tier(db, ids, tier):
    """Set display_tier for multiple photos."""
    if tier not in ('featured', 'grid', 'hidden'):
        return
    _bulk_exec(
        db,
        'UPDATE photos SET display_tier=? WHERE id IN ({placeholders})',
        ids,
        extra_params=[tier],
    )


@admin_bp.route('/bulk-action', methods=['POST'])
@login_required
def bulk_action():
    """Execute a bulk action on selected items.

    Expects JSON: {"table": "photos", "action": "delete", "ids": [1,2,3], "params": {...}}
    """
    data = request.get_json(silent=True) or {}
    table = data.get('table', '')
    action = data.get('action', '')
    ids = data.get('ids', [])
    params = data.get('params', {})

    if table not in _BULK_ACTIONS or action not in _BULK_ACTIONS[table]:
        return jsonify({'error': 'Invalid table or action'}), 400

    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids) or not ids:
        return jsonify({'error': 'ids must be a non-empty list of integers'}), 400

    db = get_db()
    handler = _BULK_ACTIONS[table][action]

    import inspect

    sig = inspect.signature(handler)
    if len(sig.parameters) > 2:
        handler(db, ids, **params)
    else:
        handler(db, ids)
    db.commit()

    from app.services.activity_log import log_activity

    log_activity(db, f'Bulk {action} on {table}', category=table, detail=f'{len(ids)} items')

    return jsonify({'ok': True, 'count': len(ids)})


@admin_bp.route('/services')
@login_required
def services():
    """List all services with inline edit forms."""
    db = get_db()
    service_list = get_all_services(db)
    return render_template('admin/services.html', services=service_list)


@admin_bp.route('/services/add', methods=['POST'])
@login_required
def services_add():
    """Add a new service card."""
    db = get_db()
    title = get_stripped(request.form, 'title')
    description = request.form.get('description', '')
    icon = request.form.get('icon', '')
    sort_order = request.form.get('sort_order', '0')

    if title:
        add_service(db, title, description, icon, sort_order)
        flash(_('Service added.'), 'success')
    return redirect(url_for('admin.services'))


@admin_bp.route('/services/<int:service_id>/edit', methods=['POST'])
@login_required
def services_edit(service_id):
    """Update an existing service card."""
    db = get_db()
    title = get_stripped(request.form, 'title')
    description = request.form.get('description', '')
    icon = request.form.get('icon', '')
    sort_order = request.form.get('sort_order', '0')
    visible = bool(request.form.get('visible'))

    update_service(db, service_id, title, description, icon, sort_order, visible)
    flash(_('Service updated.'), 'success')
    return redirect(url_for('admin.services'))


@admin_bp.route('/services/<int:service_id>/delete', methods=['POST'])
@login_required
def services_delete(service_id):
    """Delete a service card."""
    db = get_db()
    delete_service(db, service_id)
    flash(_('Service deleted.'), 'success')
    return redirect(url_for('admin.services'))


# ============================================================
# STATS MANAGER (animated counter CRUD)
# ============================================================


@admin_bp.route('/stats')
@login_required
def stats():
    """List all stat counters with inline edit forms."""
    db = get_db()
    stat_list = get_all_stats(db)
    return render_template('admin/stats.html', stats=stat_list)


@admin_bp.route('/stats/add', methods=['POST'])
@login_required
def stats_add():
    """Add a new animated stat counter for the landing page."""
    db = get_db()
    label = get_stripped(request.form, 'label')
    value = request.form.get('value', '0')
    suffix = request.form.get('suffix', '')
    sort_order = request.form.get('sort_order', '0')

    if label:
        add_stat(db, label, value, suffix, sort_order)
        flash(_('Stat added.'), 'success')
    return redirect(url_for('admin.stats'))


@admin_bp.route('/stats/<int:stat_id>/edit', methods=['POST'])
@login_required
def stats_edit(stat_id):
    """Update an existing stat counter."""
    db = get_db()
    label = get_stripped(request.form, 'label')
    value = request.form.get('value', '0')
    suffix = request.form.get('suffix', '')
    sort_order = request.form.get('sort_order', '0')
    visible = bool(request.form.get('visible'))

    update_stat(db, stat_id, label, value, suffix, sort_order, visible)
    flash(_('Stat updated.'), 'success')
    return redirect(url_for('admin.stats'))


@admin_bp.route('/stats/<int:stat_id>/delete', methods=['POST'])
@login_required
def stats_delete(stat_id):
    """Delete a stat counter."""
    db = get_db()
    delete_stat(db, stat_id)
    flash(_('Stat deleted.'), 'success')
    return redirect(url_for('admin.stats'))
