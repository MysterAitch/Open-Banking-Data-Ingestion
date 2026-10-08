"""Goals on This month and Position, through the served pages.

The world and the answers decided before the first run are in `goals_world`. This month says what
each dated goal's line asks of the month ("83.34 towards Clear the Visa", "100.00 towards
Holiday"), whether it is ahead or behind, and nothing for the fund (no date, so no line). Position
adds one line, "and 183.34 towards goals", beside the committed total and not inside it, because a
goal is the owner's choice and a commitment is an obligation.
"""

from __future__ import annotations

import html as htmllib
import re

import httpx

from goals_world import served
from obdi.core.masking import MASKED_TOTAL
from obdi.ingest.store import Store


def text_of(html: str) -> str:
    body = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    spaced = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return re.sub(r" ([.,;)])", r"\1", htmllib.unescape(spaced)).replace("( ", "(")


def month_shown(base: str, month: str = "") -> str:
    data = {"month": month} if month else None
    return text_of(httpx.post(f"{base}/this-month", data=data, timeout=60).text)


def position_shown(base: str) -> str:
    return text_of(httpx.post(f"{base}/position", timeout=60).text)


class TestThisMonth:
    def test_Goals_WhenTwoAreDated_SaysWhatEachLineAsksOfTheMonthAndWhereItStands(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _):
            page = month_shown(base)

        assert "Towards goals" in page
        assert "£83.34 towards Clear the Visa ahead" in page
        assert "£100.00 towards Holiday ahead" in page

    def test_Goals_WhenBehindItsLine_SaysBehindAndKeepsTheSameShare(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, trip_held="300.00") as (base, _):
            page = month_shown(base)

        assert "£100.00 towards Holiday behind" in page

    def test_Goals_WhenAGoalHasNoDate_HasNoLineHere(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            page = month_shown(base)

        assert "towards Rainy day" not in page

    def test_Goals_SaysTheyAreNotCommittedAndLinksToThePage(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            raw = httpx.post(f"{base}/this-month", timeout=60).text

        assert "not an obligation" in text_of(raw)
        assert 'href="/goals"' in raw

    def test_Goals_WhenARemovedGoalWasTheOnlyDatedOnes_LeavesNothingOnThePage(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                for goal in store.goals():
                    if goal.target_date is not None:
                        store.remove_goal(goal.id)
            page = month_shown(base)

        assert "Towards goals" not in page
        assert "towards" not in page.replace("Towards", "")

    def test_Goals_WhenOneIsRemoved_NoLongerAppearsAtOnce(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            assert "towards Holiday" in month_shown(base)
            with Store(db) as store:
                holiday = next(g for g in store.goals() if g.name == "Holiday")
                store.remove_goal(holiday.id)
            page = month_shown(base)

        assert "towards Holiday" not in page
        assert "towards Clear the Visa" in page

    def test_Goals_WhenNoneIsDeclared_AddsNoSection(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch, goals=False) as (base, _):
            page = month_shown(base)

        assert "Towards goals" not in page

    def test_Goals_ForTheMonthAhead_AreNotShown(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            page = month_shown(base, "next")

        assert "Towards goals" not in page

    def test_Goals_WhenMasked_ShowTheNamesAndStanceButNoAmount(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            page = text_of(httpx.get(f"{base}/this-month", timeout=60).text)

        assert "towards Holiday ahead" in page
        assert f"{MASKED_TOTAL} towards Clear the Visa" in page
        for amount in ("83.34", "100.00"):
            assert amount not in page, amount


class TestPosition:
    def test_Position_WhenGoalsAreDated_SaysTheirShareApartFromCommitted(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _):
            page = position_shown(base)

        assert "and £183.34 towards goals this month, from 2 dated goals." in page
        assert "not in what is committed or free" in page

    def test_Position_WhenGoalsAreDated_LeavesCommittedAndFreeAsTheyWereWithNone(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, _):
            with_goals = position_shown(base)
        with served(tmp_path / "bare", monkeypatch, goals=False) as (base, _):
            without = position_shown(base)

        def totals(page: str) -> str:
            start = page.index("Committed before the next income")
            return page[start : page.index("counting", page.index("Free", start))]

        assert totals(with_goals) == totals(without)

    def test_Position_WhenNoGoalIsDated_AddsNoLine(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            with Store(db) as store:
                for goal in store.goals():
                    if goal.target_date is not None:
                        store.remove_goal(goal.id)
            page = position_shown(base)

        assert "towards goals" not in page

    def test_Position_WhenMasked_SaysTheLineAndNoAmount(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, _):
            page = text_of(httpx.get(f"{base}/position", timeout=60).text)

        assert f"and {MASKED_TOTAL} towards goals this month, from 2 dated goals." in page
        assert "183.34" not in page
