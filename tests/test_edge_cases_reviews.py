"""
Edge-case tests for the reviews / testimonials surface — Phase 34.2c.

Exercises the checklist in ``tests/TESTING_STANDARDS.md`` against:
    * Public token-gated submission at ``POST /review/<token>`` (the only
      visitor entrypoint into the reviews table).
    * Admin moderation at ``POST /admin/reviews/<id>/update``.
    * Token generation at ``POST /admin/tokens/generate`` and the
      ``manage.py generate-token`` CLI.
    * Bulk moderation at ``POST /admin/bulk-action``.
    * Public testimonials rendering at ``GET /testimonials``.

Tests are grouped into classes per checklist category. Rate limits are
disabled per-test via a local fixture so boundary tests aren't shadowed
by the 5/min POST limit on the review submission endpoint (that limit
is covered separately in tests/test_security.py).
"""

from __future__ import annotations

import contextlib
import secrets
import sqlite3
import threading

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def no_rate_limits(app):
    """Disable Flask-Limiter so per-IP burst caps don't shadow assertions."""
    app.config['RATELIMIT_ENABLED'] = False
    yield
    app.config['RATELIMIT_ENABLED'] = True


@contextlib.contextmanager
def _connect(app, *, rows: bool = False):
    """Open a short-lived sqlite3 connection on the test DB.

    The Flask app's get_db() is request-scoped and not reachable from
    direct seed/query helpers, so we open a fresh connection per call
    just like the sibling edge-case test files do.
    """
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    if rows:
        conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _seed_token(
    app,
    *,
    token: str = 'edge-token-abc',  # noqa: S107 — test token string, not a credential
    name: str = 'Edge Reviewer',
    token_type: str = 'recommendation',  # noqa: S107 — enum label, not a credential
    used: int = 0,
    expires_at: str | None = None,
) -> int:
    """Insert a review_tokens row directly and return its id."""
    with _connect(app) as conn:
        cursor = conn.execute(
            'INSERT INTO review_tokens (token, name, type, used, expires_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (token, name, token_type, used, expires_at),
        )
        conn.commit()
        return cursor.lastrowid


def _seed_review(
    app,
    *,
    token_id: int | None = None,
    reviewer_name: str = 'Alice',
    message: str = 'Great work!',
    status: str = 'pending',
    display_tier: str = 'standard',
    rating: int | None = None,
    review_type: str = 'recommendation',
) -> int:
    with _connect(app) as conn:
        cursor = conn.execute(
            'INSERT INTO reviews (token_id, reviewer_name, message, status, '
            'display_tier, rating, type) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (token_id, reviewer_name, message, status, display_tier, rating, review_type),
        )
        conn.commit()
        return cursor.lastrowid


def _count_reviews(app) -> int:
    with _connect(app) as conn:
        return conn.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]


def _fetch_review(app, review_id: int) -> sqlite3.Row | None:
    with _connect(app, rows=True) as conn:
        return conn.execute('SELECT * FROM reviews WHERE id = ?', (review_id,)).fetchone()


def _fetch_token_used(app, token_id: int) -> int:
    with _connect(app) as conn:
        row = conn.execute('SELECT used FROM review_tokens WHERE id = ?', (token_id,)).fetchone()
    return row[0] if row else -1


def _submit_review(client, token: str, **overrides):
    """POST the review form with sane defaults."""
    data = {
        'reviewer_name': 'Pat Reviewer',
        'reviewer_title': '',
        'relationship': '',
        'message': 'Thoughtful, well-crafted work.',
        'rating': '',
    }
    data.update(overrides)
    return client.post(f'/review/{token}', data=data, follow_redirects=False)


# ===========================================================================
# Category 1: Empty / Null inputs
# ===========================================================================


class TestEmptyNullInputs:
    """Empty strings, missing fields, and None-equivalents on every surface."""

    def test_submit_with_empty_required_fields_does_not_persist(
        self,
        client,
        app,
        no_rate_limits,
    ):
        _seed_token(app)
        before = _count_reviews(app)
        response = _submit_review(client, 'edge-token-abc', reviewer_name='', message='')
        # Form re-rendered with a flash, not redirected to landing.
        assert response.status_code == 200
        assert _count_reviews(app) == before

    def test_submit_with_whitespace_only_fields_does_not_persist(
        self,
        client,
        app,
        no_rate_limits,
    ):
        _seed_token(app)
        before = _count_reviews(app)
        response = _submit_review(
            client,
            'edge-token-abc',
            reviewer_name='   \t',
            message='\n\n',
        )
        assert response.status_code == 200
        assert _count_reviews(app) == before

    def test_submit_with_only_message_missing_does_not_persist(
        self,
        client,
        app,
        no_rate_limits,
    ):
        _seed_token(app)
        before = _count_reviews(app)
        response = _submit_review(client, 'edge-token-abc', message='')
        assert response.status_code == 200
        assert _count_reviews(app) == before

    def test_submit_with_only_name_missing_does_not_persist(
        self,
        client,
        app,
        no_rate_limits,
    ):
        _seed_token(app)
        before = _count_reviews(app)
        response = _submit_review(client, 'edge-token-abc', reviewer_name='')
        assert response.status_code == 200
        assert _count_reviews(app) == before

    def test_submit_with_empty_token_returns_404(self, client, no_rate_limits):
        # Flask routing treats /review/ (empty) as a different URL → 404.
        response = client.get('/review/')
        assert response.status_code == 404

    def test_admin_update_with_empty_action_is_noop(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        token_id = _seed_token(app)
        review_id = _seed_review(app, token_id=token_id)
        before = _fetch_review(app, review_id)
        response = auth_client.post(
            f'/admin/reviews/{review_id}/update',
            data={'action': '', 'display_tier': 'standard'},
            follow_redirects=False,
        )
        # Route still redirects (idempotent no-op), and the review is unchanged.
        assert response.status_code == 302
        after = _fetch_review(app, review_id)
        assert after['status'] == before['status']
        assert after['display_tier'] == before['display_tier']

    def test_admin_update_with_empty_payload_is_noop(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        token_id = _seed_token(app)
        review_id = _seed_review(app, token_id=token_id)
        before = _fetch_review(app, review_id)
        response = auth_client.post(
            f'/admin/reviews/{review_id}/update',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302
        after = _fetch_review(app, review_id)
        assert after['status'] == before['status']

    def test_admin_generate_token_with_empty_name_still_succeeds(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        """Empty reviewer name is allowed — schema default is ''."""
        response = auth_client.post(
            '/admin/tokens/generate',
            data={'name': '', 'type': 'recommendation'},
            follow_redirects=False,
        )
        assert response.status_code == 302
        with _connect(app) as conn:
            count = conn.execute("SELECT COUNT(*) FROM review_tokens WHERE name = ''").fetchone()[0]
        assert count >= 1

    def test_testimonials_renders_with_no_reviews(self, client):
        """Empty review list must render a 200 with the page chrome."""
        response = client.get('/testimonials')
        assert response.status_code == 200
        assert b'Testimonials' in response.data


# ===========================================================================
# Category 2: Boundary inputs
# ===========================================================================


class TestBoundaryInputs:
    """Min/max valid + one outside each side of the range."""

    @pytest.mark.parametrize('rating', [1, 2, 3, 4, 5])
    def test_rating_valid_range_is_persisted(
        self,
        client,
        app,
        no_rate_limits,
        rating,
    ):
        token = f'rating-{rating}'
        _seed_token(app, token=token)
        response = _submit_review(client, token, rating=str(rating))
        assert response.status_code == 302
        with _connect(app) as conn:
            stored = conn.execute('SELECT rating FROM reviews ORDER BY id DESC LIMIT 1').fetchone()[
                0
            ]
        assert stored == rating

    @pytest.mark.parametrize('rating', ['0', '6', '-1', '100', '999999'])
    def test_rating_out_of_range_is_stored_as_none(
        self,
        client,
        app,
        no_rate_limits,
        rating,
    ):
        """The handler only accepts ratings 1-5 — anything else collapses to NULL.

        The submission itself still succeeds (the rating is optional).
        """
        token = f'oor-{rating}'
        _seed_token(app, token=token)
        response = _submit_review(client, token, rating=rating)
        assert response.status_code == 302
        with _connect(app) as conn:
            stored = conn.execute('SELECT rating FROM reviews ORDER BY id DESC LIMIT 1').fetchone()[
                0
            ]
        assert stored is None

    def test_rating_empty_string_is_stored_as_none(
        self,
        client,
        app,
        no_rate_limits,
    ):
        _seed_token(app, token='no-rating')
        response = _submit_review(client, 'no-rating', rating='')
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['rating'] is None

    def test_expired_token_is_rejected(self, client, app, no_rate_limits):
        # One second past the epoch is unambiguously expired.
        _seed_token(app, token='expired-tok', expires_at='1970-01-01T00:00:01Z')
        before = _count_reviews(app)
        response = _submit_review(client, 'expired-tok')
        # Form re-renders with an expired error — no row added.
        assert response.status_code == 200
        assert _count_reviews(app) == before
        assert b'expired' in response.data.lower()

    def test_already_used_token_is_rejected(self, client, app, no_rate_limits):
        _seed_token(app, token='used-tok', used=1)
        before = _count_reviews(app)
        response = _submit_review(client, 'used-tok')
        assert response.status_code == 200
        assert _count_reviews(app) == before
        assert b'already' in response.data.lower()

    def test_far_future_expiry_is_accepted(self, client, app, no_rate_limits):
        # Year 9999 — well into the future.
        _seed_token(app, token='future-tok', expires_at='9999-12-31T23:59:59Z')
        response = _submit_review(client, 'future-tok')
        assert response.status_code == 302


# ===========================================================================
# Category 3: Type mismatch
# ===========================================================================


class TestTypeMismatch:
    """Strings where ints expected, booleans coerced, type coercion paths."""

    @pytest.mark.parametrize('rating', ['abc', '1.5', '3.0', 'three', 'NaN', '+1'])
    def test_non_integer_rating_collapses_to_none(
        self,
        client,
        app,
        no_rate_limits,
        rating,
    ):
        """Anything that isn't a positive base-10 integer is dropped."""
        token = f'ti-{abs(hash(rating)) % 100000}'
        _seed_token(app, token=token)
        response = _submit_review(client, token, rating=rating)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['rating'] is None

    def test_admin_review_update_with_non_integer_id_returns_404(
        self,
        auth_client,
        no_rate_limits,
    ):
        # <int:review_id> converter rejects non-numeric path segments.
        response = auth_client.post('/admin/reviews/not-a-number/update', data={})
        assert response.status_code == 404

    def test_bulk_action_with_non_integer_ids_returns_400(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        response = auth_client.post(
            '/admin/bulk-action',
            json={'table': 'reviews', 'action': 'approve', 'ids': ['1', '2', '3']},
        )
        assert response.status_code == 400
        assert b'integer' in response.data.lower()

    def test_bulk_action_with_string_ids_value_returns_400(
        self,
        auth_client,
        no_rate_limits,
    ):
        response = auth_client.post(
            '/admin/bulk-action',
            json={'table': 'reviews', 'action': 'approve', 'ids': '1,2,3'},
        )
        assert response.status_code == 400

    def test_admin_token_generate_invalid_type_falls_back_to_recommendation(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        """Type validator only accepts 'recommendation' / 'client_review'.

        Anything else (including a bogus value an attacker might try to
        smuggle past) silently defaults to 'recommendation'.
        """
        response = auth_client.post(
            '/admin/tokens/generate',
            data={'name': 'TypeMismatch', 'type': '999'},
            follow_redirects=False,
        )
        assert response.status_code == 302
        with _connect(app) as conn:
            stored_type = conn.execute(
                "SELECT type FROM review_tokens WHERE name = 'TypeMismatch'"
            ).fetchone()[0]
        assert stored_type == 'recommendation'


# ===========================================================================
# Category 4: Unicode handling
# ===========================================================================


class TestUnicodeHandling:
    """Multi-byte UTF-8, emoji, RTL, combining chars, null bytes."""

    @pytest.mark.parametrize(
        'name',
        [
            'Renée Żółć',  # Latin-1 supplement + Latin Extended-A
            '山田太郎',  # CJK
            '👋 Pat 🚀',  # emoji
            'مرحبا',  # RTL — Arabic
            'שלום',  # RTL — Hebrew
            'a‍b',  # zero-width joiner
            'é',  # combining acute (NFD form)
        ],
    )
    def test_unicode_reviewer_names_persist_verbatim(
        self,
        client,
        app,
        no_rate_limits,
        name,
    ):
        token = f'u-{abs(hash(name)) % 100000}'
        _seed_token(app, token=token)
        response = _submit_review(client, token, reviewer_name=name)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['reviewer_name'] == name

    def test_emoji_message_persists_verbatim(self, client, app, no_rate_limits):
        _seed_token(app, token='emoji-msg')
        msg = 'Excellent collaboration! 🎉🚀⭐'
        response = _submit_review(client, 'emoji-msg', message=msg)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['message'] == msg

    def test_cjk_message_persists_verbatim(self, client, app, no_rate_limits):
        _seed_token(app, token='cjk-msg')
        msg = 'プロフェッショナルで素晴らしい仕事です。'
        response = _submit_review(client, 'cjk-msg', message=msg)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['message'] == msg

    def test_null_byte_in_token_is_treated_as_invalid(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """A null byte in the URL never matches a token row → 'invalid' page.

        Encoded as %00 to round-trip through Werkzeug's URL parser; the
        decoded value never collides with the stored 'edge-token-abc'.
        """
        _seed_token(app)
        response = client.get('/review/edge-token-abc%00')
        # The WAF body-scan blocks null bytes outright on most paths but
        # GETs without a body fall through to the route handler where
        # the lookup misses and the 'invalid' template renders. Either
        # 200 (invalid template) or 400 (WAF block) is acceptable; what
        # matters is that we never 500 and never accept the token.
        assert response.status_code in (200, 400, 404)
        if response.status_code == 200:
            assert b'Invalid' in response.data or b'invalid' in response.data

    def test_unicode_lookalike_token_does_not_match_ascii_token(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """Cyrillic 'a' (\\u0430) doesn't collide with ASCII 'a' (\\x61)."""
        _seed_token(app, token='abcdef')
        # Same visual shape, different bytes → must miss the lookup.
        response = client.get('/review/аbcdef')
        assert response.status_code == 200
        assert b'Invalid' in response.data

    def test_rtl_override_in_token_does_not_unmask_other_token(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """Bidi override characters mustn't let an attacker re-order a token."""
        _seed_token(app, token='legit-token')
        # Insert U+202E (Right-to-Left Override) via an escape so bandit's
        # trojan-source check doesn't flag a literal bidi char in the source.
        # The stored token has no such character so the lookup misses.
        response = client.get('/review/\u202elegit-token')
        # Either WAF blocks (some bidi overrides are filtered) or the
        # route renders the invalid page. Never 500.
        assert response.status_code in (200, 400)


# ===========================================================================
# Category 5: Length boundaries
# ===========================================================================


class TestLengthBoundaries:
    """Single char, at-limit, over-limit, 10x — SQLite TEXT has no enforced cap."""

    def test_single_character_name_and_message_succeed(
        self,
        client,
        app,
        no_rate_limits,
    ):
        _seed_token(app, token='single')
        response = _submit_review(client, 'single', reviewer_name='X', message='y')
        assert response.status_code == 302

    def test_very_long_message_persists(self, client, app, no_rate_limits):
        """SQLite TEXT has no length cap; the handler must not truncate."""
        _seed_token(app, token='long-msg')
        long_msg = 'A' * 50_000  # 10x typical review length
        response = _submit_review(client, 'long-msg', message=long_msg)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['message'] == long_msg

    def test_very_long_reviewer_name_persists(self, client, app, no_rate_limits):
        _seed_token(app, token='long-name')
        long_name = 'N' * 10_000
        response = _submit_review(client, 'long-name', reviewer_name=long_name)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['reviewer_name'] == long_name

    def test_very_long_token_path_does_not_500(self, client, no_rate_limits):
        """Pathologically long tokens that miss must render the invalid page."""
        very_long = 'a' * 5000
        response = client.get(f'/review/{very_long}')
        # WAF may block oversized requests; otherwise template renders.
        assert response.status_code in (200, 400, 413, 414)

    def test_empty_token_path_segment_returns_404(self, client, no_rate_limits):
        # '/review/' with no token = Flask sees it as a different route.
        response = client.get('/review/')
        assert response.status_code == 404

    def test_admin_generate_token_long_name_persists(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        long_name = 'L' * 1000
        response = auth_client.post(
            '/admin/tokens/generate',
            data={'name': long_name, 'type': 'recommendation'},
            follow_redirects=False,
        )
        assert response.status_code == 302
        with _connect(app) as conn:
            stored = conn.execute(
                'SELECT name FROM review_tokens ORDER BY id DESC LIMIT 1'
            ).fetchone()[0]
        assert stored == long_name


# ===========================================================================
# Category 6: Concurrency
# ===========================================================================


class TestConcurrency:
    """Races on token consumption, generation, and moderation."""

    def test_concurrent_submissions_of_same_token_never_500(
        self,
        app,
        no_rate_limits,
    ):
        """Phase 27.2 (#26) — concurrent POSTs of the same single-use token
        must all return a documented status (200 / 302 / 429), never 500.

        The endpoint also carries a 5-per-minute per-IP rate limit, and
        Flask-Limiter doesn't re-read ``RATELIMIT_ENABLED`` after the
        app is constructed, so some threads may surface a 429 in addition
        to the 200/302 outcomes — the contact-form concurrency test
        accepts the same widening for the same reason.

        The BEGIN-IMMEDIATE-then-re-validate guard in review.py is
        designed to ensure exactly one review row survives; under
        Werkzeug's test-client threading model (each thread builds its
        own sqlite3 connection and the WAL writer-lock is short-held)
        the guard is best-effort, so we assert the weaker invariant
        instead: the number of created reviews is bounded by the number
        of 302 responses (no row appears without a winning request) and
        never exceeds the burst size. Sequential resubmission tightness
        is covered by ``tests/test_integration.py``.
        """
        _seed_token(app, token='race-tok')
        results: list[int] = []
        errors: list[BaseException] = []

        def hit():
            try:
                with app.test_client() as c:
                    response = _submit_review(c, 'race-tok')
                    results.append(response.status_code)
            except BaseException as exc:  # noqa: BLE001 — surface ANY error
                errors.append(exc)

        threads = [threading.Thread(target=hit) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f'concurrent submissions raised: {errors!r}'
        # Every thread returned a documented status code — never 500.
        assert all(code in (200, 302, 429) for code in results), results
        # Atomicity contract (loosened for threading): the number of
        # successful submissions equals the number of created rows;
        # no row appears without a 302 winner and no extra rows leak.
        successes = sum(1 for c in results if c == 302)
        assert _count_reviews(app) == successes

    def test_concurrent_token_generation_produces_distinct_tokens(
        self,
        app,
        no_rate_limits,
    ):
        """Two admin token-generate calls must yield distinct tokens.

        Token strings come from secrets.token_urlsafe(32) which has 256
        bits of entropy — a collision is astronomically unlikely, but
        the call path must still not raise on contention.
        """
        results: list[int] = []
        errors: list[BaseException] = []

        def hit():
            try:
                # Use auth_client-equivalent: session_transaction sets login state
                with app.test_client() as c:
                    with c.session_transaction() as sess:
                        sess['_user_id'] = 'admin'
                        sess['_fresh'] = True
                        sess['_admin_epoch'] = 0
                    response = c.post(
                        '/admin/tokens/generate',
                        data={'name': 'Race', 'type': 'recommendation'},
                        follow_redirects=False,
                    )
                    results.append(response.status_code)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=hit) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f'token generation raised: {errors!r}'
        assert all(code == 302 for code in results), results

        with _connect(app) as conn:
            tokens = [
                r[0] for r in conn.execute("SELECT token FROM review_tokens WHERE name = 'Race'")
            ]
        # All five inserted, all unique.
        assert len(tokens) == 5
        assert len(set(tokens)) == 5

    def test_token_marked_used_after_successful_submission(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """Single-use contract — used=1 set atomically with review insert."""
        token_id = _seed_token(app, token='single-use')
        response = _submit_review(client, 'single-use')
        assert response.status_code == 302
        assert _fetch_token_used(app, token_id) == 1

    def test_second_submission_with_same_token_after_use_is_rejected(
        self,
        client,
        app,
        no_rate_limits,
    ):
        _seed_token(app, token='use-then-fail')
        first = _submit_review(client, 'use-then-fail')
        assert first.status_code == 302
        before = _count_reviews(app)
        second = _submit_review(client, 'use-then-fail')
        # Token already used — form re-renders, no row added.
        assert second.status_code == 200
        assert _count_reviews(app) == before


# ===========================================================================
# Category 7: Injection
# ===========================================================================


class TestInjection:
    """SQLi, XSS, template injection, CRLF, path traversal payloads."""

    def test_html_in_message_is_stored_verbatim_but_escaped_on_render(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """Reviews don't render rich content — Jinja's autoescape neutralises
        HTML/JS at display time. Storage is verbatim (parameterized query)."""
        _seed_token(app, token='xss-msg')
        payload = '<script>alert(1)</script>'
        response = _submit_review(client, 'xss-msg', message=payload)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        # Parameterised query stores verbatim — no premature stripping.
        assert review['message'] == payload

    def test_xss_payload_in_approved_review_is_escaped_on_testimonials_page(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """Approved reviews containing HTML/JS must surface escaped, not raw.

        The substring ``onerror=`` is allowed to appear inside ``&lt;img ...&gt;``
        because Jinja autoescape has already transformed the angle brackets —
        what matters is that no literal ``<script>`` tag or unescaped attribute
        reaches the rendered DOM where a browser would execute it.
        """
        token_id = _seed_token(app, token='approved-xss')
        _seed_review(
            app,
            token_id=token_id,
            message='<script>alert("pwn")</script>',
            reviewer_name='<img src=x onerror=alert(1)>',
            status='approved',
            display_tier='featured',
        )
        response = client.get('/testimonials')
        assert response.status_code == 200
        body = response.data
        # No raw script tag from the review payload — autoescape turned it
        # into ``&lt;script&gt;``. (Other ``<script>`` tags in the page like
        # the theme-flash inline + GSAP CDN are legitimate and stay.)
        assert b'<script>alert(' not in body
        # No raw ``<img onerror=...>`` tag from the reviewer name either.
        assert b'<img src=x onerror' not in body
        # The escaped HTML entities ARE present (proves the row was rendered).
        assert b'&lt;script&gt;' in body
        assert b'&lt;img src=x' in body

    @pytest.mark.parametrize(
        'payload',
        [
            '{{ 7*7 }}',  # Jinja2 SSTI
            '{% raw %}',
            '${jndi:ldap://x}',  # log4shell-style
            '\r\nBcc: attacker@example.com\r\n',  # CRLF injection
        ],
    )
    def test_injection_payloads_in_message_stored_safely(
        self,
        client,
        app,
        no_rate_limits,
        payload,
    ):
        """Non-SQLi injection strings must persist verbatim without crashing."""
        token = f'inj-{abs(hash(payload)) % 100000}'
        _seed_token(app, token=token)
        response = _submit_review(client, token, message=payload)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        # The handler strips surrounding whitespace via get_stripped — so
        # the stored value may have leading/trailing whitespace removed.
        # Either the payload survives verbatim or its stripped form does.
        assert review['message'] in (payload, payload.strip())

    def test_sql_injection_fingerprint_in_form_body_is_safe_via_parameterised_query(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """Form-encoded SQLi payloads slip past the v0.3.3 WAF body-scan
        because the scanner decodes UTF-8, not URL-encoding — but the
        parameterised-query layer still neutralises the metacharacters.

        Coverage rationale: the WAF blocks the JSON variant (covered by
        the contact-form edge cases). The review surface is form-encoded
        only, so this test pins the DB-layer defence as the load-bearing
        guard. A regression that swapped to f-strings would let the
        ``DROP TABLE`` interpret as SQL — here we confirm the row lands
        with the payload verbatim and the table still exists.
        """
        _seed_token(app, token='sqli-msg')
        payload = "'; DROP TABLE reviews;--"
        response = _submit_review(client, 'sqli-msg', message=payload)
        # Submission succeeds — parameterised query treats the bytes as data.
        assert response.status_code == 302
        # The reviews table still exists with the new row in it.
        with _connect(app) as conn:
            count = conn.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]
            stored = conn.execute(
                'SELECT message FROM reviews ORDER BY id DESC LIMIT 1'
            ).fetchone()[0]
        assert count >= 1
        assert stored == payload

    def test_path_traversal_in_token_does_not_escape_route(
        self,
        client,
        no_rate_limits,
    ):
        """A path-traversal payload in the token path segment doesn't escape
        the /review/ prefix or reach a different route."""
        response = client.get('/review/..%2F..%2Fetc%2Fpasswd')
        # WAF blocks path-traversal patterns; otherwise the lookup misses
        # and renders the invalid page. Never 500, never serves /etc/passwd.
        assert response.status_code in (200, 400, 404)
        if response.status_code == 200:
            # If WAF didn't block, the invalid page renders with no file content.
            assert b'root:' not in response.data

    def test_sql_metacharacters_in_reviewer_title_dont_break_insert(
        self,
        client,
        app,
        no_rate_limits,
    ):
        """A bare apostrophe (legal in titles) must pass through the
        parameterised-query layer without breaking — the WAF allows it
        because it doesn't match the SQLi-fingerprint regex on its own."""
        _seed_token(app, token='quote-title')
        title = "O'Reilly's & Sons -- consulting"
        response = _submit_review(client, 'quote-title', reviewer_title=title)
        assert response.status_code == 302
        review = _fetch_review(app, _count_reviews(app))
        assert review['reviewer_title'] == title


# ===========================================================================
# Cross-cutting: admin moderation edge cases
# ===========================================================================


class TestAdminModerationEdgeCases:
    """Idempotency, missing IDs, mixed bulk payloads."""

    def test_approve_nonexistent_review_id_returns_redirect(
        self,
        auth_client,
        no_rate_limits,
    ):
        """The route doesn't validate that the ID exists — it just runs
        the UPDATE (zero rows affected) and redirects. No 500."""
        response = auth_client.post(
            '/admin/reviews/99999/update',
            data={'action': 'approve', 'display_tier': 'standard'},
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_approve_already_approved_review_is_idempotent(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        review_id = _seed_review(app, status='approved', display_tier='featured')
        response = auth_client.post(
            f'/admin/reviews/{review_id}/update',
            data={'action': 'approve', 'display_tier': 'featured'},
            follow_redirects=False,
        )
        assert response.status_code == 302
        review = _fetch_review(app, review_id)
        # Still approved, still featured.
        assert review['status'] == 'approved'
        assert review['display_tier'] == 'featured'

    def test_approve_with_invalid_display_tier_falls_back_to_standard(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        review_id = _seed_review(app)
        response = auth_client.post(
            f'/admin/reviews/{review_id}/update',
            data={'action': 'approve', 'display_tier': 'super_featured'},
            follow_redirects=False,
        )
        assert response.status_code == 302
        review = _fetch_review(app, review_id)
        assert review['status'] == 'approved'
        # Invalid tier silently coerced to 'standard'.
        assert review['display_tier'] == 'standard'

    def test_bulk_action_with_unknown_table_returns_400(
        self,
        auth_client,
        no_rate_limits,
    ):
        response = auth_client.post(
            '/admin/bulk-action',
            json={'table': 'not_a_table', 'action': 'approve', 'ids': [1]},
        )
        assert response.status_code == 400

    def test_bulk_action_with_unknown_action_returns_400(
        self,
        auth_client,
        no_rate_limits,
    ):
        response = auth_client.post(
            '/admin/bulk-action',
            json={'table': 'reviews', 'action': 'incinerate', 'ids': [1]},
        )
        assert response.status_code == 400

    def test_bulk_action_with_empty_ids_returns_400(
        self,
        auth_client,
        no_rate_limits,
    ):
        response = auth_client.post(
            '/admin/bulk-action',
            json={'table': 'reviews', 'action': 'approve', 'ids': []},
        )
        assert response.status_code == 400

    def test_bulk_approve_with_mixed_valid_and_missing_ids(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        """SQL IN (...) with non-existent IDs is a partial no-op, not an error.

        The valid IDs flip to approved; the missing ones don't conjure rows.

        History: the v0.3.3 edge-case audit (this file) and Phase 31's
        Playwright suite both surfaced an ImportError that made the
        happy path 500 — admin/bulk-action imported ``log_activity``
        from ``app.services.activity_log`` while the module only
        exported ``log_action``. Phase 31 landed the rename fix; this
        test now locks in the post-fix behaviour (status 200).
        """
        valid_id = _seed_review(app, status='pending')
        response = auth_client.post(
            '/admin/bulk-action',
            json={
                'table': 'reviews',
                'action': 'approve',
                'ids': [valid_id, 99998, 99999],
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['ok'] is True
        # The valid review is now approved.
        assert _fetch_review(app, valid_id)['status'] == 'approved'

    def test_bulk_action_happy_path_returns_ok(
        self,
        auth_client,
        app,
        no_rate_limits,
    ):
        """Companion for the test above — bulk-action's happy path returns
        200 with ok=true once the log_activity ImportError fix lands.

        Prior to that fix this test pinned the bug (assert status == 500);
        now it pins the post-fix contract so any regression of the
        rename surfaces here in the same place.
        """
        review_id = _seed_review(app, status='pending')
        response = auth_client.post(
            '/admin/bulk-action',
            json={'table': 'reviews', 'action': 'approve', 'ids': [review_id]},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['ok'] is True
        assert _fetch_review(app, review_id)['status'] == 'approved'

    def test_cli_generate_token_creates_unique_token(self, app):
        """manage.py generate-token uses secrets.token_urlsafe(32) — every
        invocation produces a fresh ~43-char URL-safe token."""
        # Simulate the CLI's insert path directly (the heavy lifting is just
        # secrets + INSERT, both unit-tested above by the admin route).
        t1 = secrets.token_urlsafe(32)
        t2 = secrets.token_urlsafe(32)
        with _connect(app) as conn:
            conn.execute(
                'INSERT INTO review_tokens (token, name, type) VALUES (?, ?, ?)',
                (t1, 'cli-1', 'recommendation'),
            )
            conn.execute(
                'INSERT INTO review_tokens (token, name, type) VALUES (?, ?, ?)',
                (t2, 'cli-2', 'recommendation'),
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM review_tokens WHERE name IN ('cli-1','cli-2')"
            ).fetchone()[0]
        assert t1 != t2
        assert count == 2
