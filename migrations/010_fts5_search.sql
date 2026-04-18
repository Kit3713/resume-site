-- Migration 010: FTS5 Full-Text Search Index (Phase 14.5)
--
-- Creates an FTS5 virtual table that indexes searchable content across
-- all admin-managed content types. Triggers keep the index in sync with
-- the source tables on INSERT/UPDATE/DELETE.
--
-- Rebuild the index manually: python manage.py rebuild-search-index

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    content_type,   -- 'content_block', 'blog_post', 'review', 'contact', 'photo', 'service', 'project'
    content_id,     -- rowid in the source table
    title,          -- searchable title/name field
    body,           -- searchable body/description field
    tokenize='porter unicode61'
);

-- Triggers for content_blocks
CREATE TRIGGER IF NOT EXISTS search_content_blocks_insert AFTER INSERT ON content_blocks BEGIN
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('content_block', NEW.id, NEW.title, NEW.plain_text);
END;

CREATE TRIGGER IF NOT EXISTS search_content_blocks_update AFTER UPDATE ON content_blocks BEGIN
    DELETE FROM search_index WHERE content_type = 'content_block' AND content_id = OLD.id;
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('content_block', NEW.id, NEW.title, NEW.plain_text);
END;

CREATE TRIGGER IF NOT EXISTS search_content_blocks_delete AFTER DELETE ON content_blocks BEGIN
    DELETE FROM search_index WHERE content_type = 'content_block' AND content_id = OLD.id;
END;

-- Triggers for blog_posts
CREATE TRIGGER IF NOT EXISTS search_blog_posts_insert AFTER INSERT ON blog_posts BEGIN
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('blog_post', NEW.id, NEW.title, COALESCE(NEW.summary, '') || ' ' || COALESCE(NEW.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS search_blog_posts_update AFTER UPDATE ON blog_posts BEGIN
    DELETE FROM search_index WHERE content_type = 'blog_post' AND content_id = OLD.id;
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('blog_post', NEW.id, NEW.title, COALESCE(NEW.summary, '') || ' ' || COALESCE(NEW.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS search_blog_posts_delete AFTER DELETE ON blog_posts BEGIN
    DELETE FROM search_index WHERE content_type = 'blog_post' AND content_id = OLD.id;
END;

-- Triggers for reviews
CREATE TRIGGER IF NOT EXISTS search_reviews_insert AFTER INSERT ON reviews BEGIN
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('review', NEW.id, NEW.reviewer_name, NEW.message);
END;

CREATE TRIGGER IF NOT EXISTS search_reviews_update AFTER UPDATE ON reviews BEGIN
    DELETE FROM search_index WHERE content_type = 'review' AND content_id = OLD.id;
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('review', NEW.id, NEW.reviewer_name, NEW.message);
END;

CREATE TRIGGER IF NOT EXISTS search_reviews_delete AFTER DELETE ON reviews BEGIN
    DELETE FROM search_index WHERE content_type = 'review' AND content_id = OLD.id;
END;

-- Triggers for photos
CREATE TRIGGER IF NOT EXISTS search_photos_insert AFTER INSERT ON photos BEGIN
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('photo', NEW.id, NEW.title, COALESCE(NEW.description, '') || ' ' || COALESCE(NEW.category, ''));
END;

CREATE TRIGGER IF NOT EXISTS search_photos_update AFTER UPDATE ON photos BEGIN
    DELETE FROM search_index WHERE content_type = 'photo' AND content_id = OLD.id;
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('photo', NEW.id, NEW.title, COALESCE(NEW.description, '') || ' ' || COALESCE(NEW.category, ''));
END;

CREATE TRIGGER IF NOT EXISTS search_photos_delete AFTER DELETE ON photos BEGIN
    DELETE FROM search_index WHERE content_type = 'photo' AND content_id = OLD.id;
END;

-- Triggers for services
CREATE TRIGGER IF NOT EXISTS search_services_insert AFTER INSERT ON services BEGIN
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('service', NEW.id, NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS search_services_update AFTER UPDATE ON services BEGIN
    DELETE FROM search_index WHERE content_type = 'service' AND content_id = OLD.id;
    INSERT INTO search_index(content_type, content_id, title, body)
    VALUES ('service', NEW.id, NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS search_services_delete AFTER DELETE ON services BEGIN
    DELETE FROM search_index WHERE content_type = 'service' AND content_id = OLD.id;
END;
