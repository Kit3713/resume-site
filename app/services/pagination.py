"""Pagination utilities (app/services/pagination.py).

Small helpers to compute offset and total_pages consistently across every
paginated route. Keeps the `(page - 1) * per_page` / `ceil(total / per_page)`
math in one place so we can't accidentally off-by-one in one spot and not
another.

The utilities here are pure functions — no DB access, no Flask dependency —
so they're trivial to test and safe to call from anywhere.

Typical usage in a route:

    from app.services.pagination import clamp_page, paginate

    page = clamp_page(request.args.get('page', 1, type=int))
    per_page = int(get_setting(db, 'posts_per_page', '10'))
    offset = (page - 1) * per_page  # or call offset_for(page, per_page)

    posts, total = service.get_things(db, page=page, per_page=per_page)
    pagination = paginate(page=page, per_page=per_page, total=total)

    return render_template(..., page=page, total_pages=pagination.total_pages)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def clamp_page(raw_page: object) -> int:
    """Coerce a user-supplied page number into a safe positive int.

    `None`, negatives, zero, and non-ints all clamp to page 1 rather than
    raising — pagination should never 500 on a malformed `?page=` query string.
    """
    try:
        page = int(raw_page)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 1
    return max(1, page)


def offset_for(page: int, per_page: int) -> int:
    """Return the SQL OFFSET for a given (1-indexed) page + page size.

    Assumes `page` has already been clamped to >= 1 (caller's responsibility).
    """
    return (page - 1) * per_page


@dataclass(frozen=True)
class Pagination:
    """Summary returned by `paginate()` — intended for direct template use.

    Attributes:
        page: The clamped current page number (1-indexed).
        per_page: Rows per page.
        total: Total rows across all pages (the COUNT(*) result).
        total_pages: Number of pages; always >= 1 so templates don't need
            to special-case empty result sets.
        has_prev / has_next: Convenience booleans for pager UI.
    """

    page: int
    per_page: int
    total: int
    total_pages: int

    @property
    def has_prev(self) -> bool:
        """True if a previous page exists (current page > 1)."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """True if a next page exists (current page < total_pages)."""
        return self.page < self.total_pages


def paginate(page: int, per_page: int, total: int) -> Pagination:
    """Build a `Pagination` summary for a result set.

    `total_pages` is always at least 1 (templates render "Page 1 of 1" for
    empty collections rather than "Page 1 of 0").
    """
    if per_page <= 0:
        raise ValueError(f'per_page must be positive, got {per_page!r}')
    safe_page = max(1, int(page))
    total_pages = max(1, math.ceil(max(0, total) / per_page))
    return Pagination(
        page=safe_page, per_page=per_page, total=max(0, total), total_pages=total_pages
    )
