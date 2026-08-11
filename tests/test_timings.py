"""What a page spent its time on, shown on the page that spent it.

A slow page diagnosed by guesswork costs a day; a slow page that names its
own phases costs a glance. This project learned that from a 44-second
account page whose footer identified the culprit on the first live load,
and again from a batch upload that looked hung.

A total alone is not enough. Seven files taking four seconds is a
different fault depending on whether one took 3.4 of them or each took
0.6, so a phase carries its COUNT and its spread, not just its sum - the
same discipline as counts carrying denominators.
"""

from __future__ import annotations

from obdi.timings import Timings


class TestWhatAPageSpentItsTimeOn:
    def test_APhaseRunOnce_ReportsItsShareOfTheWhole(self) -> None:
        timings = Timings()
        timings.record("text", 0.25)
        timings.record("geometry", 0.75)

        named = {phase.name: phase for phase in timings.summary()}

        assert named["text"].total == 0.25
        assert named["geometry"].total == 0.75
        assert timings.total() == 1.0

    def test_TheSlowestPhase_IsReportedFirst(self) -> None:
        # A reader looking for the culprit should not have to scan.
        timings = Timings()
        timings.record("cheap", 0.01)
        timings.record("expensive", 2.00)
        timings.record("middling", 0.30)

        assert [phase.name for phase in timings.summary()] == [
            "expensive",
            "middling",
            "cheap",
        ]

    def test_APhaseRunManyTimes_CarriesItsCountAndItsSpread(self) -> None:
        # The distinction that matters: one slow file among many, or every
        # file equally slow. A sum cannot tell those apart.
        timings = Timings()
        for seconds in (0.10, 0.20, 0.30, 3.40):
            timings.record("per-file", seconds)

        phase = timings.summary()[0]

        assert phase.count == 4
        assert phase.total == 4.0
        assert phase.least == 0.10
        assert phase.most == 3.40
        assert phase.middle == 0.25

    def test_OneSlowSampleAmongMany_IsVisibleInTheDescription(self) -> None:
        timings = Timings()
        for seconds in (0.10, 0.10, 0.10, 3.40):
            timings.record("per-file", seconds)

        described = timings.describe()

        assert "n=4" in described, "a count without its denominator says less"
        assert "max 3.40" in described
        assert "med 0.10" in described

    def test_APhaseThatRanOnce_DoesNotClutterWithASpread(self) -> None:
        # A single sample has no distribution, and printing one invites a
        # reader to compare figures that are all the same number.
        timings = Timings()
        timings.record("text", 0.25)

        described = timings.describe()

        assert "0.25" in described
        assert "n=1" not in described
        assert "med" not in described

    def test_TimingAPhase_MeasuresIt_WithoutTheCallerDoingArithmetic(self) -> None:
        timings = Timings()

        with timings.phase("work"):
            pass

        assert timings.summary()[0].count == 1
        assert timings.summary()[0].total >= 0.0

    def test_APhaseThatRaises_IsStillRecorded(self) -> None:
        # A phase that blew up is exactly the one worth seeing the time
        # for, and losing it would make a failure look instantaneous.
        timings = Timings()

        try:
            with timings.phase("doomed"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert timings.summary()[0].name == "doomed"
        assert timings.summary()[0].count == 1

    def test_NothingMeasured_DescribesItselfAsSuch_RatherThanEmptily(self) -> None:
        # A blank where a measurement belongs reads as "instant". It is
        # not: it is "not measured", and the two want different reactions.
        assert Timings().describe() == "nothing measured"


class TestTheNumbersMustReconcile:
    """A breakdown that does not add up to the wall clock invites the wrong
    conclusion.

    Reported from use: phases summing to 44s under a request that took
    over 60. Every second between them was real work happening somewhere,
    and a display that leaves it out implies the work was accounted for.
    """

    def test_TimeNotInAnyPhase_IsNamed_RatherThanQuietlyMissing(self) -> None:
        timings = Timings()
        timings.record("read", 10.0)
        timings.record("keep", 2.0)

        described = timings.describe(wall_seconds=20.0)

        assert "unaccounted 8.00s" in described
        assert "20.00s" in described, "the wall clock it is reconciled against"

    def test_WhenEverythingIsAccountedFor_NoResidueIsClaimed(self) -> None:
        # Sub-millisecond drift between the wall clock and the sum of its
        # parts is measurement noise, not a missing phase, and reporting
        # it as one would send a reader looking for work that never
        # happened.
        timings = Timings()
        timings.record("read", 10.0)

        described = timings.describe(wall_seconds=10.0002)

        assert "unaccounted" not in described

    def test_APhaseSumExceedingTheWallClock_DoesNotReportNegativeTime(
        self,
    ) -> None:
        # Concurrent phases can legitimately overlap, so the sum can exceed
        # the elapsed time. Negative "unaccounted" would be nonsense.
        timings = Timings()
        timings.record("a", 10.0)
        timings.record("b", 10.0)

        described = timings.describe(wall_seconds=12.0)

        assert "unaccounted" not in described, "overlap is not missing work"
        assert "12.00s elapsed" in described

    def test_WithoutAWallClock_NothingIsClaimedAboutCompleteness(self) -> None:
        timings = Timings()
        timings.record("read", 10.0)

        assert "unaccounted" not in timings.describe()


class TestMeasuringManyThingsAndAddingThemUp:
    """Per item AND in aggregate, from one set of measurements.

    A batch wants both: which file was slow, and what the batch as a whole
    spent on each kind of work. Measuring twice would let the two disagree,
    so the per-item timings are the ones that get added up.
    """

    def test_MergingOnesTimings_IntoAnothers_AddsTheirSamples(self) -> None:
        first = Timings()
        first.record("text", 1.0)
        second = Timings()
        second.record("text", 3.0)
        second.record("mask", 0.5)

        first.merge(second)
        named = {phase.name: phase for phase in first.summary()}

        assert named["text"].count == 2
        assert named["text"].total == 4.0
        assert named["mask"].count == 1

    def test_MergedSamples_KeepTheirSpread_RatherThanBecomingOneNumber(
        self,
    ) -> None:
        # Merging as a sum would lose exactly what the aggregate is for:
        # a batch where one file cost twenty times the rest looks identical
        # to an even one once each file is reduced to a total.
        batch = Timings()
        for seconds in (0.04, 0.04, 2.06):
            per_file = Timings()
            per_file.record("text", seconds)
            batch.merge(per_file)

        phase = batch.summary()[0]

        assert phase.count == 3
        assert phase.most == 2.06
        assert phase.middle == 0.04
