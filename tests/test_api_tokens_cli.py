"""
API Token CLI Tests — Phase 13.4

Exercises the ``manage.py`` entry points for API token lifecycle:
``generate-api-token``, ``rotate-api-token``, ``revoke-api-token``, and
``list-api-tokens``.

Each handler is invoked directly (not via subprocess) so the tests can
assert on sqlite3 state and event emissions without spinning up a
subprocess. ``RESUME_SITE_CONFIG`` is pointed at the same temporary
config.yaml the ``app`` fixture uses, so create_app() inside each
handler loads the test DB.
"""

from __future__ import annotations

import sqlite3
from argparse import Namespace

import pytest

from app.events import Events, clear, register
from manage import (
    generate_api_token,
    list_api_tokens,
    revoke_api_token,
    rotate_api_token,
)

# ---------------------------------------------------------------------------
# Fixture: point create_app() at the test config and capture events
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch, app):
    """Point manage.py at the test app's config and DB.

    The ``app`` fixture has already built the test config.yaml and
    migrated the DB at ``tmp_path / 'test.db'``; we just advertise the
    same config to manage.py's create_app() via RESUME_SITE_CONFIG.
    """
    monkeypatch.setenv('RESUME_SITE_CONFIG', str(tmp_path / 'config.yaml'))
    # Drop any cached settings so CLI-side create_app() sees fresh state.
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()
    yield app


@pytest.fixture
def event_sink():
    """Capture every Events.API_TOKEN_CREATED emission for assertions."""
    captured = []

    def _handler(**payload):
        captured.append(payload)

    clear()
    register(Events.API_TOKEN_CREATED, _handler)
    yield captured
    clear()


def _db_conn(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# generate-api-token
# ---------------------------------------------------------------------------


def test_generate_api_token_prints_and_persists(cli_env, event_sink, capsys):
    generate_api_token(Namespace(name='CI', scope='read,write', expires=None))

    out = capsys.readouterr().out
    assert 'API TOKEN' in out
    assert 'save this value now' in out
    assert 'Name:    CI' in out
    assert 'Scope:   read,write' in out
    assert 'Expires: never' in out

    conn = _db_conn(cli_env)
    row = conn.execute('SELECT name, scope, expires_at, revoked FROM api_tokens').fetchone()
    conn.close()
    assert row['name'] == 'CI'
    assert row['scope'] == 'read,write'
    assert row['expires_at'] is None
    assert row['revoked'] == 0

    # Event emitted with the redacted payload (no raw token, no hash).
    assert len(event_sink) == 1
    payload = event_sink[0]
    assert payload['name'] == 'CI'
    assert payload['scope'] == 'read,write'
    assert 'token_id' in payload
    assert 'raw' not in payload
    assert 'token_hash' not in payload


def test_generate_api_token_supports_90d_expiry(cli_env, capsys):
    generate_api_token(Namespace(name='expiring', scope='read', expires='90d'))

    conn = _db_conn(cli_env)
    row = conn.execute('SELECT expires_at FROM api_tokens').fetchone()
    conn.close()

    assert row['expires_at'] is not None
    assert row['expires_at'].endswith('Z')
    _ = capsys


def test_generate_api_token_supports_iso_date(cli_env):
    generate_api_token(Namespace(name='y2027', scope='read', expires='2027-01-01'))

    conn = _db_conn(cli_env)
    row = conn.execute('SELECT expires_at FROM api_tokens').fetchone()
    conn.close()
    assert row['expires_at'] == '2027-01-01T00:00:00Z'


def test_generate_api_token_never_maps_to_null(cli_env):
    generate_api_token(Namespace(name='forever', scope='read', expires='never'))

    conn = _db_conn(cli_env)
    row = conn.execute('SELECT expires_at FROM api_tokens').fetchone()
    conn.close()
    assert row['expires_at'] is None


def test_generate_api_token_rejects_unknown_scope(cli_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        generate_api_token(Namespace(name='bad', scope='read,superuser', expires=None))
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert 'unknown scope' in err.lower()
    _ = cli_env


def test_generate_api_token_rejects_bad_expiry(cli_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        generate_api_token(Namespace(name='bad', scope='read', expires='tomorrow'))
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert 'invalid' in err.lower()
    _ = cli_env


# ---------------------------------------------------------------------------
# rotate-api-token
# ---------------------------------------------------------------------------


def test_rotate_api_token_revokes_old_and_prints_new(cli_env, event_sink, capsys):
    generate_api_token(Namespace(name='bot', scope='read,write', expires=None))
    first_out = capsys.readouterr().out
    # Grab the first token id from the banner.
    first_id_line = [ln for ln in first_out.splitlines() if ln.startswith('ID:')][0]
    first_id = int(first_id_line.split()[1])

    rotate_api_token(Namespace(name='bot'))
    rotated_out = capsys.readouterr().out
    assert 'API TOKEN' in rotated_out

    conn = _db_conn(cli_env)
    rows = conn.execute('SELECT id, revoked FROM api_tokens ORDER BY id').fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0]['id'] == first_id
    assert rows[0]['revoked'] == 1
    assert rows[1]['revoked'] == 0

    # Both generate and rotate fired the event.
    assert len(event_sink) == 2


def test_rotate_api_token_unknown_name_exits_2(cli_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        rotate_api_token(Namespace(name='ghost'))
    assert exc_info.value.code == 2
    assert 'no active token' in capsys.readouterr().err.lower()
    _ = cli_env


# ---------------------------------------------------------------------------
# revoke-api-token
# ---------------------------------------------------------------------------


def test_revoke_api_token_flips_the_bit(cli_env, capsys):
    generate_api_token(Namespace(name='R', scope='read', expires=None))
    capsys.readouterr()  # discard banner

    conn = _db_conn(cli_env)
    tok_id = conn.execute('SELECT id FROM api_tokens').fetchone()['id']
    conn.close()

    revoke_api_token(Namespace(id=tok_id))
    out = capsys.readouterr().out
    assert f'Revoked token id={tok_id}' in out

    conn = _db_conn(cli_env)
    row = conn.execute('SELECT revoked FROM api_tokens WHERE id = ?', (tok_id,)).fetchone()
    conn.close()
    assert row['revoked'] == 1


def test_revoke_api_token_missing_id_exits_2(cli_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        revoke_api_token(Namespace(id=9999))
    assert exc_info.value.code == 2
    assert 'no active token' in capsys.readouterr().err.lower()
    _ = cli_env


def test_revoke_api_token_already_revoked_exits_2(cli_env, capsys):
    generate_api_token(Namespace(name='R', scope='read', expires=None))
    capsys.readouterr()

    conn = _db_conn(cli_env)
    tok_id = conn.execute('SELECT id FROM api_tokens').fetchone()['id']
    conn.close()

    revoke_api_token(Namespace(id=tok_id))
    capsys.readouterr()

    # Second call on the same id should refuse (idempotent at DB level,
    # but the CLI surfaces "no active token" so operators notice).
    with pytest.raises(SystemExit) as exc_info:
        revoke_api_token(Namespace(id=tok_id))
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# list-api-tokens
# ---------------------------------------------------------------------------


def test_list_api_tokens_empty(cli_env, capsys):
    list_api_tokens(Namespace())
    assert 'No API tokens.' in capsys.readouterr().out
    _ = cli_env


def test_list_api_tokens_prints_table(cli_env, capsys):
    generate_api_token(Namespace(name='alpha', scope='read', expires=None))
    generate_api_token(Namespace(name='beta', scope='read,write', expires='2027-01-01'))
    capsys.readouterr()

    list_api_tokens(Namespace())
    out = capsys.readouterr().out

    assert 'ID' in out
    assert 'NAME' in out
    assert 'SCOPE' in out
    assert 'STATUS' in out
    assert 'alpha' in out
    assert 'beta' in out
    assert '2027-01-01' in out


def test_list_api_tokens_shows_revoked_status(cli_env, capsys):
    generate_api_token(Namespace(name='R', scope='read', expires=None))
    capsys.readouterr()

    conn = _db_conn(cli_env)
    tok_id = conn.execute('SELECT id FROM api_tokens').fetchone()['id']
    conn.close()
    revoke_api_token(Namespace(id=tok_id))
    capsys.readouterr()

    list_api_tokens(Namespace())
    out = capsys.readouterr().out
    assert 'revoked' in out
