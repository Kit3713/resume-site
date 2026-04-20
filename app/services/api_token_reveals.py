"""
API Token Reveal Handoff (Phase 22.4)

Server-side carrier for freshly-generated API token raw values. Replaces
the previous client-side-signed session cookie handoff, which leaked the
plaintext token into ``resume_session`` (Flask's default session signer
does NOT encrypt — anyone with access to the browser cookie jar could
read the token back).

Flow
----
1. ``app/routes/admin.api_tokens_generate`` calls :func:`generate_token`
   and receives the raw value once. It then calls :func:`create_reveal`
   to stash the raw value in the ``api_token_reveals`` table keyed by a
   random 16-byte URL-safe ``reveal_id``. Only the ``reveal_id`` goes
   into the Flask session.
2. The admin follows the redirect to ``/admin/api-tokens/reveal``. That
   handler pops the reveal_id from the session and calls
   :func:`consume_reveal`, which looks up the row, deletes it, and
   returns the payload (or an ``expired`` sentinel if the TTL elapsed).
3. :func:`prune_expired_reveals` runs before create + on reveal to
   clean any stale rows that never got consumed (browser closed,
   admin wandered off).

Why a separate module
---------------------
``app.services.api_tokens`` is the pure service layer for the tokens
themselves — no Flask, no session, no HTML concerns. The reveal handoff
is specifically about the admin-HTML path and is invisible to the JSON
API (which returns the raw token inline in the create response and
never persists it). Keeping the handoff module separate means tests
that import ``api_tokens`` don't drag in any of this logic.

Stdlib only. The ``api_token_reveals`` migration is 012.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections import namedtuple
from datetime import UTC, datetime, timedelta

# Five-minute window — long enough to absorb a user who clicks "generate",
# answers a phone call, then comes back to copy the token; short enough
# that a forgotten reveal row doesn't leak the plaintext indefinitely.
REVEAL_TTL_SECONDS = 300

RevealPayload = namedtuple(
    'RevealPayload',
    ('reveal_id', 'token_id', 'raw', 'name', 'scope', 'token_expires_at'),
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_iso(text: str) -> datetime:
    return datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)


def create_reveal(
    db: sqlite3.Connection,
    *,
    token_id: int,
    raw: str,
    name: str,
    scope: str,
    token_expires_at: str | None = None,
    ttl_seconds: int = REVEAL_TTL_SECONDS,
    now: datetime | None = None,
) -> str:
    """Stash a freshly-generated raw token under a new random ``reveal_id``.

    Returns the ``reveal_id`` for the caller to store in the session —
    never the raw value. Caller must commit? No, this function commits.
    """
    reveal_id = secrets.token_urlsafe(16)
    current = now.astimezone(UTC) if now else datetime.now(UTC)
    expires_at = current + timedelta(seconds=max(1, int(ttl_seconds)))
    db.execute(
        'INSERT INTO api_token_reveals '
        '(reveal_id, token_id, raw_token, name, scope, token_expires_at, expires_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            reveal_id,
            token_id,
            raw,
            name,
            scope,
            token_expires_at or None,
            _iso(expires_at),
        ),
    )
    db.commit()
    return reveal_id


def consume_reveal(
    db: sqlite3.Connection,
    reveal_id: str,
    *,
    now: datetime | None = None,
) -> tuple[str, RevealPayload | None]:
    """Look up and consume a reveal row.

    Returns ``(status, payload)`` where:

    * ``status='ok'`` and ``payload`` is the :class:`RevealPayload` —
      caller should render the one-time template. The row is deleted
      before the return so a second GET is a miss.
    * ``status='expired'`` and ``payload=None`` — row existed but its
      TTL had elapsed. Caller should emit 410 Gone. Row is deleted.
    * ``status='missing'`` and ``payload=None`` — no such reveal_id
      (stale session, server restart wiped the row, or the operator
      followed a spoofed link). Caller should redirect with a flash
      message. No DB write.
    """
    if not reveal_id:
        return 'missing', None
    row = db.execute(
        'SELECT reveal_id, token_id, raw_token, name, scope, token_expires_at, expires_at '
        'FROM api_token_reveals WHERE reveal_id = ?',
        (reveal_id,),
    ).fetchone()
    if row is None:
        return 'missing', None
    # Always delete before any further decision so a concurrent second
    # GET can't race into a successful consume.
    db.execute('DELETE FROM api_token_reveals WHERE reveal_id = ?', (reveal_id,))
    db.commit()

    expires_at_text = row['expires_at'] if hasattr(row, 'keys') else row[6]
    try:
        expires_at = _parse_iso(expires_at_text)
    except ValueError:
        # A malformed timestamp is treated as already-expired — never
        # render a token whose TTL we can't verify.
        return 'expired', None

    current = now.astimezone(UTC) if now else datetime.now(UTC)
    if current > expires_at:
        return 'expired', None

    payload = RevealPayload(
        reveal_id=row['reveal_id'] if hasattr(row, 'keys') else row[0],
        token_id=row['token_id'] if hasattr(row, 'keys') else row[1],
        raw=row['raw_token'] if hasattr(row, 'keys') else row[2],
        name=row['name'] if hasattr(row, 'keys') else row[3],
        scope=row['scope'] if hasattr(row, 'keys') else row[4],
        token_expires_at=row['token_expires_at'] if hasattr(row, 'keys') else row[5],
    )
    return 'ok', payload


def prune_expired_reveals(db: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """Delete any reveal rows whose TTL has elapsed. Returns deleted count.

    Called request-side by :mod:`app.routes.admin` so the table never
    accumulates forgotten rows. O(log N) thanks to the partial index
    on ``expires_at`` in migration 012.
    """
    current = now.astimezone(UTC) if now else datetime.now(UTC)
    cursor = db.execute(
        'DELETE FROM api_token_reveals WHERE expires_at < ?',
        (_iso(current),),
    )
    db.commit()
    return cursor.rowcount or 0
