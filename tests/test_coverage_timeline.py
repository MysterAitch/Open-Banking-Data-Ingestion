"""The coverage timeline's data, against the household drawn by hand in `coverage_timeline_world`.

Every expectation below was read off that docstring before the first run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from coverage_timeline_world import (
    AGGREGATOR,
    CARD,
    EXPORTS,
    MAIN,
    TODAY,
    build_household,
)
from obdi import coverage_timeline as ct
from obdi.fetch_gaps import gaps_for_account
from obdi.ingest.store import Store
from obdi.verify.agreement import standing_of
from obdi.verify.balance_anchors import effective_opening


def d(text: str) -> date:
    return date.fromisoformat("2026-" + text)


def timeline(db: Path, ref: str = MAIN, today: date = TODAY) -> ct.AccountTimeline:
    with Store(db) as store:
        opening = effective_opening(store, ref)
        standing = standing_of(opening, [ref], None)
        return ct.build_account_timeline(
            store, ref, today=today, agreement=standing.own, opening=opening,
            fetch_gaps=gaps_for_account(store, ref, today),
        )


@pytest.fixture(scope="module")
def household(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_household(tmp_path_factory.mktemp("household"))


@pytest.fixture(scope="module")
def main(household: Path) -> ct.AccountTimeline:
    return timeline(household)


def lane(view: ct.AccountTimeline, source: str) -> ct.Lane:
    return next(item for item in view.lanes if item.source == source)


def seam(view: ct.AccountTimeline, source: str, day: str) -> ct.Seam:
    return next(s for s in view.seams if s.source == source and s.day == d(day))


class TestLanes:
    def test_Lanes_AreOrderedFromAutomaticToByHand(self, main):
        assert [(item.source, item.kind) for item in main.lanes] == [
            ("starling", ct.FEED),
            ("truelayer", ct.AGGREGATOR),
            ("starling-csv", ct.EXPORT),
            ("manual", ct.TYPED),
        ]

    def test_Feed_CoversFromItsFirstWindowToItsLast(self, main):
        runs = lane(main, "starling").runs
        assert [(r.first, r.last) for r in runs] == [(d("07-01"), d("08-20"))]
        assert {r.first_basis for r in runs} == {ct.ASKED}

    def test_Aggregator_CoversTwoWindowsAndNotTheDaysBetween(self, main):
        runs = lane(main, "truelayer").runs
        assert [(r.first, r.last) for r in runs] == [
            (d("07-01"), d("08-10")),
            (d("08-20"), d("09-10")),
        ]

    def test_Export_CoversFirstRowToLastRowAndIsOnlyObserved(self, main):
        runs = lane(main, "starling-csv").runs
        assert [(r.first, r.last) for r in runs] == [
            (d("07-02"), d("08-25")),
            (d("09-12"), d("09-28")),
        ]
        assert {r.first_basis for r in runs} == {ct.OBSERVED}

    def test_TypedEntries_CoverNothingAndListTheirDays(self, main):
        typed = lane(main, "manual")
        assert typed.runs == ()
        assert typed.listed == {d("09-02"): 1}

    def test_Listed_CountsDistinctRowsPerDayPerSource(self, main):
        feed = lane(main, "starling")
        assert feed.listed[d("07-30")] == 2
        assert feed.listed[d("08-18")] == 2
        assert d("08-25") not in feed.listed

    def test_CoveredButQuietDays_AreCoveredAndNotListed(self, main):
        feed = lane(main, "starling")
        assert feed.covers(d("07-20"))
        assert d("07-20") not in feed.listed


class TestBoundaries:
    def test_FeedCapture_WhenTakenDuringItsLastDay_IsPartialAtTheLondonFraction(self, main):
        first = lane(main, "starling").captures[0]
        assert first.last == d("08-04")
        assert first.last_state == ct.PARTIAL
        assert first.fraction == pytest.approx(11 / 24)

    def test_AggregatorWindow_WhenItsToIsTheDayItWasAsked_IsPartialAtThatTime(self, main):
        first = lane(main, "truelayer").captures[0]
        assert (first.last, first.last_state) == (d("08-10"), ct.PARTIAL)
        assert first.fraction == pytest.approx(13 / 24)

    def test_ExportFile_StatesNoTime_SoItsLastDayIsOnlyPossiblyPartial(self, main):
        for capture in lane(main, "starling-csv").captures:
            assert capture.last_state == ct.POSSIBLY
            assert capture.fraction is None
            assert capture.taken is None


class TestSeams:
    def test_Seam_WhenTheNextCaptureStartsOnTheSameDay_IsOverlappedAndQuiet(self, main):
        for source, day in (("starling", "08-04"), ("starling-csv", "08-04")):
            found = seam(main, source, day)
            assert found.verdict == ct.OVERLAPPED
            assert not found.needs_a_look

    def test_Seam_WhenAnotherSourceHoldsARowTheExportLacks_IsMissingByCount(self, main):
        found = seam(main, "starling-csv", "07-30")
        assert found.kind == ct.ABUTTING
        assert found.verdict == ct.MISSING
        assert found.missing_rows == 1
        assert found.held_by == ("starling", "truelayer")

    def test_Seam_WhenEveryRowOnTheDayIsAccountedFor_IsCleanWhateverTheWedge(self, main):
        found = seam(main, "starling-csv", "08-18")
        assert (found.kind, found.verdict, found.missing_rows) == (ct.ABUTTING, ct.CLEAN, 0)
        assert found.last_state == ct.POSSIBLY
        assert not found.needs_a_look

    def test_Seam_WhenTheNextCaptureStartsLater_IsGappedAndStillDecidedByCount(self, main):
        found = seam(main, "truelayer", "08-10")
        assert (found.kind, found.verdict) == (ct.GAPPED, ct.CLEAN)
        assert found.next_first == d("08-20")

    def test_Seam_WhenNoOtherSourceCoversTheDay_IsUncheckedAndNeedsALook(self, main):
        found = seam(main, "starling-csv", "09-20")
        assert (found.verdict, found.missing_rows) == (ct.UNCHECKED, 0)
        assert found.needs_a_look

    def test_NewestCapture_HasNoSeam(self, main):
        assert not [s for s in main.seams if s.day in (d("09-28"), d("09-10"), d("08-20"))]

    def test_SeamsToCheck_AreTheRedOneAndTheAmberOne(self, main):
        assert [(s.source, s.day) for s in main.seams_to_check] == [
            ("starling-csv", d("07-30")),
            ("starling-csv", d("09-20")),
        ]


class TestGaps:
    def test_AskHole_WhenTheAggregatorWasNotAskedForNineDays_IsAGapWithinReach(self, main):
        holes = [g for g in main.gaps if g.kind == ct.ASK_HOLE]
        assert [(g.source, g.first, g.last, g.within_reach) for g in holes] == [
            ("truelayer", d("08-11"), d("08-19"), True)
        ]

    def test_ExportHole_WhenAnotherSourceCoversTheStretch_IsNotAGapToFill(self, main):
        # No export reaches 08-26 to 09-11, but the account is verified through 09-20 and the
        # What to fetch next page names nothing for it, so the timeline names nothing either.
        assert [g.kind for g in main.gaps] == [ct.ASK_HOLE]

    def test_Gap_WhenAnotherAggregatorAskFillsTheHole_IsGone(self, tmp_path):
        filler = (
            "2026-08-21T12:00:00+00:00",
            "from=2026-08-10&to=2026-08-20",
            ("R6",),
        )
        db = build_household(tmp_path, aggregator=(AGGREGATOR[0], filler, AGGREGATOR[1]))
        assert not [g for g in timeline(db).gaps if g.kind == ct.ASK_HOLE]


class TestIssues:
    def test_Unmatched_WhenACoveringSourceLacksARow_IsMarkedOnTheLaneThatLacksIt(self, main):
        found = [m for m in main.markers if m.kind == ct.UNMATCHED]
        assert [(m.source, m.day, m.count) for m in found] == [("truelayer", d("08-25"), 1)]

    def test_Seam_WhenTheRedSeamExplainsTheMissingRow_ThatRowIsNotMarkedTwice(self, main):
        assert not [
            m for m in main.markers if m.kind == ct.UNMATCHED and m.source == "starling-csv"
        ]

    def test_Verification_AgreesThroughTheLastReproducedBalanceAndIsHeldBackAfter(self, main):
        assert [(b.first, b.last, b.state) for b in main.verification.bands] == [
            (d("07-01"), d("09-20"), "agrees"),
            (d("09-21"), TODAY, "held"),
        ]

    def test_KnownBalance_WhenTheRowsDoNotReproduceIt_IsMarkedUnreproduced(self, main):
        assert [(m.kind, m.day) for m in main.markers if m.kind == ct.UNREPRODUCED] == [
            (ct.UNREPRODUCED, d("09-28"))
        ]
        problems = {k.day: k.problem for k in main.verification.known}
        assert problems[d("09-28")] == ct.UNREPRODUCED
        assert problems[d("09-20")] == ""


class TestOppositeOfTheSeams:
    def test_Seam_WhenTheFirstExportListsTheRowToo_IsCleanByCount(self, tmp_path):
        exports = (("R1", "R2", "R3", "R3b"), *EXPORTS[1:])
        found = seam(timeline(build_household(tmp_path, exports=exports)), "starling-csv", "07-30")
        assert (found.verdict, found.missing_rows) == (ct.CLEAN, 0)

    def test_Seam_WhenTheNextExportBeginsOnTheSameDay_IsOverlapped(self, tmp_path):
        exports = (EXPORTS[0], ("R3b", *EXPORTS[1]), *EXPORTS[2:])
        found = seam(timeline(build_household(tmp_path, exports=exports)), "starling-csv", "07-30")
        assert found.verdict == ct.OVERLAPPED


@pytest.fixture(scope="module")
def card(household: Path) -> ct.AccountTimeline:
    return timeline(household, CARD)


class TestStatements:
    def test_Statements_CoverTheirPeriodsAndStateTheirClosing(self, card):
        runs = lane(card, "santander-cc-pdf").runs
        assert [(r.first, r.last) for r in runs] == [
            (d("05-15"), d("07-11")),
            (d("08-12"), d("09-11")),
        ]
        assert runs[0].last_basis == ct.STATED

    def test_Statement_WhenItsOpeningIsThePreviousClosing_StartsTheDayAfterItAsBalancesMeet(
        self, card
    ):
        second = lane(card, "santander-cc-pdf").captures[1]
        assert (second.first, second.first_basis) == (d("06-12"), ct.MEETS)

    def test_Statement_WhenTheChainIsBroken_StartsWhereTheMissingOneIsExpectedToEnd(self, card):
        fourth = lane(card, "santander-cc-pdf").captures[2]
        assert (fourth.first, fourth.first_basis) == (d("08-12"), ct.INFERRED)

    def test_MissingStatement_IsTheGapTheFetchPageNamesWithItsDates(self, card):
        # What to fetch next puts the hole from the day after the closing 07-11 to the closing
        # the missing statement is expected to have had, 08-11. The balances differ, so the hole
        # is a fact, stated; where it ends is the guess.
        assert [(g.kind, g.first, g.last, g.stated, g.probably) for g in card.gaps] == [
            ("hole-between", d("07-12"), d("08-11"), True, 1)
        ]

    def test_NextStatement_WhenNotYetDue_IsQuietExpectedOnItsDayAndNotAGap(self, card):
        statements = lane(card, "santander-cc-pdf")
        assert statements.trailing == (d("09-12"), TODAY)
        assert statements.next_expected == d("10-11")
        assert [g.kind for g in card.gaps] == ["hole-between"]

    def test_NextStatement_WhenItsExpectedDayHasPassed_IsStillNotAGapUnlessTheFetchPageSaysSo(
        self, household
    ):
        late = timeline(household, CARD, today=date(2026, 10, 20))
        statements = lane(late, "santander-cc-pdf")
        assert statements.next_expected == d("10-11")
        assert [g.kind for g in late.gaps] == ["hole-between"]


class TestCost:
    def test_Build_UsesAFixedThreeStatements_TheStatementsComingFromTheCaller(self, main):
        assert main.queries == 3
