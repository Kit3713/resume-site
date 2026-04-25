-- Migration 013: Normalise legacy ``resume_visibility='private'`` to
-- ``'public'`` (Issue #126).
--
-- The former 'private' option was security-through-obscurity: the
-- ``/resume`` route had no auth, token, or IP gate, so 'private' was
-- indistinguishable from 'public' to anyone who already had the URL.
-- The settings UI also implied that ``?visibility=private`` was a
-- meaningful query parameter; it never was. This migration drops the
-- false access tier from the data layer to match the registry change
-- (which removes 'private' from the dropdown).
--
-- Why migrate to 'public' rather than 'off':
--   The behaviour an admin saw for 'private' was identical to 'public'
--   (anyone with the URL could download). Migrating to 'public'
--   preserves that behaviour byte-for-byte. Migrating to 'off' would be
--   a *behavioural* change — every site that had been quietly serving
--   the resume via 'private' would suddenly 404. That belongs in a
--   release note, not a silent migration. Operators who actually want
--   the resume hidden can flip the setting to 'off' from the admin UI
--   the next time they visit Settings.
--
-- The route handler now requires ``resume_visibility='public'`` exactly;
-- any other value (including a stale 'private' on a partially-migrated
-- DB) returns 404. So this UPDATE also serves as the compatibility
-- bridge between the legacy data and the post-fix route.

UPDATE settings
SET value = 'public'
WHERE key = 'resume_visibility' AND value = 'private';
