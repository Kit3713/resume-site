"""
Time formatting helpers (Phase 17.2).

Tiny stdlib-only utilities for rendering ISO-8601 timestamps as
human-readable relative strings ("3 hours ago", "yesterday", "in
2 minutes"). Registered as the ``time_ago`` Jinja filter in
``app/__init__.py`` so admin templates can render diagnostic
timestamps consistently — first consumer is the Phase 17.2 backup
health card on the admin dashboard.

The implementation deliberately avoids ``babel`` / ``humanize`` to
keep the public-route render path zero-dep beyond the existing
runtime stack. Locale-aware phrasing can be revisited in Phase 15.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Buckets are checked largest-first; the first matching bucket wins.
# Each tuple is ``(threshold_seconds, divisor_seconds, singular, plural)``.
# Threshold is the upper bound of the previous bucket (so the string
# reads "X minutes" only after we cross 60 s, etc.).
_BUCKETS = (
    (60, 1, 'second', 'seconds'),
    (60 * 60, 60, 'minute', 'minutes'),
    (60 * 60 * 24, 60 * 60, 'hour', 'hours'),
    (60 * 60 * 24 * 7, 60 * 60 * 24, 'day', 'days'),
    (60 * 60 * 24 * 30, 60 * 60 * 24 * 7, 'week', 'weeks'),
    (60 * 60 * 24 * 365, 60 * 60 * 24 * 30, 'month', 'months'),
)
_YEAR_SECONDS = 60 * 60 * 24 * 365


def _parse_iso(value):
    """Coerce ISO strings, datetimes, or Unix epochs into an aware ``datetime``.

    Accepted shapes:

    * ``datetime`` (naïve treated as UTC).
    * ISO-8601 string with trailing ``Z`` or explicit offset.
    * Numeric Unix epoch in seconds (``int`` / ``float``) — emitted by
      ``os.stat`` and by ``BackupEntry.mtime``.

    Returns ``None`` for falsy / unparseable input — callers render
    "never" in that case.
    """
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            dt = datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        try:
            # ``fromisoformat`` rejects the trailing 'Z' before 3.11;
            # normalising preemptively keeps us compatible with the
            # CI matrix (3.11 + 3.12).
            normalised = str(value).strip()
            if normalised.endswith('Z'):
                normalised = normalised[:-1] + '+00:00'
            dt = datetime.fromisoformat(normalised)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        # Treat naïve datetimes as UTC — every writer in the codebase
        # uses UTC, and assuming local time would silently skew the
        # admin dashboard for any operator outside UTC.
        dt = dt.replace(tzinfo=UTC)
    return dt


def time_ago(value: object, *, now: datetime | None = None) -> str:
    """Format an ISO-8601 timestamp (or datetime) as a relative string.

    Examples:
        ``"just now"``     — < 1 second old
        ``"5 seconds ago"``
        ``"3 minutes ago"``
        ``"in 2 hours"``   — future timestamp
        ``"yesterday"``    — exactly 1 day in the past
        ``"2 weeks ago"``
        ``"1 year ago"``

    Returns the literal string ``"never"`` when ``value`` is falsy or
    unparseable so templates can drop the result straight into a
    ``{{ ... }}`` expression without an explicit ``or 'never'`` guard.
    """
    dt = _parse_iso(value)
    if dt is None:
        return 'never'

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    delta = (now - dt).total_seconds()
    future = delta < 0
    delta = abs(delta)

    if delta < 1:
        return 'just now'

    # Special-case "yesterday" / "tomorrow" — reads naturally in a
    # diagnostic widget.
    if 60 * 60 * 24 <= delta < 60 * 60 * 48:
        return 'tomorrow' if future else 'yesterday'

    for threshold, divisor, singular, plural in _BUCKETS:
        if delta < threshold:
            count = int(delta // divisor)
            unit = singular if count == 1 else plural
            return f'in {count} {unit}' if future else f'{count} {unit} ago'

    # Anything older than a year falls out the bottom of the bucket
    # ladder; render in years.
    count = int(delta // _YEAR_SECONDS)
    unit = 'year' if count == 1 else 'years'
    return f'in {count} {unit}' if future else f'{count} {unit} ago'
