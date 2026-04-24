"""
SMTP Email Relay Service

Sends contact form submissions to the site owner's personal email via SMTP.
The visitor's email address is set as the Reply-To header so the owner can
respond directly without exposing their address on the website.

Supports two connection modes based on the configured port:
- Port 465: SMTP_SSL (implicit TLS)
- Port 587: SMTP with STARTTLS (explicit TLS, default)

SMTP credentials are read from config.yaml at runtime. If any required
field is missing (host, user, password, recipient), the function returns
False without attempting a connection.

Note: This is a synchronous call that may take 2-5 seconds. For a personal
portfolio with low contact volume, this is acceptable. The contact route
saves to the database first, so no submission is lost even if SMTP fails.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from flask import current_app


def _contains_header_injection(value: str) -> bool:
    """Return True if ``value`` contains a byte that can split a header.

    Phase 23.5 (#35). RFC 5322 separates headers with CRLF; an attacker
    who can inject a ``\\r\\n`` sequence into a value that ends up as
    ``msg['Subject']`` or ``msg['Reply-To']`` can append arbitrary
    headers (notably ``Bcc:``) to the outbound message and silently
    exfiltrate every contact submission to a third-party address.

    We also reject the bare ``\\n`` variant because some SMTP servers /
    libraries normalise LF to CRLF, and null bytes as belt-and-braces
    against any transport that treats them as terminators.
    """
    if value is None:
        return False
    return any(ch in value for ch in ('\r', '\n', '\0'))


def send_contact_email(name: str, email: str, message: str) -> bool:
    """Send a contact form submission to the admin via SMTP.

    Constructs a plain-text email with the submitter's details and sends
    it using the SMTP configuration from config.yaml.

    Args:
        name: The submitter's name.
        email: The submitter's email (set as Reply-To header).
        message: The message body from the contact form.

    Returns:
        bool: True if the email was sent successfully, False otherwise.
            Returns False on SMTP failure AND on header-injection
            attempts — a tampered submission is never forwarded.
    """
    # Read SMTP configuration from the app's YAML config
    config = current_app.config['SITE_CONFIG'].get('smtp', {})
    host = config.get('host', '')
    port = config.get('port', 587)
    user = config.get('user', '')
    password = config.get('password', '')
    recipient = config.get('recipient', '')
    # Decouple the ``From`` header from the SMTP login identity. Needed
    # for relay providers (Resend, SendGrid, Mailgun) that authenticate
    # as a fixed service user but require ``From`` to be an operator-
    # controlled verified-domain address. Falls back to ``user`` when
    # unset — identical to the pre-v0.3.1-beta-2 behavior.
    from_address = config.get('from_address') or user

    # Bail out if SMTP is not fully configured
    if not all([host, user, password, recipient]):
        return False

    # Phase 23.5 (#35) — reject any submitted name / email containing a
    # header-splitting byte before it reaches the MIMEMultipart
    # constructor. ``email.policy`` does sanitise in modern Python, but
    # belt-and-braces is cheap and makes the threat model explicit at
    # the boundary. The contact route already records the raw
    # submission in SQLite via ``save_contact_submission`` before this
    # function runs, so admin visibility is preserved even when the
    # email delivery is suppressed.
    if _contains_header_injection(name) or _contains_header_injection(email):
        return False

    # Compose the email
    msg = MIMEMultipart()
    msg['From'] = from_address
    msg['To'] = recipient
    # ``formataddr`` quotes the display name if needed and is the
    # library-blessed way to attach a Reply-To with a submitter's
    # address — safer than interpolating the raw string.
    msg['Reply-To'] = formataddr((name, email))
    msg['Subject'] = f'New Contact: {name}'

    body = f'Name: {name}\nEmail: {email}\n\n---\n\n{message}'
    msg.attach(MIMEText(body, 'plain'))

    # Send via SMTP with appropriate TLS method
    try:
        if port == 465:
            # Implicit TLS (SMTP_SSL)
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            # Explicit TLS (STARTTLS) — standard for port 587
            server = smtplib.SMTP(host, port, timeout=10)
            server.starttls()
        server.login(user, password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as exc:  # noqa: BLE001 — any SMTP failure is a relay problem
        # Phase 27.3 (#23) — surface the failure at WARNING so operators
        # tailing the logs see that contact emails aren't going out.
        # Previously this returned False silently; the only way to
        # notice was to realise no emails had arrived and go hunting.
        # We log the exception TYPE only (not the message body) so a
        # server-side detail leak in the SMTP error string doesn't
        # flow into logs aggregators. The submission is already saved
        # to ``contact_submissions`` by the route, so no data is lost.
        import logging

        logging.getLogger('app.mail').warning(
            'SMTP delivery failed: %s (host=%s port=%s)',
            type(exc).__name__,
            host,
            port,
        )
        return False
