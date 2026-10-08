"""Saying a series is not a commitment: the line folds away, the page counts it, it can be put back.

KNOWN ANSWERS, decided before the first run. The invented store is read on the real clock: a
streaming subscription taken on the 3rd of each of the last six months (live), a window cleaner
taken on the 21st of each of the last six months (live), and a gym last paid eight months ago
(stopped), with an unrelated purchase yesterday. So the page finds three series.

  - Before any answer the page carries no tally: three found is already its summary.
  - "Not a commitment" on the subscription keeps one dismissal and takes its line out of the
    account's list into a CLOSED fold at the foot of the page that says "1 series you said is not a
    commitment" and holds the line with a press to put it back. The tally reads "3 found: 0
    confirmed, 1 not a commitment, 2 still to look at".
  - With the gym confirmed as ended as well it reads "3 found: 1 confirmed, 1 not a commitment, 1
    still to look at".
  - Putting it back returns the line to the list, offered for confirming again, and the tally goes.
  - The dismissal survives a rebuild from raw. The masked page shows the fold, the counts, and the
    presses, and no payee or amount.
  - A confirmed series cannot be dismissed; a series cannot be dismissed twice; a press for a line
    that has gone, or to put back a line not dismissed, changes nothing.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from landing import rebuild_from_raw
from obdi.ingest.store import Store
from page_dom import Node, elements, parse
from recurring_press_support import (
    confirm_press,
    forms_of,
    months_back,
    press_on,
    presses,
    row_of,
    served_export,
    shown,
    today,
)

PAYEE = "Zephyrine Quokka Subscriptions"
CLEANER = "Marmalade Foundry Window Cleaning"
GYM = "Cedarwick Fernside Gym"
ALL_OPEN = "3 found: 0 confirmed, 0 not a commitment, 3 still to look at"
ONE_SET_ASIDE = "3 found: 0 confirmed, 1 not a commitment, 2 still to look at"


@pytest.fixture
def world(tmp_path, monkeypatch):
    now = today()
    rows = [(d, PAYEE, "41.37") for d in months_back(now, 6, 3)]
    rows += [(d, CLEANER, "12.00") for d in months_back(now, 6, 21)]
    rows += [(d, GYM, "25.00") for d in months_back(now, 12, 9)[:5]]
    rows.append((now - timedelta(days=1), "Corner Bakery", "3.20"))
    with served_export(tmp_path, monkeypatch, rows) as (base, db):
        yield base, db


def tally_of(page: str) -> str | None:
    found = [p for p in elements(parse(page), "p") if "recur-tally" in p.classes]
    return found[0].text() if found else None


def dismiss(base: str, fragment: str = "quokka") -> httpx.Response:
    row = row_of(shown(base), fragment)
    press = next(p for p in press_on(row) if p["action"] == "/recurring-dismiss")
    return httpx.post(f"{base}{press['action']}", data=press, timeout=60)


def fold_of(root: Node) -> list[Node]:
    return [d for d in elements(root, "details") if "recur-dismissed" in d.classes]


def put_back(base: str) -> httpx.Response:
    (fold,) = fold_of(parse(shown(base)))
    (back,) = [p for p in forms_of(fold) if p["action"] == "/recurring-restore"]
    return httpx.post(f"{base}{back['action']}", data=back, timeout=60)


class TestSayingASeriesIsNotACommitment:
    def test_Page_BeforeAnyAnswer_CarriesNoTallyAndNoFold(self, world):
        base, _db = world

        page = shown(base)

        assert tally_of(page) is None and fold_of(parse(page)) == []
        assert ALL_OPEN not in page

    def test_Press_KeepsOneDismissalAndTheSentenceNamesNothing(self, world):
        base, db = world

        response = dismiss(base)

        assert response.status_code == 200
        assert "Set aside as not a commitment." in response.text
        with Store(db) as store:
            (dismissal,) = store.dismissals()
        assert (dismissal.account, dismissal.direction, dismissal.cadence) == (
            "current-main",
            "out",
            "monthly",
        )

    def test_Line_AfterThePress_LeavesTheListForAClosedFoldAtTheFootOfThePage(self, world):
        base, _db = world

        root = parse(dismiss(base).text)

        (fold,) = fold_of(root)
        assert "open" not in fold.attrs
        (summary,) = elements(fold, "summary")
        assert summary.text().strip() == "1 series you said is not a commitment"
        assert "quokka" in fold.text().casefold()
        sections = [s for s in elements(root, "section") if "recur-account" in s.classes]
        assert sections and all("quokka" not in s.text().casefold() for s in sections)
        text = root.text().casefold()
        assert text.index("quokka") > text.index("window cleaning")

    def test_Tally_AfterOneDismissal_ReadsThreeFoundOneNotACommitment(self, world):
        base, _db = world

        assert tally_of(dismiss(base).text) == ONE_SET_ASIDE

    def test_Tally_WithOneConfirmedAndOneDismissed_ReadsWhatIsLeftToLookAt(self, world):
        base, _db = world
        gym = next(
            p for p in press_on(row_of(shown(base), "cedarwick")) if p.get("how") == "ended"
        )
        httpx.post(f"{base}{gym['action']}", data=gym, timeout=60)

        assert tally_of(dismiss(base).text) == (
            "3 found: 1 confirmed, 1 not a commitment, 1 still to look at"
        )

    def test_Tally_OnALaterRead_StillCountsTheDismissal(self, world):
        base, _db = world
        dismiss(base)

        assert tally_of(shown(base)) == ONE_SET_ASIDE

    def test_Tally_WithOnlyAConfirmation_ReadsZeroNotACommitment(self, world):
        base, _db = world
        press = confirm_press(row_of(shown(base), "quokka"))

        page = httpx.post(f"{base}{press['action']}", data=press, timeout=60).text

        assert tally_of(page) == "3 found: 1 confirmed, 0 not a commitment, 2 still to look at"
        assert fold_of(parse(page)) == []

    def test_Fold_WithTwoDismissed_SaysSoInThePlural(self, world):
        base, _db = world
        dismiss(base)

        root = parse(dismiss(base, "marmalade").text)

        (fold,) = fold_of(root)
        (summary,) = elements(fold, "summary")
        assert summary.text().strip() == "2 series you said are not commitments"


class TestPuttingASeriesBack:
    def test_Press_ReturnsTheLineToTheListAndTheTallyGoes(self, world):
        base, db = world
        dismiss(base)

        page = put_back(base).text

        assert fold_of(parse(page)) == [] and tally_of(page) is None
        offered = [p["action"] for p in press_on(row_of(page, "quokka"))]
        assert "/recurring-confirm" in offered
        with Store(db) as store:
            assert store.dismissals() == []

    def test_Press_ForALineNotDismissed_IsRefusedAndChangesNothing(self, world):
        base, db = world
        line = press_on(row_of(shown(base), "quokka"))[0]

        response = httpx.post(f"{base}/recurring-restore", data={"ref": line["ref"]}, timeout=60)

        assert response.status_code == 400
        with Store(db) as store:
            assert store.dismissals() == []

    def test_Series_AfterBeingPutBack_CanBeDismissedAgain(self, world):
        base, db = world
        dismiss(base)
        put_back(base)

        again = dismiss(base)

        assert again.status_code == 200
        with Store(db) as store:
            assert len(store.dismissals()) == 1


class TestWhatCannotBeDismissed:
    def test_Press_ForAConfirmedSeries_IsRefusedAndTheDismissalIsNotKept(self, world):
        base, db = world
        row = row_of(shown(base), "quokka")
        stale = next(p for p in press_on(row) if p["action"] == "/recurring-dismiss")
        confirm = confirm_press(row)
        httpx.post(f"{base}{confirm['action']}", data=confirm, timeout=60)

        response = httpx.post(f"{base}{stale['action']}", data=stale, timeout=60)

        assert response.status_code == 400
        assert "already a commitment" in response.text
        with Store(db) as store:
            assert store.dismissals() == []

    def test_Press_Twice_IsRefusedTheSecondTime(self, world):
        base, db = world
        row = row_of(shown(base), "quokka")
        press = next(p for p in press_on(row) if p["action"] == "/recurring-dismiss")
        httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        again = httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        assert again.status_code == 400
        with Store(db) as store:
            assert len(store.dismissals()) == 1

    def test_Press_ForALineThatHasGone_ChangesNothing(self, world):
        base, db = world

        response = httpx.post(
            f"{base}/recurring-dismiss", data={"ref": "0000000000000000.0"}, timeout=60
        )

        assert response.status_code == 400
        with Store(db) as store:
            assert store.dismissals() == []


class TestTheMaskedPage:
    def test_Page_AfterADismissal_ShowsTheFoldAndTheCountsAndNeitherPayeeNorAmount(self, world):
        base, _db = world
        dismiss(base)

        text = httpx.get(f"{base}/recurring", timeout=60).text

        assert "1 series you said is not a commitment" in text
        assert tally_of(text) == ONE_SET_ASIDE
        for hidden in ("quokka", "marmalade", "cedarwick", "41.37", "12.00", "25.00"):
            assert hidden not in text.casefold()
        assert any(p["action"] == "/recurring-restore" for p in presses(text))

    def test_Press_MadeOnTheMaskedPage_IsAnsweredMasked(self, world):
        base, db = world
        masked = httpx.get(f"{base}/recurring", timeout=60).text
        press = next(p for p in presses(masked) if p["action"] == "/recurring-dismiss")

        response = httpx.post(f"{base}{press['action']}", data=press, timeout=60)

        assert response.status_code == 200 and "VALUES ARE SHOWN" not in response.text
        with Store(db) as store:
            assert len(store.dismissals()) == 1


class TestWhatIsKept:
    def test_Dismissal_WhenTheStoreIsRebuiltFromRaw_IsStillFolded(self, world):
        base, db = world
        dismiss(base)
        with Store(db) as store:
            rebuild_from_raw(store)

        (fold,) = fold_of(parse(shown(base)))

        assert "quokka" in fold.text().casefold()
