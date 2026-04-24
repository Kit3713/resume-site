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
from app.events import Events, emit
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

        # Phase 27.5 (#13) — reject null bytes outright. They're never
        # legitimate in any free-text field; silently stripping
        # invites subtle bugs when the same string flows through SQL,
        # email headers, or filesystem operations elsewhere.
        if any('\x00' in s for s in (name, email, message)):
            flash(_('Invalid characters in input.'), 'error')
            return render_template('public/contact.html')

        # Phase 27.4 (#39) — a proper shape check.
        # ``"@" in email and "." in email`` accepts ``@.``, ``a@.``, ``a@a``.
        # This regex matches ``local@domain.tld`` with TLD ≥ 2 chars,
        # no consecutive dots, no leading/trailing dot on either side.
        import re as _re

        _EMAIL_RE = _re.compile(
            r'^[A-Za-z0-9._%+-]+(?<!\.)@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$'
        )
        if not _EMAIL_RE.match(email) or '..' in email:
            flash(_('Please enter a valid email address.'), 'error')
            return render_template('public/contact.html')

        # Rate limiting — resolve the real client IP via the central
        # helper (Phase 23.2). Before the extraction, this inlined a
        # blind X-Forwarded-For read that was spoofable on any direct-
        # exposure deployment (audit #34).
        from flask import current_app as _app

        from app.services.logging import classify_user_agent, hash_client_ip
        from app.services.request_ip import get_client_ip

        client_ip = get_client_ip(request)

        # Phase 24.2 (#60) — hash the client IP + discard the full UA
        # before either the rate-limit read or the DB write. The raw
        # IP never reaches contact_submissions.ip_address; the UA is
        # collapsed to a coarse browser+form class. The hash is stable
        # per-IP so the 5-per-window rate limit still works.
        ip_hash = hash_client_ip(client_ip or '', _app.secret_key or '')
        ua_class = classify_user_agent(request.user_agent.string)

        if count_recent_submissions(db, ip_hash) >= 5:
            flash(_('Too many submissions. Please try again later.'), 'error')
            return render_template('public/contact.html')

        # Persist to database (always, even for spam — for admin visibility)
        submission_id = save_contact_submission(
            db,
            name,
            email,
            message,
            ip_address=ip_hash,
            user_agent=ua_class,
            is_spam=is_spam,
        )

        # Phase 19.1 event bus — fire `contact.submitted` regardless of
        # spam flag so subscribers can choose to surface attack patterns.
        # Mirrors the API-side emission in app/routes/api.py:contact_submit
        # so a webhook subscriber sees the same shape regardless of
        # whether the submission came from the form or the JSON endpoint.
        emit(
            Events.CONTACT_SUBMITTED,
            submission_id=submission_id,
            is_spam=is_spam,
            source='public_form',
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
