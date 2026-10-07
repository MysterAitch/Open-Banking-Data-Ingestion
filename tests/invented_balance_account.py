"""An invented account shaped like a long-held real one, with a KNOWN set of changes.

Stated every day from 1 January 2019 to 30 June 2026. The difference between the
stated balances and the rows changes on 39 occasions, decided here before any
page was drawn:

  2019  twelve in the first half: one timing pair, eight unexplained, three explained
  2020  none, and none until April 2021: a gap of nearly two years
  2021  three unexplained, from April
  2022  nineteen, the first weeks in: eleven unexplained, four explained, four timing
        pairs, one of which straddles the end of June
  2023  none
  2024  none
  2025  three: one timing pair and two unexplained
  2026  two unexplained

A timing pair is ONE change, counted in the month of its earlier step. Every
size is distinct, so no size recurs and no size class is named.
"""

from __future__ import annotations

from datetime import date, timedelta

from obdi.read.balance_chart import WHOLE, BalanceChart
from obdi.verify.fault_structure import Point, StructureReport, structure

FIRST = date(2019, 1, 1)
LAST = date(2026, 6, 30)

U, E, P = "unexplained", "explained", "pair"

#: (day, kind, gap in days for a pair)
CHANGES: list[tuple[date, str, int]] = (
    [(date(2019, m, d), kind, gap) for m, d, kind, gap in (
        (1, 12, U, 0), (1, 30, U, 0), (2, 14, E, 0), (2, 20, U, 0), (3, 3, P, 2),
        (3, 21, U, 0), (4, 9, U, 0), (4, 22, E, 0), (5, 2, U, 0), (5, 17, U, 0),
        (6, 6, E, 0), (6, 25, U, 0),
    )]
    + [(date(2021, m, d), kind, 0) for m, d, kind in ((4, 10, U), (9, 2, U), (12, 20, U))]
    + [(date(2022, m, d), kind, gap) for m, d, kind, gap in (
        (2, 9, U, 0), (2, 21, U, 0), (2, 25, E, 0), (3, 15, U, 0), (3, 28, P, 2),
        (4, 4, U, 0), (4, 5, U, 0), (4, 6, U, 0), (4, 30, E, 0), (6, 10, U, 0),
        (6, 29, P, 3), (7, 20, E, 0), (9, 1, U, 0), (9, 15, U, 0), (9, 20, P, 1),
        (10, 11, U, 0), (10, 12, U, 0), (12, 10, P, 1), (12, 23, E, 0),
    )]
    + [(date(2025, 3, 3), U, 0), (date(2025, 8, 4), P, 1), (date(2025, 11, 18), U, 0)]
    + [(date(2026, 2, 3), U, 0), (date(2026, 5, 20), U, 0)]
)
CHANGES.sort()

TOTAL_CHANGES = 39
BY_KIND = {"transient": 6, "unexplained": 26, "explained": 7}
BY_YEAR = {2019: 12, 2021: 3, 2022: 19, 2025: 3, 2026: 2}
BUSY_YEAR = {"transient": 4, "unexplained": 11, "explained": 4}
BUSY_BY_MONTH = {2: 3, 3: 2, 4: 4, 6: 2, 7: 1, 9: 3, 10: 2, 12: 2}


def invented_chart(scale: int = 1, changes: list[tuple[date, str, int]] | None = None,
                   first: date = FIRST, last: date = LAST) -> BalanceChart:
    """The chart of one daily-stated series, whose every size is multiplied by `scale`."""
    planned: dict[date, int] = {}
    explained: set[date] = set()
    for number, (day, kind, gap) in enumerate(CHANGES if changes is None else changes):
        size = (1_000 + 37 * number) * (1 if number % 2 else -1) * scale
        planned[day] = planned.get(day, 0) + size
        if kind == P:
            planned[day + timedelta(days=gap)] = planned.get(day + timedelta(days=gap), 0) - size
        if kind == E:
            explained.add(day)
    days, differences, points = [], [], []
    level = 0
    day = first
    while day <= last:
        level += planned.get(day, 0)
        points.append(Point(day, "invented-csv", level, len(points)))
        days.append(day)
        differences.append(level)
        day += timedelta(days=1)
    built = structure(
        points,
        opened=None,
        explained={p.position: p.day in explained for p in points},
    )
    return BalanceChart(
        ref="invented-main",
        label="Invented main account",
        scope=WHOLE,
        state="ok",
        withheld="",
        spaces=(),
        days=tuple(days),
        differences=tuple(differences),
        lines=(),
        structure=StructureReport(built),
        first_day=first,
        last_day=last,
    )
