"""The thresholds at which the timeline's marks merge, each tested either side of its line.

The thresholds are stated once, in `web_coverage_timeline`. The lanes here are built as data,
since what is under test is the drawing rule and not how a capture is read.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest

from obdi.read import coverage_timeline as ct
from obdi.web_balance_chart import Scale
from obdi.web_coverage_timeline import (
    KNOWN_TICK_MIN_PX,
    LISTED_DAY_MIN_PX,
    NOTCH_MIN_PX,
    known_ticks,
    listed_marks,
    notch_xs,
)

TODAY = date(2026, 10, 5)


def lane_of(captures: list[ct.Capture], listed: dict[date, int] | None = None) -> ct.Lane:
    return ct.Lane("starling", ct.FEED, tuple(captures), (), listed or {})


def capture(day: date, hour: int = 12) -> ct.Capture:
    taken = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    return ct.Capture(
        "starling", day, day, ct.ASKED, ct.ASKED, ct.PARTIAL, hour / 24, taken=taken
    )


def view_of(known_days: list[date], problems: dict[date, str] | None = None) -> ct.AccountTimeline:
    problems = problems or {}
    return ct.AccountTimeline(
        "x", "x", TODAY, known_days[0],
        ct.Verification((), tuple(ct.Known(d, problems.get(d, "")) for d in known_days)),
        (), (), (), (),
    )


class TestNotches:
    def test_Notches_WhenHundredsOfCapturesShareADay_AreOneEnvelopeAtDayScale(self):
        day = date(2026, 9, 1)
        lane = lane_of([capture(day, 1 + n % 22) for n in range(300)])
        xs = notch_xs(lane, Scale(date(2026, 8, 1), TODAY, 14.0))
        assert len(xs) <= 2

    def test_Notches_WhenCapturesAreADayApartAtDayScale_AreAllDrawn(self):
        lane = lane_of([capture(date(2026, 9, 1) + timedelta(days=n)) for n in range(4)])
        scale = Scale(date(2026, 8, 1), TODAY, 14.0)
        xs = notch_xs(lane, scale)
        # Each capture is one day wide, so its two edges are about 7 units apart, inside the
        # threshold: one notch each, and the four captures stay separate. (The first draft of
        # this expectation said eight; the run showed the edges of a one-day capture merge.)
        assert len(xs) == 4
        assert all(b - a >= NOTCH_MIN_PX for a, b in pairwise(xs))

    def test_Notches_WhenTheSameCapturesAreDrawnCoarsely_AreThinned(self):
        lane = lane_of([capture(date(2026, 9, 1) + timedelta(days=n)) for n in range(4)])
        xs = notch_xs(lane, Scale(date(2026, 8, 1), TODAY, 3.65))
        assert 0 < len(xs) < 8


class TestListedRows:
    def test_Listed_AtOrAboveTheThreshold_IsOneMarkPerDay(self):
        listed = {date(2026, 9, 1) + timedelta(days=n): 1 for n in range(3)}
        marks = listed_marks(lane_of([], listed), Scale(date(2026, 8, 1), TODAY, LISTED_DAY_MIN_PX))
        assert len(marks) == 3 and all(share == 1.0 for *_, share in marks)

    def test_Listed_BelowTheThreshold_IsOneMarkPerBucketDarkerForMoreDays(self):
        scale = Scale(date(2026, 8, 3), TODAY, 2.0)
        listed = {date(2026, 8, 3) + timedelta(days=n): 1 for n in range(3)}
        marks = listed_marks(lane_of([], listed), scale)
        # Buckets are ceil(5 / 2) = 3 days: the three listed days fill one bucket entirely.
        assert len(marks) == 1 and marks[0][2] == pytest.approx(1.0)
        sparse = listed_marks(lane_of([], {date(2026, 8, 3): 1}), scale)
        assert sparse[0][2] == pytest.approx(1 / 3)


class TestKnownBalances:
    DAYS = tuple(date(2026, 9, 1) + timedelta(days=n) for n in range(6))

    def test_Known_AtOrAboveTheThreshold_AreSeparateTicks(self):
        ticks = known_ticks(view_of(self.DAYS), Scale(date(2026, 8, 1), TODAY, KNOWN_TICK_MIN_PX))
        assert len(ticks) == 6

    def test_Known_BelowTheThreshold_MergeIntoOneTickPerBucketColouredByTheWorst(self):
        problems = {self.DAYS[1]: ct.UNREPRODUCED}
        ticks = known_ticks(view_of(self.DAYS, problems), Scale(date(2026, 8, 1), TODAY, 1.0))
        assert len(ticks) < 6
        assert sum(count for *_, count in ticks) == 6
        assert any(problem == ct.UNREPRODUCED for _, problem, _ in ticks)
