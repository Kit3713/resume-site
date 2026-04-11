from flask import Blueprint, render_template, request, redirect, url_for, flash

from app import get_db
from app.models import save_contact_submission, count_recent_submissions, get_setting

contact_bp = Blueprint('contact', __name__, template_folder='../templates')


@contact_bp.route('/contact', methods=['GET', 'POST'])
def contact_page():
    db = get_db()

    if request.method == 'POST':
        form_enabled = get_setting(db, 'contact_form_enabled', 'true')
        if form_enabled != 'true':
            flash('Contact form is currently unavailable.', 'error')
            return redirect(url_for('contact.contact_page'))

        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        message = request.form.get('message', '').strip()
        honeypot = request.form.get('website', '').strip()

        # Honeypot check — bots fill the hidden field
        is_spam = bool(honeypot)

        # Basic validation
        if not name or not email or not message:
            flash('Please fill in all required fields.', 'error')
            return render_template('public/contact.html')

        if '@' not in email or '.' not in email:
            flash('Please enter a valid email address.', 'error')
            return render_template('public/contact.html')

        # Rate limiting
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip and ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()

        if count_recent_submissions(db, client_ip) >= 5:
            flash('Too many submissions. Please try again later.', 'error')
            return render_template('public/contact.html')

        # Save to database
        save_contact_submission(
            db, name, email, message,
            ip_address=client_ip,
            user_agent=request.user_agent.string,
            is_spam=is_spam,
        )

        # Send email (only if not spam)
        if not is_spam:
            from app.services.mail import send_contact_email
            send_contact_email(name, email, message)

        flash('Message sent successfully! I\'ll get back to you soon.', 'success')
        return redirect(url_for('contact.contact_page'))

    return render_template('public/contact.html')
