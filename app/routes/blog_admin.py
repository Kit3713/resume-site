"""
Blog Admin Routes

Provides content management for the blog engine: post listing with status
filters, a rich text/markdown editor, publish/unpublish workflow, and
tag management.

These routes are registered under /admin/blog and share the same IP
restriction and authentication requirements as the main admin panel
(inherited from the admin blueprint's before_request hooks since this
blueprint is registered with the /admin prefix).
"""

import contextlib

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.db import get_db
from app.events import Events, emit
from app.routes.admin import (
    check_session_epoch,
    check_session_timeout,
    restrict_to_allowed_networks,
    update_last_activity,
)
from app.services.activity_log import log_action
from app.services.blog import (
    archive_post,
    create_post,
    delete_post,
    get_all_posts,
    get_post_by_id,
    get_tags_for_post,
    publish_post,
    unpublish_post,
    update_post,
)


def _blog_event_payload(post_row, *, source):
    """Build the canonical payload for a blog.* event.

    Centralised so the new / edit / delete / publish / unpublish paths
    all emit the same shape — keeps webhook subscribers from having to
    branch on source.
    """
    return {
        'post_id': post_row['id'],
        'slug': post_row['slug'],
        'title': post_row['title'],
        'status': post_row['status'],
        'source': source,
    }


blog_admin_bp = Blueprint('blog_admin', __name__, template_folder='../templates')

# Share the same security middleware as the main admin blueprint.
# Phase 23.1 (#51) — check_session_epoch MUST be registered here too;
# the parent admin_bp's before_request hooks do not fire for requests
# routed to a sibling blueprint, so omitting it leaves a captured
# cookie valid after logout for every route under this blueprint.
# The `test_admin_blueprint_middleware_parity` regression test in
# tests/test_admin.py asserts this set stays aligned with admin_bp.
blog_admin_bp.before_request(restrict_to_allowed_networks)
blog_admin_bp.before_request(check_session_timeout)
blog_admin_bp.before_request(check_session_epoch)
blog_admin_bp.after_request(update_last_activity)


@blog_admin_bp.route('/blog')
@login_required
def blog_list():
    """List all blog posts with optional status filter."""
    db = get_db()
    status_filter = request.args.get('status')
    if status_filter and status_filter not in ('draft', 'published', 'archived'):
        status_filter = None
    posts = get_all_posts(db, status_filter)
    return render_template('admin/blog_list.html', posts=posts, status_filter=status_filter)


@blog_admin_bp.route('/blog/new', methods=['GET', 'POST'])
@login_required
def blog_new():
    """Create a new blog post."""
    if request.method == 'POST':
        db = get_db()
        title = request.form.get('title', '').strip()
        if not title:
            flash(_('Title is required.'), 'error')
            return render_template('admin/blog_edit.html', post=None, tags_str='')

        # Phase 27.4 (#24) — validate content_format on the HTML admin
        # path, matching the API path's existing check. Before this
        # fix, an attacker-crafted form could stuff any string into
        # the column; the rendering path falls back to html but the
        # value was never constrained at write time.
        content_format = request.form.get('content_format', 'html') or 'html'
        if content_format not in ('html', 'markdown'):
            flash(_('Invalid content_format — must be "html" or "markdown".'), 'error')
            return render_template('admin/blog_edit.html', post=None, tags_str='')

        post_id = create_post(
            db,
            title=title,
            summary=request.form.get('summary', ''),
            content=request.form.get('content', ''),
            content_format=content_format,
            cover_image=request.form.get('cover_image', ''),
            author=request.form.get('author', ''),
            tags=request.form.get('tags', ''),
            meta_description=request.form.get('meta_description', ''),
            featured=bool(request.form.get('featured')),
        )

        action = request.form.get('action', 'save')
        if action == 'publish':
            publish_post(db, post_id)
            flash(_('Post published.'), 'success')
            # Activity-log entry now fires from the BLOG_PUBLISHED subscriber
            # in app.services.event_subscribers (Phase 36.7).
        else:
            flash(_('Draft saved.'), 'success')
            # The 'Created draft' direct log was not migrated (the
            # BLOG_UPDATED subscriber only logs the status='deleted' case).
            # Preserving the pre-36.7 behaviour for save-as-draft keeps
            # the activity feed readable for a single-admin deployment.
            with contextlib.suppress(Exception):
                log_action(db, 'Created draft', 'blog', title)

        # Phase 19.1 event bus — re-read the post so the payload reflects
        # the post-publish status. blog.published fires on the publish
        # path, blog.updated on save-as-draft. Mirrors api.blog_create.
        post_row = get_post_by_id(db, post_id)
        if post_row is not None:
            emit(
                Events.BLOG_PUBLISHED if action == 'publish' else Events.BLOG_UPDATED,
                **_blog_event_payload(post_row, source='admin_ui'),
            )

        return redirect(url_for('blog_admin.blog_edit', post_id=post_id))

    return render_template('admin/blog_edit.html', post=None, tags_str='')


@blog_admin_bp.route('/blog/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def blog_edit(post_id):
    """Edit an existing blog post."""
    db = get_db()
    post = get_post_by_id(db, post_id)
    if not post:
        flash(_('Post not found.'), 'error')
        return redirect(url_for('blog_admin.blog_list'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash(_('Title is required.'), 'error')
            tags = get_tags_for_post(db, post_id)
            tags_str = ', '.join(t['name'] for t in tags)
            return render_template('admin/blog_edit.html', post=post, tags_str=tags_str)

        # Phase 27.4 (#24) — same content_format validation on edit.
        content_format = request.form.get('content_format', 'html') or 'html'
        if content_format not in ('html', 'markdown'):
            flash(_('Invalid content_format — must be "html" or "markdown".'), 'error')
            return render_template('admin/blog_edit.html', post=post, tags_str=tags_str)

        update_post(
            db,
            post_id=post_id,
            title=title,
            summary=request.form.get('summary', ''),
            content=request.form.get('content', ''),
            content_format=content_format,
            cover_image=request.form.get('cover_image', ''),
            author=request.form.get('author', ''),
            tags=request.form.get('tags', ''),
            meta_description=request.form.get('meta_description', ''),
            featured=bool(request.form.get('featured')),
            slug=request.form.get('slug', ''),
        )

        action = request.form.get('action', 'save')
        if action == 'publish':
            publish_post(db, post_id)
            flash(_('Post published.'), 'success')
        elif action == 'unpublish':
            unpublish_post(db, post_id)
            flash(_('Post reverted to draft.'), 'success')
        elif action == 'archive':
            archive_post(db, post_id)
            flash(_('Post archived.'), 'success')
        else:
            flash(_('Post saved.'), 'success')

        # Phase 19.1 event bus — re-read so the status field is current.
        # publish → blog.published; everything else (including archive)
        # → blog.updated. Mirrors api.blog_update / api.blog_publish.
        post_row = get_post_by_id(db, post_id)
        if post_row is not None:
            emit(
                Events.BLOG_PUBLISHED if action == 'publish' else Events.BLOG_UPDATED,
                **_blog_event_payload(post_row, source='admin_ui'),
            )

        return redirect(url_for('blog_admin.blog_edit', post_id=post_id))

    tags = get_tags_for_post(db, post_id)
    tags_str = ', '.join(t['name'] for t in tags)
    return render_template('admin/blog_edit.html', post=post, tags_str=tags_str)


@blog_admin_bp.route('/blog/<int:post_id>/delete', methods=['POST'])
@login_required
def blog_delete(post_id):
    """Permanently delete a blog post."""
    db = get_db()
    post = get_post_by_id(db, post_id)
    # Snapshot identifying fields BEFORE the delete so the event payload
    # can carry them — the row will be gone by the time we emit.
    payload = (
        {
            'post_id': post['id'],
            'slug': post['slug'],
            'title': post['title'],
            'status': 'deleted',
            'source': 'admin_ui',
        }
        if post is not None
        else None
    )

    delete_post(db, post_id)
    # The activity-log entry now fires from the BLOG_UPDATED subscriber
    # (Phase 36.7), which branches on ``status='deleted'`` to emit the
    # 'Deleted post' action.

    # Phase 19.1 event bus — fire `blog.updated` with status='deleted'
    # (mirrors api.blog_delete) so subscribers can distinguish a real
    # deletion from any other update.
    if payload is not None:
        emit(Events.BLOG_UPDATED, **payload)

    flash(_('Post deleted.'), 'success')
    return redirect(url_for('blog_admin.blog_list'))
