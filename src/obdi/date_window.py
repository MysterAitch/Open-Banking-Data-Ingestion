"""A window of days, worked out from a small description of it.

A chart that can only show everything held, or the last n of it, cannot answer
"what happened over the tax year" or "between these two dates". This module is
the arithmetic of that choice and knows nothing of any page, so another chart
can ask the same question. `today` is always passed in: nothing here reads the
clock, so a window is the same on every machine and in every time zone.

THE CONVENTIONS, stated once, here, and tested against hand-worked answers in
`tests/test_date_window.py`:

* A window is a first day and a last day, BOTH INCLUDED. "12 months ending
  2026-10-04" is 2025-10-05 to 2026-10-04: exactly a year of days, where
  counting the starting day too would make it a year and a day.
* A length of months or years ends on the day before an ANNIVERSARY. Ending on
  E, the first day is the day after the same day-of-month that many months
  earlier; starting on S, the last day is the day before the same day-of-month
  that many months later. Where that day does not exist (31 January plus a
  month, 29 February plus a year) the anniversary is the month's last day. So
  consecutive windows of one month starting on the 31st and then the 28th tile
  without a gap or an overlap. Rejected: ending on the month's last day, which
  makes the lengths of such windows irregular, and counting a month as 30 days,
  which is not a month on any calendar.
* A length of weeks is seven days each, and of days is days.
* A named period is a calendar month, a calendar QUARTER (January to March and
  so on, not a fiscal quarter), a calendar year, or the tax year. The tax year
  is the UK's, `TAX_YEAR_STARTS`; a different fiscal year is that one edit.
  "This" period is the whole of it and, where it ends in the future, is cut at
  today and the window says so (`Window.ended_in_future`). "To date" is the
  period's first day to today and is never cut.
* A window that ends after today is cut at today. One that begins before
  anything is held is cut at the first day held (`held_from`). Each says so, so
  nobody mistakes a clipped window for the one they asked for. A window left
  with no day in it is `empty`, with a sentence saying why, and draws nothing.
* A length of zero or less, and a first day after the last, are refused in a
  sentence (`WindowRefused`). A length so large that no date reaches it is cut to
  what is held; it does not fail.
* EVERYTHING is the first day held to today, and is what a chart shows when
  nothing is chosen.

THE RESOLUTION follows the window's length, since a mark nearer than about three
units to its neighbour is a smear on a chart 376 units wide: a point per day up
to `DAILY_UP_TO_DAYS` days, per week up to `WEEKLY_UP_TO_DAYS`, per month-end
beyond. A week ends on a SUNDAY, which is how ISO 8601 and a UK calendar close
a week that starts on Monday. The last point is always the window's last day,
so a window ending today ends on the figure held now.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from .plural import plural

#: The month and day the tax year starts: the UK's, 6 April to 5 April.
TAX_YEAR_STARTS = (4, 6)

#: The width of the plot a chart is drawn in, and the nearest two marks may sit.
CHART_WIDTH_UNITS = 376
MIN_MARK_GAP_UNITS = 3

#: Up to this many days a chart has a point per day: 376 units across 119 gaps is 3.2 each.
DAILY_UP_TO_DAYS = 120
#: Up to this many days (two years and a leap day) a chart has a point per week.
WEEKLY_UP_TO_DAYS = 731

_SUNDAY = 6


class Unit(Enum):
    DAYS = "day"
    WEEKS = "week"
    MONTHS = "month"
    YEARS = "year"


class Anchor(Enum):
    ENDING_TODAY = "ending"
    ENDING_ON = "ending-on"
    STARTING_ON = "starting-on"


class Period(Enum):
    """A named stretch aligned to a calendar or tax boundary."""

    THIS_MONTH = "This month"
    LAST_MONTH = "Last month"
    THIS_QUARTER = "This quarter"
    LAST_QUARTER = "Last quarter"
    THIS_YEAR = "This calendar year"
    LAST_YEAR = "Last calendar year"
    YEAR_TO_DATE = "Calendar year to date"
    THIS_TAX_YEAR = "This tax year"
    LAST_TAX_YEAR = "Last tax year"
    TAX_YEAR_TO_DATE = "Tax year to date"


class Resolution(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class WindowRefused(ValueError):
    """A choice that cannot be a window; the message is a sentence for the reader."""


@dataclass(frozen=True)
class WindowSpec:
    """What was asked for, before it meets today or what is held."""

    kind: str
    count: int = 0
    unit: Unit = Unit.MONTHS
    anchor: Anchor = Anchor.ENDING_TODAY
    day: date | None = None
    period: Period | None = None
    first: date | None = None
    last: date | None = None

    def describe(self, *, today: date) -> str:
        """The choice in words, as the page names it above the chart."""
        if self.kind == "everything":
            return "Everything held"
        if self.kind == "named" and self.period is not None:
            return self.period.value
        if self.kind == "between" and self.first and self.last:
            return f"From {self.first.isoformat()} to {self.last.isoformat()}"
        size = plural(self.count, self.unit.value)
        if self.anchor is Anchor.STARTING_ON and self.day:
            return f"{size} starting {self.day.isoformat()}"
        stated = self.day if self.anchor is Anchor.ENDING_ON and self.day else today
        return f"{size} ending {stated.isoformat()}"


def everything() -> WindowSpec:
    return WindowSpec(kind="everything")


def length(
    count: int, unit: Unit, anchor: Anchor = Anchor.ENDING_TODAY, day: date | None = None
) -> WindowSpec:
    if anchor is not Anchor.ENDING_TODAY and day is None:
        raise ValueError(f"A length {anchor.value} a day needs a day to anchor on.")
    return WindowSpec(kind="length", count=count, unit=unit, anchor=anchor, day=day)


def named(period: Period) -> WindowSpec:
    return WindowSpec(kind="named", period=period)


def between(first: date, last: date) -> WindowSpec:
    return WindowSpec(kind="between", first=first, last=last)


@dataclass(frozen=True)
class Window:
    """A resolved window: the days it covers, and what was cut from what was asked."""

    #: The days drawn, both included; None when `empty`.
    first: date | None
    last: date | None
    #: What was asked for, before it was cut at today or at what is held.
    asked_first: date
    asked_last: date
    ended_in_future: bool
    began_before_held: bool
    empty: bool
    empty_reason: str

    @property
    def days(self) -> int:
        if self.first is None or self.last is None:
            return 0
        return (self.last - self.first).days + 1


def _month_end(year: int, month: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    return following - timedelta(days=1)


def _add_months(day: date, months: int) -> date:
    """The same day of the month, months on; the month's last day where it has none."""
    index = day.year * 12 + day.month - 1 + months
    year, month = index // 12, index % 12 + 1
    if year < date.min.year:
        return date.min
    if year > date.max.year:
        return date.max
    return date(year, month, min(day.day, _month_end(year, month).day))


def _add_days(day: date, days: int) -> date:
    try:
        return day + timedelta(days=days)
    except OverflowError:
        return date.min if days < 0 else date.max


def _step(day: date, count: int, unit: Unit) -> date:
    """`day` moved by `count` units, later for a positive count."""
    if unit is Unit.DAYS:
        return _add_days(day, count)
    if unit is Unit.WEEKS:
        return _add_days(day, 7 * count)
    return _add_months(day, count * (12 if unit is Unit.YEARS else 1))


def _tax_year_first(day: date) -> date:
    month, of_month = TAX_YEAR_STARTS
    year = day.year if (day.month, day.day) >= (month, of_month) else day.year - 1
    return date(year, month, of_month)


def _named_span(period: Period, today: date) -> tuple[date, date]:
    month_first = today.replace(day=1)
    quarter_first = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    year_first = date(today.year, 1, 1)
    tax_first = _tax_year_first(today)

    def day_before(day: date) -> date:
        return day - timedelta(days=1)

    spans = {
        Period.THIS_MONTH: (month_first, day_before(_add_months(month_first, 1))),
        Period.LAST_MONTH: (_add_months(month_first, -1), day_before(month_first)),
        Period.THIS_QUARTER: (quarter_first, day_before(_add_months(quarter_first, 3))),
        Period.LAST_QUARTER: (_add_months(quarter_first, -3), day_before(quarter_first)),
        Period.THIS_YEAR: (year_first, date(today.year, 12, 31)),
        Period.LAST_YEAR: (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
        Period.YEAR_TO_DATE: (year_first, today),
        Period.THIS_TAX_YEAR: (tax_first, day_before(_add_months(tax_first, 12))),
        Period.LAST_TAX_YEAR: (_add_months(tax_first, -12), day_before(tax_first)),
        Period.TAX_YEAR_TO_DATE: (tax_first, today),
    }
    return spans[period]


def _asked_span(spec: WindowSpec, today: date, held_from: date) -> tuple[date, date]:
    if spec.kind == "everything":
        return held_from, today
    if spec.kind == "named" and spec.period is not None:
        return _named_span(spec.period, today)
    if spec.kind == "between" and spec.first and spec.last:
        if spec.first > spec.last:
            raise WindowRefused(
                f"The window starts on {spec.first.isoformat()}, which is after it ends on "
                f"{spec.last.isoformat()}."
            )
        return spec.first, spec.last
    if spec.count < 1:
        raise WindowRefused("A length must be a whole number of at least 1.")
    one_day = timedelta(days=1)
    if spec.anchor is Anchor.STARTING_ON and spec.day is not None:
        # The day before the anniversary; a length too large to reach one ends at the last
        # date there is, and is cut at today below.
        end = _step(spec.day, spec.count, spec.unit)
        return spec.day, end - one_day if end > spec.day else end
    end = spec.day if spec.anchor is Anchor.ENDING_ON and spec.day is not None else today
    start = _step(end, -spec.count, spec.unit)
    return (start if start == date.min else start + one_day), end


def resolve(spec: WindowSpec, *, today: date, held_from: date | None) -> Window:
    """The days `spec` covers as at `today`, given the first day anything is held.

    Raises `WindowRefused` for a choice that cannot be a window at all; one that
    can be but holds nothing comes back `empty`, with its reason.
    """
    if held_from is None:
        # Validate the choice all the same: a refusal does not depend on what is held.
        asked_first, asked_last = _asked_span(spec, today, today)
        return Window(
            first=None, last=None, asked_first=asked_first, asked_last=asked_last,
            ended_in_future=False, began_before_held=False, empty=True,
            empty_reason="Nothing is held yet, so there is nothing to show in a window.",
        )
    asked_first, asked_last = _asked_span(spec, today, held_from)
    first = max(asked_first, held_from)
    last = min(asked_last, today)
    ended_in_future = asked_last > today
    began_before_held = asked_first < held_from
    reason = ""
    if asked_first > today:
        reason = (
            f"The window starts on {asked_first.isoformat()}, which is after today "
            f"({today.isoformat()}), so nothing has happened in it yet."
        )
    elif last < held_from:
        reason = (
            f"The window ends on {last.isoformat()}, before anything is held: the first "
            f"day held is {held_from.isoformat()}."
        )
    if reason:
        return Window(
            first=None, last=None, asked_first=asked_first, asked_last=asked_last,
            ended_in_future=ended_in_future, began_before_held=began_before_held,
            empty=True, empty_reason=reason,
        )
    return Window(
        first=first, last=last, asked_first=asked_first, asked_last=asked_last,
        ended_in_future=ended_in_future, began_before_held=began_before_held,
        empty=False, empty_reason="",
    )


def resolution_for(first: date, last: date) -> Resolution:
    """How often a chart over `first` to `last` has a point; see the module docstring."""
    days = (last - first).days + 1
    if days <= DAILY_UP_TO_DAYS:
        return Resolution.DAY
    if days <= WEEKLY_UP_TO_DAYS:
        return Resolution.WEEK
    return Resolution.MONTH


def sample_days(first: date, last: date, resolution: Resolution) -> Sequence[date]:
    """The days a chart is drawn at, oldest first, the last always `last`.

    Daily is every day. Weekly is the first day, each Sunday after it, and the last
    day, so the chart covers the whole window and not only its whole weeks; a Sunday
    so near either end that its mark would sit within `MIN_MARK_GAP_UNITS` of that
    end's is left out, since the ends are the points that name the window. Monthly is
    each month-end in the window and the last day.
    """
    if resolution is Resolution.DAY:
        return [first + timedelta(days=n) for n in range((last - first).days + 1)]
    days: list[date] = []
    if resolution is Resolution.WEEK:
        days.append(first)
        ahead = (_SUNDAY - first.weekday()) % 7
        day = first + timedelta(days=ahead)
        near = -(-MIN_MARK_GAP_UNITS * (last - first).days // CHART_WIDTH_UNITS)
        while day <= last:
            clear_of_ends = (day - first).days >= near and (last - day).days >= near
            if day in (first, last) or clear_of_ends:
                days.append(day)
            day += timedelta(days=7)
    else:
        year, month = first.year, first.month
        while _month_end(year, month) <= last:
            days.append(_month_end(year, month))
            year, month = year + (month == 12), month % 12 + 1
    if not days or days[-1] != last:
        days.append(last)
    return list(dict.fromkeys(days))
