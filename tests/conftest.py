"""
Test Configuration and Fixtures

Provides pytest fixtures for creating isolated test environments. Each test
gets a fresh application instance with a temporary database, ensuring tests
don't interfere with each other or with the development database.

The test config uses:
- A random temporary directory for the database and photo storage.
- A known (but non-functional) password hash for admin login tests.
- Only localhost (127.0.0.0/8) in allowed_networks for IP restriction tests.
"""

import os
import sqlite3

import pytest

from app import create_app


@pytest.fixture
def app(tmp_path):
    """Create a test application with a temporary database and config.

    Sets up a complete test environment:
    1. Writes a minimal config.yaml to a temp directory.
    2. Creates the Flask app with TESTING mode enabled.
    3. Initializes the database with the full schema.
    4. Yields the app for use in tests.

    The temp directory is automatically cleaned up after each test.
    """
    # Write a minimal test configuration
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
        'secret_key: "test-secret-key"\n'
        'database_path: "' + str(tmp_path / 'test.db') + '"\n'
        'photo_storage: "' + str(tmp_path / 'photos') + '"\n'
        'admin:\n'
        '  username: "admin"\n'
        '  password_hash: "pbkdf2:sha256:600000$test$b109f3bbbc244eb82441917ed06d618b9008dd09b3befd1b5e07394c706a8bb980b1d7785e5976ec049b46df5f1326af5a2ea6d103fd07c95385ffab0cacbc86"\n'
        '  allowed_networks:\n'
        '    - "127.0.0.0/8"\n'
    )

    # Create the app with the test config
    app = create_app(config_path=str(config_path))
    app.config['TESTING'] = True

    # Initialize the database with the full schema
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schema.sql')
    with open(schema_path, 'r') as f:
        schema = f.read()

    conn = sqlite3.connect(str(tmp_path / 'test.db'))
    conn.executescript(schema)
    conn.close()

    yield app


@pytest.fixture
def client(app):
    """Create a test client for making HTTP requests.

    The test client simulates requests without running a live server.
    Requests come from 127.0.0.1 by default, which is within the
    test config's allowed_networks.
    """
    return app.test_client()
