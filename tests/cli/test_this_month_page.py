"""This month, through the served page: the calendar, the funded sentences, the month ahead, and
what the masked page keeps.

The world and the answers decided before the first run are in `this_month_world`: the clock is
pinned to 2026-09-15, one payment is paid on the 3rd, one is due on the 20th, one has been overdue
since the 5th, and a salary is expected on the 25th. The page is read the way a person does: the
masked GET, and the POST that shows values.
"""

from __future__ import annotations

import re

import httpx
import pytest

from landing import import_file
from obdi.core.masking import MASKED_TOTAL
from obdi.ingest.commitment_records import WindowTerms
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import remove_stated_anchor
from this_month_world import (
    BILLS,
    CURRENT,
    GAS,
    GYM,
    INSURANCE,
    SALARY,
    TOP_UP,
    WATER,
    commit,
    on_day,
    served,
    terms,
    write_export,
)


def text_of(html: str) -> str:
    body = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    spaced = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return re.sub(r" ([.,;)])", r"\1", spaced).replace("( ", "(")


def shown(base: str, month: str = "") -> str:
    data = {"month": month} if month else None
    return text_of(httpx.post(f"{base}/this-month", data=data, timeout=60).text)


def masked(base: str, query: str = "") -> str:
    return text_of(httpx.get(f"{base}/this-month{query}", timeout=60).text)


def day_block(page: str, day: str) -> str:
    """The calendar entries of one day: from its heading to the next day's, or the section's end."""
    start = re.search(rf"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) {day}\b", page)
    assert start, f"{day} is not on the page: {page}"
    rest = page[start.end() :]
    following = r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) 20\d\d-\d\d-\d\d\b|Funded before|Look one"
    end = re.search(following, rest)
    return rest[: end.start()] if end else rest


@pytest.fixture
def world(tmp_path, monkeypatch):
    with served(tmp_path, monkeypatch) as (base, db):
        yield base, db


@pytest.fixture
def with_a_short_account(tmp_path, monkeypatch):
    with served(tmp_path, monkeypatch, short_account=True) as (base, db):
        yield base, db


class TestTheHeadline:
    def test_Headline_WhenOnePaidOneDueAndOneOverdue_CountsOneOfEachAndSaysAllFunded(self, world):
        base, _ = world

        assert (
            "3 commitments this month: 1 paid, 1 due, 1 overdue; all accounts funded"
            in shown(base)
        )

    def test_Headline_WhenNothingIsOverdue_CountsNoneOverdue(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, insurance_paid=True) as (base, _):
            page = shown(base)

        assert "3 commitments this month: 2 paid, 1 due, 0 overdue; all accounts funded" in page

    def test_Headline_WhenAnAccountCannotCoverWhatLeaves_NamesItAndTheShortfallAndTheDay(
        self, with_a_short_account
    ):
        base, _ = with_a_short_account

        page = shown(base)

        assert (
            "4 commitments this month: 1 paid, 2 due, 1 overdue; "
            "Bills short by £30.00 before 2026-09-25"
        ) in page
        assert "all accounts funded" not in page

    def test_Headline_WhenNoCommitmentIsConfirmed_SaysNoneFallDueAndClaimsNothingFunded(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, commitments=False) as (base, _):
            page = shown(base)

        assert "No commitment falls due this month." in page
        assert "all accounts funded" not in page
        assert "Funded before the next income" not in page


class TestTheCalendar:
    def test_Calendar_WhenAPaymentWasMade_SaysPaidAndTheDayItWasSeen(self, world):
        base, _ = world

        block = day_block(shown(base), "2026-09-03")

        assert f"paid {GAS} from Everyday, £120.00" in block
        assert "Paid 2026-09-03." in block

    def test_Calendar_WhenADayHasPassedWithNoPayment_SaysOverdueOnThatDay(self, world):
        base, _ = world

        block = day_block(shown(base), "2026-09-05")

        assert f"overdue {INSURANCE} from Everyday, £30.00" in block
        assert "Last paid 2026-08-05." in block

    def test_Calendar_WhenTheDayIsStillToCome_SaysDueOnThatDay(self, world):
        base, _ = world

        block = day_block(shown(base), "2026-09-20")

        assert f"due {GYM} from Everyday, £45.00" in block

    def test_Calendar_WhenAnIncomeIsExpected_SaysExpectedAndWhereItGoes(self, world):
        base, _ = world

        block = day_block(shown(base), "2026-09-25")

        assert f"expected {SALARY} into Everyday, £3,000.00" in block

    def test_Calendar_GroupsByDaySoonestFirst(self, with_a_short_account):
        base, _ = with_a_short_account

        days = re.findall(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (2026-\d\d-\d\d)", shown(base))

        assert days == ["2026-09-03", "2026-09-05", "2026-09-18", "2026-09-20", "2026-09-25"]

    def test_Calendar_ShowsTheAccountEachCommitmentLeaves(self, with_a_short_account):
        base, _ = with_a_short_account

        assert f"{WATER} from Bills, £80.00" in day_block(shown(base), "2026-09-18")

    def test_Calendar_WhenTheAccountsTransactionsStopBeforeTheDayWasDue_DoesNotCallItOverdue(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, reaches_the_12th=False) as (base, _):
            page = shown(base)

        assert "1 paid, 2 due, 0 overdue" in page
        assert "run to 2026-09-03, before this was due, so it may be paid and not yet seen" in (
            day_block(page, "2026-09-05")
        )

    def test_Calendar_WhenACommitmentHasNoPaymentsAtAll_SaysNoneIsFoundAndCountsItOverdue(
        self, world
    ):
        base, db = world
        with Store(db) as store:
            commit(store, "Ghost Standing Order", CURRENT, "out", 1500, 8)

        page = shown(base)

        assert "4 commitments this month: 1 paid, 1 due, 2 overdue" in page
        assert "No payment of it is found in the transactions held." in day_block(
            page, "2026-09-08"
        )

    def test_Calendar_WhenACommitmentEndedThisMonth_ShowsTheDayItEndedAndDoesNotCountIt(
        self, world
    ):
        base, db = world
        with Store(db) as store:
            commit(store, "Old Broadband", CURRENT, "out", 2500, 10, ended=on_day(9, 8))

        page = shown(base)

        assert "ended Old Broadband" in day_block(page, "2026-09-08")
        assert "3 commitments this month" in page

    def test_Calendar_WhenACommitmentEndedBeforeThisMonth_IsNotOnIt(self, world):
        base, db = world
        with Store(db) as store:
            commit(store, "Long Gone Magazine", CURRENT, "out", 700, 12, ended=on_day(7, 30))

        assert "Long Gone Magazine" not in shown(base)

    def test_Calendar_WhenAPriceChangedMidMonth_ShowsEachDayAtItsOwnPrice(self, world):
        base, db = world
        with Store(db) as store:
            old = next(c for c in store.commitments() if c.name == GAS)
            store.change_commitment_window(old.id, on_day(9, 10), terms(12500, 3))

        this_month = shown(base)
        next_month = shown(base, month="next")

        assert f"paid {GAS} from Everyday, £120.00" in day_block(this_month, "2026-09-03")
        assert f"due {GAS} from Everyday, £125.00" in day_block(next_month, "2026-10-03")


class TestAWeeklyCommitment:
    def test_Calendar_WhenWeekly_ShowsEveryFridayWithThePastOnesPaid(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            fridays = (
                [on_day(7, d) for d in (3, 10, 17, 24, 31)]
                + [on_day(8, d) for d in (7, 14, 21, 28)]
                + [on_day(9, 4), on_day(9, 11)]
            )
            csv = tmp_path / "weekly.csv"
            write_export(csv, [(d, "Pennyfarthing Window Cleaner", "-10.00") for d in fridays])
            with Store(db) as store:
                import_file(store, csv, account_id=CURRENT)
                store.declare_commitment(
                    "Pennyfarthing Window Cleaner",
                    kind="scheduled",
                    account=CURRENT,
                    direction="out",
                    entity_id=None,
                    name_key="pennyfarthing window cleaner",
                    from_day=on_day(1, 2),
                    to_day=None,
                    terms=WindowTerms(1000, "GBP", "weekly", 4, 0, 1, "invented for a test"),
                )

            page = shown(base)

        assert "paid Pennyfarthing Window Cleaner" in day_block(page, "2026-09-04")
        assert "paid Pennyfarthing Window Cleaner" in day_block(page, "2026-09-11")
        assert "due Pennyfarthing Window Cleaner" in day_block(page, "2026-09-18")
        assert "due Pennyfarthing Window Cleaner" in day_block(page, "2026-09-25")


class TestFundedOrNot:
    def test_Funded_WhenHeldCoversWhatLeavesBeforeTheIncome_SaysFundedUntilTheIncomeDay(
        self, world
    ):
        base, _ = world

        page = shown(base)

        assert "Everyday Holds £1,000.00; £45.00 leaves before 2026-09-25." in page
        assert "Funded until the next income on 2026-09-25." in page

    def test_Funded_WhenHeldFallsShort_SaysShortByHowMuchBeforeTheDay(self, with_a_short_account):
        base, _ = with_a_short_account

        page = shown(base)

        assert (
            "Bills Holds £50.00; £80.00 leaves before 2026-09-25. "
            "Short by £30.00 before 2026-09-25."
        ) in page

    def test_Funded_FiguresAreThoseOfPosition(self, world):
        base, _ = world

        position = text_of(httpx.post(f"{base}/position", timeout=60).text)
        month = shown(base)

        assert "Committed before the next income £45.00" in position
        assert "Free £955.00. Held less committed." in position
        assert "£1,000.00; £45.00 leaves" in month

    def test_Funded_WhenACommitmentIsOverdue_SaysItIsNotCountedAbove(self, world):
        base, _ = world

        assert (
            "1 overdue commitment on this account is not counted above: "
            "Position counts what is due from today."
        ) in shown(base)

    def test_Funded_WhenTheBalanceIsNotKnown_SaysItCannotBeJudgedAndDoesNotClaimFunding(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, short_account=True) as (base, db):
            with Store(db) as store:
                remove_stated_anchor(store, BILLS, "2026-09-13")

            page = shown(base)

        assert "1 account cannot be judged" in page
        assert "Cannot be judged: Cannot be worked out: the balance is not known." in page
        assert "all accounts funded" not in page

    def test_Funded_WhenNoIncomeIsConfirmed_UsesTheRhythmAndSaysSo(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, short_account=True) as (base, db):
            with Store(db) as store:
                for commitment in store.commitments():
                    if commitment.name == TOP_UP:
                        store.remove_commitment(commitment.id)

            page = shown(base)

        assert "No income is confirmed; the next expected by rhythm is 2026-09-25." in page
        assert "Short by £30.00 before 2026-09-25." in page


class TestTheMonthAhead:
    def test_MonthAhead_ShowsNextMonthsDueDaysAndNoPaidState(self, world):
        base, _ = world

        page = shown(base, month="next")

        assert "3 commitments next month: 0 paid, 3 due, 0 overdue" in page
        calendar = page[page.index("Due next month") : page.index("Back to this month")]
        for day in ("2026-10-03", "2026-10-05", "2026-10-20", "2026-10-25"):
            assert day in calendar
        assert "paid " not in calendar
        assert "overdue " not in calendar

    def test_MonthAhead_IsOnAMaskedGetByAQueryThatHoldsNoValue(self, world):
        base, _ = world

        page = masked(base, "?month=next")

        assert "3 commitments next month: 0 paid, 3 due, 0 overdue" in page
        assert "Back to this month" in page

    def test_MonthAhead_DoesNotRepeatThisMonthsFunding(self, world):
        base, _ = world

        assert "Funded before the next income" not in masked(base, "?month=next")

    def test_MonthAhead_WhenValuesAreShown_KeepsThemShownAndCarriesTheMonth(self, world):
        base, _ = world

        page = shown(base, month="next")

        assert "VALUES ARE SHOWN" in page
        assert f"due {GAS} from Everyday, £120.00" in page

    def test_ThisMonthPage_OffersTheMonthAheadAsAPress(self, world):
        base, _ = world

        html = httpx.get(f"{base}/this-month", timeout=60).text

        assert 'href="/this-month?month=next"' in html

    def test_MonthAhead_WhenAskedFurtherThanOneMonth_ShowsThisMonthInstead(self, world):
        base, _ = world

        page = masked(base, "?month=2026-12")

        assert "commitments this month" in page
        assert "September 2026" in page


class TestWhatTheMaskedPageKeeps:
    def test_MaskedPage_ShowsNoAmountAndNoPayeeButKeepsDaysStatesAndCounts(self, world):
        base, _ = world

        page = masked(base)

        assert "3 commitments this month: 1 paid, 1 due, 1 overdue; all accounts funded" in page
        for amount in ("120.00", "45.00", "30.00", "3,000.00", "1,000.00", "955.00"):
            assert amount not in page
        for payee in (GAS, GYM, INSURANCE, SALARY):
            assert payee not in page
        assert MASKED_TOTAL in page
        assert "2026-09-03" in page
        assert "2026-09-05" in page

    def test_MaskedPage_WhenAnAccountIsShort_SaysShortBeforeTheDayWithoutTheAmount(
        self, with_a_short_account
    ):
        base, _ = with_a_short_account

        page = masked(base)

        assert "Bills short before 2026-09-25" in page
        assert "short by" not in page.lower()
        assert "30.00" not in page

    def test_MaskedPage_WhateverTheQueryString_ShowsNoValue(self, world):
        base, _ = world

        page = masked(base, "?values=1&shown=1&month=next")

        assert "120.00" not in page
        assert MASKED_TOTAL in page

    def test_MaskedPage_NamesTheAccountsAndStatesTheyAreInBecauseTheseAreStructure(self, world):
        base, _ = world

        page = masked(base)

        assert "Everyday" in page
        assert "Funded until the next income on 2026-09-25." in page

    def test_ShownPage_IsNotStoredByTheBrowser(self, world):
        base, _ = world

        response = httpx.post(f"{base}/this-month", timeout=60)

        assert "no-store" in response.headers["cache-control"]
        assert "£120.00" in response.text
        assert BILLS not in response.text
