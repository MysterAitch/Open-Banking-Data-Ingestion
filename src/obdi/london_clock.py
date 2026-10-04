"""The clock a person in London reads, worked out from the rule.

THE RULE: summer time runs from 01:00 UTC on the last Sunday of March to 01:00 UTC
on the last Sunday of October, and is an hour ahead of UTC.
It is written out here and not read from a time zone database because the
standard library's database is absent on a host that ships none (Windows has no
copy until a package is added), and a page whose times silently fall back to UTC
for half the year would be wrong without saying so.
The rule has held since 1996 and is the one the bank's own app follows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

_SUMMER = timezone(timedelta(hours=1), "BST")


def _last_sunday(year: int, month: int) -> date:
    last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def london(moment: datetime) -> datetime:
    """The same instant on London's clock; a moment with no zone is refused, not guessed."""
    if moment.tzinfo is None:
        raise ValueError("a moment with no zone cannot be placed on London's clock")
    instant = moment.astimezone(UTC)
    begins = datetime.combine(_last_sunday(instant.year, 3), datetime.min.time(), UTC) + timedelta(
        hours=1
    )
    ends = datetime.combine(_last_sunday(instant.year, 10), datetime.min.time(), UTC) + timedelta(
        hours=1
    )
    return instant.astimezone(_SUMMER if begins <= instant < ends else UTC)


__all__ = ["london"]
