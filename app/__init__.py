import os
import sqlite3

from flask import Flask, g
from flask_login import LoginManager

from app.services.config import load_config


def get_db():
    """Get database connection for the current request."""
    if 'db' not in g:
        g.db = sqlite3.connect(g.db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA busy_timeout=5000')
    return g.db


def create_app(config_path=None):
    """Application factory."""
    app = Flask(__name__)

    # Load YAML config
    if config_path is None:
        config_path = os.environ.get(
            'RESUME_SITE_CONFIG',
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml'),
        )
    site_config = load_config(config_path)

    app.secret_key = site_config['secret_key']
    app.config['DATABASE_PATH'] = site_config.get('database_path', 'data/site.db')
    app.config['PHOTO_STORAGE'] = site_config.get('photo_storage', 'photos')
    app.config['SITE_CONFIG'] = site_config

    # Database connection management
    @app.before_request
    def before_request():
        g.db_path = app.config['DATABASE_PATH']

    @app.teardown_appcontext
    def close_db(exception):
        db = g.pop('db', None)
        if db is not None:
            db.close()

    # Flask-Login
    login_manager = LoginManager()
    login_manager.login_view = 'admin.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import AdminUser
        admin_username = site_config.get('admin', {}).get('username', 'admin')
        if user_id == admin_username:
            return AdminUser(user_id)
        return None

    # Register blueprints
    from app.routes.public import public_bp
    from app.routes.admin import admin_bp
    from app.routes.contact import contact_bp
    from app.routes.review import review_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(contact_bp)
    app.register_blueprint(review_bp)

    # Analytics middleware
    from app.services.analytics import track_page_view
    app.before_request(track_page_view)

    # Template context processor — inject settings into all templates
    @app.context_processor
    def inject_settings():
        try:
            db = get_db()
            rows = db.execute('SELECT key, value FROM settings').fetchall()
            settings = {row['key']: row['value'] for row in rows}
        except Exception:
            settings = {}
        return dict(site_settings=settings, site_config=site_config)

    # Ensure storage directories exist
    os.makedirs(os.path.dirname(app.config['DATABASE_PATH']) or '.', exist_ok=True)
    os.makedirs(app.config['PHOTO_STORAGE'], exist_ok=True)

    return app
