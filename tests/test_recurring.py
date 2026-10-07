"""What recurs in the transactions the store holds: found from invented series with known answers.

Every series below is built by hand, and its answer is written beside it BEFORE the detector
existed: how many times it occurred, the cadence, the usual day, the usual amount, when it is
next expected, and the marks it carries. The amounts are invented. "Today" is fixed so that
"stopped" has a known answer.

A calendar fact the answers lean on: 2026-10-07 is a Wednesday; 2026-06-27 is a Saturday and
2026-09-27 a Sunday, so a bill taken "on the 27th" is taken on the Monday after in those months.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.recurring import Series, find_recurring

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
) -> Transaction:
    _counter[0] += 1
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        source="synthetic",
        tier=SourceTier.SYNTHETIC,
        status=status,
        entity_id=f"e{_counter[0]:06d}",
        is_internal_transfer=transfer,
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
        rows = [tx(A, date(y, 10, 14), -3800, "GITHUB SUBSCRIPTION") for y in (2023, 2024, 2025)]
        rows.append(tx(A, TODAY, -100, "NEWSAGENT"))

        series = find_one(find_recurring(rows, pairs=(), today=TODAY), "github")

        assert series.cadence == "yearly"
        assert (series.usual_month, series.usual_day) == (10, 14)
        assert series.count == 3
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

    def test_TheSamePayeeInTwoAccounts_IsOneSeriesPerAccount(self):
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

    @pytest.mark.parametrize("count", [3, 4])
    def test_ThreeOrMoreRegularOccurrences_FormASeries(self, count):
        rows = monthly(A, [(2026, m) for m in range(6, 6 + count)], 3, -500, "NEW GADGET CLUB")

        assert [s.count for s in find_recurring(rows, pairs=(), today=TODAY)] == [count]
