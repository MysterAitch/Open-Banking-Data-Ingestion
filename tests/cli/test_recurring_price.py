"""A confirmed series whose price changed offers a new window; the press keeps the old one.

KNOWN ANSWERS, decided before the first run. The invented store is read on the real clock, so its
dates are made from today: a streaming subscription taken on the 3rd of each of the last six
months, at 41.37 for the first four and 47.91 for the last two (a rise of 15.8 per cent), and an
unrelated purchase yesterday so the account is known to be read to now.

  - Before it is confirmed the line offers no new price, only the "changed" mark it always had.
  - Once confirmed (the window is 41.37 from the first payment), the line offers "Price changed to
    47.91 from <the day of the fifth payment>". Pressing it ends the 41.37 window the day before and
    opens a 47.91 window on that day, open, and the offer goes. The old window stays.
  - Masked, the offer says a price changed and from when, with the amount sealed, and the press is
    the same.
  - A price within the detector's tolerance of the window is not offered; a bill that varies every
    time is not offered; a series that has stopped is not offered; a press with nothing to record
    is refused and changes nothing; a second rise later is a third window.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from obdi.ingest.store import Store
from page_dom import parse
from recurring_press_support import (
    forms_of,
    months_back,
    press_on,
    row_of,
    served_export,
    shown,
    today,
)

PAYEE = "Zephyrine Quokka Subscriptions"
RISEN = ["41.37", "41.37", "41.37", "41.37", "47.91", "47.91"]


def rows_for(amounts: list[str], *, ending: date | None = None) -> list[tuple[date, str, str]]:
    """The payments on the 3rd of the months ending the most recent on or before `ending`, and an
    unrelated purchase yesterday, which is what shows the account was read to now."""
    days = months_back(ending or today(), len(amounts), 3)
    rows = [(d, PAYEE, a) for d, a in zip(days, amounts, strict=True)]
    rows.append((today() - timedelta(days=1), "Corner Bakery", "3.20"))
    return rows


@pytest.fixture
def risen(tmp_path, monkeypatch):
    rows = rows_for(RISEN)
    with served_export(tmp_path, monkeypatch, rows) as (base, db):
        yield base, db, sorted(d for d, payee, _ in rows if payee == PAYEE)


def confirm(base: str) -> str:
    """Press Confirm on the line, and return the reference that names it."""
    (press,) = press_on(row_of(shown(base), "quokka"))
    assert httpx.post(f"{base}{press['action']}", data=press, timeout=60).status_code == 200
    return press["ref"]


def price_press(base: str) -> dict[str, str]:
    (press,) = [p for p in press_on(row_of(shown(base), "quokka")) if "price" in p["action"]]
    return press


class TestAnOfferToRecordAChangedPrice:
    def test_Line_BeforeItIsConfirmed_OffersNoNewPrice(self, risen):
        base, _db, _days = risen

        line = row_of(shown(base), "quokka")

        assert "Price changed" not in line.text()
        assert [p["action"] for p in press_on(line)] == ["/recurring-confirm"]

    def test_Line_WhenConfirmedAndRisen_OffersTheNewPriceFromTheDayItBegan(self, risen):
        base, _db, days = risen
        confirm(base)

        line = row_of(shown(base), "quokka")

        assert f"Price changed to £47.91 from {days[4].isoformat()}" in line.text()

    def test_Press_OnTheOffer_EndsTheOldWindowTheDayBeforeAndOpensTheNew(self, risen):
        base, db, days = risen
        confirm(base)

        response = httpx.post(f"{base}/recurring-price", data=price_press(base), timeout=60)

        assert response.status_code == 200
        with Store(db) as store:
            (commitment,) = store.commitments()
        old, new = commitment.windows
        assert (old.amount_minor, old.from_day, old.to_day) == (
            4137,
            days[0],
            days[4] - timedelta(days=1),
        )
        assert (new.amount_minor, new.from_day, new.to_day) == (4791, days[4], None)
        assert (new.cadence, new.usual_day, new.tolerance_days) == (
            old.cadence,
            old.usual_day,
            old.tolerance_days,
        )
        assert new.basis.startswith(f"price changed from {days[4].isoformat()}, confirmed on ")

    def test_Line_AfterThePress_OffersNothingMore(self, risen):
        base, _db, _days = risen
        confirm(base)
        page = httpx.post(f"{base}/recurring-price", data=price_press(base), timeout=60).text

        line = row_of(page, "quokka")

        assert "Price changed" not in line.text() and press_on(line) == []
        assert "Price changed" not in row_of(shown(base), "quokka").text()

    def test_Press_WhenRepeated_IsRefusedAndKeepsTwoWindows(self, risen):
        base, db, _days = risen
        confirm(base)
        press = price_press(base)
        httpx.post(f"{base}/recurring-price", data=press, timeout=60)

        again = httpx.post(f"{base}/recurring-price", data=press, timeout=60)

        assert again.status_code == 400
        assert "no new price to record" in again.text
        with Store(db) as store:
            assert len(store.commitments()[0].windows) == 2

    def test_Press_ForASeriesNotConfirmed_IsRefusedAndKeepsNothing(self, risen):
        base, db, _days = risen
        (press,) = press_on(row_of(shown(base), "quokka"))

        response = httpx.post(f"{base}/recurring-price", data=press, timeout=60)

        assert response.status_code == 400
        with Store(db) as store:
            assert store.commitments() == []

    def test_Press_WhenTheFormClaimsAnAmount_RecordsOnlyWhatTheTransactionsHold(self, risen):
        base, db, _days = risen
        confirm(base)

        httpx.post(
            f"{base}/recurring-price", data={**price_press(base), "amount": "1.00"}, timeout=60
        )

        with Store(db) as store:
            assert [w.amount_minor for w in store.commitments()[0].windows] == [4137, 4791]

class TestTheMaskedOffer:
    def test_Page_WhenMasked_SaysAPriceChangedAndFromWhenWithTheAmountSealed(self, risen):
        base, _db, days = risen
        confirm(base)

        text = httpx.get(f"{base}/recurring", timeout=60).text

        assert f"Price changed to £••• from {days[4].isoformat()}" in text
        assert "47.91" not in text and "41.37" not in text
        assert any(p["action"] == "/recurring-price" for p in _page_forms(text))

    def test_Press_MadeOnTheMaskedPage_IsAnsweredMaskedAndRecordsTheWindow(self, risen):
        base, db, _days = risen
        confirm(base)
        masked = httpx.get(f"{base}/recurring", timeout=60).text
        press = next(p for p in _page_forms(masked) if p["action"] == "/recurring-price")

        response = httpx.post(f"{base}/recurring-price", data=press, timeout=60)

        assert response.status_code == 200 and "VALUES ARE SHOWN" not in response.text
        assert "47.91" not in response.text
        with Store(db) as store:
            assert len(store.commitments()[0].windows) == 2


def _page_forms(page: str) -> list[dict[str, str]]:
    return forms_of(parse(page))


class TestWhenNoOfferIsMade:
    def line_after_confirming(self, tmp_path, monkeypatch, rows) -> str:
        with served_export(tmp_path, monkeypatch, rows) as (base, _db):
            line = row_of(shown(base), "quokka")
            (press,) = [p for p in press_on(line) if p["action"].endswith("confirm")]
            httpx.post(f"{base}{press['action']}", data=press, timeout=60)
            return row_of(shown(base), "quokka").text()

    def test_Line_WhenTheLatestIsWithinTheToleranceOfTheWindow_OffersNothing(
        self, tmp_path, monkeypatch
    ):
        amounts = ["41.37", "41.37", "41.37", "41.37", "41.37", "42.00"]

        text = self.line_after_confirming(tmp_path, monkeypatch, rows_for(amounts))

        assert "confirmed as" in text and "Price changed" not in text

    def test_Line_WhenTheBillVariesEveryTime_OffersNothing(self, tmp_path, monkeypatch):
        amounts = ["41.37", "52.10", "33.80", "48.00", "29.99", "61.25"]

        text = self.line_after_confirming(tmp_path, monkeypatch, rows_for(amounts))

        assert "confirmed as" in text and "Price changed" not in text

    def test_Line_WhenTheSeriesHasStopped_OffersNothing(self, tmp_path, monkeypatch):
        long_before = today() - timedelta(days=150)
        rows = rows_for(RISEN, ending=long_before)
        with served_export(tmp_path, monkeypatch, rows) as (base, _db):
            line = row_of(shown(base), "quokka")
            missing = next(p for p in press_on(line) if p.get("how") == "missing")
            httpx.post(f"{base}{missing['action']}", data=missing, timeout=60)

            text = row_of(shown(base), "quokka").text()

        assert "confirmed as" in text and "Price changed" not in text
