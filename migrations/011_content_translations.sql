-- Migration 011: Content Translation Junction Tables (Phase 15.1)
--
-- Each translatable content type gets a companion _translations table.
-- The original table retains its content as the default-locale version.
-- Queries fall back to the default locale when no translation exists.

CREATE TABLE IF NOT EXISTS content_block_translations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id  INTEGER NOT NULL REFERENCES content_blocks(id) ON DELETE CASCADE,
    locale    TEXT NOT NULL,
    title     TEXT NOT NULL DEFAULT '',
    content   TEXT NOT NULL DEFAULT '',
    plain_text TEXT NOT NULL DEFAULT '',
    UNIQUE(block_id, locale)
);

CREATE TABLE IF NOT EXISTS blog_post_translations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id  INTEGER NOT NULL REFERENCES blog_posts(id) ON DELETE CASCADE,
    locale   TEXT NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    summary  TEXT NOT NULL DEFAULT '',
    content  TEXT NOT NULL DEFAULT '',
    UNIQUE(post_id, locale)
);

CREATE TABLE IF NOT EXISTS service_translations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id  INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    locale      TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    UNIQUE(service_id, locale)
);

CREATE TABLE IF NOT EXISTS stat_translations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_id  INTEGER NOT NULL REFERENCES stats(id) ON DELETE CASCADE,
    locale   TEXT NOT NULL,
    label    TEXT NOT NULL DEFAULT '',
    suffix   TEXT NOT NULL DEFAULT '',
    UNIQUE(stat_id, locale)
);

CREATE TABLE IF NOT EXISTS project_translations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    locale      TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    UNIQUE(project_id, locale)
);

CREATE TABLE IF NOT EXISTS certification_translations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_id  INTEGER NOT NULL REFERENCES certifications(id) ON DELETE CASCADE,
    locale   TEXT NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    UNIQUE(cert_id, locale)
);
