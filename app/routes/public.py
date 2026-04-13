"""
Public-Facing Routes

Handles all visitor-accessible pages including the landing page, portfolio
gallery, services, projects, testimonials, certifications, resume download,
photo serving, and SEO endpoints (sitemap.xml, robots.txt).

All data is read from SQLite and rendered through Jinja2 templates. Pages
gracefully handle empty states when no content has been added yet — the
templates show "coming soon" placeholders instead of breaking.

URL structure:
    /                       Landing page (hero, about, stats, featured content)
    /portfolio              Photo gallery with masonry grid and category filters
    /portfolio/<slug>       Case study detail page (problem/solution/result)
    /services               Service cards and expandable skill accordion
    /projects               Project cards with tech stack tags
    /projects/<slug>        Detailed project write-up
    /testimonials           Featured and standard review display
    /certifications         Professional certification badges
    /resume                 PDF resume download (visibility-controlled)
    /photos/<storage_name>  Serve uploaded photos from storage directory
    /sitemap.xml            Auto-generated XML sitemap for search engines
    /robots.txt             Crawler directives
"""

import os

from flask import (
    Blueprint,
    abort,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
)

from app.db import get_db
from app.models import (
    get_all_visible_photos,
    get_approved_reviews_by_tier,
    get_case_study_by_slug,
    get_content_block,
    get_photo_categories,
    get_photos_by_tier,
    get_project_by_slug,
    get_setting,
    get_skill_domains_with_skills,
    get_visible_certifications,
    get_visible_projects,
    get_visible_services,
    get_visible_stats,
)
from app.services.blog import get_featured_posts

public_bp = Blueprint('public', __name__, template_folder='../templates')


# ============================================================
# LANDING PAGE
# ============================================================


@public_bp.route('/')
def index():
    """Render the landing page with all scroll sections.

    Aggregates data from multiple tables for the single-page landing
    experience: about content, stats counters, services preview,
    featured portfolio items, and featured testimonials.
    """
    db = get_db()
    about_block = get_content_block(db, 'about')
    stats = get_visible_stats(db)
    services = get_visible_services(db)
    featured_photos = get_photos_by_tier(db, 'featured')[:3]  # Top 3 featured photos
    featured_reviews = get_approved_reviews_by_tier(db, 'featured')[:3]  # Top 3 featured reviews

    # Featured blog posts (only when blog is enabled)
    featured_blog_posts = []
    if get_setting(db, 'blog_enabled', 'false') == 'true':
        featured_blog_posts = get_featured_posts(db, n=3)

    return render_template(
        'public/index.html',
        about_block=about_block,
        stats=stats,
        services=services,
        featured_photos=featured_photos,
        featured_reviews=featured_reviews,
        featured_blog_posts=featured_blog_posts,
    )


# ============================================================
# PORTFOLIO
# ============================================================


@public_bp.route('/portfolio')
def portfolio():
    """Render the full portfolio gallery page.

    Displays photos in a CSS masonry grid with three-tier interaction:
    - No metadata: Click to enlarge in lightbox.
    - Has caption/description: Hover reveals overlay with info.
    - Has case study: Overlay includes link to the case study page.

    Featured photos are displayed large at the top, with category
    filter buttons for client-side filtering.
    """
    db = get_db()
    featured = get_photos_by_tier(db, 'featured')
    photos = get_all_visible_photos(db)
    categories = get_photo_categories(db)
    return render_template(
        'public/portfolio.html', featured=featured, photos=photos, categories=categories
    )


@public_bp.route('/portfolio/<slug>')
def case_study(slug):
    """Render a case study detail page (problem/solution/result format).

    Guarded by the 'case_studies_enabled' setting — returns 404 if the
    feature is disabled globally, or if the specific case study doesn't
    exist or isn't published.
    """
    db = get_db()
    if get_setting(db, 'case_studies_enabled', 'false') != 'true':
        abort(404)
    study = get_case_study_by_slug(db, slug)
    if study is None:
        abort(404)
    # Optionally load the associated photo for the hero image
    photo = None
    if study['photo_id']:
        photo = db.execute('SELECT * FROM photos WHERE id = ?', (study['photo_id'],)).fetchone()
    return render_template('public/case_study.html', study=study, photo=photo)


# ============================================================
# SERVICES & SKILLS
# ============================================================


@public_bp.route('/services')
def services():
    """Render the services page with skill domain accordion.

    Shows service cards at the top and expandable skill domains below.
    Each domain expands to reveal individual skills with experience
    details and tool tags.
    """
    db = get_db()
    service_list = get_visible_services(db)
    domains = get_skill_domains_with_skills(db)
    return render_template('public/services.html', services=service_list, domains=domains)


# ============================================================
# TESTIMONIALS
# ============================================================


@public_bp.route('/testimonials')
def testimonials():
    """Render the testimonials page with featured and standard tiers.

    Display mode (from settings) controls how reviews are grouped:
    - 'mixed': Recommendations and client reviews shown together with labels.
    - 'separate': Grouped into separate sections by type.
    - 'all': All reviews shown together without type distinction.
    """
    db = get_db()
    featured = get_approved_reviews_by_tier(db, 'featured')
    standard = get_approved_reviews_by_tier(db, 'standard')
    display_mode = get_setting(db, 'testimonial_display_mode', 'mixed')
    return render_template(
        'public/testimonials.html', featured=featured, standard=standard, display_mode=display_mode
    )


# ============================================================
# PROJECTS
# ============================================================


@public_bp.route('/projects')
def projects():
    """Render the projects listing page."""
    db = get_db()
    project_list = get_visible_projects(db)
    return render_template('public/projects.html', projects=project_list)


@public_bp.route('/projects/<slug>')
def project_detail(slug):
    """Render a detailed project page.

    Only projects with has_detail_page=1 and visible=1 are accessible.
    Others return 404 — their cards link directly to the GitHub URL instead.
    """
    db = get_db()
    project = get_project_by_slug(db, slug)
    if project is None:
        abort(404)
    return render_template('public/project_detail.html', project=project)


# ============================================================
# CERTIFICATIONS
# ============================================================


@public_bp.route('/certifications')
def certifications():
    """Render the certifications page with badge cards."""
    db = get_db()
    cert_list = get_visible_certifications(db)
    return render_template('public/certifications.html', certifications=cert_list)


# ============================================================
# RESUME DOWNLOAD
# ============================================================


@public_bp.route('/resume')
def resume_download():
    """Serve the resume PDF as a download.

    Controlled by the 'resume_visibility' setting:
    - 'public': Anyone can download.
    - 'private': Accessible via direct link only (no nav link shown).
    - 'off': Returns 404.

    The PDF must be placed at uploads/resume.pdf (uploaded via admin in
    a future enhancement, or manually placed on the server).
    """
    db = get_db()
    visibility = get_setting(db, 'resume_visibility', 'off')
    if visibility == 'off':
        abort(404)
    upload_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'uploads',
    )
    if not os.path.exists(os.path.join(upload_dir, 'resume.pdf')):
        abort(404)
    return send_from_directory(upload_dir, 'resume.pdf', as_attachment=True)


# ============================================================
# PHOTO SERVING
# ============================================================


@public_bp.route('/photos/<storage_name>')
def serve_photo(storage_name):
    """Serve an uploaded photo from the storage directory.

    Delegates to the photos service which handles path resolution
    and 404 responses for missing files.
    """
    from app.services.photos import serve_photo as _serve

    return _serve(storage_name)


# ============================================================
# SEO ENDPOINTS
# ============================================================


@public_bp.route('/sitemap.xml')
def sitemap():
    """Generate an XML sitemap dynamically from active pages.

    Includes all static public pages plus dynamically-generated pages
    (project detail pages, case study pages). Priority values indicate
    relative importance to search engines (1.0 = most important).
    """
    db = get_db()
    base_url = request.url_root.rstrip('/')

    # Static pages with their SEO priority
    pages = [
        ('/', '1.0'),
        ('/portfolio', '0.9'),
        ('/services', '0.8'),
        ('/projects', '0.8'),
        ('/testimonials', '0.7'),
        ('/certifications', '0.7'),
        ('/contact', '0.8'),
    ]

    # Add project detail pages dynamically
    projects = get_visible_projects(db)
    for p in projects:
        if p['has_detail_page']:
            pages.append((f'/projects/{p["slug"]}', '0.6'))

    # Add case study pages if the feature is enabled
    if get_setting(db, 'case_studies_enabled', 'false') == 'true':
        studies = db.execute('SELECT slug FROM case_studies WHERE published = 1').fetchall()
        for s in studies:
            pages.append((f'/portfolio/{s["slug"]}', '0.6'))

    # Add blog pages if the blog is enabled
    if get_setting(db, 'blog_enabled', 'false') == 'true':
        pages.append(('/blog', '0.8'))
        blog_posts = db.execute("SELECT slug FROM blog_posts WHERE status = 'published'").fetchall()
        for bp in blog_posts:
            pages.append((f'/blog/{bp["slug"]}', '0.6'))

    # Build the XML response
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for path, priority in pages:
        xml += f'  <url><loc>{base_url}{path}</loc><priority>{priority}</priority></url>\n'
    xml += '</urlset>'

    response = make_response(xml)
    response.headers['Content-Type'] = 'application/xml'
    return response


@public_bp.route('/robots.txt')
def robots():
    """Serve robots.txt with crawler directives.

    Allows all crawlers on public pages, blocks /admin routes,
    and points to the sitemap for discovery.
    """
    base_url = request.url_root.rstrip('/')
    txt = f'User-agent: *\nAllow: /\nDisallow: /admin\nSitemap: {base_url}/sitemap.xml\n'
    response = make_response(txt)
    response.headers['Content-Type'] = 'text/plain'
    return response


@public_bp.route('/healthz')
def healthz():
    """Lightweight health check endpoint for container orchestration.

    Returns a simple JSON response without hitting the database or
    rendering templates. Used by Podman/Docker HEALTHCHECK and
    load balancers.
    """
    return jsonify(status='ok'), 200
