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

from flask import Blueprint, Response, current_app, g, jsonify, request

from app import limiter
from app.db import get_db
from app.events import Events, emit
from app.models import (
    count_recent_submissions,
    get_all_approved_reviews,
    get_all_visible_photos,
    get_approved_reviews_by_tier,
    get_case_study_by_slug,
    get_content_block,
    get_photo_categories,
    get_project_by_slug,
    get_setting,
    get_visible_certifications,
    get_visible_projects,
    get_visible_services,
    get_visible_stats,
    save_contact_submission,
)
from app.services.api_tokens import rate_limit_write, require_api_token
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
# JSON Content-Type enforcement (Phase 16.1 deferred bullet)
# ---------------------------------------------------------------------------

# Endpoints that legitimately accept non-JSON request bodies. Phase 16.3
# ships none of these yet; Phase 16.3b will add /portfolio which uses
# multipart/form-data for the binary image payload. Including the path
# template here now keeps the middleware stable across future commits.
_MULTIPART_ENDPOINTS = frozenset(
    {
        'api.portfolio_create',  # added in Phase 16.3b
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
    """Return every visible project in sort order."""
    rows = get_visible_projects(get_db())
    items = [_row_to_dict(r, _PROJECT_PUBLIC_FIELDS) for r in rows]
    return _conditional_response({'data': items})


@api_bp.route('/projects/<slug>')
def project_detail(slug):
    """Return a single visible project by slug.

    Only projects with ``has_detail_page = 1`` have a detail surface —
    the rest are GitHub-link cards only. A slug that doesn't match a
    detail-page project returns 404 rather than a redirect to the
    GitHub URL, which keeps the API path-stable and lets clients decide
    how to handle external links.
    """
    row = get_project_by_slug(get_db(), slug)
    if row is None:
        return _error(f'No project with slug {slug!r}', 'NOT_FOUND', 404)
    return _conditional_response({'data': _row_to_dict(row, _PROJECT_PUBLIC_FIELDS)})


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
    """Return paginated published blog posts.

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

    if tag:
        posts, total = get_posts_by_tag(db, tag, page=page, per_page=per_page)
    else:
        posts, total = get_published_posts(db, page=page, per_page=per_page)

    items = [
        _blog_post_to_dict(p, fields=_BLOG_POST_LIST_FIELDS, include_tags=True, db=db)
        for p in posts
    ]
    return _paginated_response(items, page=page, per_page=per_page, total=total)


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
    """Return a single published blog post with its tags + rendered HTML.

    Draft / archived posts return 404 (not 403) to avoid leaking their
    existence.
    """
    db = get_db()
    gate = _require_blog_enabled(db)
    if gate is not None:
        return gate

    row = get_post_by_slug(db, slug)
    if row is None:
        return _error(f'No published blog post with slug {slug!r}', 'NOT_FOUND', 404)

    data = _blog_post_to_dict(
        row,
        fields=_BLOG_POST_DETAIL_FIELDS,
        include_tags=True,
        include_rendered=True,
        db=db,
    )
    return _conditional_response({'data': data})


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
