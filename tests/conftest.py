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


#: High-entropy sentinel secret_key for the test suite. The
#: ``test-do-not-use-`` prefix flags it as a test artefact to any human
#: who greps the repo, while the trailing 64 hex chars give it enough
#: entropy that an operator who copies this verbatim into prod isn't
#: handing attackers a guessable session key. Kept as a module-level
#: constant so the regression test in :mod:`tests.test_security` can
#: pin "this exact value validates cleanly" alongside "the OLD value
#: fails fatally" without a copy-paste drift hazard. See issue #125.
TEST_SECRET_KEY = 'test-do-not-use-c8f4e2d9a1b6f0e5c7d3a4b8e2f1d9c6e3a7b1f5d2c8e4a6b9d1f3c7e0a5b2d8'


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
        f'secret_key: "{TEST_SECRET_KEY}"\n'
        f'database_path: "{db_path}"\n'
        f'photo_storage: "{photos_path}"\n'
        'session_cookie_secure: false\n'  # Tests use HTTP, so disable Secure flag
        # Phase 22.6: mirror the standard dev setup where the Flask app
        # sits behind a reverse proxy on 127.0.0.0/8. Admin IP-restriction
        # tests set X-Forwarded-For to simulate an external client; the
        # XFF value is only consulted when request.remote_addr (which
        # Flask's test client hardcodes to 127.0.0.1) is inside this CIDR.
        'trusted_proxies:\n'
        '  - "127.0.0.0/8"\n'
        'admin:\n'
        '  username: "admin"\n'
        f'  password_hash: "{pw_hash}"\n'
        '  allowed_networks:\n'
        '    - "127.0.0.0/8"\n'
    )
    return str(config_path)


def _init_test_db(db_path):
    """Initialize a test database from schema.sql + all migrations.

    Mirrors the production ``manage.py migrate`` flow closely enough
    that the readiness probe (Phase 21.2) sees a fully-migrated DB:
    every migration applied gets a ``schema_version`` row, and the
    baseline migration is recorded as version 1 because ``schema.sql``
    already contains its tables.
    """
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schema.sql')
    with open(schema_path) as f:
        schema = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)

    # Materialise schema_version up front so the loop below can record
    # every applied migration. The CREATE TABLE matches manage.py's
    # _ensure_schema_version_table() exactly.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    # The baseline ships in schema.sql; record it as applied so the
    # readiness probe doesn't flag it as pending.
    conn.execute(
        'INSERT OR IGNORE INTO schema_version (version, name) VALUES (1, ?)',
        ('001_baseline.sql',),
    )

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
                conn.execute(
                    'INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)',
                    (version, fname),
                )
    conn.commit()
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

    # Drop any settings-cache entries left over from previous tests. Cache keys
    # are db paths (unique per tmp_path) so cross-test bleed is unlikely, but
    # tests that write to the settings table directly (bypassing save_many)
    # would otherwise see stale reads within the 30s TTL window.
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

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
        # Match the epoch stamp the real login handler writes so
        # ``check_session_epoch`` accepts the synthetic session.
        # Zero is the default for a never-bumped admin_session_epoch.
        sess['_admin_epoch'] = 0

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
