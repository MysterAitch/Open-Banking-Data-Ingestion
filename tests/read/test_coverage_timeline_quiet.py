"""Which stretches of a coverage timeline are quiet, against timelines drawn by hand here.

The timeline of one account is built directly (`view`), so every expectation is a date worked out
from the rule before the first run. The rule (`coverage_timeline.quiet_stretches`): a day is quiet
if it is not within MARGIN_DAYS (10) of an event and the verification lane agrees on it; a run of
at least MIN_QUIET_DAYS (28) such days is a stretch.

THE LONG ACCOUNT. Opens 2019-01-01, today 2026-10-05, one feed lane asked for every day from the
opening to 2024-03-31 and again from 2024-04-04 (three days, 04-01 to 04-03, uncovered), and
the verification lane agrees throughout.

  Events    2019-01-01 (opening), 2024-03-31 and 2024-04-01 (the lane stops), 2024-04-03 and
            2024-04-04 (it resumes), 2024-04-01 and 2024-04-03 (the gap's ends), 2026-10-05.
  Near      within 10 days of those: 2019-01-01 to 2019-01-11; 2024-03-21 to 2024-04-14;
            2026-09-25 to 2026-10-05.
  Quiet     2019-01-12 to 2024-03-20, which is 1895 days (2019-01-12 to 2024-01-12 is five years
            with one leap day, 1826 days; to 03-20 is 19 + 29 + 20 = 68 more; counted inclusively
            1826 + 68 + 1), "5 years 2 months"; and 2024-04-15 to 2026-09-24, which is 893 days
            (730 to 2026-04-15, 162 more, plus one), "2 years 5 months".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from obdi.read import coverage_timeline as ct

OPEN = date(2019, 1, 1)
TODAY = date(2026, 10, 5)
EVERYTHING = (OPEN, TODAY)


def run(first: date, last: date) -> ct.Run:
    return ct.Run(first, last, ct.ASKED, ct.ASKED)


def view(
    *,
    runs: tuple[ct.Run, ...] = (),
    first_day: date = OPEN,
    today: date = TODAY,
    bands: tuple[ct.Band, ...] | None = None,
    gaps: tuple[ct.Gap, ...] = (),
    seams: tuple[ct.Seam, ...] = (),
    markers: tuple[ct.Marker, ...] = (),
    known: tuple[ct.Known, ...] = (),
    listed: dict[date, int] | None = None,
    kind: str = ct.FEED,
    trailing: tuple[date, date] | None = None,
) -> ct.AccountTimeline:
    lane = ct.Lane("starling", kind, (), runs, listed or {}, trailing)
    return ct.AccountTimeline(
        "main", "Main", today, first_day,
        ct.Verification(bands or (ct.Band(first_day, today, "agrees"),), known),
        (lane,), seams, gaps, markers,
    )


LONG_RUNS = (run(OPEN, date(2024, 3, 31)), run(date(2024, 4, 4), TODAY))
LONG_GAP = ct.Gap(date(2024, 4, 1), date(2024, 4, 3), "hole-between", "starling", account="main")


def stretches(found: tuple[ct.Quiet, ...]) -> list[tuple[date, date]]:
    return [(q.first, q.last) for q in found]


class TestTheLongAccount:
    def test_QuietStretches_BetweenTheEventsAreWhereTheMarginsLeaveThem(self):
        found = ct.quiet_stretches(view(runs=LONG_RUNS, gaps=(LONG_GAP,)), *EVERYTHING)
        assert stretches(found) == [
            (date(2019, 1, 12), date(2024, 3, 20)),
            (date(2024, 4, 15), date(2026, 9, 24)),
        ]
        assert [q.days for q in found] == [1895, 893]

    def test_Words_NameHowLongEachStretchIsInCalendarUnits(self):
        assert ct.span_words(date(2019, 1, 12), date(2024, 3, 20)) == "5 years 2 months"
        assert ct.span_words(date(2024, 4, 15), date(2026, 9, 24)) == "2 years 5 months"
        assert ct.span_words(date(2026, 1, 1), date(2026, 1, 11)) == "11 days"
        assert ct.span_words(date(2026, 1, 1), date(2026, 3, 4)) == "2 months 4 days"
        assert ct.span_words(date(2026, 1, 1), date(2026, 1, 1)) == "1 day"

    def test_AnIssuePlantedMidStretch_SplitsItInTwoAroundItsMargins(self):
        planted = ct.Marker(ct.UNMATCHED, date(2022, 6, 15), "starling")
        found = ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,), markers=(planted,)), *EVERYTHING
        )
        assert stretches(found) == [
            (date(2019, 1, 12), date(2022, 6, 4)),
            (date(2022, 6, 26), date(2024, 3, 20)),
            (date(2024, 4, 15), date(2026, 9, 24)),
        ]

    def test_AStretchTheReaderOpened_IsNotReturnedAndTheOtherIs(self):
        found = ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,)), *EVERYTHING,
            expanded=[(date(2019, 1, 12), date(2024, 3, 20))],
        )
        assert stretches(found) == [(date(2024, 4, 15), date(2026, 9, 24))]

    def test_AMonthThePageIsShowing_IsNeverCollapsed(self):
        found = ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,)), *EVERYTHING,
            keep=[(date(2021, 5, 1), date(2021, 5, 31))],
        )
        assert stretches(found) == [
            (date(2019, 1, 12), date(2021, 4, 20)),
            (date(2021, 6, 11), date(2024, 3, 20)),
            (date(2024, 4, 15), date(2026, 9, 24)),
        ]

    def test_AWindowInsideTheQuietStretch_IsWhollyQuietAndHasNoBreaksAtItsEnds(self):
        found = ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,)), date(2020, 1, 1), date(2020, 12, 31)
        )
        assert stretches(found) == [(date(2020, 1, 1), date(2020, 12, 31))]

    def test_AWindowWithAnEventInIt_KeepsTheMarginAtItsOwnEnds(self):
        found = ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,)), date(2020, 1, 1), date(2024, 4, 1)
        )
        assert stretches(found) == [(date(2020, 1, 12), date(2024, 3, 20))]


class TestTheThreshold:
    """Events on the first day and on day 49 leave 28 quiet days between their margins."""

    @staticmethod
    def over(days_between: int) -> list[tuple[date, date]]:
        end = OPEN + timedelta(days=days_between)
        planted = ct.Marker(ct.UNMATCHED, end, "starling")
        timeline = view(runs=(run(OPEN, end),), markers=(planted,), today=end + timedelta(days=900))
        return stretches(ct.quiet_stretches(timeline, OPEN, end))

    def test_Stretch_OfTwentyEightDays_IsCollapsed(self):
        assert self.over(49) == [(OPEN + timedelta(days=11), OPEN + timedelta(days=38))]

    def test_Stretch_OfTwentySevenDays_IsLeftAlone(self):
        assert self.over(48) == []

    def test_Constants_AreWhatThisTestWasWorkedOutFor(self):
        assert (ct.MARGIN_DAYS, ct.MIN_QUIET_DAYS) == (10, 28)


class TestEachClauseOfNothingChanges:
    def test_AmberSeam_BreaksAStretch_ButAQuietOverlappedSeamDoesNot(self):
        base: dict[str, object] = {"runs": LONG_RUNS, "gaps": (LONG_GAP,)}
        amber = ct.Seam("starling", date(2021, 3, 3), ct.ABUTTING, ct.POSSIBLY, None,
                        ct.UNCHECKED)
        quiet = ct.Seam("starling", date(2021, 3, 3), ct.OVERLAPPED, ct.PARTIAL, 0.5,
                        ct.OVERLAPPED)
        plain = ct.quiet_stretches(view(**base), *EVERYTHING)
        assert len(ct.quiet_stretches(view(**base, seams=(amber,)), *EVERYTHING)) == 3
        assert ct.quiet_stretches(view(**base, seams=(quiet,)), *EVERYTHING) == plain

    def test_AGapEdge_BreaksAStretch(self):
        inner = ct.Gap(date(2021, 3, 3), date(2021, 3, 3), "no-balance", "", account="main")
        found = ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP, inner)), *EVERYTHING
        )
        assert len(found) == 3

    def test_ALaneChangingState_BreaksAStretchWithNoOtherEvent(self):
        runs = (run(OPEN, date(2021, 3, 3)), run(date(2021, 3, 20), TODAY))
        found = ct.quiet_stretches(view(runs=runs), *EVERYTHING)
        assert stretches(found) == [
            (date(2019, 1, 12), date(2021, 2, 20)),
            (date(2021, 3, 31), date(2026, 9, 24)),
        ]

    def test_ListedRows_EveryDay_DoNotBreakAStretch(self):
        listed = {OPEN + timedelta(days=n): 2 for n in range(0, 3000)}
        plain = ct.quiet_stretches(view(runs=LONG_RUNS, gaps=(LONG_GAP,)), *EVERYTHING)
        assert ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,), listed=listed), *EVERYTHING
        ) == plain

    def test_AKnownBalanceTheRowsReproduce_DoesNotBreakAStretch(self):
        ticks = tuple(ct.Known(date(2021, 1, 1) + timedelta(days=30 * n), "") for n in range(24))
        plain = ct.quiet_stretches(view(runs=LONG_RUNS, gaps=(LONG_GAP,)), *EVERYTHING)
        assert ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,), known=ticks), *EVERYTHING
        ) == plain

    def test_AKnownBalanceTheRowsDoNotReproduce_BreaksAStretch(self):
        marker = ct.Marker(ct.UNREPRODUCED, date(2021, 3, 3), "")
        found = ct.quiet_stretches(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,), markers=(marker,)), *EVERYTHING
        )
        assert len(found) == 3

    def test_VerificationNotInAgreement_IsNeverQuiet(self):
        bands = (
            ct.Band(OPEN, date(2021, 12, 31), "agrees"),
            ct.Band(date(2022, 1, 1), date(2022, 12, 31), "held"),
            ct.Band(date(2023, 1, 1), TODAY, "agrees"),
        )
        found = ct.quiet_stretches(
            view(runs=(run(OPEN, TODAY),), bands=bands), *EVERYTHING
        )
        assert stretches(found) == [
            (date(2019, 1, 12), date(2021, 12, 20)),
            (date(2023, 1, 12), date(2026, 9, 24)),
        ]

    def test_AnAccountThatNeverAgrees_HasNothingQuiet(self):
        bands = (ct.Band(OPEN, TODAY, "none"),)
        assert ct.quiet_stretches(view(runs=(run(OPEN, TODAY),), bands=bands), *EVERYTHING) == ()

    def test_NotYetAvailable_IsAStateSoItsEdgeIsAnEventAndItsInteriorCanBeQuiet(self):
        today = date(2026, 10, 5)
        timeline = view(
            runs=(run(OPEN, date(2026, 3, 31)),), kind=ct.STATEMENT,
            trailing=(date(2026, 4, 1), today),
        )
        found = ct.quiet_stretches(timeline, *EVERYTHING)
        assert stretches(found) == [
            (date(2019, 1, 12), date(2026, 3, 20)),
            (date(2026, 4, 12), date(2026, 9, 24)),
        ]

    def test_AProtectedPeriod_BreaksAtItsEdges(self):
        protected = ct.Band(date(2021, 3, 3), date(2021, 3, 9), "protected")
        timeline = view(runs=(run(OPEN, TODAY),))
        timeline = replace(
            timeline,
            verification=ct.Verification(
                timeline.verification.bands, timeline.verification.known, protected
            ),
        )
        assert len(ct.quiet_stretches(timeline, *EVERYTHING)) == 2

    def test_TheDayTheAccountOpened_IsAnEventEvenInTheMiddleOfAWindow(self):
        timeline = view(
            runs=(run(OPEN, TODAY),), first_day=date(2021, 3, 3),
            bands=(ct.Band(OPEN, TODAY, "agrees"),),
        )
        found = ct.quiet_stretches(timeline, date(2020, 1, 1), TODAY)
        assert stretches(found) == [
            (date(2020, 1, 12), date(2021, 2, 20)),
            (date(2021, 3, 14), date(2026, 9, 24)),
        ]


def range_of(found: tuple[ct.Quiet, ...]) -> set[date]:
    return {q.first + timedelta(days=n) for q in found for n in range(q.days)}


def test_Today_IsNeverInsideAStretch():
    found = ct.quiet_stretches(view(runs=LONG_RUNS, gaps=(LONG_GAP,)), *EVERYTHING)
    assert TODAY not in range_of(found)


@pytest.mark.parametrize("first,last", [(date(2026, 1, 1), date(2025, 1, 1))])
def test_BackwardsWindow_HasNoStretches(first: date, last: date):
    assert ct.quiet_stretches(view(runs=LONG_RUNS), first, last) == ()
