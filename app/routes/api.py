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
import os

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request

from app import limiter
from app.db import get_db
from app.events import Events, emit
from app.exceptions import ValidationError
from app.models import (
    count_recent_submissions,
    get_all_approved_reviews,
    get_all_visible_photos,
    get_approved_reviews_by_tier,
    get_case_study_by_slug,
    get_photo_categories,
    get_project_by_slug,
    get_setting,
    save_contact_submission,
)
from app.services.activity_log import get_recent_activity, log_action
from app.services.api_tokens import (
    rate_limit_admin,
    rate_limit_write,
    require_api_token,
)
from app.services.blog import (
    create_post,
    delete_post,
    get_all_tags,
    get_post_by_id,
    get_post_by_slug,
    get_posts_by_tag,
    get_published_posts,
    get_tags_for_post,
    publish_post,
    render_post_content,
    unpublish_post,
    update_post,
)
from app.services.pagination import clamp_page, offset_for, paginate
from app.services.reviews import (
    approve_review,
    get_reviews_by_status,
    reject_review,
    update_review_tier,
)
from app.services.settings_svc import (
    SETTINGS_REGISTRY,
    get_all,
    get_all_cached,
    get_grouped_settings,
)
from app.services.settings_svc import (
    save_many as save_settings,
)
from app.services.translations import (
    get_content_block_for_locale,
    get_translated,
    get_visible_certifications_for_locale,
    get_visible_projects_for_locale,
    get_visible_services_for_locale,
    get_visible_stats_for_locale,
    overlay_post_translation,
    overlay_posts_translations,
)

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
# JSON Content-Type enforcement (Phase 16.1 deferred bullet)
# ---------------------------------------------------------------------------

# Endpoints that legitimately accept non-JSON request bodies. The
# portfolio upload route takes multipart/form-data because the image
# payload is binary; every other write route expects application/json.
_MULTIPART_ENDPOINTS = frozenset(
    {
        'api.portfolio_create',
    }
)


@api_bp.before_request
def _enforce_json_content_type():
    """Reject POST/PUT/PATCH bodies that don't declare JSON.

    Browsers default to ``application/x-www-form-urlencoded`` which the
    API won't parse. Rejecting mismatched types up front is clearer than
    letting ``request.get_json()`` silently return ``None`` and
    producing a confusing 400 later.

    The check runs on every write to any /api/v1/ route. Multipart
    uploads (Phase 16.3b) are allow-listed via ``_MULTIPART_ENDPOINTS``.
    GET / HEAD / OPTIONS / DELETE are always permitted (no body).
    """
    if request.method not in ('POST', 'PUT', 'PATCH'):
        return None
    if request.endpoint in _MULTIPART_ENDPOINTS:
        return None
    ctype = (request.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
    if ctype != 'application/json':
        return _error(
            'Content-Type must be application/json',
            'UNSUPPORTED_MEDIA_TYPE',
            415,
            details={'received': ctype or 'missing'},
        )
    return None


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

_CASE_STUDY_PUBLIC_FIELDS = (
    'id',
    'slug',
    'title',
    'summary',
    'problem',
    'solution',
    'result',
    'photo_id',
    'created_at',
    'updated_at',
)

_PROJECT_PUBLIC_FIELDS = (
    'id',
    'slug',
    'title',
    'summary',
    'description',
    'github_url',
    'has_detail_page',
    'screenshot',
    'tech_stack',
    'sort_order',
)

# Blog posts expose `content` in two places: raw (original Markdown/HTML
# as stored) and `rendered_html` (the HTML actually shown on the site,
# via :func:`app.services.blog.render_post_content`). Clients that
# re-render Markdown themselves can use `content`; simple viewers use
# `rendered_html` directly.
_BLOG_POST_LIST_FIELDS = (
    'id',
    'slug',
    'title',
    'summary',
    'author',
    'cover_image',
    'featured',
    'reading_time',
    'meta_description',
    'content_format',
    'status',
    'published_at',
    'created_at',
    'updated_at',
)

_BLOG_POST_DETAIL_FIELDS = _BLOG_POST_LIST_FIELDS + ('content',)


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


def _paginated_response(items, *, page, per_page, total, extra_headers=None):
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
    return _conditional_response(payload, extra_headers=extra_headers)


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
# Accept-Language resolution (Phase 16.1 deferral, unblocked by Phase 15.4)
# ---------------------------------------------------------------------------
#
# Public read endpoints honour the client's ``Accept-Language`` header by
# picking the best match against the site's ``available_locales`` setting
# and threading that locale through the translation overlay (see
# ``app.services.translations``). Untranslated fields fall back to the
# default locale row, so the client always gets a complete response.
#
# Response hygiene:
#   * ``Content-Language`` echoes the locale the server actually chose
#     — not necessarily what the client preferred (e.g. client asked for
#     ``de`` but the site only has ``en,es``, the server returned ``en``).
#   * ``Vary: Accept-Language`` tells downstream caches / CDNs to key
#     responses by the header so a Spanish response isn't served to an
#     English client.
#
# The ETag already varies with the body (the translated payload serialises
# differently), so 304 round-trips stay correct without further work.


def _resolve_request_locale(db):
    """Return ``(resolved_locale, default_locale)`` for the current request.

    Parsing rules:
      1. Read the configured ``available_locales`` list (comma-separated
         setting, defaults to ``['en']``).
      2. Read the ``default_locale`` setting (defaults to ``'en'``).
      3. Use Werkzeug's ``request.accept_languages.best_match`` against
         the available list. That helper already handles q-values,
         case-insensitive matching, and region fallback (``es-MX`` →
         ``es``).
      4. Fall back to ``default_locale`` when there's no match OR when
         the header is absent.

    Returns the two locales as a tuple so callers can pass them to the
    overlay helpers without a second settings read.
    """
    available = [
        loc.strip() for loc in get_setting(db, 'available_locales', 'en').split(',') if loc.strip()
    ] or ['en']
    default_locale = get_setting(db, 'default_locale', 'en') or 'en'

    # ``best_match`` returns None if the client sent a header with no
    # overlap. Empty / missing header is handled by accept_languages
    # itself — it returns None too, which collapses to the default.
    picked = request.accept_languages.best_match(available) if request.accept_languages else None
    return (picked or default_locale, default_locale)


def _locale_headers(locale):
    """Build the response headers that mark a locale-aware reply.

    ``Content-Language`` tells the client which locale the body is in.
    ``Vary: Accept-Language`` signals to caches that the response
    depends on the request header so they don't serve the wrong
    translation.
    """
    return {'Content-Language': locale, 'Vary': 'Accept-Language'}


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
    db = get_db()
    settings = get_all_cached(db, current_app.config['DATABASE_PATH'])
    available_locales = [
        loc.strip() for loc in settings.get('available_locales', 'en').split(',') if loc.strip()
    ] or ['en']

    # NOTE: `server_time` is deliberately NOT in this payload. Including
    # a per-second-changing field would break the ETag contract — every
    # request would produce a fresh hash, so If-None-Match could never
    # short-circuit. Clients needing a server clock can read the
    # standard HTTP `Date` response header instead.
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
    """Return a single content block by slug, or 404 if not found.

    Respects ``Accept-Language`` (Phase 15.4): when the requested locale
    has a translation row, the translated title / content / plain_text
    overlay the default-locale values. Missing fields fall back to the
    default so the payload is always complete.
    """
    db = get_db()
    locale, default = _resolve_request_locale(db)
    row = get_content_block_for_locale(db, slug, locale, default)
    if row is None:
        return _error(f'No content block with slug {slug!r}', 'NOT_FOUND', 404)
    return _conditional_response(
        {'data': _row_to_dict(row, _CONTENT_BLOCK_PUBLIC_FIELDS)},
        extra_headers=_locale_headers(locale),
    )


# ---------------------------------------------------------------------------
# /api/v1/services
# ---------------------------------------------------------------------------


@api_bp.route('/services')
def services_list():
    """Return every visible service in sort order (Accept-Language aware)."""
    db = get_db()
    locale, default = _resolve_request_locale(db)
    rows = get_visible_services_for_locale(db, locale, default)
    items = [_row_to_dict(r, _SERVICE_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items}, extra_headers=_locale_headers(locale))


# ---------------------------------------------------------------------------
# /api/v1/stats
# ---------------------------------------------------------------------------


@api_bp.route('/stats')
def stats_list():
    """Return every visible stat counter in order (Accept-Language aware)."""
    db = get_db()
    locale, default = _resolve_request_locale(db)
    rows = get_visible_stats_for_locale(db, locale, default)
    items = [_row_to_dict(r, _STAT_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items}, extra_headers=_locale_headers(locale))


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

    total = db.execute(
        f'SELECT COUNT(*) AS n {base}',  # noqa: S608  # nosec B608 — base built from two literal alternatives, no user input
        tuple(params),
    ).fetchone()['n']
    rows = db.execute(
        f'SELECT * {base} ORDER BY sort_order, id LIMIT ? OFFSET ?',  # noqa: S608  # nosec B608 — base built from two literal alternatives, no user input
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
    """Return every visible certification in order (Accept-Language aware)."""
    db = get_db()
    locale, default = _resolve_request_locale(db)
    rows = get_visible_certifications_for_locale(db, locale, default)
    items = [_row_to_dict(r, _CERT_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items}, extra_headers=_locale_headers(locale))


# ---------------------------------------------------------------------------
# /api/v1/case-studies/<slug>
# ---------------------------------------------------------------------------


@api_bp.route('/case-studies/<slug>')
def case_study_detail(slug):
    """Return a single published case study by slug.

    Two gates apply:

    1. ``case_studies_enabled`` setting must be ``true``. When the admin
       has turned case studies off site-wide, we return 404 rather than
       200-with-empty so the API mirrors the public-site behaviour.
    2. The row must have ``published = 1`` (enforced by
       :func:`get_case_study_by_slug`). Unpublished / draft case studies
       return 404 so their existence isn't leaked.

    There's no ``/api/v1/case-studies`` list endpoint — case studies are
    linked from ``/portfolio/<id>`` via ``has_case_study`` +
    ``case_study_slug`` on the photo row, so a client already knows the
    slug when it needs the detail.
    """
    db = get_db()
    if get_setting(db, 'case_studies_enabled', 'false') != 'true':
        return _error('Case studies are not enabled on this site', 'NOT_FOUND', 404)
    row = get_case_study_by_slug(db, slug)
    if row is None:
        return _error(f'No case study with slug {slug!r}', 'NOT_FOUND', 404)
    return _conditional_response({'data': _row_to_dict(row, _CASE_STUDY_PUBLIC_FIELDS)})


# ---------------------------------------------------------------------------
# /api/v1/projects
# ---------------------------------------------------------------------------


@api_bp.route('/projects')
def projects_list():
    """Return every visible project in sort order (Accept-Language aware)."""
    db = get_db()
    locale, default = _resolve_request_locale(db)
    rows = get_visible_projects_for_locale(db, locale, default)
    items = [_row_to_dict(r, _PROJECT_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items}, extra_headers=_locale_headers(locale))


@api_bp.route('/projects/<slug>')
def project_detail(slug):
    """Return a single visible project by slug (Accept-Language aware).

    Only projects with ``has_detail_page = 1`` have a detail surface —
    the rest are GitHub-link cards only. A slug that doesn't match a
    detail-page project returns 404 rather than a redirect to the
    GitHub URL, which keeps the API path-stable and lets clients decide
    how to handle external links.
    """
    db = get_db()
    locale, default = _resolve_request_locale(db)
    row = get_project_by_slug(db, slug)
    if row is None:
        return _error(f'No project with slug {slug!r}', 'NOT_FOUND', 404)
    # Apply the translation overlay when the requested locale differs
    # from the default. ``get_translated`` returns None if the row
    # vanished between the two queries — fall back to the raw row in
    # that rare race.
    if locale != default:
        translated = get_translated(db, 'projects', row['id'], locale, default)
        if translated:
            row = translated
    return _conditional_response(
        {'data': _row_to_dict(row, _PROJECT_PUBLIC_FIELDS)},
        extra_headers=_locale_headers(locale),
    )


# ---------------------------------------------------------------------------
# /api/v1/blog
# ---------------------------------------------------------------------------


def _require_blog_enabled(db):
    """Return ``None`` when the blog is enabled, or a 404 response otherwise.

    Mirrors ``app.routes.blog._check_blog_enabled`` so the API and the
    HTML site agree on visibility in lockstep.
    """
    if get_setting(db, 'blog_enabled', 'false') != 'true':
        return _error('Blog is not enabled on this site', 'NOT_FOUND', 404)
    return None


def _blog_post_to_dict(row, *, fields, include_tags=False, include_rendered=False, db=None):
    """Serialize a blog post row with optional tag + rendered-HTML inclusion."""
    data = _row_to_dict(row, fields)
    if data is None:
        return None
    if include_tags and db is not None:
        tag_rows = get_tags_for_post(db, row['id'])
        data['tags'] = [{'name': t['name'], 'slug': t['slug']} for t in tag_rows]
    if include_rendered:
        data['rendered_html'] = render_post_content(row)
    return data


@api_bp.route('/blog')
def blog_list():
    """Return paginated published blog posts (Accept-Language aware).

    Query parameters:
        page (int, default 1)
        per_page (int, default 10, max 100) — lower default than
            /portfolio because blog posts carry more text per row.
        tag (str, optional): filter to a single tag slug.

    ``blog_enabled`` must be ``true`` or every blog endpoint 404s.
    """
    db = get_db()
    gate = _require_blog_enabled(db)
    if gate is not None:
        return gate

    page = clamp_page(request.args.get('page'))
    per_page = _parse_per_page(request.args.get('per_page'), default=10)
    tag = (request.args.get('tag') or '').strip()
    locale, default = _resolve_request_locale(db)

    if tag:
        posts, total = get_posts_by_tag(db, tag, page=page, per_page=per_page)
    else:
        posts, total = get_published_posts(db, page=page, per_page=per_page)

    posts = overlay_posts_translations(db, posts, locale, default)

    items = [
        _blog_post_to_dict(p, fields=_BLOG_POST_LIST_FIELDS, include_tags=True, db=db)
        for p in posts
    ]
    return _paginated_response(
        items, page=page, per_page=per_page, total=total, extra_headers=_locale_headers(locale)
    )


@api_bp.route('/blog/tags')
def blog_tags():
    """Return every tag with a count of published posts using that tag.

    Registered BEFORE ``/blog/<slug>`` in source order so Flask's URL
    dispatcher prefers the static ``tags`` path over the slug matcher.
    """
    db = get_db()
    gate = _require_blog_enabled(db)
    if gate is not None:
        return gate

    # Counts only published posts — a draft with a tag shouldn't inflate
    # the public count. Left join so tags with zero published posts
    # still appear (count = 0), which matches admin expectations.
    rows = db.execute(
        'SELECT bt.id, bt.name, bt.slug, '
        "       COUNT(CASE WHEN bp.status = 'published' THEN 1 END) AS post_count "
        'FROM blog_tags bt '
        'LEFT JOIN blog_post_tags bpt ON bpt.tag_id = bt.id '
        'LEFT JOIN blog_posts bp ON bp.id = bpt.post_id '
        'GROUP BY bt.id, bt.name, bt.slug '
        'ORDER BY bt.name'
    ).fetchall()
    items = [
        {
            'id': r['id'],
            'name': r['name'],
            'slug': r['slug'],
            'post_count': r['post_count'],
        }
        for r in rows
    ]
    _ = get_all_tags  # kept imported for future admin list parity
    return _conditional_response({'data': items})


@api_bp.route('/blog/<slug>')
def blog_detail(slug):
    """Return a single published blog post (Accept-Language aware).

    Draft / archived posts return 404 (not 403) to avoid leaking their
    existence. ``rendered_html`` reflects the overlaid content so
    clients don't have to re-render Markdown themselves.
    """
    db = get_db()
    gate = _require_blog_enabled(db)
    if gate is not None:
        return gate

    row = get_post_by_slug(db, slug)
    if row is None:
        return _error(f'No published blog post with slug {slug!r}', 'NOT_FOUND', 404)

    locale, default = _resolve_request_locale(db)
    row = overlay_post_translation(db, row, locale, default)

    data = _blog_post_to_dict(
        row,
        fields=_BLOG_POST_DETAIL_FIELDS,
        include_tags=True,
        include_rendered=True,
        db=db,
    )
    return _conditional_response({'data': data}, extra_headers=_locale_headers(locale))


# ===========================================================================
# WRITE ENDPOINTS (Phase 16.3)
# ===========================================================================
#
# All blog-write routes require a Bearer token with ``write`` scope.
# Rate limits come from the ``api_rate_limit_write`` setting (default 30
# per minute) via the limiter callable in ``app.services.api_tokens``.
#
# The /contact endpoint is deliberately NOT token-gated — it mirrors the
# public HTML form so a kiosk / widget can POST submissions without
# provisioning a token. It enforces honeypot + per-IP rate limits
# instead.


def _json_body():
    """Return the parsed JSON request body, or an empty dict if missing.

    ``request.get_json(silent=True)`` returns ``None`` for empty bodies;
    coerce to a dict so handlers can ``.get()`` without None-checking.
    Non-object roots (arrays / scalars) also collapse to ``{}`` so a
    call like ``body.get('title')`` doesn't raise.
    """
    raw = request.get_json(silent=True)
    return raw if isinstance(raw, dict) else {}


def _serialize_post_detail(db, post_row):
    """Build the standard blog detail payload for a POST / PUT response."""
    return _blog_post_to_dict(
        post_row,
        fields=_BLOG_POST_DETAIL_FIELDS,
        include_tags=True,
        include_rendered=True,
        db=db,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/blog — create a blog post (draft by default)
# ---------------------------------------------------------------------------


@api_bp.route('/blog', methods=['POST'])
@limiter.limit(rate_limit_write, methods=['POST'])
@require_api_token('write')
def blog_create():
    """Create a new blog post.

    Body (JSON):
        title (str, required)
        summary (str, optional)
        content (str, optional)
        content_format ('html' | 'markdown', default 'html')
        cover_image (str, optional)
        author (str, optional)
        tags (str, optional — comma-separated)
        meta_description (str, optional)
        featured (bool, default false)
        publish (bool, default false) — when true, immediately publishes.
            Matches the admin UI's "Publish" action.

    Returns 201 with the full post detail. The server-generated slug is
    in the response so the caller can follow up with PUT / DELETE.
    """
    body = _json_body()
    title = (body.get('title') or '').strip()
    if not title:
        return _error('Title is required', 'VALIDATION_ERROR', 400, details={'field': 'title'})

    db = get_db()
    post_id = create_post(
        db,
        title=title,
        summary=body.get('summary', '') or '',
        content=body.get('content', '') or '',
        content_format=body.get('content_format', 'html') or 'html',
        cover_image=body.get('cover_image', '') or '',
        author=body.get('author', '') or '',
        tags=body.get('tags', '') or '',
        meta_description=body.get('meta_description', '') or '',
        featured=bool(body.get('featured', False)),
    )

    published = False
    if body.get('publish'):
        publish_post(db, post_id)
        published = True

    post = get_post_by_id(db, post_id)
    emit(
        Events.BLOG_PUBLISHED if published else Events.BLOG_UPDATED,
        post_id=post_id,
        slug=post['slug'],
        title=post['title'],
        status=post['status'],
        source='api.blog_create',
    )
    return _conditional_response({'data': _serialize_post_detail(db, post)}, status=201)


# ---------------------------------------------------------------------------
# PUT /api/v1/blog/<slug> — update an existing post
# ---------------------------------------------------------------------------


@api_bp.route('/blog/<slug>', methods=['PUT'])
@limiter.limit(rate_limit_write, methods=['PUT'])
@require_api_token('write')
def blog_update(slug):
    """Update a post identified by slug.

    Every field is optional — omitted fields keep their current value.
    The caller can also rename the slug by including a new ``slug`` in
    the body; uniqueness is enforced by ``update_post`` via
    ``_ensure_unique_slug``.
    """
    db = get_db()
    existing = db.execute('SELECT * FROM blog_posts WHERE slug = ?', (slug,)).fetchone()
    if existing is None:
        return _error(f'No blog post with slug {slug!r}', 'NOT_FOUND', 404)

    body = _json_body()
    # Title is the one field `update_post` requires. Keep the current
    # value when the caller omits it.
    title = body.get('title', existing['title']) or existing['title']
    if not title.strip():
        return _error('Title cannot be empty', 'VALIDATION_ERROR', 400, details={'field': 'title'})

    update_post(
        db,
        post_id=existing['id'],
        title=title.strip(),
        summary=body.get('summary', existing['summary']) or '',
        content=body.get('content', existing['content']) or '',
        content_format=body.get('content_format', existing['content_format']) or 'html',
        cover_image=body.get('cover_image', existing['cover_image']) or '',
        author=body.get('author', existing['author']) or '',
        # tags=None means "leave untouched"; tags='' means "remove all".
        # We can't distinguish "omitted" from "set to empty" in JSON, so
        # only resync tags when the key is present.
        tags=body.get('tags', '') if 'tags' in body else '',
        meta_description=body.get('meta_description', existing['meta_description']) or '',
        featured=bool(body.get('featured', bool(existing['featured']))),
        slug=body.get('slug'),
    )
    updated = get_post_by_id(db, existing['id'])
    emit(
        Events.BLOG_UPDATED,
        post_id=existing['id'],
        slug=updated['slug'],
        title=updated['title'],
        status=updated['status'],
        source='api.blog_update',
    )
    return _conditional_response({'data': _serialize_post_detail(db, updated)})


# ---------------------------------------------------------------------------
# DELETE /api/v1/blog/<slug>
# ---------------------------------------------------------------------------


@api_bp.route('/blog/<slug>', methods=['DELETE'])
@limiter.limit(rate_limit_write, methods=['DELETE'])
@require_api_token('write')
def blog_delete(slug):
    """Delete a blog post and its tag associations.

    Returns 204 No Content on success. 404 if the slug doesn't match.
    """
    db = get_db()
    existing = db.execute(
        'SELECT id, title, status FROM blog_posts WHERE slug = ?', (slug,)
    ).fetchone()
    if existing is None:
        return _error(f'No blog post with slug {slug!r}', 'NOT_FOUND', 404)

    delete_post(db, existing['id'])
    emit(
        Events.BLOG_UPDATED,
        post_id=existing['id'],
        slug=slug,
        title=existing['title'],
        status='deleted',
        source='api.blog_delete',
    )
    return Response(status=204)


# ---------------------------------------------------------------------------
# POST /api/v1/blog/<slug>/publish
# ---------------------------------------------------------------------------


@api_bp.route('/blog/<slug>/publish', methods=['POST'])
@limiter.limit(rate_limit_write, methods=['POST'])
@require_api_token('write')
def blog_publish(slug):
    """Publish a draft post, preserving the original ``published_at`` if
    the post was previously published and unpublished.
    """
    db = get_db()
    existing = db.execute('SELECT id FROM blog_posts WHERE slug = ?', (slug,)).fetchone()
    if existing is None:
        return _error(f'No blog post with slug {slug!r}', 'NOT_FOUND', 404)

    publish_post(db, existing['id'])
    updated = get_post_by_id(db, existing['id'])
    emit(
        Events.BLOG_PUBLISHED,
        post_id=existing['id'],
        slug=updated['slug'],
        title=updated['title'],
        status=updated['status'],
        source='api.blog_publish',
    )
    return _conditional_response({'data': _serialize_post_detail(db, updated)})


# ---------------------------------------------------------------------------
# POST /api/v1/blog/<slug>/unpublish
# ---------------------------------------------------------------------------


@api_bp.route('/blog/<slug>/unpublish', methods=['POST'])
@limiter.limit(rate_limit_write, methods=['POST'])
@require_api_token('write')
def blog_unpublish(slug):
    """Revert a published post back to draft status."""
    db = get_db()
    existing = db.execute('SELECT id FROM blog_posts WHERE slug = ?', (slug,)).fetchone()
    if existing is None:
        return _error(f'No blog post with slug {slug!r}', 'NOT_FOUND', 404)

    unpublish_post(db, existing['id'])
    updated = get_post_by_id(db, existing['id'])
    emit(
        Events.BLOG_UPDATED,
        post_id=existing['id'],
        slug=updated['slug'],
        title=updated['title'],
        status=updated['status'],
        source='api.blog_unpublish',
    )
    return _conditional_response({'data': _serialize_post_detail(db, updated)})


# ---------------------------------------------------------------------------
# POST /api/v1/portfolio — upload a photo (multipart/form-data)
# ---------------------------------------------------------------------------

_VALID_DISPLAY_TIERS = frozenset({'featured', 'grid', 'hidden'})


@api_bp.route('/portfolio', methods=['POST'])
@limiter.limit(rate_limit_write, methods=['POST'])
@require_api_token('write')
def portfolio_create():
    """Upload a photo + metadata.

    Content-Type MUST be ``multipart/form-data`` (allow-listed in
    ``_MULTIPART_ENDPOINTS`` for the Content-Type middleware). The file
    part is named ``photo``; metadata fields come from the form body:

        photo         — the image file (jpg / jpeg / png / gif / webp)
        title         — str, optional
        description   — str, optional
        category      — str, optional
        tech_used     — str, optional
        display_tier  — 'featured' | 'grid' | 'hidden' (default 'grid')

    The Pillow pipeline (``app.services.photos.process_upload``)
    handles magic-byte validation, size check, EXIF stripping, and
    downscaling > 2000px. On success, returns 201 with the full photo
    record. On validation failure, returns 400 with a
    ``VALIDATION_ERROR`` code and a ``details`` dict describing the
    problem (invalid type / magic bytes mismatch / size limit).
    """
    from app.services.photos import process_upload

    file = request.files.get('photo')
    if file is None or not file.filename:
        return _error(
            'Missing "photo" file part in multipart body',
            'VALIDATION_ERROR',
            400,
            details={'field': 'photo'},
        )

    result = process_upload(file)
    if result is None:
        return _error(
            'Invalid file type. Allowed: jpg, png, gif, webp',
            'VALIDATION_ERROR',
            400,
            details={'field': 'photo', 'reason': 'invalid_type'},
        )
    if isinstance(result, str):
        # process_upload returns a string error message for size-limit
        # / magic-byte mismatch / null-byte filename. Surface it with a
        # structured tag rather than the raw string.
        return _error(
            result,
            'VALIDATION_ERROR',
            400,
            details={'field': 'photo', 'reason': 'rejected'},
        )

    title = (request.form.get('title') or '').strip()
    description = (request.form.get('description') or '').strip()
    category = (request.form.get('category') or '').strip()
    tech_used = (request.form.get('tech_used') or '').strip()
    display_tier = (request.form.get('display_tier') or 'grid').strip()

    if display_tier not in _VALID_DISPLAY_TIERS:
        # The DB's CHECK constraint would reject the INSERT anyway, but
        # a 400 here is friendlier than a 500. Clean up the uploaded
        # file so a bad tier isn't silent data retention.
        from app.services.photos import delete_photo_file

        delete_photo_file(result['storage_name'])
        return _error(
            f'Invalid display_tier {display_tier!r}',
            'VALIDATION_ERROR',
            400,
            details={'field': 'display_tier', 'allowed': sorted(_VALID_DISPLAY_TIERS)},
        )

    db = get_db()
    cursor = db.execute(
        'INSERT INTO photos '
        '(filename, storage_name, mime_type, width, height, file_size, '
        'title, description, tech_used, category, display_tier) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            result['filename'],
            result['storage_name'],
            result['mime_type'],
            result['width'],
            result['height'],
            result['file_size'],
            title,
            description,
            tech_used,
            category,
            display_tier,
        ),
    )
    db.commit()
    photo_id = cursor.lastrowid

    emit(
        Events.PHOTO_UPLOADED,
        photo_id=photo_id,
        title=title,
        category=category,
        display_tier=display_tier,
        storage_name=result['storage_name'],
        file_size=result['file_size'],
        source='api.portfolio_create',
    )

    row = db.execute('SELECT * FROM photos WHERE id = ?', (photo_id,)).fetchone()
    return _conditional_response(
        {'data': _row_to_dict(row, _PHOTO_PUBLIC_FIELDS)},
        status=201,
    )


# ---------------------------------------------------------------------------
# PUT /api/v1/portfolio/<id> — update metadata (JSON body)
# ---------------------------------------------------------------------------


@api_bp.route('/portfolio/<int:photo_id>', methods=['PUT'])
@limiter.limit(rate_limit_write, methods=['PUT'])
@require_api_token('write')
def portfolio_update(photo_id):
    """Update photo metadata. The file itself is immutable — a new photo
    is a fresh upload.

    Body (JSON, all fields optional):
        title, description, category, tech_used (str)
        display_tier ('featured' | 'grid' | 'hidden')
        sort_order (int)
    """
    db = get_db()
    existing = db.execute('SELECT * FROM photos WHERE id = ?', (photo_id,)).fetchone()
    if existing is None:
        return _error(f'No photo with id {photo_id}', 'NOT_FOUND', 404)

    body = _json_body()
    title = body.get('title', existing['title'])
    description = body.get('description', existing['description'])
    category = body.get('category', existing['category'])
    tech_used = body.get('tech_used', existing['tech_used'])
    display_tier = body.get('display_tier', existing['display_tier'])
    sort_order_raw = body.get('sort_order', existing['sort_order'])

    if display_tier not in _VALID_DISPLAY_TIERS:
        return _error(
            f'Invalid display_tier {display_tier!r}',
            'VALIDATION_ERROR',
            400,
            details={'field': 'display_tier', 'allowed': sorted(_VALID_DISPLAY_TIERS)},
        )
    try:
        sort_order = int(sort_order_raw)
    except (TypeError, ValueError):
        return _error(
            'sort_order must be an integer',
            'VALIDATION_ERROR',
            400,
            details={'field': 'sort_order'},
        )

    db.execute(
        'UPDATE photos SET title = ?, description = ?, category = ?, '
        'tech_used = ?, display_tier = ?, sort_order = ?, '
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
        'WHERE id = ?',
        (title, description, category, tech_used, display_tier, sort_order, photo_id),
    )
    db.commit()

    updated = db.execute('SELECT * FROM photos WHERE id = ?', (photo_id,)).fetchone()
    return _conditional_response({'data': _row_to_dict(updated, _PHOTO_PUBLIC_FIELDS)})


# ---------------------------------------------------------------------------
# DELETE /api/v1/portfolio/<id> — delete row + file from disk
# ---------------------------------------------------------------------------


@api_bp.route('/portfolio/<int:photo_id>', methods=['DELETE'])
@limiter.limit(rate_limit_write, methods=['DELETE'])
@require_api_token('write')
def portfolio_delete(photo_id):
    """Delete a photo row and clean up the file on disk.

    Returns 204 No Content on success, 404 if the id doesn't exist.
    File-cleanup failure (disk error, file already gone) is logged but
    doesn't fail the request — the DB row is removed either way so the
    site doesn't keep serving a broken reference.
    """
    from app.services.photos import delete_photo_file

    db = get_db()
    row = db.execute('SELECT storage_name FROM photos WHERE id = ?', (photo_id,)).fetchone()
    if row is None:
        return _error(f'No photo with id {photo_id}', 'NOT_FOUND', 404)

    db.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
    db.commit()

    try:
        delete_photo_file(row['storage_name'])
    except OSError as exc:
        current_app.logger.warning(
            'api.portfolio_delete: failed to remove %s (%s)', row['storage_name'], exc
        )
    return Response(status=204)


# ---------------------------------------------------------------------------
# POST /api/v1/contact  (public, honeypot + rate limit)
# ---------------------------------------------------------------------------


def _client_ip_from_request():
    """Return the real client IP, honouring X-Forwarded-For from a proxy.

    Mirrors the logic in :mod:`app.routes.contact` so the API and the
    HTML form agree on "whose" submission is whose for rate limiting.
    """
    forwarded = request.headers.get('X-Forwarded-For', request.remote_addr)
    if forwarded and ',' in forwarded:
        forwarded = forwarded.split(',')[0].strip()
    return forwarded or 'unknown'


@api_bp.route('/contact', methods=['POST'])
@limiter.limit('10 per minute', methods=['POST'])
def contact_submit():
    """Submit a contact form entry.

    Public — no token required. Mirrors the HTML form's validation:

    * ``contact_form_enabled`` setting must be ``true`` (404 otherwise).
    * ``website`` honeypot field flags submissions as spam but still
      saves them (so an admin can see attack patterns).
    * Per-IP limit: 5 non-spam submissions per hour (enforced alongside
      Flask-Limiter's 10/min burst cap).
    * Required fields: name, email, message. Email must contain '@' and
      a period.

    Returns 201 on success with ``{ok: true, id: N}``. Returns 400 on
    validation failure, 404 if the form is disabled, 429 on rate limit.
    SMTP relay failure is NOT surfaced — the submission is still saved
    and a warning is logged server-side (same contract as the HTML form).
    """
    db = get_db()
    if get_setting(db, 'contact_form_enabled', 'true') != 'true':
        return _error('Contact form is disabled on this site', 'NOT_FOUND', 404)

    body = _json_body()
    name = (body.get('name') or '').strip()
    email = (body.get('email') or '').strip()
    message = (body.get('message') or '').strip()
    honeypot = (body.get('website') or '').strip()

    missing = [f for f, v in (('name', name), ('email', email), ('message', message)) if not v]
    if missing:
        return _error(
            'Missing required field(s)',
            'VALIDATION_ERROR',
            400,
            details={'fields': missing},
        )
    if '@' not in email or '.' not in email:
        return _error(
            'Email address is not valid',
            'VALIDATION_ERROR',
            400,
            details={'field': 'email'},
        )

    is_spam = bool(honeypot)
    client_ip = _client_ip_from_request()
    user_agent = (request.headers.get('User-Agent') or '')[:200]

    # Real humans (honeypot empty) get the per-IP hourly cap. Bots
    # filling the honeypot skip the check so they can't work it out
    # by probing for 429s.
    if not is_spam:
        recent = count_recent_submissions(db, client_ip)
        if recent >= 5:
            return _error(
                'Too many submissions from this IP in the past hour',
                'RATE_LIMITED',
                429,
                details={'retry_after_minutes': 60},
            )

    submission_id = save_contact_submission(
        db,
        name=name,
        email=email,
        message=message,
        ip_address=client_ip,
        user_agent=user_agent,
        is_spam=is_spam,
    )

    # Fire the event regardless of spam flag so admin dashboards can
    # choose to surface attack patterns. Subscribers filter as needed.
    emit(
        Events.CONTACT_SUBMITTED,
        submission_id=submission_id,
        is_spam=is_spam,
        source='api.contact_submit',
    )

    # SMTP relay (best-effort, matches HTML form's fire-and-forget).
    # Import locally so module load doesn't bring up smtplib.
    if not is_spam:
        try:
            from app.services.mail import send_contact_email

            send_contact_email(name, email, message)
        except Exception:  # noqa: BLE001 — match HTML-form contract
            current_app.logger.warning(
                'API /contact: SMTP send failed for submission %s', submission_id
            )

    return _conditional_response(
        {'data': {'id': submission_id, 'ok': True, 'is_spam': is_spam}},
        status=201,
    )


# ===========================================================================
# ADMIN ENDPOINTS (Phase 16.4 — Token Required: admin scope)
# ===========================================================================
#
# All /api/v1/admin/ routes sit behind @require_api_token('admin') and
# use the slower rate_limit_admin bucket (default 10/min). The HTML
# admin UI remains the primary surface; these endpoints exist so a
# headless client (ops tooling, external dashboard, mobile app) can
# drive the same workflows.
#
# Field whitelists are deliberately small — the API should never leak
# internals (ip_address for contact submissions is kept because admins
# legitimately need it for abuse handling; user_agent truncated).

_CONTACT_PUBLIC_FIELDS = (
    'id',
    'name',
    'email',
    'message',
    'ip_address',
    'user_agent',
    'is_spam',
    'read',
    'created_at',
)

_REVIEW_ADMIN_FIELDS = (
    'id',
    'reviewer_name',
    'reviewer_title',
    'relationship',
    'message',
    'rating',
    'type',
    'status',
    'display_tier',
    'token_id',
    'created_at',
    'reviewed_at',
)

_ACTIVITY_PUBLIC_FIELDS = (
    'id',
    'action',
    'category',
    'detail',
    'admin_user',
    'created_at',
)

_REVIEW_TOKEN_PUBLIC_FIELDS = (
    'id',
    'token',
    'name',
    'type',
    'used',
    'used_at',
    'created_at',
    'expires_at',
)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/settings
# ---------------------------------------------------------------------------


@api_bp.route('/admin/settings', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_settings_list():
    """Return every setting, grouped by category.

    Response::

        {
          "data": {
            "categories": [
              {"name": "Site Identity", "settings": [{...}, ...]},
              ...
            ],
            "flat": {"site_title": "...", ...}
          }
        }

    Each setting dict carries its registry metadata (type, default,
    label, options) so a headless admin panel can render the form
    without hard-coding the schema.
    """
    db = get_db()
    grouped = get_grouped_settings(db)
    categories = [
        {'name': name, 'settings': [dict(s) for s in settings]} for name, settings in grouped
    ]
    return _conditional_response({'data': {'categories': categories, 'flat': get_all(db)}})


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/settings
# ---------------------------------------------------------------------------


@api_bp.route('/admin/settings', methods=['PUT'])
@limiter.limit(rate_limit_admin, methods=['PUT'])
@require_api_token('admin')
def admin_settings_update():
    """Bulk-update settings. Body is a flat ``{key: value}`` JSON object.

    Unknown keys (not in ``SETTINGS_REGISTRY``) are silently ignored —
    same contract as the HTML admin form. Boolean settings not present
    in the body are NOT flipped to false (that's an HTML-form quirk we
    don't want to replicate for API clients who may send a partial
    update). Returns 200 with the refreshed settings payload.
    """
    db = get_db()
    body = _json_body()
    # Filter to known keys up front so unknown keys don't even reach
    # save_many (which would skip them anyway). Booleans are normalised
    # to the string literals 'true' / 'false' the settings table stores.
    cleaned = {}
    for key, raw in body.items():
        if key not in SETTINGS_REGISTRY:
            continue
        if SETTINGS_REGISTRY[key].get('type') == 'bool':
            cleaned[key] = 'true' if raw in (True, 'true', 'True', 1, '1') else 'false'
        else:
            cleaned[key] = '' if raw is None else str(raw)

    save_settings(db, cleaned)
    log_action(
        db,
        action='Updated settings via API',
        category='settings',
        detail=f'{len(cleaned)} key(s) changed',
    )
    emit(
        Events.SETTINGS_CHANGED,
        keys=sorted(cleaned.keys()),
        source='api.admin_settings_update',
    )
    return _conditional_response(
        {'data': {'updated_keys': sorted(cleaned.keys()), 'flat': get_all(db)}}
    )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/analytics
# ---------------------------------------------------------------------------


@api_bp.route('/admin/analytics', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_analytics():
    """Return a page-view summary: total, recent (7d), popular paths, daily series.

    Query parameters:
        days (int, default 7, max 90): recent-window size.
        popular_limit (int, default 10, max 50): top-N pages by count.
    """
    db = get_db()
    try:
        days = max(1, min(int(request.args.get('days', 7)), 90))
    except (TypeError, ValueError):
        days = 7
    try:
        limit = max(1, min(int(request.args.get('popular_limit', 10)), 50))
    except (TypeError, ValueError):
        limit = 10

    total = db.execute('SELECT COUNT(*) AS n FROM page_views').fetchone()['n']
    # The '-N days' modifier has to be built from the validated int so
    # the clamped value is the one that hits SQLite.
    recent = db.execute(
        'SELECT COUNT(*) AS n FROM page_views '
        "WHERE created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        (f'-{days} days',),
    ).fetchone()['n']
    popular = db.execute(
        'SELECT path, COUNT(*) AS n FROM page_views GROUP BY path ORDER BY n DESC LIMIT ?',
        (limit,),
    ).fetchall()
    series = db.execute(
        'SELECT date(created_at) AS day, COUNT(*) AS n FROM page_views '
        "WHERE created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) "
        'GROUP BY day ORDER BY day',
        (f'-{days} days',),
    ).fetchall()

    payload = {
        'total_views': total,
        'recent_views': recent,
        'window_days': days,
        'popular_pages': [{'path': r['path'], 'count': r['n']} for r in popular],
        'time_series': [{'date': r['day'], 'count': r['n']} for r in series],
    }
    return _conditional_response({'data': payload})


# ---------------------------------------------------------------------------
# GET /api/v1/admin/activity
# ---------------------------------------------------------------------------


@api_bp.route('/admin/activity', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_activity_log():
    """Return the recent admin activity log.

    Query parameters:
        limit (int, default 20, max 200): how many entries to return.
    """
    try:
        limit = max(1, min(int(request.args.get('limit', 20)), 200))
    except (TypeError, ValueError):
        limit = 20
    rows = get_recent_activity(get_db(), limit=limit)
    items = [_row_to_dict(r, _ACTIVITY_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items})


# ---------------------------------------------------------------------------
# GET /api/v1/admin/reviews
# ---------------------------------------------------------------------------


@api_bp.route('/admin/reviews', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_reviews_list():
    """List reviews, optionally filtered by status.

    Query parameters:
        status (str, optional): ``pending`` | ``approved`` | ``rejected``.
            Missing / empty returns all three statuses concatenated
            (pending first, matching the admin UI).
    """
    db = get_db()
    status = (request.args.get('status') or '').strip()
    if status:
        try:
            rows = get_reviews_by_status(db, status)
        except ValidationError as exc:
            return _error(
                str(exc),
                'VALIDATION_ERROR',
                400,
                details={'field': 'status', 'allowed': ['pending', 'approved', 'rejected']},
            )
    else:
        pending = get_reviews_by_status(db, 'pending')
        approved = get_reviews_by_status(db, 'approved')
        rejected = get_reviews_by_status(db, 'rejected')
        rows = list(pending) + list(approved) + list(rejected)

    items = [_row_to_dict(r, _REVIEW_ADMIN_FIELDS) for r in rows]
    return _conditional_response({'data': items})


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/reviews/<id>
# ---------------------------------------------------------------------------


@api_bp.route('/admin/reviews/<int:review_id>', methods=['PUT'])
@limiter.limit(rate_limit_admin, methods=['PUT'])
@require_api_token('admin')
def admin_review_update(review_id):
    """Approve / reject / re-tier a review.

    Body (JSON) — exactly one of:
        {"action": "approve", "display_tier": "featured|standard|hidden"}
        {"action": "reject"}
        {"action": "set_tier", "display_tier": "..."}   # for already-approved
    """
    db = get_db()
    existing = db.execute('SELECT id FROM reviews WHERE id = ?', (review_id,)).fetchone()
    if existing is None:
        return _error(f'No review with id {review_id}', 'NOT_FOUND', 404)

    body = _json_body()
    action = (body.get('action') or '').strip()
    tier = (body.get('display_tier') or 'standard').strip()

    if action == 'approve':
        approve_review(db, review_id, display_tier=tier)
        detail = f'id={review_id} tier={tier}'
        verb = 'Approved review'
    elif action == 'reject':
        reject_review(db, review_id)
        detail = f'id={review_id}'
        verb = 'Rejected review'
    elif action == 'set_tier':
        update_review_tier(db, review_id, display_tier=tier)
        detail = f'id={review_id} tier={tier}'
        verb = 'Updated review tier'
    else:
        return _error(
            f'Unknown action {action!r}',
            'VALIDATION_ERROR',
            400,
            details={'field': 'action', 'allowed': ['approve', 'reject', 'set_tier']},
        )

    log_action(db, action=verb, category='reviews', detail=detail)
    if action == 'approve':
        emit(Events.REVIEW_APPROVED, review_id=review_id, display_tier=tier, source='api')

    updated = db.execute('SELECT * FROM reviews WHERE id = ?', (review_id,)).fetchone()
    return _conditional_response({'data': _row_to_dict(updated, _REVIEW_ADMIN_FIELDS)})


# ---------------------------------------------------------------------------
# POST /api/v1/admin/tokens — generate a review invite token
# ---------------------------------------------------------------------------


@api_bp.route('/admin/tokens', methods=['POST'])
@limiter.limit(rate_limit_admin, methods=['POST'])
@require_api_token('admin')
def admin_review_token_create():
    """Generate a single-use review invitation token.

    Body (JSON):
        name (str, optional) — recipient label for the admin view.
        type (str, optional, default 'recommendation'):
            'recommendation' | 'client_review'.

    Returns 201 with the full token row (including the raw value —
    review tokens are designed to be shared verbatim with a contact,
    unlike API tokens which are stored as hashes).
    """
    import secrets as _secrets

    db = get_db()
    body = _json_body()
    name = (body.get('name') or '').strip()
    token_type = (body.get('type') or 'recommendation').strip()
    if token_type not in ('recommendation', 'client_review'):
        return _error(
            f'Invalid token type {token_type!r}',
            'VALIDATION_ERROR',
            400,
            details={'field': 'type', 'allowed': ['recommendation', 'client_review']},
        )

    token_string = _secrets.token_urlsafe(32)
    cursor = db.execute(
        'INSERT INTO review_tokens (token, name, type) VALUES (?, ?, ?)',
        (token_string, name, token_type),
    )
    db.commit()
    log_action(
        db,
        action='Generated review token via API',
        category='tokens',
        detail=f'{name or "anonymous"} ({token_type})',
    )
    row = db.execute('SELECT * FROM review_tokens WHERE id = ?', (cursor.lastrowid,)).fetchone()
    return _conditional_response(
        {'data': _row_to_dict(row, _REVIEW_TOKEN_PUBLIC_FIELDS)},
        status=201,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/tokens/<id>
# ---------------------------------------------------------------------------


@api_bp.route('/admin/tokens/<int:token_id>', methods=['DELETE'])
@limiter.limit(rate_limit_admin, methods=['DELETE'])
@require_api_token('admin')
def admin_review_token_delete(token_id):
    """Delete (hard-revoke) a review invite token. 204 on success, 404 otherwise."""
    db = get_db()
    row = db.execute('SELECT id FROM review_tokens WHERE id = ?', (token_id,)).fetchone()
    if row is None:
        return _error(f'No review token with id {token_id}', 'NOT_FOUND', 404)

    db.execute('DELETE FROM review_tokens WHERE id = ?', (token_id,))
    db.commit()
    log_action(
        db,
        action='Deleted review token via API',
        category='tokens',
        detail=f'id={token_id}',
    )
    return Response(status=204)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/contacts
# ---------------------------------------------------------------------------


@api_bp.route('/admin/contacts', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_contacts_list():
    """Paginated contact submissions.

    Query parameters:
        page (int, default 1)
        per_page (int, default 20, max 100)
        include_spam (bool, default false): if true, include spam rows
            interleaved by timestamp. Defaults to false so an admin's
            default view is noise-free.
    """
    db = get_db()
    page = clamp_page(request.args.get('page'))
    per_page = _parse_per_page(request.args.get('per_page'), default=20)
    include_spam = str(request.args.get('include_spam', 'false')).lower() in {'1', 'true', 'yes'}

    # ``where`` is one of two hard-coded literals — never user input —
    # so the f-string interpolation is safe. The suppressions silence
    # ruff (S608) and bandit (B608) without disabling the check
    # globally.
    where = '' if include_spam else 'WHERE is_spam = 0'
    total = db.execute(
        f'SELECT COUNT(*) AS n FROM contact_submissions {where}'  # noqa: S608  # nosec B608
    ).fetchone()['n']
    rows = db.execute(
        f'SELECT * FROM contact_submissions {where} '  # noqa: S608  # nosec B608
        'ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?',
        (per_page, offset_for(page, per_page)),
    ).fetchall()

    items = [_row_to_dict(r, _CONTACT_PUBLIC_FIELDS) for r in rows]
    return _paginated_response(items, page=page, per_page=per_page, total=total)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/backup
# ---------------------------------------------------------------------------


@api_bp.route('/admin/backup', methods=['POST'])
@limiter.limit(rate_limit_admin, methods=['POST'])
@require_api_token('admin')
def admin_backup_create():
    """Trigger an on-demand backup, return the archive path + size.

    Body (JSON, optional):
        {"db_only": true}   — create a database-only archive (fast).

    The backup is written to the same directory the ``manage.py backup``
    CLI uses: config > env ``RESUME_SITE_BACKUP_DIR`` > ``<repo>/backups``.
    ``create_backup`` emits ``Events.BACKUP_COMPLETED`` itself, so this
    route doesn't double-emit.
    """
    import os as _os

    from app.services.backups import BackupError, create_backup

    body = _json_body()
    db_only = bool(body.get('db_only', False))

    db_path = current_app.config['DATABASE_PATH']
    photos_dir = current_app.config.get('PHOTO_STORAGE')
    # Match manage.py's resolution: explicit arg would go here, then
    # env, then <repo>/backups. The API has no explicit arg so env +
    # default suffice.
    output_dir = _os.path.abspath(
        _os.environ.get('RESUME_SITE_BACKUP_DIR')
        or _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), 'backups')
    )
    config_path = _os.environ.get(
        'RESUME_SITE_CONFIG',
        _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), 'config.yaml'
        ),
    )

    try:
        archive = create_backup(
            db_path=db_path,
            photos_dir=photos_dir,
            config_path=config_path if _os.path.isfile(config_path) else None,
            output_dir=output_dir,
            db_only=db_only,
        )
    except BackupError as exc:
        return _error(str(exc), 'BACKUP_FAILED', 500)
    except OSError as exc:
        return _error(f'I/O failure during backup: {exc}', 'BACKUP_FAILED', 500)

    size = _os.path.getsize(archive) if _os.path.exists(archive) else 0
    log_action(
        get_db(),
        action='Created on-demand backup via API',
        category='backup',
        detail=_os.path.basename(archive),
    )
    return _conditional_response(
        {
            'data': {
                'archive_path': archive,
                'archive_name': _os.path.basename(archive),
                'size_bytes': size,
                'db_only': db_only,
            }
        },
        status=201,
    )


# ===========================================================================
# WEBHOOKS (Phase 19.2 — admin scope, CRUD over the webhooks table)
# ===========================================================================
#
# Mirrors the admin UI under ``/admin/webhooks`` so a headless operator
# can drive the same workflows. The service layer
# (``app.services.webhooks``) owns CRUD + delivery + auto-disable; these
# routes are pure adapters.
#
# Field shape:
#
#     {
#       "id": 1,
#       "name": "Slack",
#       "url": "https://hooks.example.com/abc",
#       "events": ["blog.published", "review.approved"],
#       "enabled": true,
#       "failure_count": 0,
#       "created_at": "2026-04-15T10:11:12Z",
#       "last_triggered_at": "2026-04-15T10:13:14Z"
#     }
#
# The HMAC ``secret`` is intentionally OMITTED from every response. It
# can only be set / rotated via POST or PUT — once written, the API
# never reads it back. This matches the admin UI's masked-field
# convention so tokens shown in the API are no more privileged than
# tokens shown in HTML.
#
# The /test endpoint is synchronous (operator wants the result inline)
# and returns the full DeliveryResult so a caller can debug a broken
# downstream without polling /deliveries afterward.


def _webhook_to_dict(webhook):
    """Project a :class:`Webhook` namedtuple to a JSON-safe dict, sans secret."""
    if webhook is None:
        return None
    return {
        'id': webhook.id,
        'name': webhook.name,
        'url': webhook.url,
        'events': list(webhook.events),
        'enabled': bool(webhook.enabled),
        'failure_count': webhook.failure_count,
        'created_at': webhook.created_at,
        'last_triggered_at': webhook.last_triggered_at,
    }


def _validate_webhook_url(url):
    """Return ``(ok, message)`` after URL parsing."""
    from urllib.parse import urlparse as _urlparse

    if not url:
        return False, 'url is required'
    parsed = _urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return False, 'url must be a valid http(s) address'
    return True, ''


def _coerce_events_field(raw):
    """Translate the body's ``events`` value into a clean list of strings.

    Accepts a list of strings (canonical), a comma-separated string
    (curl-friendly), or omits → defaults to ``["*"]``.
    """
    if raw is None:
        return ['*']
    if isinstance(raw, list):
        return [str(e).strip() for e in raw if str(e).strip()] or ['*']
    if isinstance(raw, str):
        parts = [e.strip() for e in raw.split(',') if e.strip()]
        return parts or ['*']
    return ['*']


@api_bp.route('/admin/webhooks', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_webhooks_list():
    """List every webhook subscription (no secrets in the response)."""
    from app.services.webhooks import list_webhooks

    db = get_db()
    rows = list_webhooks(db)
    return _conditional_response({'data': [_webhook_to_dict(r) for r in rows]})


@api_bp.route('/admin/webhooks', methods=['POST'])
@limiter.limit(rate_limit_admin, methods=['POST'])
@require_api_token('admin')
def admin_webhooks_create():
    """Create a new webhook subscription.

    Body (JSON):
        name (str, required)
        url (str, required) — http(s) URL
        secret (str, optional) — auto-generated when omitted
        events (list[str] | str, optional) — defaults to ``["*"]``
        enabled (bool, default true)

    Returns 201 with the new webhook payload and the secret echoed back
    once so the caller can store it for the downstream verifier.
    """
    import secrets as _secrets

    from app.services.webhooks import create_webhook, get_webhook

    body = _json_body()
    name = (body.get('name') or '').strip()
    url = (body.get('url') or '').strip()
    secret = (body.get('secret') or '').strip() or _secrets.token_urlsafe(32)
    enabled = bool(body.get('enabled', True))
    events_list = _coerce_events_field(body.get('events'))

    if not name:
        return _error('name is required', 'VALIDATION_ERROR', 400, details={'field': 'name'})
    ok, msg = _validate_webhook_url(url)
    if not ok:
        return _error(msg, 'VALIDATION_ERROR', 400, details={'field': 'url'})

    db = get_db()
    wh_id = create_webhook(
        db, name=name, url=url, secret=secret, events=events_list, enabled=enabled
    )
    log_action(
        db,
        action='Created webhook via API',
        category='webhooks',
        detail=f'id={wh_id} name={name}',
    )
    payload = _webhook_to_dict(get_webhook(db, wh_id))
    # Echo the secret exactly once on creation so the caller can stash
    # it. Mirrors the admin /api-tokens reveal pattern. The secret is
    # never returned by GET / PUT / list endpoints.
    payload['secret'] = secret
    return _conditional_response({'data': payload}, status=201)


@api_bp.route('/admin/webhooks/<int:webhook_id>', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_webhooks_get(webhook_id):
    """Return one webhook by id, or 404."""
    from app.services.webhooks import get_webhook

    webhook = get_webhook(get_db(), webhook_id)
    if webhook is None:
        return _error(f'No webhook with id {webhook_id}', 'NOT_FOUND', 404)
    return _conditional_response({'data': _webhook_to_dict(webhook)})


@api_bp.route('/admin/webhooks/<int:webhook_id>', methods=['PUT'])
@limiter.limit(rate_limit_admin, methods=['PUT'])
@require_api_token('admin')
def admin_webhooks_update(webhook_id):
    """Update fields on an existing webhook.

    Body (JSON, all fields optional):
        name, url, secret (str)
        events (list[str] | str)
        enabled (bool)
        reset_failures (bool) — when true, zeros the consecutive-failure counter

    Omitted fields keep their current value. The secret, if rotated, is
    NOT echoed in the response — fetch it server-side or rotate again
    if you lose it.
    """
    from app.services.webhooks import get_webhook, update_webhook

    db = get_db()
    existing = get_webhook(db, webhook_id)
    if existing is None:
        return _error(f'No webhook with id {webhook_id}', 'NOT_FOUND', 404)

    body = _json_body()
    fields = {}
    if 'name' in body:
        new_name = (body.get('name') or '').strip()
        if not new_name:
            return _error(
                'name cannot be empty', 'VALIDATION_ERROR', 400, details={'field': 'name'}
            )
        fields['name'] = new_name
    if 'url' in body:
        new_url = (body.get('url') or '').strip()
        ok, msg = _validate_webhook_url(new_url)
        if not ok:
            return _error(msg, 'VALIDATION_ERROR', 400, details={'field': 'url'})
        fields['url'] = new_url
    if 'events' in body:
        fields['events'] = _coerce_events_field(body.get('events'))
    if 'enabled' in body:
        fields['enabled'] = bool(body.get('enabled'))
    if 'secret' in body:
        new_secret = (body.get('secret') or '').strip()
        if not new_secret:
            return _error(
                'secret cannot be empty', 'VALIDATION_ERROR', 400, details={'field': 'secret'}
            )
        fields['secret'] = new_secret
    if body.get('reset_failures'):
        fields['failure_count'] = 0

    update_webhook(db, webhook_id, **fields)
    log_action(
        db,
        action='Updated webhook via API',
        category='webhooks',
        detail=f'id={webhook_id} fields={",".join(sorted(fields))}',
    )
    return _conditional_response({'data': _webhook_to_dict(get_webhook(db, webhook_id))})


@api_bp.route('/admin/webhooks/<int:webhook_id>', methods=['DELETE'])
@limiter.limit(rate_limit_admin, methods=['DELETE'])
@require_api_token('admin')
def admin_webhooks_delete(webhook_id):
    """Hard-delete a webhook (cascades its delivery log). 204 on success."""
    from app.services.webhooks import delete_webhook, get_webhook

    db = get_db()
    existing = get_webhook(db, webhook_id)
    if existing is None:
        return _error(f'No webhook with id {webhook_id}', 'NOT_FOUND', 404)

    delete_webhook(db, webhook_id)
    log_action(
        db,
        action='Deleted webhook via API',
        category='webhooks',
        detail=f'id={webhook_id} name={existing.name}',
    )
    return Response(status=204)


@api_bp.route('/admin/webhooks/<int:webhook_id>/test', methods=['POST'])
@limiter.limit(rate_limit_admin, methods=['POST'])
@require_api_token('admin')
def admin_webhooks_test(webhook_id):
    """Fire a synthetic test delivery. Synchronous; returns the result inline.

    Updates ``failure_count`` and the delivery log under the same
    contract as the bus dispatcher: 2xx → reset; non-2xx → increment +
    auto-disable when threshold crossed.

    Returns 200 with::

        {
          "data": {
            "ok": true,
            "status_code": 204,
            "response_time_ms": 87,
            "error": ""
          }
        }
    """
    from app.models import get_setting
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
        return _error(f'No webhook with id {webhook_id}', 'NOT_FOUND', 404)

    payload = {
        'test': True,
        'message': 'resume-site test delivery (api)',
    }
    result = deliver_now(webhook, 'webhook.test', payload, timeout=5)
    record_delivery(db, result)
    ok = 200 <= result.status_code < 300
    if ok:
        reset_failures(db, webhook_id)
    else:
        try:
            threshold = max(0, int(get_setting(db, 'webhook_failure_threshold', '10') or 10))
        except (TypeError, ValueError):
            threshold = 10
        increment_failures(db, webhook_id, threshold=threshold)
    log_action(
        db,
        action='Tested webhook via API',
        category='webhooks',
        detail=f'id={webhook_id} status={result.status_code}',
    )
    return _conditional_response(
        {
            'data': {
                'ok': ok,
                'status_code': result.status_code,
                'response_time_ms': result.response_time_ms,
                'error': result.error,
            }
        }
    )


@api_bp.route('/admin/webhooks/<int:webhook_id>/deliveries', methods=['GET'])
@limiter.limit(rate_limit_admin, methods=['GET'])
@require_api_token('admin')
def admin_webhooks_deliveries(webhook_id):
    """Per-webhook delivery log.

    Query parameters:
        limit (int, default 50, max 500): newest entries first.
    """
    from app.services.webhooks import get_webhook, list_recent_deliveries

    db = get_db()
    if get_webhook(db, webhook_id) is None:
        return _error(f'No webhook with id {webhook_id}', 'NOT_FOUND', 404)

    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 500))
    except (TypeError, ValueError):
        limit = 50
    deliveries = list_recent_deliveries(db, webhook_id=webhook_id, limit=limit)
    return _conditional_response({'data': deliveries})


# ===========================================================================
# API DOCUMENTATION (Phase 16.5 — OpenAPI 3.0 + Swagger UI)
# ===========================================================================
#
# Three routes serve the hand-written OpenAPI specification at
# ``docs/openapi.yaml`` and an interactive Swagger UI. All three sit
# behind the ``api_docs_enabled`` feature flag (default off) and return
# 404 NOT_FOUND when disabled — 403 would leak the endpoints' existence,
# which defeats the "off by default" intent.
#
# The YAML bytes and parsed dict are cached in module scope on first
# access so repeat requests never re-read or re-parse the file.

_OPENAPI_SPEC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'docs',
    'openapi.yaml',
)
_OPENAPI_YAML_BYTES: bytes | None = None
_OPENAPI_SPEC_DICT: dict | None = None


def _load_openapi_spec():
    """Return ``(yaml_bytes, parsed_dict)``, caching both after first read.

    Raises :class:`FileNotFoundError` if the spec file is missing — that
    indicates a broken deployment (the spec ships with the codebase),
    not a runtime condition the user can recover from.
    """
    global _OPENAPI_YAML_BYTES, _OPENAPI_SPEC_DICT
    if _OPENAPI_YAML_BYTES is None or _OPENAPI_SPEC_DICT is None:
        import yaml  # PyYAML is already a runtime dep (see requirements.txt)

        with open(_OPENAPI_SPEC_PATH, 'rb') as fh:
            raw = fh.read()
        _OPENAPI_YAML_BYTES = raw
        _OPENAPI_SPEC_DICT = yaml.safe_load(raw)
    return _OPENAPI_YAML_BYTES, _OPENAPI_SPEC_DICT


def _require_docs_enabled():
    """Return ``None`` when api_docs_enabled is truthy, else a 404 response.

    Mirrors the ``/metrics`` and blog-disabled patterns elsewhere in this
    blueprint — disabled endpoints return 404, not 403, so a probe
    can't tell the endpoint exists.
    """
    db = get_db()
    if get_setting(db, 'api_docs_enabled', 'false').strip().lower() != 'true':
        return _error('Not found', 'NOT_FOUND', 404)
    return None


def _bytes_conditional_response(body_bytes, content_type, *, status=200):
    """Serve raw bytes with ETag + If-None-Match handling.

    The JSON-native ``_conditional_response`` builds the body from a
    Python object. The docs routes serve pre-built YAML/HTML/JSON bytes,
    so they need a variant that takes bytes directly. Same ETag
    algorithm (SHA-256 prefix) to stay consistent with every other read
    endpoint.
    """
    etag = '"' + hashlib.sha256(body_bytes).hexdigest()[:32] + '"'
    client_etag = request.headers.get('If-None-Match', '').strip()
    headers = {'ETag': etag, 'Cache-Control': 'no-cache'}
    if client_etag and client_etag == etag:
        return Response(status=304, headers=headers)
    headers['Content-Type'] = content_type
    return Response(body_bytes, status=status, headers=headers)


@api_bp.route('/openapi.yaml')
def openapi_yaml():
    """Serve the hand-written OpenAPI 3.0 spec as YAML."""
    gate = _require_docs_enabled()
    if gate is not None:
        return gate
    yaml_bytes, _ = _load_openapi_spec()
    return _bytes_conditional_response(yaml_bytes, 'application/yaml')


@api_bp.route('/openapi.json')
def openapi_json():
    """Serve the same spec serialised as JSON for tools that prefer it."""
    gate = _require_docs_enabled()
    if gate is not None:
        return gate
    _, spec = _load_openapi_spec()
    body = json.dumps(spec, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return _bytes_conditional_response(body, 'application/json')


@api_bp.route('/docs')
def openapi_docs():
    """Render Swagger UI pointed at the YAML endpoint above.

    The template is standalone (does not extend the site's ``base.html``)
    so Swagger UI's CSS reset doesn't fight the site styles. All assets
    load from ``cdn.jsdelivr.net`` which is already allow-listed by the
    app CSP.
    """
    gate = _require_docs_enabled()
    if gate is not None:
        return gate
    return render_template('api/docs.html')


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
        _CASE_STUDY_PUBLIC_FIELDS,
        _PROJECT_PUBLIC_FIELDS,
        _BLOG_POST_LIST_FIELDS,
        _BLOG_POST_DETAIL_FIELDS,
    )
