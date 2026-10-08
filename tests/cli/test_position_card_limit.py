"""A card's declared limit reaches Position, through the served page.

KNOWN ANSWERS, decided before the first run, on top of the Position world (`free_position_world`):
a second card "Amex" with 500.00 owed, stated two days ago. With a limit of 2,000.00 declared its
free figure is 1,500.00; with a limit of 400.00 it is short by 100.00; with no limit in force today
(none declared, one that ended yesterday, one that starts tomorrow) it says "No limit declared."
and makes no free figure. Where two windows cover today the one that began last is in force.
Every account and figure is invented.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import httpx
import pytest

from free_position_world import served, today
from obdi.core.masking import MASKED_TOTAL
from obdi.ingest.accounts import AccountRecord, AccountRef, LimitWindow
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor

AMEX = "card-amex"
HEADING = "<h2>Held, owed, committed, and free</h2>"


def text_of(html: str) -> str:
    spaced = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return re.sub(r" ([.,)])", r"\1", spaced).replace("( ", "(")


def amex(page: str) -> str:
    """The Amex card's figures as text."""
    start = page.index(HEADING)
    body = page[start : page.index("<h2>", start + len(HEADING))]
    for chunk in body.split("</li></ul></li>"):
        if "<li><strong>Amex</strong>" in chunk:
            return text_of(chunk)
    raise AssertionError("the Amex card is not on the page")


def declare_amex(db, *windows: LimitWindow, owed: str = "-500.00") -> None:
    with Store(db) as store:
        store.declare_account(
            AccountRecord(
                ref=AccountRef(AMEX), kind="credit card", label="Amex", limits=tuple(windows)
            )
        )
        record_stated_anchor(store, AMEX, (today() - timedelta(days=2)).isoformat(), owed)


def shown(base: str) -> str:
    return httpx.post(f"{base}/position", timeout=60).text


def window(first: date | None, last: date | None, minor: int) -> LimitWindow:
    return LimitWindow("credit", first, last, minor)


LONG_AGO = date(2020, 1, 1)


class TestACardWithADeclaredLimit:
    def test_Free_WhenLimitIsTwoThousandAndFiveHundredIsOwed_IsFifteenHundredWithItsBasis(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, window(LONG_AGO, None, 200000))

            card = amex(shown(base))

        assert "Owed £500.00" in card
        assert "Limit £2,000.00" in card
        assert (
            "Free £1,500.00. The limit you declared in force from 2020-01-01, "
            "less what is owed."
        ) in card
        assert "No limit declared" not in card

    def test_Free_WhenMoreIsOwedThanTheLimit_IsShortByTheExcess(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, window(LONG_AGO, None, 40000))

            card = amex(shown(base))

        assert "Free short by £100.00." in card

    def test_Free_WhenTheWindowHasNoFirstDay_StillCountsAndSaysNothingOfADate(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, window(None, None, 200000))

            card = amex(shown(base))

        assert "Free £1,500.00. The limit you declared, less what is owed." in card

    def test_Free_WhenTwoWindowsCoverToday_TheOneThatBeganLastIsInForce(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(
                db,
                window(LONG_AGO, None, 100000),
                window(today() - timedelta(days=30), None, 300000),
            )

            card = amex(shown(base))

        assert "Limit £3,000.00" in card
        assert "Free £2,500.00" in card

    def test_Totals_CountTheCardsFreeFigure(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, window(LONG_AGO, None, 200000))

            page = shown(base)

        start = page.index(HEADING)
        totals = text_of(page[start : page.index("<details>", start)])
        # The current account's 880.00, the Amex's 1,500.00, and no figure for the others.
        assert "Free £2,380.00, counting 2 of 4 accounts." in totals


class TestACardWithNoLimitInForce:
    @pytest.mark.parametrize(
        "given",
        [
            pytest.param(None, id="none-declared"),
            pytest.param("ended-yesterday", id="ended-yesterday"),
            pytest.param("starts-tomorrow", id="starts-tomorrow"),
        ],
    )
    def test_Free_WhenNoWindowCoversToday_SaysNoLimitAndMakesNoFigure(
        self, tmp_path, monkeypatch, given
    ):
        windows = {
            None: (),
            "ended-yesterday": (window(LONG_AGO, today() - timedelta(days=1), 200000),),
            "starts-tomorrow": (window(today() + timedelta(days=1), None, 200000),),
        }[given]
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, *windows)

            card = amex(shown(base))

        assert "No limit declared." in card
        assert "£1,500.00" not in card
        assert "£2,000.00" not in card


class TestWhenTheLimitIsEditedOrRemoved:
    def test_Free_WhenTheLimitIsRemovedFromTheAccount_GoesBackToNoLimitDeclared(
        self, tmp_path, monkeypatch
    ):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, window(LONG_AGO, None, 200000))
            assert "Free £1,500.00" in amex(shown(base))

            declare_amex(db)

            assert "No limit declared." in amex(shown(base))

    def test_Free_WhenTheLimitIsRaised_ReadsTheNewFigureAtOnce(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, window(LONG_AGO, None, 200000))
            assert "Free £1,500.00" in amex(shown(base))

            declare_amex(db, window(LONG_AGO, None, 250000))

            assert "Free £2,000.00" in amex(shown(base))


class TestMasked:
    def test_Get_ShowsTheBasisAndNoLimitOrFreeAmount(self, tmp_path, monkeypatch):
        with served(tmp_path, monkeypatch) as (base, db):
            declare_amex(db, window(LONG_AGO, None, 200000))

            card = amex(httpx.get(f"{base}/position", timeout=60).text)

        assert "The limit you declared in force from 2020-01-01, less what is owed." in card
        assert MASKED_TOTAL in card
        for amount in ("2,000.00", "1,500.00", "500.00"):
            assert amount not in card, amount
