"""
``time_ago`` Filter Tests — Phase 17.2

Covers ``app.services.time_helpers.time_ago``, the Jinja filter that
renders the admin dashboard's backup health card. The filter is
deliberately stdlib-only and locale-agnostic; these tests pin the
contract so future edits can't silently regress.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.time_helpers import time_ago

# ---------------------------------------------------------------------------
# Reference "now" — every test passes this in so wall-clock skew can't
# make the suite flaky.
# ---------------------------------------------------------------------------

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Falsy / unparseable input → "never"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('value', [None, '', '   ', 'not-a-timestamp', '2026-13-99T99:99:99Z'])
def test_falsy_or_garbage_returns_never(value):
    assert time_ago(value, now=NOW) == 'never'


# ---------------------------------------------------------------------------
# Past intervals — bucket boundaries
# ---------------------------------------------------------------------------


def _ago(seconds):
    """Return an ISO-8601 string for `seconds` before NOW."""
    return (NOW - timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


def test_just_now_under_one_second():
    assert time_ago(_ago(0.4), now=NOW) == 'just now'


def test_seconds_singular_and_plural():
    assert time_ago(_ago(1), now=NOW) == '1 second ago'
    assert time_ago(_ago(45), now=NOW) == '45 seconds ago'


def test_minutes():
    assert time_ago(_ago(60), now=NOW) == '1 minute ago'
    assert time_ago(_ago(60 * 30), now=NOW) == '30 minutes ago'


def test_hours():
    assert time_ago(_ago(60 * 60), now=NOW) == '1 hour ago'
    assert time_ago(_ago(60 * 60 * 5), now=NOW) == '5 hours ago'


def test_yesterday_special_case():
    # Anything in the [24h, 48h) window collapses to 'yesterday'.
    assert time_ago(_ago(60 * 60 * 24), now=NOW) == 'yesterday'
    assert time_ago(_ago(60 * 60 * 47), now=NOW) == 'yesterday'


def test_days_after_yesterday_window():
    assert time_ago(_ago(60 * 60 * 48), now=NOW) == '2 days ago'
    assert time_ago(_ago(60 * 60 * 24 * 6), now=NOW) == '6 days ago'


def test_weeks():
    assert time_ago(_ago(60 * 60 * 24 * 7), now=NOW) == '1 week ago'
    assert time_ago(_ago(60 * 60 * 24 * 21), now=NOW) == '3 weeks ago'


def test_months():
    assert time_ago(_ago(60 * 60 * 24 * 31), now=NOW) == '1 month ago'
    assert time_ago(_ago(60 * 60 * 24 * 90), now=NOW) == '3 months ago'


def test_years():
    assert time_ago(_ago(60 * 60 * 24 * 400), now=NOW) == '1 year ago'
    assert time_ago(_ago(60 * 60 * 24 * 365 * 5), now=NOW) == '5 years ago'


# ---------------------------------------------------------------------------
# Future intervals — symmetric "in N <unit>" phrasing
# ---------------------------------------------------------------------------


def _from_now(seconds):
    return (NOW + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


def test_future_seconds():
    assert time_ago(_from_now(30), now=NOW) == 'in 30 seconds'


def test_future_hours():
    assert time_ago(_from_now(60 * 60 * 2), now=NOW) == 'in 2 hours'


def test_tomorrow_special_case():
    assert time_ago(_from_now(60 * 60 * 24), now=NOW) == 'tomorrow'
    assert time_ago(_from_now(60 * 60 * 47), now=NOW) == 'tomorrow'


# ---------------------------------------------------------------------------
# Input format coverage
# ---------------------------------------------------------------------------


def test_accepts_trailing_z_iso():
    """The settings table writes the trailing-Z UTC convention."""
    assert time_ago('2026-04-15T11:55:00Z', now=NOW) == '5 minutes ago'


def test_accepts_offset_iso():
    """datetime.isoformat() emits +00:00 instead of Z."""
    assert time_ago('2026-04-15T11:55:00+00:00', now=NOW) == '5 minutes ago'


def test_accepts_naive_iso_treated_as_utc():
    """A naïve string from a hand-built timestamp must not silently skew."""
    assert time_ago('2026-04-15T11:55:00', now=NOW) == '5 minutes ago'


def test_accepts_datetime_object():
    dt = NOW - timedelta(minutes=5)
    assert time_ago(dt, now=NOW) == '5 minutes ago'


def test_accepts_unix_epoch_float():
    """``BackupEntry.mtime`` is a float (st_mtime) — the dashboard relies on this."""
    epoch = (NOW - timedelta(minutes=5)).timestamp()
    assert time_ago(epoch, now=NOW) == '5 minutes ago'


def test_accepts_unix_epoch_int():
    epoch = int((NOW - timedelta(hours=2)).timestamp())
    assert time_ago(epoch, now=NOW) == '2 hours ago'


def test_bool_does_not_get_treated_as_int():
    """``True``/``False`` are int subclasses; passing them must not render '57 years ago'."""
    # Both should fall through to "never" — they're not real timestamps.
    assert time_ago(True, now=NOW) == 'never'
    assert time_ago(False, now=NOW) == 'never'


# ---------------------------------------------------------------------------
# Jinja registration — sanity check via the app's filter map
# ---------------------------------------------------------------------------


def test_filter_is_registered_on_jinja_env(app):
    assert 'time_ago' in app.jinja_env.filters
    # And it's actually our function, not a stub.
    assert app.jinja_env.filters['time_ago'] is time_ago
