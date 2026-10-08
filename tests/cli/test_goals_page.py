"""Goals, through the served page: progress against the straight line, the masked GET, and the
presses that add, change, and remove a goal.

The world and the answers decided before the first run are in `goals_world`: the clock is pinned
to 2026-09-15, a card owing 300.00 was owing 500.00 when its goal was declared two months before
(200.00 cleared, 33.34 ahead of the line), a fund holds 400.00 of 1,000.00 with no date, and a
holiday of 1,200.00 declared four months before for twelve is on its line with 400.00 held and
100.00 behind it with 300.00. The page is read the way a person does: the masked GET, the POST
that shows values, and the presses.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import date

import httpx

from goals_world import CARD, SAVINGS, TRIP, served
from landing import rebuild_from_raw
from obdi.core.masking import MASKED_TOTAL
from obdi.ingest.goal_records import CLEAR, SAVE
from obdi.ingest.store import Store


def text_of(html: str) -> str:
    body = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    spaced = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return re.sub(r" ([.,;)])", r"\1", htmllib.unescape(spaced)).replace("( ", "(")


def goal_of(html: str, name: str) -> str:
    """One goal's lines as text: from its name to the end of its list entry."""
    found = re.search(rf"<li><strong>{re.escape(name)}</strong>.*?</details></li>", html, re.S)
    assert found, f"{name} is not on the page"
    return text_of(found.group(0))


def shown(base: str) -> str:
    return httpx.post(f"{base}/goals", timeout=60).text


def masked(base: str) -> str:
    return httpx.get(f"{base}/goals", timeout=60).text


def press(base: str, route: str, **fields: str) -> httpx.Response:
    """A press as the page makes it from a shown page."""
    return httpx.post(f"{base}{route}", data={"shown": "1", **fields}, timeout=60)


class TestADebtToClear:
    def test_Progress_WhenFiveHundredOwedIsNowThreeHundred_SaysTwoHundredClearedAndThreeToGo(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _):
            card = goal_of(shown(base), "Clear the Visa")

        assert "£200.00 of £500.00 cleared, £300.00 to go." in card

    def test_Rate_WhenFourMonthsRemain_IsSeventyFivePoundsAMonth(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            card = goal_of(shown(base), "Clear the Visa")

        assert "Wanted by 2027-01-15 (4 months left): £75.00 a month gets there." in card

    def test_Stance_WhenTwoHundredIsClearedAgainstALineOfOneHundredAndSixtySix_IsAheadByThirtyFour(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _):
            card = goal_of(shown(base), "Clear the Visa")

        assert "ahead by £33.34 on the straight line from 2026-07-15." in card

    def test_Stance_WhenLessIsClearedThanTheLine_IsBehind(self, tmp_path, monkeypatch):
        # 450.00 owed: 50.00 cleared against a line of 166.66 is behind by 116.66.
        with served(tmp_path, monkeypatch, owed="-450.00") as (base, _):
            card = goal_of(shown(base), "Clear the Visa")

        assert "£50.00 of £500.00 cleared, £450.00 to go." in card
        assert "behind by £116.66 on the straight line" in card

    def test_Progress_WhenMoreIsOwedThanWhenDeclared_SaysSoInsteadOfAnArithmeticOddity(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, owed="-620.00") as (base, _):
            card = goal_of(shown(base), "Clear the Visa")

        assert "£120.00 more is owed than when it was declared (£500.00); £620.00 to go." in card
        assert "behind by £286.66" in card

    def test_Progress_WhenNothingIsOwedNow_SaysClearedAndNoStance(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, owed="0.00") as (base, _):
            card = goal_of(shown(base), "Clear the Visa")

        assert "Cleared: nothing is owed on it now." in card
        assert "ahead" not in card
        assert "behind" not in card

    def test_Progress_WhenTheBalanceOwedIsNotKnown_SaysSoAndMakesNoFigure(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, card_balance_known=False) as (base, _):
            card = goal_of(shown(base), "Clear the Visa")

        assert "The balance owed is not known, so progress cannot be measured." in card
        assert "£200.00" not in card

    def test_Progress_WhenTheDateHasPassed_IsBehindAgainstTheWholeAmount(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                store.declare_goal(
                    "Old debt",
                    kind=CLEAR,
                    account=CARD,
                    target_minor=0,
                    target_date=date(2026, 8, 1),
                    declared_on=date(2026, 2, 1),
                    start_minor=500_00,
                )
            card = goal_of(shown(base), "Old debt")

        assert "(the date has passed)" in card
        assert "behind by £300.00" in card


class TestAFundToBuild:
    def test_Progress_WhenFourHundredIsHeldOfAThousand_SaysSixHundredToGoAndHasNoLine(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _):
            fund = goal_of(shown(base), "Rainy day")

        assert "£400.00 of £1,000.00 held, £600.00 to go." in fund
        assert "No date set, so there is no line to be ahead of or behind." in fund
        assert "ahead" not in fund.replace("ahead of or behind", "")
        assert "a month" not in fund

    def test_Progress_WhenTheTargetIsHeld_SaysReached(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, fund_held="1500.00") as (base, _):
            fund = goal_of(shown(base), "Rainy day")

        assert "Reached: the whole of £1,000.00 is held." in fund


class TestASavingForAThing:
    def test_Stance_WhenHeldMatchesTheLine_IsAheadByNilAndTheShareIsOneHundred(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _):
            holiday = goal_of(shown(base), "Holiday")

        assert "£400.00 of £1,200.00 set aside, £800.00 to go." in holiday
        assert "ahead by £0.00 on the straight line from 2026-05-15." in holiday
        assert "This month's share £100.00." in holiday

    def test_Stance_WhenThreeHundredIsHeldAfterFourMonths_IsBehindByOneHundred(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, trip_held="300.00") as (base, _):
            holiday = goal_of(shown(base), "Holiday")

        assert "behind by £100.00 on the straight line" in holiday
        assert "This month's share £100.00." in holiday

    def test_Stance_WhenMoreThanTheLineIsHeld_IsAheadByTheExcess(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, trip_held="550.00") as (base, _):
            holiday = goal_of(shown(base), "Holiday")

        assert "ahead by £150.00" in holiday

    def test_Progress_WhenTheAccountIsOverdrawn_HoldsNothingTowardsIt(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, trip_held="-20.00") as (base, _):
            holiday = goal_of(shown(base), "Holiday")

        assert "£0.00 of £1,200.00 set aside, £1,200.00 to go." in holiday
        assert "behind by £400.00" in holiday


class TestTwoGoalsOnOneAccount:
    def test_Progress_WhenTheEarlierGoalTakesItsLine_TheLaterSeesOnlyWhatIsLeft(
        self, tmp_path, monkeypatch
    ):
        # The account holds 500.00. "Holiday" (declared first) is owed 400.00 by now, so it sees
        # 500.00 and is ahead by 100.00; "Boiler" (1,200.00 over the same twelve months, so also
        # 400.00 by now) sees the 100.00 left and is behind by 300.00.
        with served(tmp_path, monkeypatch, trip_held="500.00") as (base, db):
            with Store(db) as store:
                store.declare_goal(
                    "Boiler",
                    kind=SAVE,
                    account=TRIP,
                    target_minor=120000,
                    target_date=date(2027, 5, 15),
                    declared_on=date(2026, 5, 15),
                    start_minor=0,
                )
            page = shown(base)

        assert "ahead by £100.00" in goal_of(page, "Holiday")
        later = goal_of(page, "Boiler")
        assert "£100.00 of £1,200.00 set aside" in later
        assert "behind by £300.00" in later


class TestTheHeadlineAndShare:
    def test_Headline_CountsAheadBehindAndGoalsWithNoLine(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            page = text_of(shown(base))

        assert "3 goals: 2 ahead, 0 behind, 1 with no line." in page

    def test_Share_SumsTheDatedGoalsStepsAndSaysItIsNotCommitted(self, tmp_path, monkeypatch):
        # 83.34 for the Visa and 100.00 for the holiday; the fund has no date and no share.
        with served(tmp_path, monkeypatch) as (base, _):
            page = text_of(shown(base))

        assert "This month's share of the dated goals: £183.34, from 2 goals." in page
        assert "it is not counted as committed" in page

    def test_Page_WhenNoGoalIsDeclared_SaysSoAndOffersToAddOne(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, goals=False) as (base, _):
            page = text_of(shown(base))

        assert "No goal is declared yet." in page
        assert "Add a goal" in page


class TestMasked:
    def test_Get_ShowsNamesKindsAccountsDatesAndTheStanceButNoAmount(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, trip_held="300.00") as (base, _):
            page = text_of(masked(base))

        for shown_word in (
            "Clear the Visa",
            "debt to clear",
            "Rainy day",
            "fund to build",
            "Holiday",
            "saving",
            "Wanted by 2027-01-15",
            "Wanted by 2027-05-15",
            "ahead",
            "behind",
        ):
            assert shown_word in page, shown_word
        assert MASKED_TOTAL in page
        amounts = ("500.00", "300.00", "200.00", "75.00", "33.34", "1,000.00", "1,200.00")
        for amount in (*amounts, "100.00", "400.00"):
            assert amount not in page, amount

    def test_Get_PutsNoAmountInAnyFieldOfTheChangeForm(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            html = masked(base)

        assert 'name="amount" value=""' in html
        assert not re.search(r'name="amount" value="[^"]', html)


class TestPresses:
    def test_Add_WhenAFundIsPressedOnTheMaskedPage_IsKeptAndAnsweredMasked(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch, goals=False) as (base, db):
            reply = httpx.post(
                f"{base}/goals-add",
                data={
                    "kind": "build",
                    "account": SAVINGS,
                    "name": "Boiler fund",
                    "amount": "2,500.00",
                    "date": "2027-03-15",
                },
                timeout=60,
            )
            with Store(db) as store:
                (kept,) = store.goals()

        assert reply.status_code == 200
        page = text_of(reply.text)
        assert "Goal added." in page
        assert "Boiler fund" in page
        assert MASKED_TOTAL in page
        assert "2,500.00" not in page
        assert (kept.kind, kept.target_minor, kept.start_minor) == ("build", 250000, 40000)
        assert kept.declared_on.isoformat() == "2026-09-15"

    def test_Add_WhenADebtIsPressed_StartsFromWhatIsOwedNow(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, goals=False) as (base, db):
            press(base, "/goals-add", kind="clear", account=CARD, name="Visa to nil", amount="")
            with Store(db) as store:
                (kept,) = store.goals()
            page = goal_of(shown(base), "Visa to nil")

        assert (kept.start_minor, kept.target_minor) == (30000, 0)
        assert "£0.00 of £300.00 cleared, £300.00 to go." in page

    def test_Add_WhenADebtsBalanceIsNotKnown_IsRefusedAndKeepsNothing(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, goals=False, card_balance_known=False) as (base, db):
            reply = press(base, "/goals-add", kind="clear", account=CARD, name="Visa to nil")
            with Store(db) as store:
                kept = store.goals()

        assert reply.status_code == 400
        assert "The balance owed on that account is not known" in text_of(reply.text)
        assert kept == []

    def test_Add_WhenMalformed_IsRefusedWithTheReasonAndKeepsNothing(self, tmp_path, monkeypatch):
        cases = {
            "a saving with no date": ({"kind": "save", "amount": "100.00"}, "needs the date"),
            "an amount that is not one": ({"kind": "build", "amount": "lots"}, "pounds and pence"),
            "a date that is not one": (
                {"kind": "build", "amount": "10.00", "date": "next spring"},
                "year-month-day",
            ),
            "a date in the past": (
                {"kind": "build", "amount": "10.00", "date": "2026-01-01"},
                "after the day it is declared",
            ),
            "no name": ({"kind": "build", "amount": "10.00", "name": ""}, "needs a name"),
            "no such account": (
                {"kind": "build", "amount": "10.00", "account": "nowhere"},
                "no such account",
            ),
        }
        with served(tmp_path, monkeypatch, goals=False) as (base, db):
            for label, (given, said) in cases.items():
                fields = {"account": SAVINGS, "name": "Something", **given}
                reply = press(base, "/goals-add", **fields)

                assert reply.status_code == 400, label
                assert said in text_of(reply.text), label
            with Store(db) as store:
                assert store.goals() == []

    def test_Edit_WhenTheTargetIsRaised_ChangesTheSumAndKeepsTheStart(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                holiday = next(g for g in store.goals() if g.name == "Holiday")
            reply = press(base, "/goals-edit", goal=str(holiday.id), amount="2,400.00")
            page = goal_of(reply.text, "Holiday")

        # 2,400 over twelve months is 200.00 a month: the line is 800.00 after four.
        assert "Goal changed." in text_of(reply.text)
        assert "£400.00 of £2,400.00 set aside, £2,000.00 to go." in page
        assert "behind by £400.00" in page
        assert "This month's share £200.00." in page

    def test_Edit_WhenTheDateIsRemovedFromAFund_LeavesNoLine(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                visa = next(g for g in store.goals() if g.name == "Clear the Visa")
            press(base, "/goals-edit", goal=str(visa.id), no_date="1")
            card = goal_of(shown(base), "Clear the Visa")

        assert "No date set" in card
        assert "a month gets there" not in card

    def test_Edit_WhenTheDateIsNotAfterTheDayItWasDeclared_IsRefusedAndChangesNothing(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                visa = next(g for g in store.goals() if g.name == "Clear the Visa")
            reply = press(base, "/goals-edit", goal=str(visa.id), name="Renamed", date="2026-07-01")
            with Store(db) as store:
                after = next(g for g in store.goals() if g.id == visa.id)

        assert reply.status_code == 400
        assert (after.name, after.target_date) == ("Clear the Visa", visa.target_date)

    def test_Remove_WhenPressed_ListsNoMoreAndTheNextPressIsRefused(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                holiday = next(g for g in store.goals() if g.name == "Holiday")
            reply = press(base, "/goals-remove", goal=str(holiday.id))
            again = press(base, "/goals-remove", goal=str(holiday.id))

        assert "Goal removed." in text_of(reply.text)
        assert "<li><strong>Holiday</strong>" not in reply.text
        assert again.status_code == 400
        assert "no such goal" in text_of(again.text)

    def test_Remove_WhenGivenNoNumber_IsRefusedAndRemovesNothing(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            reply = press(base, "/goals-remove", goal="x")
            with Store(db) as store:
                kept = store.goals()

        assert reply.status_code == 400
        assert len(kept) == 3

    def test_Press_WhenNotShown_IsAnsweredMaskedAndWhenShownIsAnsweredWithValues(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                fund = next(g for g in store.goals() if g.name == "Rainy day")
            hidden = httpx.post(
                f"{base}/goals-edit", data={"goal": str(fund.id), "name": "Rainy"}, timeout=60
            )
            visible = press(base, "/goals-edit", goal=str(fund.id), name="Rainy day")

        assert MASKED_TOTAL in hidden.text and "£400.00" not in hidden.text
        assert "£400.00 of £1,000.00 held" in text_of(visible.text)
        assert "no-store" in visible.headers["cache-control"]


class TestAcrossTheRebuildAndRemoval:
    def test_Goals_WhenTheStoreIsRebuilt_AreStillThereWithTheSameProgress(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            before = goal_of(shown(base), "Holiday")
            with Store(db) as store:
                rebuild_from_raw(store)
            after = goal_of(shown(base), "Holiday")

        assert before == after
        assert "ahead by £0.00" in after

