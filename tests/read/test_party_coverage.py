"""Which days and months of an account name their other party by a source's statement, and which by
the printed description alone.

Each case is built by hand with its answer decided before the run. Every figure is invented.
"""

from __future__ import annotations

from datetime import date

from obdi.read.party_coverage import MERGE_DAYS, PartyStated, Stretch, party_stated, stretch_words


def d(month: int, day: int, year: int = 2026) -> date:
    return date(year, month, day)


class TestTheBar:
    def test_AccountWhoseEveryTransactionStatesItsParty_IsOneSolidRunAndSaysNothing(self):
        rows = [(d(3, 2), True), (d(3, 5), True), (d(3, 9), True), (d(4, 1), True)]

        found = party_stated(rows)

        assert found.stated_runs == ((d(3, 2), d(3, 9)), (d(4, 1), d(4, 1)))
        assert found.described_runs == ()
        assert found.stretches == ()
        assert not found.said

    def test_DayWithOneStatedAndOneDescribedTransaction_IsHollow(self):
        found = party_stated([(d(3, 2), True), (d(3, 2), False)])

        assert found.stated_runs == ()
        assert found.described_runs == ((d(3, 2), d(3, 2)),)

    def test_DaysWithNoTransactions_AreNeitherSolidNorHollow(self):
        # Seven quiet days between two stated days is more than the join allows.
        found = party_stated([(d(3, 1), True), (d(3, 1 + MERGE_DAYS + 2), True)])

        assert found.stated_runs == ((d(3, 1), d(3, 1)), (d(3, 9), d(3, 9)))

    def test_NoTransactions_DrawNoRow(self):
        assert not party_stated([]).drawn

    def test_AHollowDayBetweenTwoSolidDays_SplitsTheSolidRunsHoweverClose(self):
        found = party_stated([(d(3, 1), True), (d(3, 2), False), (d(3, 3), True)])

        assert found.stated_runs == ((d(3, 1), d(3, 1)), (d(3, 3), d(3, 3)))
        assert found.described_runs == ((d(3, 2), d(3, 2)),)


class TestTheStretches:
    def test_FeedMonthsThenStatementOnlyMonths_OneStretchOverExactlyThoseMonths(self):
        feed = [(d(month, day), True) for month, day in (
            (1, 3), (1, 10), (1, 17), (1, 24), (1, 31), (2, 7), (2, 14), (2, 20))]
        statement = [(d(month, day), False) for month, day in (
            (3, 4), (3, 10), (3, 16), (3, 22), (3, 28), (4, 3), (4, 9), (4, 15), (4, 21),
            (4, 27), (4, 30))]

        found = party_stated(feed + statement)

        assert found.stretches == (Stretch(d(3, 4), d(4, 30), 11),)
        assert found.described_runs == ((d(3, 4), d(4, 30)),)
        assert found.stated_runs == ((d(1, 3), d(2, 20)),)

    def test_AFewDescribedRowsAmongManyStatedOnes_DrawHollowButSayNothing(self):
        rows = [(d(5, day), True) for day in range(1, 29)] + [(d(5, 12), False)]

        found = party_stated(rows)

        assert found.described_runs == ((d(5, 12), d(5, 12)),)
        assert found.stretches == ()

    def test_AMonthWithAsManyDescribedAsStated_IsNotAStretch(self):
        found = party_stated([(d(5, 1), True), (d(5, 2), False)])

        assert found.stretches == ()

    def test_TwoDescribedMonthsSeparatedByAStatedMonth_AreTwoStretches(self):
        rows = [(d(1, 5), False), (d(2, 5), True), (d(2, 6), True), (d(3, 5), False),
                (d(3, 6), False)]

        found = party_stated(rows)

        assert found.stretches == (Stretch(d(1, 5), d(1, 5), 1), Stretch(d(3, 5), d(3, 6), 2))

    def test_DescribedMonthsAcrossAYearEnd_AreOneStretch(self):
        rows = [(d(12, 10, 2025), False), (d(1, 10), False)]

        found = party_stated(rows)

        assert found.stretches == (Stretch(d(12, 10, 2025), d(1, 10), 2),)

    def test_AQuietMonthBetweenTwoDescribedMonths_SplitsThem(self):
        found = party_stated([(d(1, 10), False), (d(3, 10), False)])

        assert len(found.stretches) == 2


class TestTheWords:
    def test_ManyRows_SayTheCountTheDatesAndWhatWouldFixIt(self):
        said = stretch_words(Stretch(d(3, 4), d(4, 30), 5), askable=True)

        assert said == (
            "5 transactions from 2026-03-04 to 2026-04-30 are named by the description only - "
            "an export file for those months would state the party."
        )

    def test_OneRow_IsSingularAndNamesTheDay(self):
        said = stretch_words(Stretch(d(3, 4), d(3, 4), 1), askable=True)

        assert said.startswith("1 transaction on 2026-03-04 is named by the description only")

    def test_ASourceThatCouldNeverStateOne_SaysSoAndAsksForNothing(self):
        said = stretch_words(Stretch(d(3, 4), d(4, 30), 5), askable=False)

        assert said.endswith("named by the description only - this source states no party.")
        assert "export" not in said

    def test_NothingIsAskedAboutAccountsWithoutAStretch(self):
        assert PartyStated().stretches == ()
