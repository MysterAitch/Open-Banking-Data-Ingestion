"""The one way a page writes a date, a month, a range, an instant, an age, and a percentage.

A date is `2026-06-28`; a month `2026-06`; a range `2026-01-10 to 2026-06-28`; an instant
`2026-10-04 15:24`, with the zone said once on the page (`UTC_NOTE`) and never a trailing "Z", an
offset, seconds, or microseconds. A relative age is whole days in brackets after its date,
`(98 days ago)`; where the age exists to flag a stale date, `date_with_age` writes it in coarser
words. A percentage is `66.7%`. Diagnostic pages that quote a provider's own stamps as
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


#: A date younger than this many days is written bare: a fortnight is not yet "a while ago".
RECENT_DAYS = 14


def date_with_age(day: date, today: date) -> str:
    """A date, with how long ago it was in words where that is worth saying.

    The age exists to make a STALE date stand out beside fresh ones, so a recent date is bare and
    the words grow coarser as precision stops mattering. The thresholds, stated here and nowhere
    else:

    - under `RECENT_DAYS` days, or in the future: `2026-10-01`, nothing added;
    - from `RECENT_DAYS` days until two whole calendar months have passed: `(3 weeks ago)`, in
      whole weeks, so never more than `(8 weeks ago)`;
    - from two whole months to eleven: `(2 months ago)`, counted in calendar months so that
      the first of a month reads the same on any day of the next;
    - from twelve months: `(over a year ago)`, then `(over 2 years ago)` from twenty-four.

    A calendar month, not thirty days, because "2 months ago" read beside a date three days short
    of two months would be wrong by a visible margin and nobody can check thirty-day months.
    """
    days = (today - day).days
    if days < RECENT_DAYS:
        return day.isoformat()
    months = (today.year - day.year) * 12 + today.month - day.month - (today.day < day.day)
    if months < 2:
        age = f"{days // 7} weeks ago"
    elif months < 12:
        age = f"{months} months ago"
    elif months < 24:
        age = "over a year ago"
    else:
        age = f"over {months // 12} years ago"
    return f"{day.isoformat()} ({age})"


def percent_text(share: float) -> str:
    """A share between 0 and 1 as `66.7%`, with no ".0" on a whole percentage."""
    return f"{share * 100:.1f}".removesuffix(".0") + "%"
