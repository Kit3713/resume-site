-- ==========================================================================
-- Migration 003: Admin Panel Customization
-- ==========================================================================
--
-- Adds theme customization settings, navigation visibility toggles,
-- and an activity log for admin audit trails.
--
-- Tables created:
--   admin_activity_log — Records admin actions for dashboard display
--
-- Settings seeded:
--   custom_css, font_pairing, color_preset, nav_hide_* toggles
-- ==========================================================================

-- ============================================================
-- ADMIN ACTIVITY LOG
-- ============================================================

CREATE TABLE IF NOT EXISTS admin_activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    admin_user  TEXT NOT NULL DEFAULT 'admin',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_admin_activity_log_created
    ON admin_activity_log(created_at);

-- ============================================================
-- THEME & NAVIGATION SETTINGS (seed defaults)
-- ============================================================

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('custom_css', ''),
    ('font_pairing', 'inter'),
    ('color_preset', 'default'),
    ('nav_hide_about', 'false'),
    ('nav_hide_services', 'false'),
    ('nav_hide_portfolio', 'false'),
    ('nav_hide_projects', 'false'),
    ('nav_hide_testimonials', 'false'),
    ('nav_hide_contact', 'false'),
    ('nav_hide_certifications', 'false');
