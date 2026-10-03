"""The structure of the difference, over accounts whose faults are known beforehand.

`fault_structure_corpus` records the answer beside each variant. The synthetic
series below are for the shapes a one-account corpus cannot make cheaply: two
stating sources, a pair separated by years, a class that skips a month.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

import fault_structure_corpus as corpus
from obdi.balance_anchors import FamilyWalk
from obdi.fault_structure import (
    EXPLAINED,
    IRREGULAR,
    MONTHLY,
    RECURRING,
    TRANSIENT,
    UNEXPLAINED,
    UNHELD,
    WEEKLY,
    FaultStructure,
    Point,
    class_letter,
    structure,
    walk_report,
)
from obdi.masking import MASKED_TOTAL, Disclosed


def whole(walk: FamilyWalk) -> FaultStructure:
    return walk_report(walk).whole


def points(differences: list[int | None], *, source: str = "s", start: date = date(2026, 1, 1)):
    """A stated balance a day, each with the given difference."""
    return [
        Point(start + timedelta(days=n), source, diff, n) for n, diff in enumerate(differences)
    ]


class TestAnAccountWithNothingWrong:
    def test_Structure_WhenNothingIsWrong_HasNoStepsOneLevelAndEveryBalanceAgrees(self):
        found = whole(corpus.walk())

        assert found.balances == corpus.STATED
        assert found.steps == ()
        assert len(found.levels) == 1
        assert found.share_agreeing == 1.0
        assert found.pairs == 0
        assert found.classes == ()
        assert found.permanent == ()
        assert found.present_minor == 0
        assert found.exact


class TestOneRowMissing:
    def test_Structure_WhenOneRowIsMissing_HasOnePermanentStepAndOneLongLevelAfterIt(self):
        found = whole(corpus.walk(missing=True))

        (step,) = found.steps
        assert (step.day, step.size_minor, step.kind) == (
            corpus.MISSING_ON,
            corpus.MISSING_ROW,
            UNEXPLAINED,
        )
        assert found.pairs == 0
        assert found.permanent == (0,)
        assert found.agreeing == corpus.STATED - 1
        assert len(found.levels) == 2
        (longest, *_) = found.longest_levels
        assert (longest.first_day, longest.last_day) == (corpus.MISSING_ON, corpus.LAST)
        assert longest.balances == (corpus.LAST - corpus.MISSING_ON).days + 1
        assert longest.difference_minor == corpus.MISSING_ROW

    def test_Structure_WhenOneRowIsMissing_PresentDifferenceEqualsThePermanentStep(self):
        found = whole(corpus.walk(missing=True))

        assert found.present_minor == corpus.MISSING_ROW == found.permanent_sum_minor
        assert found.exact


class TestARowHeldThreeDaysLate:
    def test_Structure_WhenARowIsHeldThreeDaysLate_IsOnePairWithAGapOfThreeAndNetsToNil(self):
        found = whole(corpus.walk(late=True))

        first, second = found.steps
        assert (first.day, first.size_minor) == (corpus.LATE_ON, corpus.LATE_ROW)
        assert second.day == corpus.LATE_ON + timedelta(days=corpus.LATE_BY)
        assert second.size_minor == -corpus.LATE_ROW
        assert (first.kind, second.kind) == (TRANSIENT, TRANSIENT)
        assert (first.partner, second.partner) == (1, 0)
        assert (first.gap_days, second.gap_days) == (3, 3)
        assert found.pairs == 1
        assert found.gap_counts == (0, 0, 1, 0, 0)
        assert found.unpaired == 0
        assert found.permanent == ()
        assert found.present_minor == 0
        assert len(found.levels) == 3

    def test_Structure_WhenARowIsHeldLate_CountsOneFaultNotTwo(self):
        found = whole(corpus.walk(late=True))

        assert len(found.steps) == 2
        assert found.pairs == 1


class TestAMonthlyChargeHeldTwice:
    def test_Structure_WhenAMonthlyChargeIsHeldTwice_NamesOneRecurringClassOfEightOnThe5th(self):
        found = whole(corpus.walk(monthly_twice=True))

        (size_class,) = found.classes
        assert size_class.letter == "A"
        assert size_class.members == len(corpus.CHARGE_DAYS) == 8
        assert size_class.days == corpus.CHARGE_DAYS
        assert size_class.rhythm == MONTHLY
        assert size_class.same_day_of_month
        assert size_class.size_minor == -corpus.CHARGE
        assert {step.kind for step in found.steps} == {RECURRING}
        assert {step.size_class for step in found.steps} == {"A"}
        assert found.pairs == 0
        assert found.present_minor == 8 * -corpus.CHARGE

    def test_Structure_WhenAMonthlyChargeIsHeldTwice_EveryStepIsPermanent(self):
        found = whole(corpus.walk(monthly_twice=True))

        assert len(found.permanent) == len(found.steps) == 8
        assert found.exact


class TestAllThreeFaultsTogether:
    @pytest.fixture
    def found(self) -> FaultStructure:
        return whole(corpus.walk(missing=True, late=True, monthly_twice=True))

    def test_Structure_WhenAllThreeFaultsMix_CountsEachCorrectly(self, found):
        assert len(found.steps) == 11
        assert found.pairs == 1
        assert found.gap_counts == (0, 0, 1, 0, 0)
        assert found.unpaired == 9
        assert [c.letter for c in found.classes] == ["A"]
        assert found.classes[0].members == 8
        assert sorted(s.kind for s in found.steps).count(UNEXPLAINED) == 1

    def test_Structure_WhenAllThreeFaultsMix_TheNetEqualsTheSumOfThePermanentSteps(self, found):
        expected = corpus.MISSING_ROW + 8 * -corpus.CHARGE
        assert found.present_minor == expected
        assert found.permanent_sum_minor == expected
        assert found.exact
        assert sum(step.size_minor for step in found.permanent_steps) == expected
        assert {s.day for s in found.permanent_steps} == {corpus.MISSING_ON, *corpus.CHARGE_DAYS}

    def test_Structure_WhenAllThreeFaultsMix_AgreesWithTheWalksOwnChanges(self):
        walk = corpus.walk(missing=True, late=True, monthly_twice=True)

        found = whole(walk)

        assert [(s.day, s.unheld, s.position) for s in found.steps] == [
            (c.day, c.unheld, c.index) for c in walk.changes
        ]


class TestTheRhythmOfARecurringClass:
    def test_Class_WhenOneMonthIsSkipped_IsStillMonthly(self):
        gaps = [0, 31, 28, 62, 30, 31]
        diffs = []
        total = 0
        for _ in gaps:
            total += 700
            diffs.append(total)
        days = [date(2026, 1, 5)]
        for gap in gaps[1:]:
            days.append(days[-1] + timedelta(days=gap))
        series = [
            Point(d, "s", diff, n) for n, (d, diff) in enumerate(zip(days, diffs, strict=True))
        ]

        (size_class,) = structure(series).classes

        assert size_class.rhythm == MONTHLY

    def test_Class_WhenEverySevenDays_IsWeekly(self):
        series = [
            Point(date(2026, 1, 1) + timedelta(days=7 * n), "s", 50 * (n + 1), n) for n in range(5)
        ]

        (size_class,) = structure(series).classes

        assert size_class.rhythm == WEEKLY
        assert not size_class.same_day_of_month

    def test_Class_WhenTheDaysHaveNoRhythm_IsIrregular(self):
        offsets = [0, 3, 11, 12, 40]
        series = [
            Point(date(2026, 1, 1) + timedelta(days=d), "s", 50 * (n + 1), n)
            for n, d in enumerate(offsets)
        ]

        (size_class,) = structure(series).classes

        assert size_class.rhythm == IRREGULAR

    def test_Class_WhenOnlyTwoStepsShareASize_IsNotNamed(self):
        found = structure(points([100, 200]))

        assert found.classes == ()
        assert {s.kind for s in found.steps} == {UNEXPLAINED}

    def test_Classes_AreNamedInOrderOfHowManyMembersTheyHave(self):
        # Three steps of one size (+100) and four of another (+7), interleaved.
        sizes = [100, 7, 100, 7, 100, 7, 7]
        running, diffs = 0, []
        for size in sizes:
            running += size
            diffs.append(running)

        found = structure(points(diffs))

        assert [(c.letter, c.members, c.size_minor) for c in found.classes] == [
            ("A", 4, 7),
            ("B", 3, 100),
        ]

    def test_ClassLetter_RunsOnPastZ(self):
        assert [class_letter(n) for n in (0, 1, 25, 26, 27, 51, 52)] == [
            "A", "B", "Z", "AA", "AB", "AZ", "BA",
        ]


class TestPairingOfOppositeSteps:
    def test_Pair_WhenTheOppositeStepIsYearsLater_StillPairsAndReportsTheLongGap(self):
        series = [
            Point(date(2020, 1, 1), "s", 500, 0),
            Point(date(2020, 1, 2), "s", 500, 1),
            Point(date(2023, 6, 1), "s", 0, 2),
        ]

        found = structure(series)

        assert found.pairs == 1
        assert found.gap_counts == (0, 0, 0, 0, 1)
        assert found.steps[0].gap_days == (date(2023, 6, 1) - date(2020, 1, 1)).days

    def test_Pair_WhenTwoStepsOfOneSizeFollowedByOneOpposite_PairsTheNearestAndLeavesTheOther(self):
        series = points([500, 1000, 500])

        found = structure(series)

        assert found.pairs == 1
        assert found.steps[1].partner == 2
        assert found.steps[0].partner == -1
        assert found.permanent == (0,)
        assert found.present_minor == found.permanent_sum_minor == 500

    def test_Pair_WhenStepsAreEachOthersOppositeButNotInOrder_NetStillEqualsPresent(self):
        series = points([300, 300, 100, 100, 400, 0])

        found = structure(series)

        assert found.exact

    @pytest.mark.parametrize(
        ("gap", "bucket"),
        [(0, 0), (1, 0), (2, 1), (3, 2), (7, 2), (8, 3), (35, 3), (36, 4)],
    )
    def test_GapBuckets_PutEachGapInItsRange(self, gap, bucket):
        start = date(2026, 1, 1)
        series = [
            Point(start, "s", 100, 0),
            Point(start + timedelta(days=gap), "s", 0, 1),
        ]
        # On one day the two balances are two readings; the second undoes the first.
        found = structure(series)

        counts = [0, 0, 0, 0, 0]
        counts[bucket] = 1
        assert found.gap_counts == tuple(counts)


class TestWhatTheLedgerAlreadyExplained:
    def test_Kind_WhenTheStepCoincidesWithAnUnheldSpace_IsNamedAsThat(self):
        series = points([0, 800, 800])

        found = structure(series, unheld_days=[date(2026, 1, 2)])

        (step,) = found.steps
        assert (step.kind, step.unheld) == (UNHELD, True)
        assert found.permanent_unheld == (step,)

    def test_Kind_WhenFaultExplanationFoundAnExplanation_IsNamedAsExplained(self):
        series = points([0, 800, 800])

        found = structure(series, explained={1: True})

        (step,) = found.steps
        assert (step.kind, step.explained) == (EXPLAINED, True)
        assert found.permanent_explained == (step,)

    def test_Kind_WhenNeitherAppliesAndNoClassOrPairExists_IsUnexplained(self):
        found = structure(points([0, 800]))

        assert found.steps[0].kind == UNEXPLAINED
        assert found.permanent_unheld == () == found.permanent_explained


class TestEachStatingSourceHasItsOwnStructure:
    def test_Report_WhenTwoSourcesState_GivesEachItsOwnSteps(self):
        from obdi.fault_structure import _report

        start = date(2026, 1, 1)
        mixed = [
            Point(start + timedelta(days=n), "feed" if n % 2 == 0 else "export", diff, n)
            for n, diff in enumerate([0, 0, 0, 0, 900, 0, 900, 0, 900, 0])
        ]

        report = _report(mixed, None, (), {})

        by_name = {s.source: s.structure for s in report.by_source}
        assert set(by_name) == {"export", "feed"}
        assert len(by_name["export"].steps) == 0
        assert [s.day for s in by_name["feed"].steps] == [start + timedelta(days=4)]
        assert by_name["feed"].present_minor == 900
        assert len(report.whole.steps) > len(by_name["feed"].steps)

    def test_Report_WhenOneSourceStates_HasNoSeparateSources(self):
        assert walk_report(corpus.walk(missing=True)).by_source == ()


class TestSizesAreFigures:
    def test_Disclosed_WhenMasked_ShowsNoSizeAnywhereInTheStructure(self):
        report = walk_report(corpus.walk(missing=True, late=True, monthly_twice=True))

        view = Disclosed(report, unmasked=False).whole

        assert view.present_minor == MASKED_TOTAL
        assert view.permanent_sum_minor == MASKED_TOTAL
        assert {step.size_minor for step in view.steps} == {MASKED_TOTAL}
        assert {level.difference_minor for level in view.levels} == {MASKED_TOTAL}
        assert {c.size_minor for c in view.classes} == {MASKED_TOTAL}
        assert view.steps[0].day == corpus.date(2026, 1, 5)

    def test_Disclosed_WhenUnmasked_ShowsTheSizes(self):
        report = walk_report(corpus.walk(missing=True))

        view = Disclosed(report, unmasked=True).whole

        assert view.present_minor == str(corpus.MISSING_ROW)


class TestCostIsLinear:
    def test_Structure_WhenTwoThousandBalancesAndThreeHundredSteps_FinishesQuickly(self):
        diffs = []
        level = 0
        for n in range(2000):
            if n % 6 == 0:
                level += (n % 11 + 1) * (1 if n % 12 else -1)
            diffs.append(level)
        series = points(diffs)

        began = time.perf_counter()
        found = structure(series)
        took = time.perf_counter() - began

        assert 300 <= len(found.steps) <= 340
        assert found.balances == 2000
        assert found.exact
        assert took < 0.5, f"{took:.2f}s for 2,000 balances"
