import os

from flask import Blueprint, render_template, abort, send_from_directory, current_app

from app import get_db
from app.models import (
    get_content_block, get_visible_stats, get_visible_services,
    get_photos_by_tier, get_all_visible_photos, get_photo_categories,
    get_case_study_by_slug, get_approved_reviews_by_tier,
    get_all_approved_reviews, get_visible_projects, get_project_by_slug,
    get_visible_certifications, get_skill_domains_with_skills,
    get_setting,
)

public_bp = Blueprint('public', __name__, template_folder='../templates')


@public_bp.route('/')
def index():
    db = get_db()
    about_block = get_content_block(db, 'about')
    stats = get_visible_stats(db)
    services = get_visible_services(db)
    featured_photos = get_photos_by_tier(db, 'featured')[:3]
    featured_reviews = get_approved_reviews_by_tier(db, 'featured')[:3]
    return render_template('public/index.html',
                           about_block=about_block,
                           stats=stats,
                           services=services,
                           featured_photos=featured_photos,
                           featured_reviews=featured_reviews)


@public_bp.route('/portfolio')
def portfolio():
    db = get_db()
    featured = get_photos_by_tier(db, 'featured')
    photos = get_all_visible_photos(db)
    categories = get_photo_categories(db)
    return render_template('public/portfolio.html',
                           featured=featured,
                           photos=photos,
                           categories=categories)


@public_bp.route('/portfolio/<slug>')
def case_study(slug):
    db = get_db()
    if get_setting(db, 'case_studies_enabled', 'false') != 'true':
        abort(404)
    study = get_case_study_by_slug(db, slug)
    if study is None:
        abort(404)
    photo = None
    if study['photo_id']:
        photo = db.execute('SELECT * FROM photos WHERE id = ?', (study['photo_id'],)).fetchone()
    return render_template('public/case_study.html', study=study, photo=photo)


@public_bp.route('/services')
def services():
    db = get_db()
    service_list = get_visible_services(db)
    domains = get_skill_domains_with_skills(db)
    return render_template('public/services.html',
                           services=service_list,
                           domains=domains)


@public_bp.route('/testimonials')
def testimonials():
    db = get_db()
    featured = get_approved_reviews_by_tier(db, 'featured')
    standard = get_approved_reviews_by_tier(db, 'standard')
    display_mode = get_setting(db, 'testimonial_display_mode', 'mixed')
    return render_template('public/testimonials.html',
                           featured=featured,
                           standard=standard,
                           display_mode=display_mode)


@public_bp.route('/projects')
def projects():
    db = get_db()
    project_list = get_visible_projects(db)
    return render_template('public/projects.html', projects=project_list)


@public_bp.route('/projects/<slug>')
def project_detail(slug):
    db = get_db()
    project = get_project_by_slug(db, slug)
    if project is None:
        abort(404)
    return render_template('public/project_detail.html', project=project)


@public_bp.route('/certifications')
def certifications():
    db = get_db()
    cert_list = get_visible_certifications(db)
    return render_template('public/certifications.html', certifications=cert_list)


@public_bp.route('/resume')
def resume_download():
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


@public_bp.route('/photos/<storage_name>')
def serve_photo(storage_name):
    from app.services.photos import serve_photo as _serve
    return _serve(storage_name)
