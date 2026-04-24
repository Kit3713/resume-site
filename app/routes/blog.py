"""
Public Blog Routes

Handles visitor-facing blog pages: paginated post listing, individual post
display, tag filtering, and RSS feed generation.

All routes respect the 'blog_enabled' setting — when disabled, every blog
URL returns 404 and the nav link is hidden (handled by the template).

URL structure:
    /blog                   Paginated list of published posts
    /blog/<slug>            Full post display
    /blog/tag/<tag_slug>    Posts filtered by tag
    /blog/feed.xml          RSS 2.0 feed
"""

from html import escape

from flask import Blueprint, abort, make_response, render_template, request

from app.db import get_db
from app.models import get_setting
from app.services.blog import (
    get_post_by_slug,
    get_posts_by_tag,
    get_published_posts,
    get_tag_by_slug,
    get_tags_for_post,
    get_tags_for_posts,
    render_post_content,
)
from app.services.pagination import clamp_page, paginate
from app.services.translations import (
    get_available_post_locales,
    og_locale,
    overlay_post_translation,
    overlay_posts_translations,
)

blog_bp = Blueprint('blog', __name__, template_folder='../templates')


def _current_and_default_locale(db):
    """Return ``(current_locale, default_locale)`` for the active request."""
    from flask_babel import get_locale

    return str(get_locale()), get_setting(db, 'default_locale', 'en')


def _check_blog_enabled(db):
    """Abort with 404 if the blog feature is disabled."""
    if get_setting(db, 'blog_enabled', 'false') != 'true':
        abort(404)


def _attach_tags(db, posts):
    """Attach tag lists to each post for template rendering.

    Uses the batched `get_tags_for_posts` to fetch every post's tags in
    one query, replacing the prior O(N) call to `get_tags_for_post`.
    """
    if not posts:
        return []
    tags_by_post = get_tags_for_posts(db, [p['id'] for p in posts])
    return [{'post': p, 'tags': tags_by_post.get(p['id'], [])} for p in posts]


@blog_bp.route('/blog')
def blog_index():
    """Paginated list of published blog posts, newest first."""
    db = get_db()
    _check_blog_enabled(db)

    page = clamp_page(request.args.get('page', 1))
    per_page = int(get_setting(db, 'posts_per_page', '10'))

    posts, total = get_published_posts(db, page=page, per_page=per_page)
    locale, default = _current_and_default_locale(db)
    posts = overlay_posts_translations(db, posts, locale, default)
    pagination = paginate(page=page, per_page=per_page, total=total)

    blog_title = get_setting(db, 'blog_title', 'Blog')
    show_reading_time = get_setting(db, 'show_reading_time', 'true') == 'true'

    return render_template(
        'public/blog_index.html',
        posts=_attach_tags(db, posts),
        blog_title=blog_title,
        show_reading_time=show_reading_time,
        page=page,
        total_pages=pagination.total_pages,
    )


@blog_bp.route('/blog/<slug>')
def blog_post(slug):
    """Display a single published blog post."""
    db = get_db()
    _check_blog_enabled(db)

    post = get_post_by_slug(db, slug)
    if post is None:
        abort(404)

    locale, default = _current_and_default_locale(db)
    post_id = post['id']
    post = overlay_post_translation(db, post, locale, default)
    tags = get_tags_for_post(db, post_id)
    show_reading_time = get_setting(db, 'show_reading_time', 'true') == 'true'
    rendered_content = render_post_content(post)

    # Get prev/next posts for navigation
    prev_post = db.execute(
        "SELECT slug, title FROM blog_posts WHERE status='published' "
        'AND published_at < ? ORDER BY published_at DESC LIMIT 1',
        (post['published_at'],),
    ).fetchone()
    next_post = db.execute(
        "SELECT slug, title FROM blog_posts WHERE status='published' "
        'AND published_at > ? ORDER BY published_at ASC LIMIT 1',
        (post['published_at'],),
    ).fetchone()

    # Locale coverage for og:locale:alternate (Phase 15.4). The
    # template receives the short locale codes plus their OG-format
    # pair so Jinja doesn't have to re-derive the mapping.
    post_locales = get_available_post_locales(db, post_id, default)
    post_og_alternates = [og_locale(loc) for loc in post_locales if loc != locale]

    return render_template(
        'public/blog_post.html',
        post=post,
        rendered_content=rendered_content,
        tags=tags,
        show_reading_time=show_reading_time,
        prev_post=prev_post,
        next_post=next_post,
        post_locales=post_locales,
        post_og_alternates=post_og_alternates,
    )


@blog_bp.route('/blog/tag/<tag_slug>')
def blog_tag(tag_slug):
    """Display published posts filtered by a specific tag."""
    db = get_db()
    _check_blog_enabled(db)

    tag = get_tag_by_slug(db, tag_slug)
    if tag is None:
        abort(404)

    page = clamp_page(request.args.get('page', 1))
    per_page = int(get_setting(db, 'posts_per_page', '10'))

    posts, total = get_posts_by_tag(db, tag_slug, page=page, per_page=per_page)
    locale, default = _current_and_default_locale(db)
    posts = overlay_posts_translations(db, posts, locale, default)
    pagination = paginate(page=page, per_page=per_page, total=total)

    blog_title = get_setting(db, 'blog_title', 'Blog')
    show_reading_time = get_setting(db, 'show_reading_time', 'true') == 'true'

    return render_template(
        'public/blog_index.html',
        posts=_attach_tags(db, posts),
        blog_title=blog_title,
        show_reading_time=show_reading_time,
        page=page,
        total_pages=pagination.total_pages,
        active_tag=tag,
    )


@blog_bp.route('/blog/feed.xml')
def blog_feed():
    """Generate an RSS 2.0 feed of published blog posts.

    Phase 15.4: accepts an optional ``?lang=<code>`` query string for a
    locale-specific feed. Post titles / summaries fall through the
    translation overlay when the requested locale has entries; untranslated
    posts fall back to the default-locale content. The ``<language>``
    channel element reflects the resolved locale so clients know which
    translation they're consuming.
    """
    db = get_db()
    _check_blog_enabled(db)

    if get_setting(db, 'enable_rss', 'true') != 'true':
        abort(404)

    posts, _total = get_published_posts(db, page=1, per_page=20)
    default_locale = get_setting(db, 'default_locale', 'en')

    # Resolve the feed locale: ``?lang=<code>`` wins if present AND the
    # locale is configured; otherwise fall back to the site default so
    # operators don't have to worry about typo'd query strings.
    available = [
        loc.strip() for loc in get_setting(db, 'available_locales', 'en').split(',') if loc.strip()
    ]
    requested = request.args.get('lang', '').strip()
    feed_locale = requested if requested in available else default_locale
    posts = overlay_posts_translations(db, posts, feed_locale, default_locale)

    site_title = get_setting(db, 'site_title', 'Portfolio')
    blog_title = get_setting(db, 'blog_title', 'Blog')
    # Phase 23.5 (#57) — RSS readers cache the feed URL and every
    # inside-feed link for the lifetime of a subscription. A spoofed
    # Host header would permanently redirect subscribers to the wrong
    # origin; the canonical helper pins this to ``canonical_host``
    # when set.
    from app.services.urls import canonical_url_root

    base_url = canonical_url_root().rstrip('/')
    self_href = f'{base_url}/blog/feed.xml'
    if feed_locale != default_locale:
        self_href += f'?lang={feed_locale}'

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
    xml += '<channel>\n'
    xml += f'  <title>{escape(site_title)} — {escape(blog_title)}</title>\n'
    xml += f'  <link>{base_url}/blog</link>\n'
    xml += f'  <description>{escape(blog_title)}</description>\n'
    xml += f'  <language>{escape(feed_locale)}</language>\n'
    xml += f'  <atom:link href="{self_href}" rel="self" type="application/rss+xml"/>\n'

    for post in posts:
        xml += '  <item>\n'
        xml += f'    <title>{escape(post["title"])}</title>\n'
        xml += f'    <link>{base_url}/blog/{escape(post["slug"])}</link>\n'
        xml += f'    <guid isPermaLink="true">{base_url}/blog/{escape(post["slug"])}</guid>\n'
        if post['summary']:
            xml += f'    <description>{escape(post["summary"])}</description>\n'
        if post['published_at']:
            xml += f'    <pubDate>{escape(post["published_at"])}</pubDate>\n'
        if post['author']:
            xml += f'    <author>{escape(post["author"])}</author>\n'
        xml += '  </item>\n'

    xml += '</channel>\n'
    xml += '</rss>'

    response = make_response(xml)
    response.headers['Content-Type'] = 'application/rss+xml; charset=utf-8'
    return response
