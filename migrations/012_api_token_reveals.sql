-- Migration 012: Server-side API Token Reveal Handoff (Phase 22.4)
--
-- Replaces the client-side-signed session cookie as the carrier for
-- freshly-generated API token raw values. Flask's default session is
-- signed-not-encrypted: the plaintext bytes were visible to anyone who
-- inspected the ``resume_session`` cookie. Post-Phase 22.4 the session
-- carries only a random ``reveal_id`` that indexes this server-side
-- table; the reveal route looks up the row, deletes it, and renders the
-- one-time view. Stale rows are pruned at request time by the
-- ``prune_expired_reveals`` helper (5-minute TTL).
--
-- Design notes:
--  * ``reveal_id`` is a 16-byte URL-safe random token (``secrets.token_urlsafe(16)``).
--    It never names an api_tokens row; it identifies the reveal *attempt*.
--  * ``token_id`` foreign-keys back to api_tokens so deleting a token
--    cascades its pending reveal.
--  * ``raw_token`` holds the plaintext. Never SELECTed outside the
--    handoff; purged by the consume / prune helpers.
--  * ``expires_at`` is ISO-8601 UTC. Checked against ``datetime.now(UTC)``
--    by consume_reveal — an expired row returns 410 Gone rather than
--    rendering the template.
--  * Index on ``expires_at`` keeps the prune query O(log N).

CREATE TABLE IF NOT EXISTS api_token_reveals (
    reveal_id   TEXT PRIMARY KEY,
    token_id    INTEGER NOT NULL REFERENCES api_tokens(id) ON DELETE CASCADE,
    raw_token   TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT '',
    token_expires_at TEXT,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_api_token_reveals_expires_at
    ON api_token_reveals(expires_at);
