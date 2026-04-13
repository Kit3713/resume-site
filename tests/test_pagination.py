"""Tests for the shared pagination helpers (app/services/pagination.py).

These are pure-function utilities — tests don't need a Flask context
or a DB fixture.
"""

import pytest

from app.services.pagination import Pagination, clamp_page, offset_for, paginate


class TestClampPage:
    def test_positive_int_passes_through(self):
        assert clamp_page(5) == 5

    def test_one_passes_through(self):
        assert clamp_page(1) == 1

    @pytest.mark.parametrize('bad', [0, -1, -100])
    def test_non_positive_clamps_to_one(self, bad):
        assert clamp_page(bad) == 1

    @pytest.mark.parametrize('bad', [None, 'abc', '', 'foo'])
    def test_non_numeric_clamps_to_one(self, bad):
        assert clamp_page(bad) == 1

    def test_numeric_string_coerces(self):
        assert clamp_page('3') == 3

    def test_float_string_is_rejected(self):
        # int('3.5') raises ValueError → clamp to 1
        assert clamp_page('3.5') == 1


class TestOffsetFor:
    @pytest.mark.parametrize(
        ('page', 'per_page', 'expected'),
        [
            (1, 10, 0),
            (2, 10, 10),
            (5, 10, 40),
            (1, 25, 0),
            (3, 25, 50),
        ],
    )
    def test_basic_math(self, page, per_page, expected):
        assert offset_for(page, per_page) == expected


class TestPaginate:
    def test_exact_multiple(self):
        p = paginate(page=1, per_page=10, total=30)
        assert p.total_pages == 3

    def test_partial_last_page(self):
        p = paginate(page=1, per_page=10, total=25)
        assert p.total_pages == 3  # ceil(25/10)

    def test_single_item(self):
        p = paginate(page=1, per_page=10, total=1)
        assert p.total_pages == 1

    def test_zero_total_still_one_page(self):
        """Templates render 'Page 1 of 1' even when there's nothing to show."""
        p = paginate(page=1, per_page=10, total=0)
        assert p.total_pages == 1

    def test_negative_total_clamps_to_zero(self):
        """Defensive — COUNT(*) shouldn't return negative, but don't trust it."""
        p = paginate(page=1, per_page=10, total=-5)
        assert p.total == 0
        assert p.total_pages == 1

    def test_page_clamped_to_one(self):
        p = paginate(page=0, per_page=10, total=30)
        assert p.page == 1

    def test_zero_per_page_rejected(self):
        with pytest.raises(ValueError, match='per_page must be positive'):
            paginate(page=1, per_page=0, total=10)

    def test_negative_per_page_rejected(self):
        with pytest.raises(ValueError, match='per_page must be positive'):
            paginate(page=1, per_page=-5, total=10)

    def test_has_prev_false_on_first_page(self):
        assert paginate(page=1, per_page=10, total=30).has_prev is False

    def test_has_prev_true_after_first_page(self):
        assert paginate(page=2, per_page=10, total=30).has_prev is True

    def test_has_next_true_when_more_pages(self):
        assert paginate(page=1, per_page=10, total=30).has_next is True

    def test_has_next_false_on_last_page(self):
        assert paginate(page=3, per_page=10, total=30).has_next is False

    def test_pagination_is_frozen_dataclass(self):
        """Ensures callers can't mutate values passed to templates."""
        p = paginate(page=1, per_page=10, total=30)
        with pytest.raises(Exception):  # noqa: B017  # FrozenInstanceError
            p.page = 5  # type: ignore[misc]

    def test_pagination_type_is_dataclass(self):
        assert isinstance(paginate(1, 10, 0), Pagination)
