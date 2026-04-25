-- Migration 013: Admin Activity Log Append-Only Enforcement (#105)
--
-- The ``admin_activity_log`` table is documented as the audit trail —
-- "append-only", per Migration 003 and the service-module docstring.
-- That documentation enforced nothing: any code path with a ``db``
-- handle could ``DELETE FROM admin_activity_log WHERE ...`` and erase
-- the trail. A future bug or an admin with terminal access could
-- silently truncate history.
--
-- This migration adds two BEFORE triggers that ``RAISE(ABORT)`` on
-- DELETE and UPDATE against the audit log. The only legitimate
-- mutation path — the retention purge in
-- ``app/services/activity_log.py:purge_old_entries`` — drops the
-- DELETE trigger inside a try/finally so the deletable path is
-- gated behind a single, documented function. Any other DELETE or
-- UPDATE attempt fails with ``sqlite3.IntegrityError``.
--
-- Idempotent: ``CREATE TRIGGER IF NOT EXISTS`` makes re-execution a
-- no-op, satisfying the Phase 21.5 idempotency contract that
-- ``tests/test_migrations.py::test_every_migration_is_idempotent``
-- locks in.
--
-- Rollback (manual; reversibility is the operator's call):
--   DROP TRIGGER IF EXISTS admin_activity_log_no_delete;
--   DROP TRIGGER IF EXISTS admin_activity_log_no_update;

CREATE TRIGGER IF NOT EXISTS admin_activity_log_no_delete
BEFORE DELETE ON admin_activity_log
BEGIN
    SELECT RAISE(ABORT, 'admin_activity_log is append-only; safe-purge required');
END;

CREATE TRIGGER IF NOT EXISTS admin_activity_log_no_update
BEFORE UPDATE ON admin_activity_log
BEGIN
    SELECT RAISE(ABORT, 'admin_activity_log is append-only; rows are immutable');
END;
