-- ==========================================================================
-- Default Settings Seed Data
-- ==========================================================================
--
-- Contains INSERT OR IGNORE statements for all baseline settings. These
-- provide sensible defaults for a fresh installation — existing values are
-- never overwritten (INSERT OR IGNORE).
--
-- Run automatically by `manage.py init-db` after all migrations have been
-- applied. Can also be run manually:
--
--   sqlite3 data/site.db < seeds/defaults.sql
--
-- To add a new default setting:
--   1. Add a registry entry in app/services/settings_svc.py
--   2. Add an INSERT OR IGNORE line here
--   3. If the setting must exist for upgrades, also add a migration
-- ==========================================================================

-- Site identity and display
INSERT OR IGNORE INTO settings (key, value) VALUES ('site_title', 'My Portfolio');
INSERT OR IGNORE INTO settings (key, value) VALUES ('site_tagline', 'Welcome to my portfolio');
INSERT OR IGNORE INTO settings (key, value) VALUES ('dark_mode_default', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('availability_status', 'available');
INSERT OR IGNORE INTO settings (key, value) VALUES ('hero_heading', '');
INSERT OR IGNORE INTO settings (key, value) VALUES ('hero_subheading', '');
INSERT OR IGNORE INTO settings (key, value) VALUES ('hero_tagline', '');
INSERT OR IGNORE INTO settings (key, value) VALUES ('accent_color', '#0071e3');
INSERT OR IGNORE INTO settings (key, value) VALUES ('logo_mode', 'title');
INSERT OR IGNORE INTO settings (key, value) VALUES ('footer_text', '');

-- Contact and social
INSERT OR IGNORE INTO settings (key, value) VALUES ('contact_form_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('contact_email_visible', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('contact_phone_visible', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('contact_github_url', '');
INSERT OR IGNORE INTO settings (key, value) VALUES ('contact_linkedin_url', '');
INSERT OR IGNORE INTO settings (key, value) VALUES ('resume_visibility', 'off');

-- Content toggles
INSERT OR IGNORE INTO settings (key, value) VALUES ('case_studies_enabled', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('testimonial_display_mode', 'mixed');
INSERT OR IGNORE INTO settings (key, value) VALUES ('analytics_retention_days', '90');

-- Blog (added in migration 002)
INSERT OR IGNORE INTO settings (key, value) VALUES ('blog_enabled', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('blog_title', 'Blog');
INSERT OR IGNORE INTO settings (key, value) VALUES ('posts_per_page', '10');
INSERT OR IGNORE INTO settings (key, value) VALUES ('show_reading_time', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('enable_rss', 'true');

-- Appearance (added in migration 003)
INSERT OR IGNORE INTO settings (key, value) VALUES ('custom_css', '');
INSERT OR IGNORE INTO settings (key, value) VALUES ('font_pairing', 'inter');
INSERT OR IGNORE INTO settings (key, value) VALUES ('color_preset', 'blue');
INSERT OR IGNORE INTO settings (key, value) VALUES ('nav_hide_about', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('nav_hide_services', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('nav_hide_portfolio', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('nav_hide_projects', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('nav_hide_testimonials', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('nav_hide_contact', 'false');

-- Internationalization (added in migration 004)
INSERT OR IGNORE INTO settings (key, value) VALUES ('default_locale', 'en');
INSERT OR IGNORE INTO settings (key, value) VALUES ('available_locales', 'en');
