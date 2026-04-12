-- ==========================================================================
-- Migration 002: Blog Engine Tables
-- ==========================================================================
--
-- Adds the blog/articles system: posts with rich text or markdown content,
-- a tagging system via a junction table, and blog-specific settings.
--
-- Tables created:
--   blog_posts      — Published articles with metadata, status, and SEO fields
--   blog_tags       — Unique tag names for categorization
--   blog_post_tags  — Many-to-many junction between posts and tags
--
-- Settings seeded:
--   blog_enabled, blog_title, posts_per_page, show_reading_time, enable_rss
-- ==========================================================================

-- ============================================================
-- BLOG POSTS
-- ============================================================

CREATE TABLE IF NOT EXISTS blog_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL DEFAULT '',
    content_format  TEXT NOT NULL DEFAULT 'html'
                    CHECK(content_format IN ('html', 'markdown')),
    cover_image     TEXT NOT NULL DEFAULT '',
    author          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft', 'published', 'archived')),
    featured        INTEGER NOT NULL DEFAULT 0,
    reading_time    INTEGER NOT NULL DEFAULT 0,
    meta_description TEXT NOT NULL DEFAULT '',
    published_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_blog_posts_slug ON blog_posts(slug);
CREATE INDEX IF NOT EXISTS idx_blog_posts_status ON blog_posts(status);
CREATE INDEX IF NOT EXISTS idx_blog_posts_published_at ON blog_posts(published_at);

-- ============================================================
-- BLOG TAGS
-- ============================================================

CREATE TABLE IF NOT EXISTS blog_tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT UNIQUE NOT NULL,
    slug    TEXT UNIQUE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blog_tags_slug ON blog_tags(slug);

-- ============================================================
-- BLOG POST ↔ TAG JUNCTION TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS blog_post_tags (
    post_id INTEGER NOT NULL,
    tag_id  INTEGER NOT NULL,
    PRIMARY KEY (post_id, tag_id),
    FOREIGN KEY (post_id) REFERENCES blog_posts(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)  REFERENCES blog_tags(id)  ON DELETE CASCADE
);

-- ============================================================
-- BLOG SETTINGS (seed defaults)
-- ============================================================

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('blog_enabled', 'false'),
    ('blog_title', 'Blog'),
    ('posts_per_page', '10'),
    ('show_reading_time', 'true'),
    ('enable_rss', 'true');
