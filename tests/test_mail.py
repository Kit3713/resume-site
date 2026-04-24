"""Tests for ``app.services.mail`` — SMTP sender decoupling.

Locks in the Resend / SendGrid / Mailgun-compatible behavior where the
SMTP login user (e.g. Resend's ``"resend"``) is different from the
verified-domain address that must appear in the ``From`` header. Also
locks in backward compatibility for existing Gmail / Outlook / self-
hosted setups where login user and From address are identical.
"""

from unittest.mock import MagicMock, patch

from app.services.mail import send_contact_email


def _smtp_config(**overrides):
    base = {
        'host': 'smtp.example.com',
        'port': 587,
        'user': 'resend',
        'password': 'rp_secret',
        'recipient': 'owner@example.com',
    }
    base.update(overrides)
    return base


def _patched_smtp(captured):
    """Return a ``(patch, server)`` pair that captures the MIME message."""
    server = MagicMock()
    server.send_message.side_effect = lambda msg: captured.append(msg)
    return patch('smtplib.SMTP', return_value=server), server


def test_from_address_sets_from_header_when_configured(app):
    """``smtp.from_address`` populates ``From`` even when it differs from login user."""
    captured = []
    smtp_patch, server = _patched_smtp(captured)

    with smtp_patch, app.app_context():
        app.config['SITE_CONFIG'] = {
            'smtp': _smtp_config(from_address='contact@collverkit.com'),
        }
        ok = send_contact_email('Alice', 'alice@example.com', 'hello')

    assert ok is True
    assert len(captured) == 1
    assert captured[0]['From'] == 'contact@collverkit.com'
    # Login identity is kept separate from the sender identity.
    server.login.assert_called_once_with('resend', 'rp_secret')


def test_from_address_falls_back_to_user_when_unset(app):
    """Deployments that never set ``from_address`` keep the pre-change behavior."""
    captured = []
    smtp_patch, _ = _patched_smtp(captured)

    with smtp_patch, app.app_context():
        app.config['SITE_CONFIG'] = {'smtp': _smtp_config()}
        ok = send_contact_email('Bob', 'bob@example.com', 'hi')

    assert ok is True
    assert captured[0]['From'] == 'resend'


def test_from_address_empty_string_falls_back_to_user(app):
    """Explicit empty string (e.g. blank env var override) still falls back."""
    captured = []
    smtp_patch, _ = _patched_smtp(captured)

    with smtp_patch, app.app_context():
        app.config['SITE_CONFIG'] = {'smtp': _smtp_config(from_address='')}
        send_contact_email('Carol', 'carol@example.com', 'hi')

    assert captured[0]['From'] == 'resend'


def test_reply_to_always_the_submitter_not_from_address(app):
    """Adding ``from_address`` must not bleed into Reply-To — replies still go to the visitor."""
    captured = []
    smtp_patch, _ = _patched_smtp(captured)

    with smtp_patch, app.app_context():
        app.config['SITE_CONFIG'] = {
            'smtp': _smtp_config(from_address='contact@collverkit.com'),
        }
        send_contact_email('Dana', 'visitor@example.com', 'hello')

    msg = captured[0]
    assert msg['From'] == 'contact@collverkit.com'
    # Phase 23.5 (#35) — Reply-To now uses email.utils.formataddr to
    # include the submitter's display name. The visitor's email must
    # still be the ONLY reply target (not the from_address), which is
    # what this test locks in regardless of the display-name format.
    reply_to = msg['Reply-To']
    assert 'visitor@example.com' in reply_to
    assert 'contact@collverkit.com' not in reply_to


# ============================================================
# Phase 23.5 — header injection rejection (#35)
# ============================================================


def test_header_injection_in_name_rejects_send(app):
    """A name containing CR/LF must cause ``send_contact_email`` to
    return False without invoking SMTP, so the injected ``Bcc:`` (or
    any other forged header) never reaches the wire.
    """
    captured = []
    smtp_patch, sent_fn = _patched_smtp(captured)

    with smtp_patch, app.app_context():
        app.config['SITE_CONFIG'] = {'smtp': _smtp_config()}
        result = send_contact_email(
            'Evil\r\nBcc: exfil@attacker.example',
            'v@example.com',
            'hi',
        )

    assert result is False
    assert captured == []  # No message composed or sent.


def test_header_injection_in_email_rejects_send(app):
    """Injection through the email field must also be rejected."""
    captured = []
    smtp_patch, _ = _patched_smtp(captured)

    with smtp_patch, app.app_context():
        app.config['SITE_CONFIG'] = {'smtp': _smtp_config()}
        result = send_contact_email(
            'Dana',
            'v@example.com\nBcc: exfil@attacker.example',
            'hi',
        )

    assert result is False
    assert captured == []


def test_benign_name_with_accented_chars_still_sends(app):
    """23.5 must not regress non-ASCII legitimate names — formataddr
    handles MIME-encoding for display names with accented characters.
    """
    captured = []
    smtp_patch, _ = _patched_smtp(captured)

    with smtp_patch, app.app_context():
        app.config['SITE_CONFIG'] = {'smtp': _smtp_config()}
        result = send_contact_email('Amélie Dupont', 'a@example.com', 'bonjour')

    assert result is True
    assert len(captured) == 1
    assert 'a@example.com' in captured[0]['Reply-To']
