-- Migration 007 — api_tokens (Phase 13.4 API token authentication)
--
-- Token-based auth for the forthcoming REST API (Phase 16). Only the
-- SHA-256 hash of the raw token is stored; the raw value is printed
-- once at generation time and never persisted. Name is a human label,
-- scope is a comma-separated whitelist of {read,write,admin}. Revoked
-- tokens are retained for audit (soft delete) rather than removed, so
-- `rotate-api-token` leaves a trail showing which tokens were superseded.

CREATE TABLE IF NOT EXISTS api_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 64 hex chars (SHA-256). UNIQUE so the cosmically improbable case
    -- of two raw tokens hashing to the same value surfaces as an
    -- IntegrityError at insert rather than silently authenticating both.
    token_hash    TEXT    NOT NULL UNIQUE,
    -- Human label shown in the admin UI and used as a lookup key by
    -- `rotate-api-token`. Not UNIQUE — an operator may keep the same
    -- name across rotations (the old row stays as revoked=1).
    name          TEXT    NOT NULL,
    -- Comma-separated scope whitelist (e.g. "read" or "read,write").
    -- Stored as TEXT rather than a CHECK constraint so additional
    -- scopes introduced in later phases don't require another migration.
    scope         TEXT    NOT NULL DEFAULT 'read',
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- NULL means "no expiry". Comparisons use IS NULL so the column
    -- cannot silently expire a token via a malformed timestamp.
    expires_at    TEXT,
    -- Updated best-effort on every successful auth. Kept as a single
    -- UPDATE statement so concurrent requests serialise cleanly under
    -- SQLite's default locking without wrapping verification in a
    -- transaction.
    last_used_at  TEXT,
    -- Soft-delete flag. 1 = revoked. Indexed lookups filter on this.
    revoked       INTEGER NOT NULL DEFAULT 0,
    -- Admin username at creation time. Defaults to 'admin' for
    -- single-user deployments; reserved for future multi-user (v0.4.0+).
    created_by    TEXT    NOT NULL DEFAULT 'admin'
);

-- Auth hot-path lookup: hash is highly selective (64 hex chars), so an
-- index on token_hash alone serves every Bearer-header verification.
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);

-- Admin UI lists active tokens in creation order (newest first).
CREATE INDEX IF NOT EXISTS idx_api_tokens_created_at ON api_tokens(created_at);

-- `rotate-api-token --name X` and the admin list-by-name view hit this.
CREATE INDEX IF NOT EXISTS idx_api_tokens_name ON api_tokens(name);
