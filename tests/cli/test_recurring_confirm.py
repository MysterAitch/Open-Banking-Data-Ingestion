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

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.core.page_times import local_day
from obdi.ingest.pipeline import import_file
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from page_dom import Node, elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
PAYEE = "Zephyrine Quokka Subscriptions"
GYM = "Cedarwick Fernside Gym"
USUAL = "41.37"
GYM_USUAL = "25.00"


def months_back(today: date, count: int, day: int) -> list[date]:
    """The `day` of each of the `count` months that end the most recent one on or before today."""
    index = today.year * 12 + today.month - 1
    year, month = divmod(index, 12)
    if date(year, month + 1, day) > today:
        index -= 1
    found = []
    for back in range(count):
        year, month = divmod(index - back, 12)
        found.append(date(year, month + 1, day))
    return sorted(found)


def _export(path, rows: list[tuple[date, str, str]]) -> None:
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{d:%d/%m/%Y},{payee},,CARD,-{amount},0" for d, payee, amount in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    today = local_day(datetime.now(UTC))
    rows = [(d, PAYEE, USUAL) for d in months_back(today, 6, 3)]
    gym_end = months_back(today, 12, 9)[:5]
    rows += [(d, GYM, GYM_USUAL) for d in gym_end]
    rows.append((today - timedelta(days=1), "Corner Bakery", "3.20"))
    csv = tmp_path / "export.csv"
    _export(csv, rows)
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db, rows, gym_end
    stop()


def presses(page: str) -> list[dict[str, str]]:
    """Each press form on the page: its action and its hidden fields."""
    found = []
    for form in elements(parse(page), "form"):
        action = form.attrs.get("action", "")
        if not action.startswith("/recurring-"):
            continue
        fields = {
            i.attrs["name"]: i.attrs.get("value", "")
            for i in elements(form, "input")
            if "name" in i.attrs
        }
        found.append({"action": action, **fields})
    return found


def row_of(page: str, fragment: str) -> Node:
    (row,) = [
        li
        for li in elements(parse(page), "li")
        if "recur-row" in li.classes and fragment in li.text().casefold()
    ]
    return row


def press_on(row: Node) -> list[dict[str, str]]:
    return [
        {
            "action": form.attrs["action"],
            **{i.attrs["name"]: i.attrs.get("value", "") for i in elements(form, "input")},
        }
        for form in elements(row, "form")
    ]


def shown(base: str) -> str:
    return httpx.post(f"{base}/recurring", timeout=60).text


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
