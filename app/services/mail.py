import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import current_app


def send_contact_email(name, email, message):
    """Send contact form submission via SMTP. Returns True on success."""
    config = current_app.config['SITE_CONFIG'].get('smtp', {})
    host = config.get('host', '')
    port = config.get('port', 587)
    user = config.get('user', '')
    password = config.get('password', '')
    recipient = config.get('recipient', '')

    if not all([host, user, password, recipient]):
        return False

    msg = MIMEMultipart()
    msg['From'] = user
    msg['To'] = recipient
    msg['Reply-To'] = email
    msg['Subject'] = f'New Contact: {name}'

    body = (
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"\n---\n\n"
        f"{message}"
    )
    msg.attach(MIMEText(body, 'plain'))

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            server.starttls()
        server.login(user, password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception:
        return False
