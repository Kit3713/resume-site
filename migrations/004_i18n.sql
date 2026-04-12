-- ==========================================================================
-- Migration 004: Internationalization (i18n)
-- ==========================================================================
--
-- Seeds default locale settings for the i18n framework.
--
-- Settings seeded:
--   default_locale      — Default language for the site (en)
--   available_locales   — Comma-separated list of enabled locales (en)
-- ==========================================================================

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('default_locale', 'en'),
    ('available_locales', 'en');
