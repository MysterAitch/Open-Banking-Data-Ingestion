"""Does the data we hold actually agree with itself?

The store is deliberately fed the same account by several routes - an
aggregator, the bank's own API, a CSV export - because two independent sources
agreeing is real evidence, and where they disagree the disagreement IS the
finding. None of that is worth anything unless something checks.

The comparison is windowed to the OVERLAP on purpose. A CSV covering three
months and an API feed covering two years will always differ in total, and
reporting that as a discrepancy would bury the real ones under arithmetic that
was never going to match.
"""

from __future__ import annotations

from datetime import date

from obdi.coverage import agreements, coverage, gaps, transpositions
from obdi.models import SourceTier, Transaction


def txn(source, day, amount, *, account="current", source_id=None, month=1, desc=None,
        tier=SourceTier.SYNTHETIC):
    return Transaction(
        account_id=account,
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, month, day),
        booking_date=date(2026, month, day),
        description=desc or f"txn {day}",
        source=source,
        source_id=source_id,
        tier=tier,
        content_key=f"k{month}{day}{amount}",
    )


class TestWhatWeHold:
    def test_Coverage_WhenSeveralSourcesFeedOneAccount_ReportsEachSeparately(self):
        rows = coverage(
            [txn("truelayer", 1, -500), txn("truelayer", 5, 2000), txn("halifax-qif", 1, -500)]
        )

        by_source = {row.source: row for row in rows}
        assert set(by_source) == {"truelayer", "halifax-qif"}
        assert by_source["truelayer"].count == 2
        assert by_source["truelayer"].earliest == date(2026, 1, 1)
        assert by_source["truelayer"].latest == date(2026, 1, 5)

    def test_Coverage_SeparatesMoneyInFromMoneyOut_NotJustTheNet(self):
        rows = coverage([txn("truelayer", 1, -500), txn("truelayer", 2, 2000)])

        row = rows[0]
        # A net figure hides the case where both sides are wrong by the same
        # amount, which is exactly what a sign-convention bug looks like.
        assert row.outflow_minor == 500
        assert row.inflow_minor == 2000
        assert row.net_minor == 1500

    def test_Coverage_ReportsHowMuchOfItCarriesADurableId(self):
        rows = coverage(
            [
                txn("truelayer", 1, -500, source_id="stable-1", tier=SourceTier.AUTHORITATIVE),
                txn("truelayer", 2, -600),
            ]
        )

        # Tells you how much of the store rests on the provider's own identity
        # versus on content matching - which is what determines how much a
        # matching change could disturb.
        assert rows[0].with_durable_id == 1
        assert rows[0].count == 2


class TestWhetherSourcesAgree:
    def test_Agreement_WhenTwoSourcesSeeTheSamePeriodIdentically_ReportsAgreement(self):
        found = agreements(
            [
                txn("truelayer", 1, -500),
                txn("truelayer", 2, 2000),
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 2, 2000),
            ]
        )

        assert len(found) == 1
        assert found[0].agrees
        assert found[0].left_count == found[0].right_count == 2

    def test_Agreement_WhenOneSourceIsMissingATransaction_SaysSo(self):
        found = agreements(
            [
                txn("truelayer", 1, -500),
                txn("truelayer", 2, 2000),
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 2, 2000),
                txn("halifax-qif", 2, -750),
            ]
        )

        assert not found[0].agrees

    def test_Agreement_WhenTheSourcesCoverDifferentPeriods_ComparesOnlyTheOverlap(self):
        # The CSV covers days 1-2; the feed covers 1-9. Comparing wholesale would
        # always "disagree" and teach the reader to ignore the report.
        found = agreements(
            [
                txn("truelayer", 1, -500),
                txn("truelayer", 2, 2000),
                txn("truelayer", 9, -9999),
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 2, 2000),
            ]
        )

        assert found[0].overlap_from == date(2026, 1, 1)
        assert found[0].overlap_to == date(2026, 1, 2)
        assert found[0].agrees, "the day-9 transaction is outside the overlap and irrelevant"

    def test_Agreement_WhenSourcesNeverOverlap_IsNotReportedAsADisagreement(self):
        found = agreements([txn("truelayer", 1, -500), txn("halifax-qif", 20, -500)])

        # Nothing to compare is not the same as comparing and differing.
        assert found == []

    def test_Agreement_OnlyComparesWithinOneAccount(self):
        found = agreements(
            [txn("truelayer", 1, -500, account="a"), txn("halifax-qif", 1, -500, account="b")]
        )

        assert found == []


class TestHolesInWhatWeHold:
    """A missing month usually means a missing file, not a quiet month.

    Only months ENCLOSED by data count. An account that stopped being used has
    empty months at the end, and flagging those would produce a permanent
    complaint about something that is simply true - the fastest way to make a
    report ignored.
    """

    def test_Gaps_WhenAMonthIsMissingBetweenTwoWithData_IsReportedAsAGap(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
            ]
        )

        assert [(g.source, g.month) for g in found] == [("halifax-qif", "2026-02")]

    def test_Gaps_WhenTheAccountSimplyStopsBeingUsed_ReportsNothing(self):
        # Two consecutive months then silence: nothing encloses the silence, so
        # there is no evidence anything is missing.
        found = gaps([txn("halifax-qif", 1, -500), txn("halifax-qif", 2, -500)])

        assert found == []

    def test_Gaps_AreReportedPerSource_SoTheMissingFileCanBeIdentified(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
                txn("truelayer", 1, -500),
                txn("truelayer", 1, -500, month=2),
                txn("truelayer", 1, -500, month=3),
            ]
        )

        # The feed is complete; only the file import has a hole, and that is
        # the one you can actually go and download.
        assert [g.source for g in found] == ["halifax-qif"]

    def test_Gaps_WhenEverySourceAgreesTheMonthIsEmpty_IsNotTreatedAsMissingData(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
                txn("truelayer", 1, -500),
                txn("truelayer", 1, -500, month=3),
            ]
        )

        # Both routes say February was quiet. Corroborated absence is evidence
        # the account was idle, not evidence that a file is missing.
        assert all(not g.contradicted for g in found)

    def test_Gaps_WhenAnotherSourceHasTheMonth_TheAbsenceIsContradictedAndNamed(self):
        found = gaps(
            [
                txn("halifax-qif", 1, -500),
                txn("halifax-qif", 1, -500, month=3),
                txn("truelayer", 1, -500),
                txn("truelayer", 1, -500, month=2),
                txn("truelayer", 1, -500, month=3),
            ]
        )

        missing = [g for g in found if g.contradicted]
        assert [(g.source, g.month, g.seen_in) for g in missing] == [
            ("halifax-qif", "2026-02", ("truelayer",))
        ]


class TestDatesReadTheWrongWayRound:
    """A transposed date is the quietest corruption available.

    Nothing looks wrong: the amount is right, the payee is right, the date is a
    real date. It only shows when a second source disagrees about WHICH day -
    and count-and-total checks are blind to it, because moving a transaction
    between months changes neither.

    Only days 1-12 can transpose; 13 upwards is unambiguous. So the classic
    signature is a file where some rows moved and others did not.
    """

    def test_Transposition_WhenTwoSourcesSwapDayAndMonth_IsDetected(self):
        found = transpositions(
            [
                txn("truelayer", 3, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -2500, month=3, desc="RENT"),
            ]
        )

        assert len(found) == 1
        assert {found[0].left_date, found[0].right_date} == {date(2026, 5, 3), date(2026, 3, 5)}

    def test_Transposition_WhenTheSameSourceHasBothDates_IsNotFlagged(self):
        # Two genuine payments of equal value within one source. Suspicious
        # only across sources, where the same payment cannot be in two places.
        found = transpositions(
            [
                txn("halifax-qif", 3, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -2500, month=3, desc="RENT"),
            ]
        )

        assert found == []

    def test_Transposition_WhenAmountsDiffer_IsNotFlagged(self):
        found = transpositions(
            [
                txn("truelayer", 3, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -9900, month=3, desc="RENT"),
            ]
        )

        assert found == []

    def test_Transposition_WhenDatesAreSimplyTheSame_IsNotFlagged(self):
        found = transpositions(
            [
                txn("truelayer", 5, -2500, month=5, desc="RENT"),
                txn("halifax-qif", 5, -2500, month=5, desc="RENT"),
            ]
        )

        # Day equals month here, so a swap is undetectable AND harmless.
        assert found == []

    def test_Transposition_CatchesTheMixedCase_WhereOnlyAmbiguousRowsMoved(self):
        found = transpositions(
            [
                # Day 20 cannot transpose, and agrees.
                txn("truelayer", 20, -100, month=3, desc="BILL"),
                txn("halifax-qif", 20, -100, month=3, desc="BILL"),
                # Day 4 can, and does not agree.
                txn("truelayer", 4, -700, month=9, desc="GYM"),
                txn("halifax-qif", 9, -700, month=4, desc="GYM"),
            ]
        )

        assert len(found) == 1, "the unambiguous row is fine; the ambiguous one moved"
        assert found[0].amount_minor == -700
