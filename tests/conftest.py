import os
import sqlite3
import tempfile

import pytest

from app import create_app


@pytest.fixture
def app(tmp_path):
    """Create a test application with temporary database and config."""
    # Write a minimal test config
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

    app = create_app(config_path=str(config_path))
    app.config['TESTING'] = True

    # Initialize the database
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schema.sql')
    with open(schema_path, 'r') as f:
        schema = f.read()

    conn = sqlite3.connect(str(tmp_path / 'test.db'))
    conn.executescript(schema)
    conn.close()

    yield app


@pytest.fixture
def client(app):
    """Test client for making requests."""
    return app.test_client()
