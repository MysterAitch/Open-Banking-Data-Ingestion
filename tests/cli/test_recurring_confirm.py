"""Confirming a recurring series from the Recurring page: the press, the page after, what is kept.

KNOWN ANSWERS, decided before the first run. The invented store is read on the real clock, so its
dates are made from today: a streaming subscription taken on the 3rd of each of the last six
months at 41.37 (live, not stopped), and a gym taken on the 9th of each of the months ending
twelve and eleven and ten and nine and eight months ago at 25.00 (stopped long before), with an
unrelated purchase yesterday so the account is known to be read to now.

  - Pressing Confirm on the subscription keeps one commitment and one window (4137, monthly, the
    3rd, from its first sighting, open), says so in a sentence with no payee or amount, and the
    line reads "confirmed as" and offers no press any more. A second press on the same line is
    refused and keeps nothing more.
  - The gym is asked "ended, or missing?" and nothing else; "It ended" closes the window at the
    last payment, "It is missing" leaves it open; either way it is not asked again.
  - The masked page carries the presses and no payee or amount, and answers a press masked; a
    shown page answers a press shown.
  - A press whose line has gone, or that names nothing, changes nothing.
  - Everything kept survives a rebuild from raw.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from recurring_press_support import (
    months_back,
    press_on,
    presses,
    row_of,
    served_export,
    shown,
    today,
)

PAYEE = "Zephyrine Quokka Subscriptions"
GYM = "Cedarwick Fernside Gym"
USUAL = "41.37"
GYM_USUAL = "25.00"
ACCOUNT = "current-main"


@pytest.fixture
def world(tmp_path, monkeypatch):
    now = today()
    rows = [(d, PAYEE, USUAL) for d in months_back(now, 6, 3)]
    gym_end = months_back(now, 12, 9)[:5]
    rows += [(d, GYM, GYM_USUAL) for d in gym_end]
    rows.append((now - timedelta(days=1), "Corner Bakery", "3.20"))
    with served_export(tmp_path, monkeypatch, rows) as (base, db):
        yield base, db, rows, gym_end


class TestConfirmingALiveSeries:
    def test_Confirm_WhenPressedOnAMonthlySeries_KeepsOneCommitmentWithTheWindowFromItsHistory(
        self, world
    ):
        base, db, rows, _ = world
        (press,) = press_on(row_of(shown(base), "quokka"))

        response = httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        assert response.status_code == 200
        with Store(db) as store:
            (commitment,) = store.commitments()
        (window,) = commitment.windows
        first = min(d for d, payee, _ in rows if payee == PAYEE)
        assert (commitment.name.casefold(), commitment.kind, commitment.account) == (
            PAYEE.casefold(),
            "pulled",
            ACCOUNT,
        )
        assert (window.amount_minor, window.cadence, window.usual_day) == (4137, "monthly", 3)
        assert (window.from_day, window.to_day) == (first, None)
        assert window.basis.startswith("confirmed from the series on ")

    def test_Confirm_WhenPressed_SaysSoWithoutAPayeeOrAnAmountAndTheLineReadsConfirmed(
        self, world
    ):
        base, _db, _rows, _ = world
        (press,) = press_on(row_of(shown(base), "quokka"))

        page = httpx.post(f"{base}{press['action']}", data=press, timeout=60).text

        assert "Confirmed as a commitment." in page
        line = row_of(page, "quokka")
        assert "confirmed as" in line.text()
        assert press_on(line) == []

    def test_Confirm_OnALaterRead_TheLineStillReadsConfirmedAndOffersNoPress(self, world):
        base, _db, _rows, _ = world
        (press,) = press_on(row_of(shown(base), "quokka"))
        httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        line = row_of(shown(base), "quokka")

        assert "confirmed as" in line.text() and press_on(line) == []

    def test_Confirm_WhenPressedTwice_IsRefusedTheSecondTimeAndKeepsOne(self, world):
        base, db, _rows, _ = world
        (press,) = press_on(row_of(shown(base), "quokka"))
        httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        again = httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        assert again.status_code == 400
        assert "already a commitment" in again.text
        with Store(db) as store:
            assert len(store.commitments()) == 1

    def test_Confirm_WhenTheLineHasGone_ChangesNothing(self, world):
        base, db, _rows, _ = world

        response = httpx.post(
            f"{base}/recurring-confirm", data={"ref": "0000000000000000.0"}, timeout=60
        )

        assert response.status_code == 400
        assert "Reload the page" in response.text
        with Store(db) as store:
            assert store.commitments() == []

    def test_Confirm_WhenNoLineIsNamed_ChangesNothing(self, world):
        base, db, _rows, _ = world

        response = httpx.post(f"{base}/recurring-confirm", data={}, timeout=60)

        assert response.status_code == 400
        with Store(db) as store:
            assert store.commitments() == []

    def test_Confirm_WhenTheWayIsNotOneTheyKnow_ChangesNothing(self, world):
        base, db, _rows, _ = world
        (press,) = press_on(row_of(shown(base), "quokka"))

        response = httpx.post(
            f"{base}{press['action']}", data={**press, "how": "perhaps"}, timeout=60
        )

        assert response.status_code == 400
        with Store(db) as store:
            assert store.commitments() == []

    def test_Confirm_WhenGetIsUsedInsteadOfPost_IsNotAnAction(self, world):
        base, db, _rows, _ = world

        response = httpx.get(f"{base}/recurring-confirm?ref=0000000000000000.0", timeout=60)

        assert response.status_code in (404, 405)
        with Store(db) as store:
            assert store.commitments() == []


class TestAStoppedSeriesIsAskedOnce:
    def test_Page_ForAStoppedSeries_OffersEndedOrMissingAndNoPlainConfirm(self, world):
        base, _db, _rows, _ = world

        line = row_of(shown(base), "cedarwick")

        assert "ended, or missing?" in line.text()
        assert sorted(p.get("how", "") for p in press_on(line)) == ["ended", "missing"]

    def test_Confirm_AsEnded_ClosesTheWindowAtTheLastPaymentAndIsNotAskedAgain(self, world):
        base, db, _rows, gym_end = world
        ended = next(p for p in press_on(row_of(shown(base), "cedarwick")) if p["how"] == "ended")

        page = httpx.post(f"{base}{ended['action']}", data=ended, timeout=60).text

        with Store(db) as store:
            (commitment,) = store.commitments()
        assert commitment.ended == max(gym_end)
        line = row_of(page, "cedarwick")
        assert "ended, or missing?" not in line.text() and press_on(line) == []
        assert "confirmed as" in line.text() and "ended" in line.text()

    def test_Confirm_AsMissing_LeavesTheWindowOpenAndIsNotAskedAgain(self, world):
        base, db, _rows, _ = world
        missing = next(
            p for p in press_on(row_of(shown(base), "cedarwick")) if p["how"] == "missing"
        )

        page = httpx.post(f"{base}{missing['action']}", data=missing, timeout=60).text

        with Store(db) as store:
            (commitment,) = store.commitments()
        assert commitment.ended is None
        assert press_on(row_of(page, "cedarwick")) == []


class TestTheMaskedPage:
    def test_Page_WhenFetched_CarriesThePressesAndNeitherPayeeNorAmount(self, world):
        base, _db, _rows, _ = world

        text = httpx.get(f"{base}/recurring", timeout=60).text

        assert len(presses(text)) == 3
        for hidden in ("quokka", "cedarwick", USUAL, GYM_USUAL, "41.37", "25.00"):
            assert hidden not in text.casefold()

    def test_Press_WhenMadeOnTheMaskedPage_IsAnsweredMaskedAndKeepsTheCommitment(self, world):
        base, db, _rows, _ = world
        masked = httpx.get(f"{base}/recurring", timeout=60).text
        press = next(p for p in presses(masked) if "how" not in p)
        assert "shown" not in press

        response = httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        assert response.status_code == 200
        assert "VALUES ARE SHOWN" not in response.text
        assert "confirmed as" in response.text
        for hidden in ("quokka", USUAL):
            assert hidden not in response.text.casefold()
        with Store(db) as store:
            assert len(store.commitments()) == 1

    def test_Press_WhenMadeOnAShownPage_IsAnsweredShown(self, world):
        base, _db, _rows, _ = world
        (press,) = press_on(row_of(shown(base), "quokka"))
        assert press["shown"] == "1"

        response = httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        assert "VALUES ARE SHOWN" in response.text
        assert "quokka" in response.text.casefold()
        assert "no-store" in response.headers["cache-control"]

    def test_Refs_OnTheMaskedAndTheShownPage_AreTheSame(self, world):
        base, _db, _rows, _ = world

        masked = sorted(p["ref"] for p in presses(httpx.get(f"{base}/recurring", timeout=60).text))
        unmasked = sorted(p["ref"] for p in presses(shown(base)))

        # Two series: the gym's two presses (ended, missing) name the one line.
        assert masked == unmasked and len(set(masked)) == 2


class TestWhatIsKept:
    def test_Commitment_WhenTheStoreIsRebuiltFromRaw_IsStillConfirmedOnThePage(self, world):
        base, db, _rows, _ = world
        (press,) = press_on(row_of(shown(base), "quokka"))
        httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        with Store(db) as store:
            rebuild_from_raw(store)

        line = row_of(shown(base), "quokka")
        assert "confirmed as" in line.text() and press_on(line) == []
