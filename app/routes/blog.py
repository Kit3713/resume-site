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

import math
from xml.sax.saxutils import escape

from flask import Blueprint, render_template, abort, request, make_response

from app.db import get_db
from app.models import get_setting
from app.services.blog import (
    get_published_posts, get_post_by_slug, get_posts_by_tag,
    get_tags_for_post, get_tag_by_slug,
    render_post_content,
)

blog_bp = Blueprint('blog', __name__, template_folder='../templates')


def _check_blog_enabled(db):
    """Abort with 404 if the blog feature is disabled."""
    if get_setting(db, 'blog_enabled', 'false') != 'true':
        abort(404)


def _attach_tags(db, posts):
    """Attach tag lists to each post for template rendering."""
    return [{'post': p, 'tags': get_tags_for_post(db, p['id'])} for p in posts]


@blog_bp.route('/blog')
def blog_index():
    """Paginated list of published blog posts, newest first."""
    db = get_db()
    _check_blog_enabled(db)

    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    per_page = int(get_setting(db, 'posts_per_page', '10'))

    posts, total = get_published_posts(db, page=page, per_page=per_page)
    total_pages = max(1, math.ceil(total / per_page))

    blog_title = get_setting(db, 'blog_title', 'Blog')
    show_reading_time = get_setting(db, 'show_reading_time', 'true') == 'true'

    return render_template('public/blog_index.html',
                           posts=_attach_tags(db, posts),
                           blog_title=blog_title,
                           show_reading_time=show_reading_time,
                           page=page,
                           total_pages=total_pages)


@blog_bp.route('/blog/<slug>')
def blog_post(slug):
    """Display a single published blog post."""
    db = get_db()
    _check_blog_enabled(db)

    post = get_post_by_slug(db, slug)
    if post is None:
        abort(404)

    tags = get_tags_for_post(db, post['id'])
    show_reading_time = get_setting(db, 'show_reading_time', 'true') == 'true'
    rendered_content = render_post_content(post)

    # Get prev/next posts for navigation
    prev_post = db.execute(
        "SELECT slug, title FROM blog_posts WHERE status='published' "
        "AND published_at < ? ORDER BY published_at DESC LIMIT 1",
        (post['published_at'],),
    ).fetchone()
    next_post = db.execute(
        "SELECT slug, title FROM blog_posts WHERE status='published' "
        "AND published_at > ? ORDER BY published_at ASC LIMIT 1",
        (post['published_at'],),
    ).fetchone()

    return render_template('public/blog_post.html',
                           post=post,
                           rendered_content=rendered_content,
                           tags=tags,
                           show_reading_time=show_reading_time,
                           prev_post=prev_post,
                           next_post=next_post)


@blog_bp.route('/blog/tag/<tag_slug>')
def blog_tag(tag_slug):
    """Display published posts filtered by a specific tag."""
    db = get_db()
    _check_blog_enabled(db)

    tag = get_tag_by_slug(db, tag_slug)
    if tag is None:
        abort(404)

    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    per_page = int(get_setting(db, 'posts_per_page', '10'))

    posts, total = get_posts_by_tag(db, tag_slug, page=page, per_page=per_page)
    total_pages = max(1, math.ceil(total / per_page))

    blog_title = get_setting(db, 'blog_title', 'Blog')
    show_reading_time = get_setting(db, 'show_reading_time', 'true') == 'true'

    return render_template('public/blog_index.html',
                           posts=_attach_tags(db, posts),
                           blog_title=blog_title,
                           show_reading_time=show_reading_time,
                           page=page,
                           total_pages=total_pages,
                           active_tag=tag)


@blog_bp.route('/blog/feed.xml')
def blog_feed():
    """Generate an RSS 2.0 feed of published blog posts."""
    db = get_db()
    _check_blog_enabled(db)

    if get_setting(db, 'enable_rss', 'true') != 'true':
        abort(404)

    posts, _ = get_published_posts(db, page=1, per_page=20)
    site_title = get_setting(db, 'site_title', 'Portfolio')
    blog_title = get_setting(db, 'blog_title', 'Blog')
    base_url = request.url_root.rstrip('/')

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
    xml += '<channel>\n'
    xml += f'  <title>{escape(site_title)} — {escape(blog_title)}</title>\n'
    xml += f'  <link>{base_url}/blog</link>\n'
    xml += f'  <description>{escape(blog_title)}</description>\n'
    xml += f'  <atom:link href="{base_url}/blog/feed.xml" rel="self" type="application/rss+xml"/>\n'

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
