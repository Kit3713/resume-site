-- Migration 006 — login attempts (Phase 13.6 login lockout)
--
-- Tracks admin login attempts so the application-level lockout can count
-- failures across a sliding window, even after a Flask-Limiter rate
-- window has elapsed. The IP is stored as a per-deployment hash (see
-- app.services.logging.hash_client_ip) so raw IPs never hit disk.

CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_hash    TEXT    NOT NULL,
    success    INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Lockout window query filters by ip_hash + created_at descending.
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_hash_created_at
    ON login_attempts(ip_hash, created_at);

-- Retention purge scans by created_at alone.
CREATE INDEX IF NOT EXISTS idx_login_attempts_created_at
    ON login_attempts(created_at);
