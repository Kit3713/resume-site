-- Migration 009 — webhooks (Phase 19.2 outbound dispatch)
--
-- Two tables:
--
-- 1. ``webhooks`` — operator-managed subscriptions. Each row is a single
--    HTTP destination plus the set of events that should fan out to it,
--    a shared HMAC secret, and a soft-disable flag that
--    ``app.services.webhooks`` flips on after too many consecutive
--    delivery failures.
--
-- 2. ``webhook_deliveries`` — append-only per-attempt log. Used by the
--    admin UI ("show me the last 50 deliveries for this webhook"), the
--    dashboard ("how many 5xx in the last hour?"), and the auto-disable
--    logic (which actually consults ``webhooks.failure_count``, not this
--    log — the log is for human consumption).
--
-- Both tables follow the project conventions: AUTOINCREMENT integer
-- primary key, ISO-8601 UTC timestamps via ``strftime('%Y-%m-%dT%H:%M:%SZ', 'now')``,
-- snake_case columns, foreign key with ``ON DELETE CASCADE`` so deleting a
-- webhook drops its delivery history (the log is per-webhook diagnostic
-- data, not an audit trail — the admin activity log fills that role).

CREATE TABLE IF NOT EXISTS webhooks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Human label shown in the admin UI. Not UNIQUE — operators may
    -- legitimately want two endpoints with the same friendly name (e.g.
    -- "Slack" pointing at two different channels).
    name               TEXT    NOT NULL,
    -- Destination URL. Must be HTTPS in production; the service layer
    -- accepts any scheme so test fixtures can use http://localhost:N.
    url                TEXT    NOT NULL,
    -- Shared HMAC-SHA256 secret. Generated client-side and posted by the
    -- admin; never displayed back. Stored as plain text because the bus
    -- handler needs it on every dispatch and operators must be able to
    -- copy it back into a downstream verifier (Slack, n8n, custom
    -- listener) — symmetric secret, no asymmetric option.
    secret             TEXT    NOT NULL,
    -- JSON array of event-name subscriptions. ``["*"]`` (a single-item
    -- array containing the literal asterisk) means "every event". The
    -- service layer parses this with ``json.loads`` and falls back to an
    -- empty list on parse error so a malformed row never crashes the
    -- dispatch path.
    events             TEXT    NOT NULL DEFAULT '["*"]',
    -- Soft-enable flag. The auto-disable logic flips this to 0 once the
    -- consecutive ``failure_count`` crosses the threshold (default 10).
    -- Stored as INTEGER (0/1) per the project convention.
    enabled            INTEGER NOT NULL DEFAULT 1,
    -- Consecutive-failure counter. Reset to 0 on any 2xx response;
    -- incremented on every non-2xx / network error. The dispatch path
    -- consults this to decide whether to flip ``enabled`` to 0.
    failure_count      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- Best-effort wall-clock of the last attempted delivery (any
    -- outcome). Updated under the same connection that records the
    -- delivery row so they cannot diverge.
    last_triggered_at  TEXT
);

-- Hot path: every event fans out via "SELECT * FROM webhooks WHERE
-- enabled = 1" before applying per-row event-name filtering in Python.
CREATE INDEX IF NOT EXISTS idx_webhooks_enabled ON webhooks(enabled);


CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Cascade so deleting a webhook drops its delivery history. The
    -- admin activity_log captures the deletion itself for audit; this
    -- table is per-webhook operational telemetry only.
    webhook_id        INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event             TEXT    NOT NULL,
    -- Always populated. ``0`` is the sentinel for "no HTTP response"
    -- (network error / timeout) — the dispatch path never writes NULL
    -- so admin queries can use simple equality predicates.
    status_code       INTEGER NOT NULL DEFAULT 0,
    response_time_ms  INTEGER NOT NULL DEFAULT 0,
    -- Truncated to the first 500 chars by the service layer. Empty
    -- string for successful deliveries; never NULL.
    error_message     TEXT    NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Admin "show last 50 deliveries for webhook X" page.
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook
    ON webhook_deliveries(webhook_id, created_at DESC);

-- Periodic purge ("delete deliveries older than 30 days").
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_created
    ON webhook_deliveries(created_at);
