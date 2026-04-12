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

import ipaddress
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

from app import get_db
from app.models import (
    AdminUser, get_all_settings, get_setting, set_setting,
    get_visible_services, get_visible_projects, get_visible_certifications,
    get_all_approved_reviews,
)

admin_bp = Blueprint('admin', __name__, template_folder='../templates')


# ============================================================
# IP RESTRICTION MIDDLEWARE
# ============================================================

@admin_bp.before_request
def restrict_to_allowed_networks():
    """Block admin access from IPs outside the configured allowed networks.

    Runs before every admin route. Reads the client IP from X-Forwarded-For
    (set by the Caddy reverse proxy) and checks it against the CIDR ranges
    defined in config.yaml's admin.allowed_networks.

    Security notes:
    - Trusts X-Forwarded-For because the app runs behind Caddy (the container
      port is not directly exposed to the internet).
    - Takes the leftmost IP from X-Forwarded-For (the original client).
    - Fails closed: unparseable IPs get 403, malformed network entries are skipped.
    - Uses Python's ipaddress module for CIDR matching (no external dependencies).
    """
    config = current_app.config['SITE_CONFIG']
    allowed = config.get('admin', {}).get('allowed_networks', [])

    # If no networks are configured, allow all (not recommended for production)
    if not allowed:
        return

    # Extract the real client IP from the proxy chain
    client_ip_str = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip_str and ',' in client_ip_str:
        client_ip_str = client_ip_str.split(',')[0].strip()

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


# ============================================================
# AUTHENTICATION
# ============================================================

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle admin login form display and credential validation.

    Validates the username and password against the values stored in
    config.yaml (not in the database). Uses Werkzeug's secure password
    hash comparison to prevent timing attacks.
    """
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        config = current_app.config['SITE_CONFIG']
        admin_config = config.get('admin', {})
        username = request.form.get('username', '')
        password = request.form.get('password', '')

        # Verify credentials against YAML config
        if (
            username == admin_config.get('username', 'admin')
            and admin_config.get('password_hash')
            and check_password_hash(admin_config['password_hash'], password)
        ):
            user = AdminUser(username)
            login_user(user)
            # Redirect to the page they were trying to access, or the dashboard
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin.dashboard'))

        flash('Invalid credentials.', 'error')

    return render_template('admin/login.html')


@admin_bp.route('/logout')
@login_required
def logout():
    """Log out the admin and redirect to the public landing page."""
    logout_user()
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
        "SELECT path, COUNT(*) as cnt FROM page_views GROUP BY path ORDER BY cnt DESC LIMIT 5"
    ).fetchall()

    # Review and contact metrics
    pending_reviews = db.execute(
        "SELECT COUNT(*) as cnt FROM reviews WHERE status = 'pending'"
    ).fetchone()['cnt']
    recent_contacts = db.execute(
        "SELECT * FROM contact_submissions WHERE is_spam = 0 ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    unread_contacts = db.execute(
        "SELECT COUNT(*) as cnt FROM contact_submissions WHERE is_spam = 0 AND read = 0"
    ).fetchone()['cnt']

    return render_template('admin/dashboard.html',
                           total_views=total_views,
                           recent_views=recent_views,
                           popular_pages=popular_pages,
                           pending_reviews=pending_reviews,
                           recent_contacts=recent_contacts,
                           unread_contacts=unread_contacts)


# ============================================================
# CONTENT EDITOR (Quill.js rich text blocks)
# ============================================================

@admin_bp.route('/content')
@login_required
def content():
    """List all content blocks for editing."""
    db = get_db()
    blocks = db.execute('SELECT * FROM content_blocks ORDER BY sort_order').fetchall()
    return render_template('admin/content.html', blocks=blocks)


@admin_bp.route('/content/edit/<slug>', methods=['GET', 'POST'])
@login_required
def content_edit(slug):
    """Edit an existing content block or create one if the slug is new.

    The Quill.js editor on the frontend submits HTML content via a hidden
    input field. The content is stored as-is in the database and rendered
    with Jinja2's |safe filter in templates.
    """
    db = get_db()
    block = db.execute('SELECT * FROM content_blocks WHERE slug = ?', (slug,)).fetchone()

    if request.method == 'POST':
        title = request.form.get('title', '')
        content_html = request.form.get('content', '')
        if block:
            db.execute(
                "UPDATE content_blocks SET title = ?, content = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE slug = ?",
                (title, content_html, slug),
            )
        else:
            db.execute(
                "INSERT INTO content_blocks (slug, title, content) VALUES (?, ?, ?)",
                (slug, title, content_html),
            )
        db.commit()
        flash('Content saved.', 'success')
        return redirect(url_for('admin.content'))

    return render_template('admin/content_edit.html', block=block, slug=slug)


@admin_bp.route('/content/new', methods=['GET', 'POST'])
@login_required
def content_new():
    """Create a new content block with a unique slug identifier.

    Slugs are auto-normalized: lowercased and spaces replaced with underscores.
    Templates reference blocks by slug (e.g., 'about', 'hero_description').
    """
    if request.method == 'POST':
        db = get_db()
        slug = request.form.get('slug', '').strip().lower().replace(' ', '_')
        title = request.form.get('title', '').strip()
        content_html = request.form.get('content', '')
        if slug:
            db.execute(
                "INSERT OR IGNORE INTO content_blocks (slug, title, content) VALUES (?, ?, ?)",
                (slug, title, content_html),
            )
            db.commit()
            flash('Content block created.', 'success')
        return redirect(url_for('admin.content'))
    return render_template('admin/content_edit.html', block=None, slug='')


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
        flash('No file selected.', 'error')
        return redirect(url_for('admin.photos'))

    from app.services.photos import process_upload
    result = process_upload(file)
    if result is None:
        flash('Invalid file type. Allowed: jpg, png, gif, webp.', 'error')
        return redirect(url_for('admin.photos'))

    # Read optional metadata from the upload form
    title = request.form.get('title', '')
    description = request.form.get('description', '')
    category = request.form.get('category', '')
    display_tier = request.form.get('display_tier', 'grid')

    db.execute(
        'INSERT INTO photos (filename, storage_name, mime_type, width, height, file_size, title, description, category, display_tier) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (result['filename'], result['storage_name'], result['mime_type'],
         result['width'], result['height'], result['file_size'],
         title, description, category, display_tier),
    )
    db.commit()
    flash('Photo uploaded successfully.', 'success')
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
        "UPDATE photos SET title=?, description=?, tech_used=?, category=?, display_tier=?, sort_order=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (title, description, tech_used, category, display_tier, int(sort_order), photo_id),
    )
    db.commit()
    flash('Photo updated.', 'success')
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
        flash('Photo deleted.', 'success')
    return redirect(url_for('admin.photos'))


# ============================================================
# REVIEW MANAGER
# ============================================================

@admin_bp.route('/reviews')
@login_required
def reviews():
    """List all reviews grouped by status (pending, approved, rejected)."""
    db = get_db()
    pending = db.execute("SELECT * FROM reviews WHERE status = 'pending' ORDER BY created_at DESC").fetchall()
    approved = db.execute("SELECT * FROM reviews WHERE status = 'approved' ORDER BY created_at DESC").fetchall()
    rejected = db.execute("SELECT * FROM reviews WHERE status = 'rejected' ORDER BY created_at DESC").fetchall()
    return render_template('admin/reviews.html', pending=pending, approved=approved, rejected=rejected)


@admin_bp.route('/reviews/<int:review_id>/update', methods=['POST'])
@login_required
def reviews_update(review_id):
    """Update a review's status or display tier.

    Actions:
    - 'approve': Set status to approved and assign a display tier.
    - 'reject': Set status to rejected.
    - 'update_tier': Change the display tier of an already-approved review.
    """
    db = get_db()
    action = request.form.get('action', '')
    display_tier = request.form.get('display_tier', 'standard')

    if action == 'approve':
        db.execute(
            "UPDATE reviews SET status='approved', display_tier=?, reviewed_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
            (display_tier, review_id),
        )
    elif action == 'reject':
        db.execute(
            "UPDATE reviews SET status='rejected', reviewed_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
            (review_id,),
        )
    elif action == 'update_tier':
        db.execute("UPDATE reviews SET display_tier=? WHERE id=?", (display_tier, review_id))

    db.commit()
    flash('Review updated.', 'success')
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
    name = request.form.get('name', '').strip()
    token_type = request.form.get('type', 'recommendation')
    if token_type not in ('recommendation', 'client_review'):
        token_type = 'recommendation'

    # Generate a 32-byte URL-safe token (43 characters)
    token_string = secrets.token_urlsafe(32)
    db.execute(
        'INSERT INTO review_tokens (token, name, type) VALUES (?, ?, ?)',
        (token_string, name, token_type),
    )
    db.commit()
    flash(f'Token generated for {name or "anonymous"}.', 'success')
    return redirect(url_for('admin.tokens'))


@admin_bp.route('/tokens/<int:token_id>/delete', methods=['POST'])
@login_required
def tokens_delete(token_id):
    """Delete a review token (revokes the invitation)."""
    db = get_db()
    db.execute('DELETE FROM review_tokens WHERE id = ?', (token_id,))
    db.commit()
    flash('Token deleted.', 'success')
    return redirect(url_for('admin.tokens'))


# ============================================================
# SETTINGS (all site-wide toggles and configuration)
# ============================================================

@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Display and save site-wide settings.

    Settings are stored as key-value pairs in the SQLite settings table.
    The form fields map directly to setting keys — each field is saved
    individually using set_setting() (UPSERT semantics).

    Settings categories:
    - Site identity: title, tagline, footer text, accent color
    - Hero section: heading, subheading, tagline, availability status
    - Display: default theme, logo mode, testimonial display mode
    - Contact & social: form toggle, email/phone visibility, social URLs
    - Analytics: data retention period
    """
    db = get_db()

    if request.method == 'POST':
        # List of all setting keys to read from the form
        settings_fields = [
            'site_title', 'site_tagline', 'dark_mode_default',
            'availability_status', 'contact_form_enabled',
            'contact_email_visible', 'contact_phone_visible',
            'contact_github_url', 'contact_linkedin_url',
            'resume_visibility', 'case_studies_enabled',
            'testimonial_display_mode', 'analytics_retention_days',
            'hero_heading', 'hero_subheading', 'hero_tagline',
            'accent_color', 'logo_mode', 'footer_text',
        ]
        for field in settings_fields:
            value = request.form.get(field, '')
            set_setting(db, field, value)

        flash('Settings saved.', 'success')
        return redirect(url_for('admin.settings'))

    all_settings = get_all_settings(db)
    return render_template('admin/settings.html', settings=all_settings)


# ============================================================
# SERVICES MANAGER (CRUD)
# ============================================================

@admin_bp.route('/services')
@login_required
def services():
    """List all services with inline edit forms."""
    db = get_db()
    service_list = db.execute('SELECT * FROM services ORDER BY sort_order').fetchall()
    return render_template('admin/services.html', services=service_list)


@admin_bp.route('/services/add', methods=['POST'])
@login_required
def services_add():
    """Add a new service card."""
    db = get_db()
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '')
    icon = request.form.get('icon', '')
    sort_order = int(request.form.get('sort_order', '0'))

    if title:
        db.execute(
            'INSERT INTO services (title, description, icon, sort_order) VALUES (?, ?, ?, ?)',
            (title, description, icon, sort_order),
        )
        db.commit()
        flash('Service added.', 'success')
    return redirect(url_for('admin.services'))


@admin_bp.route('/services/<int:service_id>/edit', methods=['POST'])
@login_required
def services_edit(service_id):
    """Update an existing service card."""
    db = get_db()
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '')
    icon = request.form.get('icon', '')
    sort_order = int(request.form.get('sort_order', '0'))
    visible = 1 if request.form.get('visible') else 0

    db.execute(
        "UPDATE services SET title=?, description=?, icon=?, sort_order=?, visible=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (title, description, icon, sort_order, visible, service_id),
    )
    db.commit()
    flash('Service updated.', 'success')
    return redirect(url_for('admin.services'))


@admin_bp.route('/services/<int:service_id>/delete', methods=['POST'])
@login_required
def services_delete(service_id):
    """Delete a service card."""
    db = get_db()
    db.execute('DELETE FROM services WHERE id = ?', (service_id,))
    db.commit()
    flash('Service deleted.', 'success')
    return redirect(url_for('admin.services'))


# ============================================================
# STATS MANAGER (animated counter CRUD)
# ============================================================

@admin_bp.route('/stats')
@login_required
def stats():
    """List all stat counters with inline edit forms."""
    db = get_db()
    stat_list = db.execute('SELECT * FROM stats ORDER BY sort_order').fetchall()
    return render_template('admin/stats.html', stats=stat_list)


@admin_bp.route('/stats/add', methods=['POST'])
@login_required
def stats_add():
    """Add a new animated stat counter for the landing page."""
    db = get_db()
    label = request.form.get('label', '').strip()
    value = int(request.form.get('value', '0'))
    suffix = request.form.get('suffix', '')         # e.g., "+", "%", "k"
    sort_order = int(request.form.get('sort_order', '0'))

    if label:
        db.execute(
            'INSERT INTO stats (label, value, suffix, sort_order) VALUES (?, ?, ?, ?)',
            (label, value, suffix, sort_order),
        )
        db.commit()
        flash('Stat added.', 'success')
    return redirect(url_for('admin.stats'))


@admin_bp.route('/stats/<int:stat_id>/edit', methods=['POST'])
@login_required
def stats_edit(stat_id):
    """Update an existing stat counter."""
    db = get_db()
    label = request.form.get('label', '').strip()
    value = int(request.form.get('value', '0'))
    suffix = request.form.get('suffix', '')
    sort_order = int(request.form.get('sort_order', '0'))
    visible = 1 if request.form.get('visible') else 0

    db.execute(
        'UPDATE stats SET label=?, value=?, suffix=?, sort_order=?, visible=? WHERE id=?',
        (label, value, suffix, sort_order, visible, stat_id),
    )
    db.commit()
    flash('Stat updated.', 'success')
    return redirect(url_for('admin.stats'))


@admin_bp.route('/stats/<int:stat_id>/delete', methods=['POST'])
@login_required
def stats_delete(stat_id):
    """Delete a stat counter."""
    db = get_db()
    db.execute('DELETE FROM stats WHERE id = ?', (stat_id,))
    db.commit()
    flash('Stat deleted.', 'success')
    return redirect(url_for('admin.stats'))
