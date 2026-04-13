"""Regression tests for the Phase 12.1 N+1 eliminations.

Locks in the query-count contract for two batched loaders:

  * `app.models.get_skill_domains_with_skills` — must issue exactly two
    queries (one for domains, one batched IN-clause for skills) regardless
    of how many domains exist.

  * `app.services.blog.get_tags_for_posts` — must issue at most one query
    per call regardless of post count, and zero queries on empty input.

These tests instrument sqlite3 via `set_trace_callback` so they observe
the *real* query stream from the connection used by the code under test.
"""

import sqlite3

import pytest

from app.models import get_skill_domains_with_skills
from app.services.blog import get_tags_for_post, get_tags_for_posts


@pytest.fixture
def counting_db(app):
    """Yield (conn, queries) where queries is a list every executed SQL
    statement is appended to. Caller can len(queries) to count, and clear
    between phases of a test."""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    queries: list[str] = []
    conn.set_trace_callback(queries.append)
    yield conn, queries
    conn.close()


# ---------------------------------------------------------------------------
# Skills loader
# ---------------------------------------------------------------------------


def _seed_skills(conn, num_domains, skills_per_domain):
    """Insert N visible domains, each with K visible skills."""
    for d in range(num_domains):
        cursor = conn.execute(
            'INSERT INTO skill_domains (name, sort_order, visible) VALUES (?, ?, 1)',
            (f'Domain {d}', d),
        )
        domain_id = cursor.lastrowid
        for s in range(skills_per_domain):
            conn.execute(
                'INSERT INTO skills (domain_id, name, sort_order, visible) VALUES (?, ?, ?, 1)',
                (domain_id, f'Skill {d}.{s}', s),
            )
    conn.commit()


def test_get_skill_domains_with_skills_uses_two_queries(counting_db):
    """Five domains with three skills each must still be exactly two SELECTs."""
    conn, queries = counting_db
    _seed_skills(conn, num_domains=5, skills_per_domain=3)
    queries.clear()

    result = get_skill_domains_with_skills(conn)

    selects = [q for q in queries if q.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 2, f'expected 2 SELECTs, got {len(selects)}: {selects}'
    assert len(result) == 5
    assert all(len(d['skills']) == 3 for d in result)


def test_get_skill_domains_with_skills_empty(counting_db):
    """No domains should short-circuit to a single SELECT (the domains query)."""
    conn, queries = counting_db
    queries.clear()

    result = get_skill_domains_with_skills(conn)

    selects = [q for q in queries if q.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 1
    assert result == []


def test_get_skill_domains_with_skills_preserves_shape(counting_db):
    """The batched implementation must keep the original return shape so
    templates that read `domain.name` / `skill.name` still work."""
    conn, _ = counting_db
    _seed_skills(conn, num_domains=2, skills_per_domain=2)

    result = get_skill_domains_with_skills(conn)

    assert isinstance(result, list)
    assert set(result[0].keys()) == {'domain', 'skills'}
    # Skill rows still expose the underlying columns by name. sqlite3.Row
    # supports __getitem__ but not __contains__, so check via .keys().
    skill = result[0]['skills'][0]
    skill_columns = list(skill.keys())
    assert 'name' in skill_columns
    assert 'domain_id' in skill_columns


# ---------------------------------------------------------------------------
# Blog tags batch loader
# ---------------------------------------------------------------------------


def _seed_blog_posts_with_tags(conn, num_posts, tags_per_post):
    """Insert N published posts, each with K tags. Returns the post IDs."""
    post_ids = []
    for p in range(num_posts):
        cursor = conn.execute(
            'INSERT INTO blog_posts (slug, title, content, status, published_at) '
            "VALUES (?, ?, '', 'published', '2026-01-01T00:00:00Z')",
            (f'post-{p}', f'Post {p}'),
        )
        post_ids.append(cursor.lastrowid)
    for t in range(tags_per_post):
        cursor = conn.execute(
            'INSERT INTO blog_tags (name, slug) VALUES (?, ?)',
            (f'Tag{t}', f'tag-{t}'),
        )
        tag_id = cursor.lastrowid
        for pid in post_ids:
            conn.execute(
                'INSERT INTO blog_post_tags (post_id, tag_id) VALUES (?, ?)',
                (pid, tag_id),
            )
    conn.commit()
    return post_ids


def test_get_tags_for_posts_one_query_for_many(counting_db):
    """Ten posts with three tags each: must be exactly ONE SELECT, not eleven."""
    conn, queries = counting_db
    post_ids = _seed_blog_posts_with_tags(conn, num_posts=10, tags_per_post=3)
    queries.clear()

    result = get_tags_for_posts(conn, post_ids)

    selects = [q for q in queries if q.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 1, f'expected 1 SELECT, got {len(selects)}: {selects}'
    assert all(len(result[pid]) == 3 for pid in post_ids)


def test_get_tags_for_posts_empty_input_no_query(counting_db):
    """Empty post list must not hit the database at all."""
    conn, queries = counting_db
    queries.clear()

    result = get_tags_for_posts(conn, [])

    assert result == {}
    assert queries == []


def test_get_tags_for_posts_includes_post_with_no_tags(counting_db):
    """Every requested post_id must appear in the result, even with no tags."""
    conn, _ = counting_db
    post_ids = _seed_blog_posts_with_tags(conn, num_posts=2, tags_per_post=0)

    result = get_tags_for_posts(conn, post_ids)

    assert set(result.keys()) == set(post_ids)
    assert all(result[pid] == [] for pid in post_ids)


def test_get_tags_for_posts_matches_get_tags_for_post(counting_db):
    """The batched and per-post APIs must return equivalent data."""
    conn, _ = counting_db
    post_ids = _seed_blog_posts_with_tags(conn, num_posts=3, tags_per_post=2)

    batched = get_tags_for_posts(conn, post_ids)
    for pid in post_ids:
        single = get_tags_for_post(conn, pid)
        # Compare by the unique tag id since name/slug are equal in the seed
        assert sorted(t['id'] for t in batched[pid]) == sorted(t['id'] for t in single)
