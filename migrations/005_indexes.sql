-- ============================================================
-- Migration 005: Performance Indexes (v0.3.0 Phase 12.1)
-- ============================================================
--
-- Adds indexes driven by the Phase 12.1 query audit. Every index below
-- corresponds to at least one WHERE / ORDER BY / JOIN clause in the
-- current app codebase (see ROADMAP_v0.3.0.md for the audit catalog).
--
-- Rationale per index is inline. All indexes are `IF NOT EXISTS` so
-- this migration is safe to re-run.
--
-- Pre-existing indexes (from migrations 001-003) are NOT dropped — SQLite
-- picks whichever index best fits each query, and keeping the singles
-- alongside a composite costs very little on a low-write table.
-- ============================================================

-- ------------------------------------------------------------
-- page_views
-- ------------------------------------------------------------
-- path + created_at are already indexed (001_baseline). Adding
-- ip_address for Phase 13's planned IP-based analytics and for the
-- eventual WAF-lite request filter.
CREATE INDEX IF NOT EXISTS idx_page_views_ip
    ON page_views(ip_address);

-- ------------------------------------------------------------
-- blog_posts — composite for the most common public query:
--   SELECT ... FROM blog_posts
--   WHERE status = 'published'
--   ORDER BY published_at DESC
-- SQLite can use this single composite for both the WHERE filter
-- and the ORDER BY, avoiding a separate sort step.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_blog_posts_status_published
    ON blog_posts(status, published_at DESC);

-- ------------------------------------------------------------
-- blog_post_tags — the PRIMARY KEY (post_id, tag_id) already covers
-- the (post_id, *) lookup pattern (leftmost-prefix), but queries that
-- start from tag_id need their own index. Used by get_posts_by_tag()
-- which joins: blog_tags.slug → blog_post_tags.tag_id → blog_posts.id.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_blog_post_tags_tag
    ON blog_post_tags(tag_id);

-- ------------------------------------------------------------
-- reviews — public testimonials listing:
--   SELECT ... FROM reviews
--   WHERE status = 'approved' AND display_tier = ?
--   ORDER BY created_at DESC
-- Composite (status, display_tier) covers both filter columns.
-- created_at is a separate helper index for the ORDER BY fallback.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_reviews_status_tier
    ON reviews(status, display_tier);
CREATE INDEX IF NOT EXISTS idx_reviews_created
    ON reviews(created_at DESC);

-- ------------------------------------------------------------
-- photos — portfolio gallery queries filter by display_tier and
-- order by sort_order. Composite serves both in one index:
--   SELECT ... FROM photos WHERE display_tier = ? ORDER BY sort_order
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_photos_tier_sort
    ON photos(display_tier, sort_order);

-- ------------------------------------------------------------
-- skills — get_skill_domains_with_skills() now batches with a single
-- IN-clause query instead of one query per domain (Phase 12.1 N+1 fix):
--   SELECT * FROM skills WHERE domain_id IN (?, ?, ...) AND visible = 1
-- An index on domain_id lets the planner pick index lookups over a scan
-- once the table grows. Cheap on inserts because skills is a small table.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_skills_domain
    ON skills(domain_id);

-- ------------------------------------------------------------
-- contact_submissions — rate-limit check runs on every contact POST:
--   SELECT COUNT(*) FROM contact_submissions
--   WHERE ip_address = ? AND created_at > ?
-- Composite (ip_address, created_at) is a perfect fit.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_contact_submissions_ip_created
    ON contact_submissions(ip_address, created_at);

-- ------------------------------------------------------------
-- admin_activity_log — created_at is already indexed (003). No
-- additional indexes needed here; queries only filter by created_at.
-- ------------------------------------------------------------

-- ============================================================
-- NOTE ON MAINTENANCE
-- ============================================================
-- After this migration, run `sqlite3 data/site.db 'ANALYZE;'` to
-- update query planner statistics. ANALYZE is fast on small tables
-- and helps SQLite pick the right index when multiple candidates exist.
-- The schema_version tracker in manage.py migrate records this migration
-- as applied; re-running is a no-op thanks to IF NOT EXISTS.
ANALYZE;
