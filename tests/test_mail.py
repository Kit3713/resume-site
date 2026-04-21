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
    assert msg['Reply-To'] == 'visitor@example.com'
