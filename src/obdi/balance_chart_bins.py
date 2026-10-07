"""Counting the changes in a range into bins for the strip and periods for the table.

Nothing here reads a size: a change is a day and a kind, so a count is always
safe to draw and to say.

A TIMING PAIR IS ONE CHANGE, counted on the day of its earlier step and in the
range that holds that day. Its later step is where the strip ends the pair's
join, and is never a second change.

THE FITTING RULE, stated here and only here: the strip is a fixed number of
drawing units wide, scaled to the screen by the browser, whatever range it
holds. Marks that would be closer than `MIN_BIN_WIDTH` units are combined, so
the bin is the finest of day, week, month, or quarter that still leaves every
bin that wide, and a year where none does. A bin's mark is the same height and
width as every other whatever it holds, and a count beside it says how many.

The table of counts is independent of the bins: its periods are calendar years
for a range of several years, calendar months for a range of months, and days
for a range of weeks, so each period is something a person names and a link can
open.
"""

from __future__ import annotations

import calendar
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .verify.fault_structure import TRANSIENT, FaultStructure

DAY, WEEK, MONTH, QUARTER, YEAR = "day", "week", "month", "quarter", "year"

#: Each unit with the days it nominally lasts, finest first.
_BIN_UNITS: tuple[tuple[str, float], ...] = (
    (DAY, 1),
    (WEEK, 7),
    (MONTH, 30.44),
    (QUARTER, 91.31),
    (YEAR, 365.25),
)

#: The narrowest a bin may be, in drawing units: a two-figure count and its mark.
MIN_BIN_WIDTH = 8.0

#: A range longer than this is counted per year in the table, and one longer
#: than `_MONTHLY_ABOVE_DAYS` per month; shorter ones per day.
_YEARLY_ABOVE_DAYS = 1100
_MONTHLY_ABOVE_DAYS = 62


@dataclass(frozen=True)
class Change:
    """One change in the difference: a step, or a whole timing pair."""

    day: date
    kind: str
    size_class: str
    #: The later day of a pair; None for any other change.
    partner_day: date | None = None

    @property
    def last_day(self) -> date:
        return self.partner_day or self.day


def changes_in(structure: FaultStructure, start: date, end: date) -> list[Change]:
    """The changes whose day lies in the range, in date order."""
    found: list[Change] = []
    for number, step in enumerate(structure.steps):
        if not start <= step.day <= end:
            continue
        partner: date | None = None
        if step.kind == TRANSIENT:
            if step.partner < number:
                continue
            partner = structure.steps[step.partner].day
        found.append(Change(step.day, step.kind, step.size_class, partner))
    return found


def count_by_kind(changes: Iterable[Change]) -> Counter[str]:
    return Counter(change.kind for change in changes)


# ---------------------------------------------------------------------------
# Calendar spans.


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _quarter_end(day: date) -> date:
    return month_end(_quarter_start(day).replace(month=_quarter_start(day).month + 2))


def calendar_span(unit: str, day: date) -> tuple[date, date]:
    """The whole calendar day, month, quarter, or year that holds `day`."""
    if unit == DAY:
        return day, day
    if unit == MONTH:
        return day.replace(day=1), month_end(day)
    if unit == QUARTER:
        return _quarter_start(day), _quarter_end(day)
    if unit == YEAR:
        return date(day.year, 1, 1), date(day.year, 12, 31)
    raise ValueError(f"no calendar span for {unit}")


def bin_span(unit: str, day: date, start: date, end: date) -> tuple[date, date]:
    """The bin holding `day`, clipped to the range. Weeks are counted from the
    range's own first day, so a week bin never straddles an edge of the strip."""
    if unit == WEEK:
        first = start + timedelta(days=(day - start).days // 7 * 7)
        return first, min(first + timedelta(days=6), end)
    first, last = calendar_span(unit, day)
    return max(first, start), min(last, end)


def choose_bin_unit(days: int, plot_width: float) -> str:
    """The finest unit that leaves every bin at least `MIN_BIN_WIDTH` wide."""
    for unit, length in _BIN_UNITS[:-1]:
        if days / length * MIN_BIN_WIDTH <= plot_width:
            return unit
    return YEAR


def nominal_bin_width(unit: str, days: int, plot_width: float) -> float:
    length = dict(_BIN_UNITS)[unit]
    return min(length, days) / days * plot_width


# ---------------------------------------------------------------------------
# Bins.


@dataclass(frozen=True)
class Bin:
    first: date
    last: date
    count: int


def bins_of(days: Iterable[date], unit: str, start: date, end: date) -> list[Bin]:
    """One bin per stretch holding at least one of `days`, in date order."""
    counts: Counter[tuple[date, date]] = Counter(bin_span(unit, day, start, end) for day in days)
    return [Bin(first, last, count) for (first, last), count in sorted(counts.items())]


# ---------------------------------------------------------------------------
# Periods for the table.


def period_unit(days: int) -> str:
    if days > _YEARLY_ABOVE_DAYS:
        return YEAR
    if days > _MONTHLY_ABOVE_DAYS:
        return MONTH
    return DAY


def period_label(unit: str, first: date) -> str:
    if unit == YEAR:
        return str(first.year)
    if unit == MONTH:
        return f"{first:%b %Y}"
    return first.isoformat()


@dataclass(frozen=True)
class Period:
    """A calendar period that overlaps the range, whole: a link opens all of it."""

    unit: str
    first: date
    last: date
    counts: Counter[str]

    @property
    def label(self) -> str:
        return period_label(self.unit, self.first)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def periods_of(changes: Sequence[Change], unit: str, start: date, end: date) -> list[Period]:
    """Every period from the one holding `start` to the one holding `end`,
    empty ones included, with the changes it holds by kind."""
    found: list[Period] = []
    day = start
    while day <= end:
        first, last = calendar_span(unit, day)
        held = [c for c in changes if first <= c.day <= last]
        found.append(Period(unit, first, last, count_by_kind(held)))
        day = last + timedelta(days=1)
    return found


def empty_runs(periods: Sequence[Period]) -> list[tuple[Period, Period]]:
    """Maximal runs of consecutive periods that hold nothing, as (first, last)."""
    runs: list[tuple[Period, Period]] = []
    began: Period | None = None
    previous: Period | None = None
    for period in periods:
        if period.total == 0:
            began = began or period
        elif began is not None and previous is not None:
            runs.append((began, previous))
            began = None
        previous = period
    if began is not None and previous is not None:
        runs.append((began, previous))
    return runs
