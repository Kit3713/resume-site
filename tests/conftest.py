"""
Test Configuration and Fixtures

Provides pytest fixtures for creating isolated test environments. Each test
gets a fresh application instance with a temporary database, ensuring tests
don't interfere with each other or with the development database.

Fixtures:
    app           — Base Flask app with an empty database (schema + seeds only).
    client        — Test client for HTTP requests (from 127.0.0.1 by default).
    auth_client   — Test client pre-logged-in as admin.
    populated_db  — In-process database populated with sample content.
"""

import os
import sqlite3

import pytest

from app import create_app

# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------


def _write_test_config(tmp_path):
    """Write a minimal test config.yaml to tmp_path and return its path."""
    config_path = tmp_path / 'config.yaml'
    db_path = str(tmp_path / 'test.db')
    photos_path = str(tmp_path / 'photos')
    # Hash for "testpassword123" (pbkdf2:sha256, 600k iterations).
    # Generated with werkzeug.security.generate_password_hash().
    pw_hash = (
        'pbkdf2:sha256:600000$bngNDaCGXphoecmK$'
        '7e35934ae555af4c418e1399fa0c866411b05f64bf8c3ef64d50c93990a7497b'
    )
    config_path.write_text(
        'secret_key: "test-secret-key-for-testing-only"\n'
        f'database_path: "{db_path}"\n'
        f'photo_storage: "{photos_path}"\n'
        'session_cookie_secure: false\n'  # Tests use HTTP, so disable Secure flag
        'admin:\n'
        '  username: "admin"\n'
        f'  password_hash: "{pw_hash}"\n'
        '  allowed_networks:\n'
        '    - "127.0.0.0/8"\n'
    )
    return str(config_path)


def _init_test_db(db_path):
    """Initialize a test database from schema.sql + all migrations."""
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schema.sql')
    with open(schema_path) as f:
        schema = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)

    # Apply any additional migrations beyond the baseline schema
    migrations_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations')
    if os.path.isdir(migrations_dir):
        for fname in sorted(os.listdir(migrations_dir)):
            if fname.endswith('.sql') and fname[0].isdigit():
                version = int(fname.split('_')[0])
                if version <= 1:
                    continue  # baseline is covered by schema.sql
                migration_path = os.path.join(migrations_dir, fname)
                with open(migration_path) as f:
                    conn.executescript(f.read())

    conn.close()


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    """Create a test application with a temporary database.

    Sets up a complete test environment:
    1. Writes a minimal config.yaml to a temp directory.
    2. Creates the Flask app with TESTING mode enabled.
    3. Initializes the database with the full schema + seed data.

    The temp directory is automatically cleaned up after each test.
    """
    config_path = _write_test_config(tmp_path)
    flask_app = create_app(config_path=config_path)
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF in tests (tested separately)

    _init_test_db(str(tmp_path / 'test.db'))

    yield flask_app


@pytest.fixture
def client(app):
    """Create a test client for HTTP requests.

    Requests come from 127.0.0.1 by default, which passes the IP restriction
    check (127.0.0.0/8 is in the test config's allowed_networks).
    """
    return app.test_client()


@pytest.fixture
def auth_client(app):
    """Create a test client pre-authenticated as the admin user.

    Uses Flask-Login's test utilities to set a valid session without
    going through the login form. Useful for testing admin routes that
    require @login_required without testing the login flow itself.
    """
    client = app.test_client()
    with app.test_request_context(), client.session_transaction() as sess:
        # Manually set the Flask-Login session cookie
        sess['_user_id'] = 'admin'
        sess['_fresh'] = True

    return client


@pytest.fixture
def smtp_mock(app, monkeypatch):
    """Mock the SMTP email sending to capture sent messages without a real relay.

    Returns a list that collects (name, email, message) tuples for each call
    to send_contact_email(). The actual SMTP connection is never attempted.
    """
    sent = []

    def _mock_send(name, email, message):
        sent.append((name, email, message))
        return True

    monkeypatch.setattr('app.services.mail.send_contact_email', _mock_send)
    return sent


@pytest.fixture
def populated_db(app):
    """Return a database connection pre-populated with sample content.

    Inserts representative rows for each content type so tests can verify
    display logic, filtering, and ordering without setting up data inline.

    Returns:
        sqlite3.Connection: An open connection to the populated test database.
        Remember to close it when done (or use it as a context manager).
    """
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')

    # Sample service
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order) VALUES (?, ?, ?, ?)',
        ('Web Development', 'Full-stack web applications', '🌐', 1),
    )

    # Sample stat
    conn.execute(
        'INSERT INTO stats (label, value, suffix, sort_order) VALUES (?, ?, ?, ?)',
        ('Projects', 42, '+', 1),
    )

    # Sample review token and approved review
    conn.execute(
        'INSERT INTO review_tokens (token, name, type) VALUES (?, ?, ?)',
        ('test-token-abc123', 'Alice Smith', 'recommendation'),
    )
    conn.execute(
        'INSERT INTO reviews (token_id, reviewer_name, reviewer_title, message, type, status, display_tier) '
        "VALUES (1, 'Alice Smith', 'Engineer', 'Great work!', 'recommendation', 'approved', 'featured')",
    )

    # Sample content block
    conn.execute(
        'INSERT INTO content_blocks (slug, title, content) VALUES (?, ?, ?)',
        ('about', 'About Me', '<p>Test about content.</p>'),
    )

    conn.commit()
    yield conn
    conn.close()
