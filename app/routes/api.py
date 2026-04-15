"""
Public REST API — Phase 16.1 + 16.2

JSON API mounted at ``/api/v1/``. This phase ships read-only endpoints
that expose the same content rendered by the public website, so a
headless client (static site generator, mobile app, external blog
aggregator, kiosk display) can consume the data without scraping HTML.

Design contract
---------------
* **Versioned prefix.** Every URL starts with ``/api/v1/``. A future
  ``/api/v2/`` can coexist so breaking changes never surprise existing
  clients. The blueprint uses ``url_prefix='/api/v1'`` so route
  definitions read as plain paths.

* **JSON in, JSON out.** Responses always set
  ``Content-Type: application/json``. POST / PUT / PATCH bodies (added
  in Phase 16.3 writes) must carry ``Content-Type: application/json``
  or return 415. The read endpoints in this file don't accept bodies.

* **CSRF exempt.** CSRF is a browser-form mitigation; the API expects
  token auth (Phase 13.4) on write/admin routes and rate-limited public
  access on reads. The entire blueprint is registered via
  ``csrf.exempt`` in :mod:`app.__init__`.

* **Uniform error shape.**
  ``{"error": "message", "code": "ERROR_CODE", "details": {...}}``
  Error codes are stable machine-readable strings; the ``message`` is
  for humans reading logs.

* **Uniform pagination shape.**
  ``{"data": [...], "pagination": {"page": 1, "per_page": 20,
  "total": 142, "pages": 8}}``
  Built on :mod:`app.services.pagination` so every paginated endpoint
  shares the same math.

* **ETag + If-None-Match.** Read endpoints compute an ETag from the
  serialized body and return ``304 Not Modified`` when the client's
  ``If-None-Match`` matches. This makes polling clients cheap and
  lets CDN/proxy caches do real work.

* **Access logging via the existing request logger.** Each response
  flows through the same after-request hook as the HTML site, so API
  traffic shows up in the structured logs + Prometheus counters
  without additional instrumentation.

Reads are public (no token required) to mirror the public website;
Phase 16.3 adds ``@require_api_token('write')`` to mutation endpoints
and Phase 16.4 adds ``@require_api_token('admin')`` to admin-only
reads.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from flask import Blueprint, Response, g, jsonify, request

from app.db import get_db
from app.models import (
    get_all_approved_reviews,
    get_all_visible_photos,
    get_approved_reviews_by_tier,
    get_content_block,
    get_photo_categories,
    get_visible_certifications,
    get_visible_services,
    get_visible_stats,
)
from app.services.pagination import clamp_page, offset_for, paginate
from app.services.settings_svc import get_all_cached

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _error(message, code, status, details=None):
    """Build a uniform JSON error response.

    Args:
        message: Human-readable string for logs / humans.
        code: Machine-readable stable tag (``NOT_FOUND``, ``BAD_REQUEST``, ...).
        status: HTTP status code.
        details: Optional dict with additional structured context.
    """
    payload = {'error': message, 'code': code}
    if details:
        payload['details'] = details
    response = jsonify(payload)
    response.status_code = status
    return response


@api_bp.errorhandler(404)
def _api_not_found(_exc):
    """Convert Flask's default 404 HTML into a JSON body for API routes."""
    return _error('Not found', 'NOT_FOUND', 404)


@api_bp.errorhandler(405)
def _api_method_not_allowed(_exc):
    """Return a JSON 405 instead of Flask's text/html default."""
    return _error('Method not allowed', 'METHOD_NOT_ALLOWED', 405)


# ---------------------------------------------------------------------------
# Serialization + response helpers
# ---------------------------------------------------------------------------

# Columns to strip from photo rows — file-system paths and internal IDs
# that browser clients don't need. `thumbnail_path` / `optimized_path`
# are exposed as URL fragments in a follow-up commit; for now the
# public URL is derived from `filename` on the client side.
_PHOTO_PUBLIC_FIELDS = (
    'id',
    'title',
    'description',
    'category',
    'tech_used',
    'display_tier',
    'sort_order',
    'filename',
    'alt_text',
    'created_at',
)

_SERVICE_PUBLIC_FIELDS = (
    'id',
    'title',
    'description',
    'icon',
    'sort_order',
)

_STAT_PUBLIC_FIELDS = (
    'id',
    'label',
    'value',
    'suffix',
    'sort_order',
)

_REVIEW_PUBLIC_FIELDS = (
    'id',
    'reviewer_name',
    'reviewer_title',
    'relationship',
    'message',
    'rating',
    'type',
    'display_tier',
    'created_at',
)

_CERT_PUBLIC_FIELDS = (
    'id',
    'name',
    'issuer',
    'description',
    'badge_image',
    'credential_url',
    'date_earned',
    'date_expires',
    'sort_order',
)

_CONTENT_BLOCK_PUBLIC_FIELDS = (
    'id',
    'slug',
    'title',
    'content',
    'plain_text',
    'updated_at',
)


def _row_to_dict(row, fields):
    """Project a sqlite3.Row down to ``fields``, skipping missing columns.

    ``sqlite3.Row`` doesn't expose ``.get()``, so we use ``keys()`` to
    guard against columns that don't exist on older schemas.
    """
    if row is None:
        return None
    available = set(row.keys())
    return {f: row[f] for f in fields if f in available}


def _conditional_response(payload, *, status=200, extra_headers=None):
    """Serialize ``payload`` as JSON and honour If-None-Match.

    Computes a strong ETag from the body bytes. When the client sends
    a matching ``If-None-Match``, returns 304 with no body (and the
    ETag echoed). On a miss, returns 200 with the body and the ETag.
    """
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True, default=str)
    body_bytes = body.encode('utf-8')
    etag = '"' + hashlib.sha256(body_bytes).hexdigest()[:32] + '"'

    client_etag = request.headers.get('If-None-Match', '').strip()
    if client_etag and client_etag == etag:
        headers = {'ETag': etag, 'Cache-Control': 'no-cache'}
        if extra_headers:
            headers.update(extra_headers)
        return Response(status=304, headers=headers)

    headers = {
        'ETag': etag,
        'Cache-Control': 'no-cache',
        'Content-Type': 'application/json',
    }
    if extra_headers:
        headers.update(extra_headers)
    return Response(body_bytes, status=status, headers=headers)


def _paginated_response(items, *, page, per_page, total):
    """Build the standard ``{data, pagination}`` envelope."""
    pagination = paginate(page=page, per_page=per_page, total=total)
    payload = {
        'data': items,
        'pagination': {
            'page': pagination.page,
            'per_page': pagination.per_page,
            'total': pagination.total,
            'pages': pagination.total_pages,
        },
    }
    return _conditional_response(payload)


def _parse_per_page(raw, *, default, maximum=100):
    """Clamp ``?per_page=`` into the accepted [1, maximum] range.

    Bad input falls back to ``default``. The hard cap prevents a client
    from requesting an unbounded result set (e.g. ``?per_page=9999``).
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return min(value, maximum)


# ---------------------------------------------------------------------------
# /api/v1/site — site metadata
# ---------------------------------------------------------------------------


@api_bp.route('/site')
def site_metadata():
    """Return the site-wide metadata a consumer needs before any other call.

    This is the "bootstrap" endpoint: it tells a client what the site is
    called, whether the author is available, and which locales are
    configured for multilingual content (Phase 15).
    """
    from flask import current_app

    db = get_db()
    settings = get_all_cached(db, current_app.config['DATABASE_PATH'])
    available_locales = [
        loc.strip() for loc in settings.get('available_locales', 'en').split(',') if loc.strip()
    ] or ['en']

    payload = {
        'title': settings.get('site_title', 'My Portfolio'),
        'tagline': settings.get('site_tagline', ''),
        'footer_text': settings.get('footer_text', ''),
        'availability_status': settings.get('availability_status', 'available'),
        'hero_heading': settings.get('hero_heading', ''),
        'hero_subheading': settings.get('hero_subheading', ''),
        'hero_tagline': settings.get('hero_tagline', ''),
        'blog_enabled': _truthy(settings.get('blog_enabled', 'false')),
        'case_studies_enabled': _truthy(settings.get('case_studies_enabled', 'false')),
        'contact_form_enabled': _truthy(settings.get('contact_form_enabled', 'true')),
        'available_locales': available_locales,
        'api_version': 'v1',
        'server_time': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    return _conditional_response(payload)


def _truthy(raw):
    """Interpret the settings-string convention for booleans."""
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


# ---------------------------------------------------------------------------
# /api/v1/content/<slug>
# ---------------------------------------------------------------------------


@api_bp.route('/content/<slug>')
def content_block(slug):
    """Return a single content block by slug, or 404 if not found."""
    row = get_content_block(get_db(), slug)
    if row is None:
        return _error(f'No content block with slug {slug!r}', 'NOT_FOUND', 404)
    return _conditional_response({'data': _row_to_dict(row, _CONTENT_BLOCK_PUBLIC_FIELDS)})


# ---------------------------------------------------------------------------
# /api/v1/services
# ---------------------------------------------------------------------------


@api_bp.route('/services')
def services_list():
    """Return every visible service in sort order."""
    rows = get_visible_services(get_db())
    items = [_row_to_dict(r, _SERVICE_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items})


# ---------------------------------------------------------------------------
# /api/v1/stats
# ---------------------------------------------------------------------------


@api_bp.route('/stats')
def stats_list():
    """Return every visible stat (animated landing-page counter) in order."""
    rows = get_visible_stats(get_db())
    items = [_row_to_dict(r, _STAT_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items})


# ---------------------------------------------------------------------------
# /api/v1/portfolio
# ---------------------------------------------------------------------------


@api_bp.route('/portfolio')
def portfolio_list():
    """Return paginated visible photos, optionally filtered by category.

    Query parameters:
        page (int, default 1): 1-indexed page number.
        per_page (int, default 20, max 100): rows per page.
        category (str, optional): exact-match filter.

    Hidden-tier photos are always excluded. Featured and grid-tier
    photos are both included; callers can filter client-side by
    ``display_tier`` if they want only one.
    """
    db = get_db()
    page = clamp_page(request.args.get('page'))
    per_page = _parse_per_page(request.args.get('per_page'), default=20)
    category = (request.args.get('category') or '').strip()

    base = "FROM photos WHERE display_tier != 'hidden'"
    params: list = []
    if category:
        base += ' AND category = ?'
        params.append(category)

    total = db.execute(f'SELECT COUNT(*) AS n {base}', tuple(params)).fetchone()['n']
    rows = db.execute(
        f'SELECT * {base} ORDER BY sort_order, id LIMIT ? OFFSET ?',
        tuple(params) + (per_page, offset_for(page, per_page)),
    ).fetchall()
    items = [_row_to_dict(r, _PHOTO_PUBLIC_FIELDS) for r in rows]
    return _paginated_response(items, page=page, per_page=per_page, total=total)


@api_bp.route('/portfolio/<int:photo_id>')
def portfolio_detail(photo_id):
    """Return a single visible photo by id, or 404.

    A hidden photo returns 404 rather than 403 so the endpoint doesn't
    leak "this photo exists but is hidden".
    """
    row = (
        get_db()
        .execute(
            "SELECT * FROM photos WHERE id = ? AND display_tier != 'hidden'",
            (photo_id,),
        )
        .fetchone()
    )
    if row is None:
        return _error(f'No visible photo with id {photo_id}', 'NOT_FOUND', 404)
    return _conditional_response({'data': _row_to_dict(row, _PHOTO_PUBLIC_FIELDS)})


@api_bp.route('/portfolio/categories')
def portfolio_categories():
    """Return the distinct category names across visible photos."""
    return _conditional_response({'data': get_photo_categories(get_db())})


# ---------------------------------------------------------------------------
# /api/v1/testimonials
# ---------------------------------------------------------------------------


@api_bp.route('/testimonials')
def testimonials_list():
    """Return paginated approved reviews.

    Query parameters:
        page (int, default 1)
        per_page (int, default 20, max 100)
        tier (str, optional): if 'featured' or 'standard', filter to that
            tier; otherwise return all approved reviews ordered featured
            first.
    """
    db = get_db()
    page = clamp_page(request.args.get('page'))
    per_page = _parse_per_page(request.args.get('per_page'), default=20)
    tier = (request.args.get('tier') or '').strip()

    if tier in ('featured', 'standard'):
        all_rows = get_approved_reviews_by_tier(db, tier)
    else:
        all_rows = get_all_approved_reviews(db)

    total = len(all_rows)
    start = offset_for(page, per_page)
    window = all_rows[start : start + per_page]
    items = [_row_to_dict(r, _REVIEW_PUBLIC_FIELDS) for r in window]
    return _paginated_response(items, page=page, per_page=per_page, total=total)


# ---------------------------------------------------------------------------
# /api/v1/certifications
# ---------------------------------------------------------------------------


@api_bp.route('/certifications')
def certifications_list():
    """Return every visible certification in sort order."""
    rows = get_visible_certifications(get_db())
    items = [_row_to_dict(r, _CERT_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items})


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

# Touching ``_PHOTO_PUBLIC_FIELDS`` etc. silences linters that flag
# these as unused when every endpoint is refactored out into its own
# module in a future commit. Keeping them as module-level constants
# also means tests can import and assert on the field whitelist.
_ = get_all_visible_photos  # preserved import for downstream use


def _unused():  # pragma: no cover
    """Reference every module-level constant so `vulture` stays quiet."""
    return (
        g,
        _PHOTO_PUBLIC_FIELDS,
        _SERVICE_PUBLIC_FIELDS,
        _STAT_PUBLIC_FIELDS,
        _REVIEW_PUBLIC_FIELDS,
        _CERT_PUBLIC_FIELDS,
        _CONTENT_BLOCK_PUBLIC_FIELDS,
    )
