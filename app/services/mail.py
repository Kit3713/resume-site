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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import current_app


def send_contact_email(name, email, message):
    """Send a contact form submission to the admin via SMTP.

    Constructs a plain-text email with the submitter's details and sends
    it using the SMTP configuration from config.yaml.

    Args:
        name: The submitter's name.
        email: The submitter's email (set as Reply-To header).
        message: The message body from the contact form.

    Returns:
        bool: True if the email was sent successfully, False otherwise.
    """
    # Read SMTP configuration from the app's YAML config
    config = current_app.config['SITE_CONFIG'].get('smtp', {})
    host = config.get('host', '')
    port = config.get('port', 587)
    user = config.get('user', '')
    password = config.get('password', '')
    recipient = config.get('recipient', '')

    # Bail out if SMTP is not fully configured
    if not all([host, user, password, recipient]):
        return False

    # Compose the email
    msg = MIMEMultipart()
    msg['From'] = user
    msg['To'] = recipient
    msg['Reply-To'] = email       # Allows the admin to reply directly to the submitter
    msg['Subject'] = f'New Contact: {name}'

    body = (
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"\n---\n\n"
        f"{message}"
    )
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
    except Exception:
        return False
