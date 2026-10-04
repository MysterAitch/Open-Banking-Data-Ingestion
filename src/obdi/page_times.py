"""The one way a page writes a date, a month, a range, an instant, an age, and a percentage.

A date is `2026-06-28`; a month `2026-06`; a range `2026-01-10 to 2026-06-28`; an instant
`2026-10-04 15:24`, with the zone said once on the page (`UTC_NOTE`) and never a trailing "Z", an
offset, seconds, or microseconds. A relative age is whole days in brackets after its date,
`(98 days ago)`. A percentage is `66.7%`. Diagnostic pages that quote a provider's own stamps as
evidence are the only exception, and say so by being the pages the wording tests exempt.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

#: Said once on a page that shows instants, wherever the page converts to UTC.
UTC_NOTE = "Times are UTC."


def date_text(day: date) -> str:
    return day.isoformat()


def month_text(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def range_text(first: date, last: date) -> str:
    return f"{first.isoformat()} to {last.isoformat()}"


def instant_text(moment: datetime) -> str:
    """The instant on UTC's clock to the minute; a moment with no zone is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def instant_of(raw: object) -> str:
    """A recorded ISO stamp as `instant_text` writes it; an unreadable one as it came."""
    text = str(raw)
    try:
        return instant_text(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return text


def age_text(days: int) -> str:
    """A whole-day age for brackets after a date: `(98 days ago)`, `(today)`, `(yesterday)`."""
    if days <= 0:
        return "(today)"
    if days == 1:
        return "(yesterday)"
    return f"({days:,} days ago)"


def percent_text(share: float) -> str:
    """A share between 0 and 1 as `66.7%`, with no ".0" on a whole percentage."""
    return f"{share * 100:.1f}".removesuffix(".0") + "%"
