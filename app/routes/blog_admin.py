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
from app.routes.admin import (
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

blog_admin_bp = Blueprint('blog_admin', __name__, template_folder='../templates')

# Share the same security middleware as the main admin blueprint
blog_admin_bp.before_request(restrict_to_allowed_networks)
blog_admin_bp.before_request(check_session_timeout)
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

        post_id = create_post(
            db,
            title=title,
            summary=request.form.get('summary', ''),
            content=request.form.get('content', ''),
            content_format=request.form.get('content_format', 'html'),
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
            with contextlib.suppress(Exception):
                log_action(db, 'Published post', 'blog', title)
        else:
            flash(_('Draft saved.'), 'success')
            with contextlib.suppress(Exception):
                log_action(db, 'Created draft', 'blog', title)

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

        update_post(
            db,
            post_id=post_id,
            title=title,
            summary=request.form.get('summary', ''),
            content=request.form.get('content', ''),
            content_format=request.form.get('content_format', 'html'),
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
    detail = post['title'] if post else f'ID {post_id}'
    delete_post(db, post_id)
    with contextlib.suppress(Exception):
        log_action(db, 'Deleted post', 'blog', detail)
    flash(_('Post deleted.'), 'success')
    return redirect(url_for('blog_admin.blog_list'))
