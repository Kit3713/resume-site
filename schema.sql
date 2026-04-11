-- resume-site Database Schema
-- All tables created at init; populated progressively across phases.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================================
-- SETTINGS (key-value store)
-- ============================================================

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- CONTENT BLOCKS (rich text sections edited via Quill)
-- ============================================================

CREATE TABLE IF NOT EXISTS content_blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    plain_text  TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- PHOTOS
-- ============================================================

CREATE TABLE IF NOT EXISTS photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    storage_name    TEXT UNIQUE NOT NULL,
    mime_type       TEXT NOT NULL DEFAULT 'image/jpeg',
    width           INTEGER,
    height          INTEGER,
    file_size       INTEGER,
    title           TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    tech_used       TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT '',
    display_tier    TEXT NOT NULL DEFAULT 'grid'
                    CHECK(display_tier IN ('featured', 'grid', 'hidden')),
    has_case_study  INTEGER NOT NULL DEFAULT 0,
    case_study_slug TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- CASE STUDIES
-- ============================================================

CREATE TABLE IF NOT EXISTS case_studies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    problem     TEXT NOT NULL DEFAULT '',
    solution    TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',
    photo_id    INTEGER,
    published   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE SET NULL
);

-- ============================================================
-- SERVICES
-- ============================================================

CREATE TABLE IF NOT EXISTS services (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon        TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    visible     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- SKILLS (grouped by domain)
-- ============================================================

CREATE TABLE IF NOT EXISTS skill_domains (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    visible     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    experience  TEXT NOT NULL DEFAULT '',
    tools       TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    visible     INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (domain_id) REFERENCES skill_domains(id) ON DELETE CASCADE
);

-- ============================================================
-- PROJECTS
-- ============================================================

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    github_url      TEXT NOT NULL DEFAULT '',
    has_detail_page INTEGER NOT NULL DEFAULT 0,
    screenshot      TEXT NOT NULL DEFAULT '',
    tech_stack      TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    visible         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- CERTIFICATIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS certifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    issuer          TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    badge_image     TEXT NOT NULL DEFAULT '',
    credential_url  TEXT NOT NULL DEFAULT '',
    date_earned     TEXT,
    date_expires    TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    visible         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- REVIEW TOKENS
-- ============================================================

CREATE TABLE IF NOT EXISTS review_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'recommendation'
                CHECK(type IN ('recommendation', 'client_review')),
    used        INTEGER NOT NULL DEFAULT 0,
    used_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at  TEXT
);

-- ============================================================
-- REVIEWS
-- ============================================================

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id        INTEGER,
    reviewer_name   TEXT NOT NULL,
    reviewer_title  TEXT NOT NULL DEFAULT '',
    relationship    TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL,
    rating          INTEGER CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
    type            TEXT NOT NULL DEFAULT 'recommendation'
                    CHECK(type IN ('recommendation', 'client_review')),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'approved', 'rejected')),
    display_tier    TEXT NOT NULL DEFAULT 'standard'
                    CHECK(display_tier IN ('featured', 'standard', 'hidden')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reviewed_at     TEXT,
    FOREIGN KEY (token_id) REFERENCES review_tokens(id) ON DELETE SET NULL
);

-- ============================================================
-- CONTACT SUBMISSIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS contact_submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    message     TEXT NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    is_spam     INTEGER NOT NULL DEFAULT 0,
    read        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ============================================================
-- ANALYTICS (page views)
-- ============================================================

CREATE TABLE IF NOT EXISTS page_views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    referrer    TEXT NOT NULL DEFAULT '',
    user_agent  TEXT NOT NULL DEFAULT '',
    ip_address  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_page_views_path ON page_views(path);
CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_at);

-- ============================================================
-- STATS (animated counters on landing page)
-- ============================================================

CREATE TABLE IF NOT EXISTS stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    value       INTEGER NOT NULL DEFAULT 0,
    suffix      TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    visible     INTEGER NOT NULL DEFAULT 1
);

-- ============================================================
-- SEED DEFAULT SETTINGS
-- ============================================================

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('site_title', 'My Portfolio'),
    ('site_tagline', 'Welcome to my portfolio'),
    ('dark_mode_default', 'true'),
    ('availability_status', 'available'),
    ('contact_form_enabled', 'true'),
    ('contact_email_visible', 'false'),
    ('contact_phone_visible', 'false'),
    ('contact_github_url', ''),
    ('contact_linkedin_url', ''),
    ('resume_visibility', 'off'),
    ('case_studies_enabled', 'false'),
    ('testimonial_display_mode', 'mixed'),
    ('analytics_retention_days', '90'),
    ('hero_heading', ''),
    ('hero_subheading', ''),
    ('hero_tagline', ''),
    ('accent_color', '#0071e3'),
    ('logo_mode', 'title'),
    ('footer_text', '');
