"""The one way a page writes a date, a month, a range, an instant, an age, and a percentage.

A date is `2026-06-28`; a month `2026-06`; a range `2026-01-10 to 2026-06-28`; an instant
`2026-10-04 15:24` on the owner's clock (`PAGE_ZONE`), with no zone written beside it and never a
trailing "Z", an offset, seconds, or microseconds. A relative age is whole days in brackets after
its date, `(98 days ago)`; where the age exists to flag a stale date, `date_with_age` writes it in
coarser words. A percentage is `66.7%`. Diagnostic pages that quote a provider's own stamps as
evidence are the only exception, and say so by being the pages the wording tests exempt.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from .london_clock import london

#: The zone every clock time on a page is written in. The owner is in the UK and the host runs
#: UTC, so a page that printed the host's clock read an hour out for half the year. The store's
#: instants stay UTC; only rendering converts, here, and the zone is therefore never written
#: beside a time. `london_clock.london` is the implementation of this zone's rule, because the
#: standard library's zone database is absent on a Windows host and a page must not fall back to
#: UTC without saying so.
PAGE_ZONE = "Europe/London"


def local_time(moment: datetime) -> datetime:
    """The instant on the page's clock (`PAGE_ZONE`); a moment with no zone is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return london(moment)


def local_day(moment: datetime) -> date:
    """The calendar day on the page's clock, which is the next day for a late summer evening in
    UTC."""
    return local_time(moment).date()


def clock_text(moment: datetime) -> str:
    """The time of day on the page's clock, to the minute: `15:24`."""
    return local_time(moment).strftime("%H:%M")


def date_text(day: date) -> str:
    return day.isoformat()


def month_text(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def range_text(first: date, last: date) -> str:
    return f"{first.isoformat()} to {last.isoformat()}"


def instant_text(moment: datetime) -> str:
    """The instant on the page's clock (`PAGE_ZONE`) to the minute; a moment with no zone is taken
    as UTC."""
    return local_time(moment).strftime("%Y-%m-%d %H:%M")


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


#: A range of fewer days than `WEEKS_FROM_DAYS` is counted in days, one of fewer than
#: `MONTHS_FROM_DAYS` in weeks, and a longer one in months and years.
WEEKS_FROM_DAYS = 8
MONTHS_FROM_DAYS = 27
_DAYS_PER_MONTH = 30.4375


def _counted(count: int, unit: str) -> str:
    return f"a {unit}" if count == 1 else f"{count} {unit}s"


def span_words(first: date, last: date) -> str:
    """How long `first` to `last` is, both days counted, in rounded words: `a month`, `2 weeks`.

    The largest unit that fits, rounded to the nearest whole one and never exact: 62 days is
    `2 months`. The thresholds are stated here and nowhere else: under `WEEKS_FROM_DAYS` days in
    days (`1 day`, `7 days`); under `MONTHS_FROM_DAYS` in weeks (8 to 10 days is `a week`, 11 to
    17 `2 weeks`); then in months of 30.4375 days (27 to 45 days is `a month`), which from twelve
    are years and months (`a year`, `1 year 9 months`). One of a unit is "a", two or more is the
    numeral; one year with months is `1 year 1 month`, so the numeral reads as a pair there. The
    words are an affordance beside dates that are exact, so no "about" is written. A range that
    ends before it starts is a fault in the caller and raises.
    """
    if last < first:
        raise ValueError(f"the range ends {last.isoformat()}, before it starts {first.isoformat()}")
    days = (last - first).days + 1
    if days < WEEKS_FROM_DAYS:
        return "1 day" if days == 1 else f"{days} days"
    if days < MONTHS_FROM_DAYS:
        return _counted(round(days / 7), "week")
    months = round(days / _DAYS_PER_MONTH)
    if months < 12:
        return _counted(months, "month")
    years, rest = divmod(months, 12)
    if not rest:
        return _counted(years, "year")
    return f"{years} year{'s' if years > 1 else ''} {rest} month{'s' if rest > 1 else ''}"


#: Wrap the words of a duration inside a sentence that is escaped before it is put in a page, so
#: that the sentence stays plain text through every layer and `render_page` (the one place that
#: assembles a page) turns the marks into the muted span. Private-use characters: nothing
#: escapes them and no source text carries them.
SPAN_OPEN = chr(0xE000)
SPAN_CLOSE = chr(0xE001)


def range_with_span(first: date, last: date, statements: int | None = None) -> str:
    """`2026-07-11 to 2026-08-10 (a month)` with the bracketed words marked for `marks_as_html`.

    `statements` is how many statements the range stands for where the cadence of the statements
    held says so (`2026-07-11 to 2026-09-10 (2 months, 2 statements)`); None, or one, says only
    the length, because one statement is what a range of days already is.
    """
    return f"{range_text(first, last)} {span_phrase(first, last, statements)}"


def span_phrase(first: date, last: date, statements: int | None = None) -> str:
    """The marked `(a month)` alone, for a page that sets the dates in a face of their own."""
    words = span_words(first, last)
    if statements is not None and statements >= 2:
        words += f", {statements} statements"
    return f"{SPAN_OPEN}({words}){SPAN_CLOSE}"


def marks_as_html(page: str) -> str:
    """Marked words as the muted, smaller span the stylesheet's `.span-words` rule styles."""
    return page.replace(SPAN_OPEN, '<span class="span-words">').replace(SPAN_CLOSE, "</span>")


def marks_removed(text: str) -> str:
    """Marked text as plain words, for a line written to a terminal or into an attribute."""
    return text.replace(SPAN_OPEN, "").replace(SPAN_CLOSE, "")


def percent_text(share: float) -> str:
    """A share between 0 and 1 as `66.7%`, with no ".0" on a whole percentage."""
    return f"{share * 100:.1f}".removesuffix(".0") + "%"
