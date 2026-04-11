import ipaddress

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

from app.models import AdminUser

admin_bp = Blueprint('admin', __name__, template_folder='../templates')


@admin_bp.before_request
def restrict_to_allowed_networks():
    """Block admin access from IPs outside configured allowed networks."""
    config = current_app.config['SITE_CONFIG']
    allowed = config.get('admin', {}).get('allowed_networks', [])

    if not allowed:
        return

    # Trust X-Forwarded-For when behind a reverse proxy (Caddy)
    client_ip_str = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip_str and ',' in client_ip_str:
        client_ip_str = client_ip_str.split(',')[0].strip()

    try:
        client_ip = ipaddress.ip_address(client_ip_str)
    except (ValueError, TypeError):
        abort(403)

    for network_str in allowed:
        try:
            network = ipaddress.ip_network(network_str, strict=False)
            if client_ip in network:
                return
        except ValueError:
            continue

    abort(403)


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        config = current_app.config['SITE_CONFIG']
        admin_config = config.get('admin', {})
        username = request.form.get('username', '')
        password = request.form.get('password', '')

        if (
            username == admin_config.get('username', 'admin')
            and admin_config.get('password_hash')
            and check_password_hash(admin_config['password_hash'], password)
        ):
            user = AdminUser(username)
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin.dashboard'))

        flash('Invalid credentials.', 'error')

    return render_template('admin/login.html')


@admin_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('public.index'))


@admin_bp.route('/')
@login_required
def dashboard():
    return render_template('admin/dashboard.html')
