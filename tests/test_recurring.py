"""What recurs in the transactions the store holds: found from invented series with known answers.

Every series below is built by hand, and its answer is written beside it BEFORE the detector
existed: how many times it occurred, the cadence, the usual day, the usual amount, when it is
next expected, and the marks it carries. The amounts are invented. "Today" is fixed so that
"stopped" has a known answer.

A calendar fact the answers lean on: 2026-10-07 is a Wednesday; 2026-06-27 is a Saturday and
2026-09-27 a Sunday, so a bill taken "on the 27th" is taken on the Monday after in those months.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import pytest

from obdi.jsontypes import JsonObject
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.recurring import HABIT, PULLED, SCHEDULED, Series, find_recurring

TODAY = date(2026, 10, 7)
A, B, C = "acct-a", "acct-b", "acct-c"

_counter = [0]


def tx(
    account: str,
    day: date,
    minor: int,
    description: str,
    *,
    transfer: bool = False,
    status: TransactionStatus = TransactionStatus.BOOKED,
    source: str = "synthetic",
    raw: JsonObject | None = None,
) -> Transaction:
    _counter[0] += 1
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        source=source,
        tier=SourceTier.SYNTHETIC,
        status=status,
        entity_id=f"e{_counter[0]:06d}",
        is_internal_transfer=transfer,
        raw=raw or {},
    )


def monthly(account: str, months: list[tuple[int, int]], day: int, minor: int, text: str):
    return [tx(account, date(y, m, day), minor, text) for y, m in months]


def find_one(found: list[Series], fragment: str, account: str = A) -> Series:
    hits = [s for s in found if s.account == account and fragment in s.label.casefold()]
    assert len(hits) == 1, f"{fragment!r}: {[(s.account, s.label) for s in found]}"
    return hits[0]


class TestMonthlyDirectDebit:
    def rows(self) -> list[Transaction]:
        # The 27th, shifted to the following Monday when it falls on a weekend; the reference
        # number in the description changes each month.
        days = [
            date(2026, 4, 27),
            date(2026, 5, 27),
            date(2026, 6, 29),
            date(2026, 7, 27),
            date(2026, 8, 27),
            date(2026, 9, 28),
        ]
        return [
            tx(A, d, -6900, f"DIRECT DEBIT BRITISH GAS {4400 + i} REF {88100 + 7 * i}")
            for i, d in enumerate(days)
        ]

    def test_MonthlyDirectDebitWithWeekendShifts_IsOneMonthlySeriesOnThe27th(self):
        found = find_recurring(self.rows(), pairs=(), today=TODAY)

        assert len(found) == 1
        gas = found[0]
        assert gas.cadence == "monthly"
        assert gas.usual_day == 27
        assert gas.count == 6
        assert (gas.first_seen, gas.last_seen) == (date(2026, 4, 27), date(2026, 9, 28))
        assert gas.next_expected == date(2026, 10, 27)
        assert (gas.usual_minor, gas.min_minor, gas.max_minor, gas.latest_minor) == (6900,) * 4
        assert gas.direction == "out"
        assert not (gas.stopped or gas.changed or gas.is_transfer or gas.is_income)
        assert gas.missed == 0

    def test_ReferenceNumbersInTheDescription_DoNotSplitTheSeries(self):
        found = find_recurring(self.rows(), pairs=(), today=TODAY)

        assert [s.shape for s in found] == ["direct debit british gas ref"]

    def test_PendingAndHistoryRows_AreNotOccurrences(self):
        rows = [
            *self.rows(),
            tx(
                A,
                date(2026, 10, 1),
                -6900,
                "DIRECT DEBIT BRITISH GAS 4410 REF 1",
                status=TransactionStatus.PENDING,
            ),
            tx(
                A,
                date(2026, 9, 28),
                -6900,
                "DIRECT DEBIT BRITISH GAS 4410 REF 2",
                status=TransactionStatus.FOLDED,
            ),
        ]

        found = find_recurring(rows, pairs=(), today=TODAY)

        assert [s.count for s in found] == [6]


class TestOtherCadences:
    def test_YearlySubscription_IsYearlyInOctoberOnThe14th(self):
        rows = [
            tx(A, date(y, 10, 14), -3800, "GITHUB SUBSCRIPTION") for y in (2022, 2023, 2024, 2025)
        ]
        rows.append(tx(A, TODAY, -100, "NEWSAGENT"))

        series = find_one(find_recurring(rows, pairs=(), today=TODAY), "github")

        assert series.cadence == "yearly"
        assert (series.usual_month, series.usual_day) == (10, 14)
        assert series.count == 4
        assert series.next_expected == date(2026, 10, 14)
        assert not series.stopped

    def test_WeeklyOnFridays_IsWeeklyWithTheFridayRecorded(self):
        first = date(2026, 8, 7)
        rows = [tx(A, first + timedelta(weeks=n), -450, "PARKRUN COFFEE CLUB") for n in range(9)]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.cadence == "weekly"
        assert series.weekday == 4
        assert series.count == 9
        assert series.last_seen == date(2026, 10, 2)
        assert series.next_expected == date(2026, 10, 9)
        assert not series.stopped

    def test_EveryTwentyEightDays_IsFourWeeklyAndNotMonthly(self):
        first = date(2026, 5, 1)
        rows = [tx(A, first + timedelta(days=28 * n), -1500, "WINDOW CLEANER") for n in range(6)]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.cadence == "four-weekly"
        assert series.count == 6
        assert series.next_expected == date(2026, 10, 16)

    def test_QuarterlyBill_IsQuarterly(self):
        rows = [
            tx(A, date(y, m, 5), -12000, "WATER SERVICES")
            for y, m in ((2025, 10), (2026, 1), (2026, 4), (2026, 7))
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.cadence == "quarterly"
        assert series.usual_day == 5
        assert series.next_expected == date(2026, 10, 5)


class TestChangeAndAbsence:
    def test_PriceRiseMidSeries_IsMarkedChangedWithTheRiseAsAPercentage(self):
        months = [(2026, m) for m in range(3, 11)]
        rows = [
            tx(A, date(y, m, 3), -1099 if m < 8 else -1299, "STREAMING SERVICE") for y, m in months
        ]
        # 3 October is a Saturday: the Monday after.
        rows[-1] = replace(rows[-1], value_date=date(2026, 10, 5), booking_date=date(2026, 10, 5))

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.cadence == "monthly"
        assert series.changed
        assert (series.usual_minor, series.latest_minor) == (1099, 1299)
        assert (series.min_minor, series.max_minor) == (1099, 1299)
        assert round(series.drift_percent, 1) == 18.2
        assert not series.stopped

    def test_SmallDriftUnderThreePercent_IsNotAChange(self):
        rows = [
            tx(A, date(2026, m, 9), -(5000 + (20 if m > 7 else 0)), "INSURANCE")
            for m in range(3, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert not series.changed

    def test_AVariableBill_IsRecurringButNotSaidToHaveChanged(self):
        amounts = [4100, 5600, 3300, 7900, 6100, 2900, 5200]
        rows = [
            tx(A, date(2026, m, 20), -a, "ELECTRICITY")
            for m, a in zip(range(3, 10), amounts, strict=True)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.cadence == "monthly"
        assert not series.steady
        assert not series.changed
        assert (series.min_minor, series.max_minor) == (2900, 7900)

    def test_AMissedMonth_StaysOneMonthlySeriesAndCountsTheMiss(self):
        months = [(2026, m) for m in (3, 4, 5, 7, 8, 9)]

        (series,) = find_recurring(monthly(A, months, 15, -3500, "GYM MEMBERSHIP"), (), TODAY)

        assert series.cadence == "monthly"
        assert (series.count, series.missed) == (6, 1)
        assert not series.stopped
        assert series.next_expected == date(2026, 10, 15)

    def test_SeriesWhoseExpectedDatePassedWithNoneSeen_IsStopped(self):
        months = [(2026, m) for m in range(1, 6)]
        rows = [*monthly(A, months, 10, -799, "OLD MAGAZINE"), tx(A, TODAY, -100, "NEWSAGENT")]

        series = find_one(find_recurring(rows, (), TODAY), "magazine")

        assert series.stopped
        assert series.last_seen == date(2026, 5, 10)
        assert series.next_expected == date(2026, 6, 10)

    def test_AccountWhoseDataEndedInJune_IsNotSaidToHaveStoppedPayingAnything(self):
        months = [(2026, m) for m in range(2, 7)]
        rows = [
            *monthly(C, months, 10, -799, "OLD MAGAZINE"),
            tx(C, date(2026, 6, 20), -100, "UNRELATED"),
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert not series.stopped

    def test_JustPastTheExpectedDate_IsNotYetStopped(self):
        months = [(2026, m) for m in range(4, 10)]
        rows = [*monthly(A, months, 5, -2000, "STORAGE UNIT"), tx(A, TODAY, -100, "NEWSAGENT")]

        series = find_one(find_recurring(rows, (), TODAY), "storage")

        # Expected 5 October; two days late is within what a weekend and a slow bank explain.
        assert not series.stopped

    def test_AWeekPastTheExpectedDate_IsStopped(self):
        months = [(2026, m) for m in range(4, 10)]
        later = date(2026, 10, 13)
        rows = [*monthly(A, months, 5, -2000, "STORAGE UNIT"), tx(A, later, -100, "NEWSAGENT")]

        series = find_one(find_recurring(rows, (), later), "storage")

        assert series.stopped


class TestIncomeAndTransfers:
    def test_SalaryPaidEarlyWhenThe25thIsAWeekend_IsIncomeAndMonthlyOnThe25th(self):
        days = [
            date(2026, 4, 24),
            date(2026, 5, 25),
            date(2026, 6, 25),
            date(2026, 7, 24),
            date(2026, 8, 25),
            date(2026, 9, 25),
        ]
        rows = [tx(A, d, 250000, "ACME LTD SALARY") for d in days]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.is_income and not series.is_transfer
        assert series.direction == "in"
        assert series.cadence == "monthly"
        assert series.usual_day == 25

    def test_TransferBetweenTwoAccounts_IsOneSeriesMarkedTransferAndNotIncome(self):
        days = [
            date(2026, 6, 1),
            date(2026, 7, 1),
            date(2026, 8, 3),
            date(2026, 9, 1),
            date(2026, 10, 1),
        ]
        out = [tx(A, d, -10000, "TO SAVINGS POT", transfer=True) for d in days]
        into = [tx(B, d, 10000, "FROM MAIN ACCOUNT", transfer=True) for d in days]
        pairs = [(o.entity_id, i.entity_id) for o, i in zip(out, into, strict=True)]

        found = find_recurring(out + into, pairs=pairs, today=TODAY)

        assert len(found) == 1
        (series,) = found
        assert series.is_transfer and not series.is_income
        assert (series.account, series.other_account) == (A, B)
        assert series.count == 5
        assert series.usual_day == 1

    def test_TransferLegWithNoPairedOpposite_IsStillMarkedATransfer(self):
        rows = [tx(A, date(2026, m, 1), -2000, "TO POT", transfer=True) for m in range(5, 10)]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.is_transfer
        assert series.other_account == ""

    def test_TwoDifferentTransfersBetweenTheSameAccounts_AreTwoSeriesByAmount(self):
        rent = [
            tx(A, date(2026, m, 1), -50000, "TO BILLS POT", transfer=True) for m in range(5, 10)
        ]
        fun = [tx(A, date(2026, m, 15), -2000, "TO BILLS POT", transfer=True) for m in range(5, 10)]
        into = [
            tx(B, r.value_date, -r.amount_minor, "FROM MAIN", transfer=True) for r in rent + fun
        ]
        pairs = [(o.entity_id, i.entity_id) for o, i in zip(rent + fun, into, strict=True)]

        found = find_recurring(rent + fun + into, pairs=pairs, today=TODAY)

        assert sorted((s.usual_minor, s.usual_day) for s in found) == [(2000, 15), (50000, 1)]


class TestWhatMustNotJoinOrForm:
    def test_TwoPayeesSharingAWord_StayTwoSeries(self):
        months = [(2026, m) for m in range(6, 10)]
        rows = monthly(A, months, 12, -5000, "BRIGHT ENERGY") + monthly(
            A, months, 12, -4000, "BRIGHT DENTAL"
        )

        found = find_recurring(rows, pairs=(), today=TODAY)

        assert sorted((s.label, s.count, s.usual_minor) for s in found) == [
            ("BRIGHT DENTAL", 4, 4000),
            ("BRIGHT ENERGY", 4, 5000),
        ]

    def test_MonthlySubscriptionPaidOnceFromAnotherAccount_IsOneSeriesWithOneAccountChanged(self):
        # Eight months on the 8th: the seventh (August) paid from B, every other from A.
        rows = [
            tx(B if m == 8 else A, date(2026, m, 8), -999, "MUSIC STREAMING") for m in range(2, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.account, series.count, series.off_account) == (A, 8, 1)
        assert series.cadence == "monthly"
        assert series.missed == 0
        assert not series.stopped
        assert series.next_expected == date(2026, 10, 8)

    def test_SubscriptionMovedForGoodToAnotherAccount_IsOneSeriesNotStopped(self):
        rows = [
            tx(A if m < 6 else B, date(2026, m, 8), -999, "MUSIC STREAMING") for m in range(2, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.account, series.count, series.off_account) == (B, 8, 4)
        assert not series.stopped

    def test_TheSamePayeeTakenTwiceAMonthFromTwoAccounts_IsOneSeriesPerAccount(self):
        months = [(2026, m) for m in range(5, 10)]
        rows = monthly(A, months, 8, -999, "MUSIC STREAMING") + monthly(
            B, months, 8, -999, "MUSIC STREAMING"
        )

        found = find_recurring(rows, pairs=(), today=TODAY)

        assert sorted(s.account for s in found) == [A, B]

    def test_IrregularCafeVisitsAndTwoOffRepeats_FormNoSeries(self):
        visits = [date(2026, 8, d) for d in (2, 3, 9, 17, 30)] + [date(2026, 9, d) for d in (4, 5)]
        rows = [tx(A, d, -350 - 10 * i, "CORNER CAFE") for i, d in enumerate(visits)]
        rows += [
            tx(A, date(2026, 8, 14), -2500, "ONE OFF TWICE"),
            tx(A, date(2026, 9, 14), -2500, "ONE OFF TWICE"),
        ]

        assert find_recurring(rows, pairs=(), today=TODAY) == []

    def test_FixedMonthlyChargeAmongRandomPurchasesAtOnePayee_IsFoundByItsAmount(self):
        prime = [tx(A, date(2026, m, 9), -799, "AMAZON MKTPLACE") for m in range(4, 10)]
        random_days = [(4, 2), (4, 21), (5, 30), (6, 11), (7, 4), (7, 19), (8, 27), (9, 15)]
        others = [
            tx(A, date(2026, m, d), -(1000 + 137 * i), "AMAZON MKTPLACE")
            for i, (m, d) in enumerate(random_days)
        ]

        found = find_recurring(prime + others, pairs=(), today=TODAY)

        assert [(s.cadence, s.usual_minor, s.count) for s in found] == [("monthly", 799, 6)]

    def test_NoTransactions_FindNothing(self):
        assert find_recurring([], pairs=(), today=TODAY) == []

    def test_RowsWithNoReadableName_AreNotGroupedTogether(self):
        rows = [tx(A, date(2026, m, 4), -100, "4417 88123") for m in range(4, 10)]

        assert find_recurring(rows, pairs=(), today=TODAY) == []

    def test_TooFewOccurrences_FormNoSeries(self):
        rows = monthly(A, [(2026, 8), (2026, 9)], 3, -500, "NEW GADGET CLUB")

        assert find_recurring(rows, pairs=(), today=TODAY) == []

    @pytest.mark.parametrize("count", [4, 5])
    def test_FourOrMoreRegularOccurrences_FormASeries(self, count):
        rows = monthly(A, [(2026, m) for m in range(5, 5 + count)], 3, -500, "NEW GADGET CLUB")

        assert [s.count for s in find_recurring(rows, pairs=(), today=TODAY)] == [count]


class TestHowLongARunMustSpan:
    """A series spans at least four slots of its cadence: three gaps from first to last.

    Three occurrences in a fortnight are a coincidence of a payee and a gap, not a rhythm. A slot
    that was missed still counts towards the span, so three seen over four slots qualifies
    (a third of four is one missing, and two thirds of four is three present).
    """

    TUESDAYS = tuple(date(2026, 9, d) for d in (1, 8, 15, 22, 29))

    def weekly(self, days: Sequence[date]) -> list[Transaction]:
        return [tx(A, d, -450, "CORNER CAFE CLUB") for d in days]

    def test_ThreeWeeklyOccurrencesOverTwoWeeks_AreNotASeries(self):
        found = find_recurring(self.weekly(self.TUESDAYS[2:]), pairs=(), today=TODAY)

        assert found == []

    def test_FourWeeklyOccurrencesOverThreeWeeks_AreAWeeklySeries(self):
        (series,) = find_recurring(self.weekly(self.TUESDAYS[1:]), pairs=(), today=TODAY)

        assert (series.cadence, series.weekday, series.count, series.missed) == ("weekly", 1, 4, 0)

    def test_ThreeWeeklyOccurrencesOverThreeWeeksWithOneMissed_AreAWeeklySeries(self):
        days = [self.TUESDAYS[1], self.TUESDAYS[3], self.TUESDAYS[4]]

        (series,) = find_recurring(self.weekly(days), pairs=(), today=TODAY)

        # A weekly rhythm with no stated type is a habit, which is never missing a payment: the
        # skipped week shows as three of four periods seen.
        assert (series.cadence, series.count, series.periods, series.missed) == ("weekly", 3, 4, 0)

    def test_ThreeMonthlyOccurrencesOverTwoMonths_AreNotASeries(self):
        rows = monthly(A, [(2026, m) for m in (7, 8, 9)], 12, -5000, "BRIGHT ENERGY")

        assert find_recurring(rows, pairs=(), today=TODAY) == []

    def test_FourMonthsWithOneMissed_AreAMonthlySeriesOfThreeSeen(self):
        rows = monthly(A, [(2026, m) for m in (6, 7, 9)], 12, -5000, "BRIGHT ENERGY")

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.cadence, series.count, series.missed) == ("monthly", 3, 1)

    def test_FiveMonthsWithTwoMissed_AreNotASeries(self):
        rows = monthly(A, [(2026, m) for m in (5, 6, 9)], 12, -5000, "BRIGHT ENERGY")

        assert find_recurring(rows, pairs=(), today=TODAY) == []

    def test_ThreeYearlyOccurrencesOverTwoYears_AreNotASeries(self):
        rows = [tx(A, date(y, 3, 2), -3800, "ANNUAL LICENCE") for y in (2024, 2025, 2026)]

        assert find_recurring(rows, pairs=(), today=TODAY) == []


def starling(word_field: str, word: str) -> JsonObject:
    return {word_field: word}


class TestKindAndBasis:
    """Who starts a payment: the other side (pulled), a schedule the owner set (scheduled), or
    the owner each time by choice (a habit), and which signal decided it.

    KNOWN ANSWERS, decided before the first run. A type the source states decides first; where
    the type is a card payment or none is stated, the shape decides: a month-counted cadence is
    pulled (a payee collecting on its own day), a weekday rhythm is a habit. Only pulled and
    scheduled series can be stopped or have missed periods.
    """

    def test_DirectDebitOnThe27th_IsPulledByType(self):
        rows = [
            tx(
                A,
                date(2026, m, 27),
                -6900 - 13 * m,
                "BRITISH GAS",
                source="starling",
                raw=starling("source", "DIRECT_DEBIT"),
            )
            for m in range(4, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.kind, series.basis) == (PULLED, "by type: Direct Debit")

    def test_StandingOrderOnThe1st_IsScheduledByType(self):
        rows = [
            tx(
                A,
                date(2026, m, 1),
                -50000,
                "LANDLORD RENT",
                source="truelayer",
                raw=starling("transaction_category", "STANDING_ORDER"),
            )
            for m in range(4, 11)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.kind, series.basis) == (SCHEDULED, "by type: standing order")

    def test_StandingOrderEveryWeek_IsScheduledAndStoppedWhenItsWeeksStopArriving(self):
        first = date(2026, 6, 2)
        rows = [
            tx(
                A,
                first + timedelta(weeks=n),
                -2500,
                "GYM MEMBERSHIP",
                source="truelayer",
                raw=starling("transaction_category", "STANDING_ORDER"),
            )
            for n in range(6)
        ]
        rows.append(tx(A, TODAY, -100, "NEWSAGENT"))

        series = find_one(find_recurring(rows, (), TODAY), "gym")

        assert (series.kind, series.cadence) == (SCHEDULED, "weekly")
        assert series.stopped

    def test_CardPaymentOfOneAmountOnThe14th_IsPulledByShape(self):
        rows = [
            tx(
                A,
                date(2026, m, 14),
                -999,
                "STREAMING CO",
                source="starling",
                raw={"source": "MASTER_CARD", "sourceSubType": "CONTACTLESS"},
            )
            for m in range(4, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.kind, series.basis) == (PULLED, "by shape: steady amount, same day")

    def test_CardSubscriptionWord_IsPulledByTypeEvenWhenTheAmountVaries(self):
        rows = [
            tx(
                A,
                date(2026, m, 14),
                -(900 + 70 * m),
                "CLOUD CO",
                source="starling",
                raw={"source": "MASTER_CARD", "sourceSubType": "CARD_SUBSCRIPTION"},
            )
            for m in range(4, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.kind, series.basis) == (PULLED, "by type: card subscription")

    def sundays(self) -> list[Transaction]:
        """Thirty-eight Sundays of the fifty-two from 2025-10-12 to 2026-10-04, amounts varying."""
        first = date(2025, 10, 12)
        skipped = {1, 2, 6, 10, 14, 18, 22, 26, 30, 34, 38, 42, 46, 50}
        return [
            tx(A, first + timedelta(weeks=n), -(1250 + (n * 37) % 400), "CLEAN AIR ZONE CHARGE")
            for n in range(52)
            if n not in skipped
        ]

    def test_SundayCardPaymentsOfVaryingAmountsIn38Of52Weeks_AreAHabitByShape(self):
        (series,) = find_recurring(self.sundays(), pairs=(), today=TODAY)

        assert (series.kind, series.basis) == (HABIT, "by shape: weekday rhythm, amounts vary")
        assert (series.cadence, series.weekday) == ("weekly", 6)
        assert (series.count, series.periods) == (38, 52)
        assert not series.stopped
        assert series.missed == 0

    def test_AHabitWhoseLastSundayWasMonthsAgo_IsNeverSaidToHaveStopped(self):
        later = date(2027, 3, 1)

        (series,) = find_recurring(self.sundays(), pairs=(), today=later)

        assert series.kind == HABIT
        assert not series.stopped

    def test_CardProviderCollectionOnThe10thWithVaryingAmountsAndAStatedDirectDebit_IsPulledByType(
        self,
    ):
        rows = [
            tx(
                A,
                date(2026, m, 10),
                -(20000 + 3100 * m),
                "AMEX COLLECTION",
                source="truelayer",
                raw=starling("transaction_category", "DIRECT_DEBIT"),
            )
            for m in range(3, 8)
        ]
        rows.append(tx(A, TODAY, -100, "NEWSAGENT"))

        series = find_one(find_recurring(rows, (), TODAY), "amex")

        assert (series.kind, series.basis) == (PULLED, "by type: Direct Debit")
        # No collection since July, past its day with room to spare: a pulled series stops.
        assert series.stopped

    def test_CardProviderCollectionOnThe10thWithVaryingAmountsAndNoStatedType_IsPulledByShape(self):
        rows = [
            tx(A, date(2026, m, 10), -(20000 + 3100 * m), "AMEX COLLECTION") for m in range(5, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        # What tells it from a habit: it falls on one day of the month, within a weekend's shift,
        # which a payment made by choice does not do; and a habit has a weekday rhythm.
        assert (series.kind, series.basis) == (
            PULLED,
            "by shape: same day each month, amount varies",
        )

    def test_TransferOfOneAmountOnThe1stWithNoStatedType_IsScheduledByShape(self):
        rows = [
            tx(A, date(2026, m, 1), -10000, "TO SAVINGS POT", transfer=True) for m in range(5, 10)
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.kind, series.basis) == (
            SCHEDULED,
            "by shape: transfer between own accounts, same day",
        )

    def test_DirectDebitStatedOnOnlySomeOccurrences_StillDecidesTheSeriesByType(self):
        stated: JsonObject = {"source": "DIRECT_DEBIT"}
        words: list[JsonObject] = [stated, {}, {}, {}, stated]
        rows = [
            tx(A, date(2026, m, 3), -4400, "WATER BOARD", source="starling", raw=words[i])
            for i, m in enumerate(range(5, 10))
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert (series.kind, series.basis) == (PULLED, "by type: Direct Debit")

    def collection(self, months: list[int]) -> tuple[list[Transaction], list[tuple[str, str]]]:
        """The card provider collecting on the 10th from A to the held card B, by Direct Debit."""
        out = [
            tx(
                A,
                date(2026, m, 10),
                -(20000 + 900 * m),
                "CARD COLLECTION",
                transfer=True,
                source="truelayer",
                raw=starling("transaction_category", "DIRECT_DEBIT"),
            )
            for m in months
        ]
        into = [
            tx(B, o.value_date, -o.amount_minor, "PAYMENT RECEIVED", transfer=True) for o in out
        ]
        pairs = [(o.entity_id, i.entity_id) for o, i in zip(out, into, strict=True)]
        return [*out, *into], pairs

    def test_CollectionSkippedAfterTheCardClosedAtNil_IsExplainedAndNotMissed(self):
        rows, pairs = self.collection([3, 4, 6, 7, 8])
        rows.append(tx(A, date(2026, 8, 25), -100, "NEWSAGENT"))

        nil = {B: [(date(2026, 4, 25), 0)]}

        found = find_recurring(rows, pairs, date(2026, 8, 25), closings=nil)

        (series,) = [s for s in found if s.account == A and "collection" in s.label.casefold()]
        assert series.kind == PULLED
        assert (series.missed, series.explained, series.stopped) == (0, 1, False)

    def test_CollectionSkippedAfterTheCardClosedOwingMoney_IsMissedWithNoReasonHeld(self):
        rows, pairs = self.collection([3, 4, 6, 7, 8])
        rows.append(tx(A, date(2026, 8, 25), -100, "NEWSAGENT"))

        found = find_recurring(
            rows, pairs, date(2026, 8, 25), closings={B: [(date(2026, 4, 25), -5000)]}
        )

        (series,) = [s for s in found if s.account == A and "collection" in s.label.casefold()]
        assert (series.missed, series.explained) == (1, 0)

    def test_CollectionSkippedWithNoStatementHeldForTheCycle_IsMissedNotExplained(self):
        rows, pairs = self.collection([3, 4, 6, 7, 8])

        old = {B: [(date(2026, 1, 25), 0)]}

        found = find_recurring(rows, pairs, date(2026, 8, 25), closings=old)

        (series,) = [s for s in found if s.account == A]
        assert (series.missed, series.explained) == (1, 0)

    def test_CollectionNotTakenSinceTheCardClosedAtNil_HasNotStopped(self):
        rows, pairs = self.collection([3, 4, 5, 6, 7])
        rows.append(tx(A, date(2026, 8, 25), -100, "NEWSAGENT"))

        nil = {B: [(date(2026, 7, 25), 0)]}

        found = find_recurring(rows, pairs, date(2026, 8, 25), closings=nil)

        (series,) = [s for s in found if s.account == A and "collection" in s.label.casefold()]
        assert (series.stopped, series.explained, series.missed) == (False, 1, 0)

    def test_CollectionNotTakenSinceTheCardClosedOwingMoney_HasStopped(self):
        rows, pairs = self.collection([3, 4, 5, 6, 7])
        rows.append(tx(A, date(2026, 8, 25), -100, "NEWSAGENT"))

        found = find_recurring(
            rows, pairs, date(2026, 8, 25), closings={B: [(date(2026, 7, 25), -5000)]}
        )

        (series,) = [s for s in found if s.account == A and "collection" in s.label.casefold()]
        assert (series.stopped, series.explained) == (True, 0)

    def test_ScheduledStandingOrderToTheCard_IsNotExplainedByTheCardsNilClosing(self):
        out = [
            tx(
                A,
                date(2026, m, 10),
                -30000,
                "CARD PAYMENT",
                transfer=True,
                source="truelayer",
                raw=starling("transaction_category", "STANDING_ORDER"),
            )
            for m in (3, 4, 6, 7, 8)
        ]
        into = [tx(B, o.value_date, 30000, "PAYMENT RECEIVED", transfer=True) for o in out]
        pairs = [(o.entity_id, i.entity_id) for o, i in zip(out, into, strict=True)]

        found = find_recurring(
            [*out, *into], pairs, date(2026, 8, 25), closings={B: [(date(2026, 4, 25), 0)]}
        )

        (series,) = [s for s in found if s.account == A]
        assert (series.kind, series.missed, series.explained) == (SCHEDULED, 1, 0)

    def test_OccurrencesStatingConflictingTypes_AreDecidedByTheCommonerOne(self):
        kinds = [
            {"transaction_category": "STANDING_ORDER"},
            {"transaction_category": "STANDING_ORDER"},
            {"transaction_category": "STANDING_ORDER"},
            {"transaction_category": "DIRECT_DEBIT"},
        ]
        rows = [
            tx(A, date(2026, m, 2), -1000, "CLUB FEES", source="truelayer", raw=kinds[i])
            for i, m in enumerate(range(6, 10))
        ]

        (series,) = find_recurring(rows, pairs=(), today=TODAY)

        assert series.kind == SCHEDULED
