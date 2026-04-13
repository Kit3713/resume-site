"""
Contact Form Route

Handles the public contact page with form submission, validation, spam
protection, rate limiting, and email relay.

Anti-spam measures:
- Honeypot field: A hidden "website" input that bots tend to fill. If
  populated, the submission is silently flagged as spam (saved to DB but
  no email sent) without revealing the detection to the bot.
- Short-window rate limit: Flask-Limiter caps POSTs at 10 per minute per IP
  to absorb burst abuse (returns 429).
- Long-window rate limit: A database count query caps submissions at
  5 per hour per IP to stop slow-and-steady spam that evades the burst limit.

All submissions are persisted to the contact_submissions table regardless
of spam status, giving the admin full visibility in the dashboard.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app import limiter
from app.db import get_db
from app.models import count_recent_submissions, get_setting, save_contact_submission

contact_bp = Blueprint('contact', __name__, template_folder='../templates')


@contact_bp.route('/contact', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'])
def contact_page():
    """Handle the contact page display and form submission.

    GET: Renders the contact form with the info sidebar.
    POST: Validates the submission, checks for spam, saves to database,
          and relays via SMTP if the submission is legitimate.
    """
    db = get_db()

    if request.method == 'POST':
        # Check if the contact form is enabled in site settings
        form_enabled = get_setting(db, 'contact_form_enabled', 'true')
        if form_enabled != 'true':
            flash(_('Contact form is currently unavailable.'), 'error')
            return redirect(url_for('contact.contact_page'))

        # Extract form data
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        message = request.form.get('message', '').strip()
        honeypot = request.form.get('website', '').strip()

        # Honeypot check — the "website" field is hidden via CSS;
        # legitimate users never see or fill it, but bots do
        is_spam = bool(honeypot)

        # Server-side validation
        if not name or not email or not message:
            flash(_('Please fill in all required fields.'), 'error')
            return render_template('public/contact.html')

        if '@' not in email or '.' not in email:
            flash(_('Please enter a valid email address.'), 'error')
            return render_template('public/contact.html')

        # Rate limiting — extract the real client IP from the proxy chain
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip and ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()

        if count_recent_submissions(db, client_ip) >= 5:
            flash(_('Too many submissions. Please try again later.'), 'error')
            return render_template('public/contact.html')

        # Persist to database (always, even for spam — for admin visibility)
        save_contact_submission(
            db,
            name,
            email,
            message,
            ip_address=client_ip,
            user_agent=request.user_agent.string,
            is_spam=is_spam,
        )

        # Relay via email (only for legitimate, non-spam submissions)
        if not is_spam:
            from app.services.mail import send_contact_email

            send_contact_email(name, email, message)

        # Show the same success message for both spam and real submissions
        # to avoid revealing the honeypot detection to bots
        flash(_("Message sent successfully! I'll get back to you soon."), 'success')
        return redirect(url_for('contact.contact_page'))

    return render_template('public/contact.html')
