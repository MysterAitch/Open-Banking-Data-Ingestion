"""Protecting an account through the ledger page, over real HTTP and a real store.

The account is `everyday` (see test_protection): five rows, known balances of 1,000.00 at the end of
03-05, 980.00 at 03-10, and 952.00 at 03-20, so it is in agreement through 03-20 and the page offers
a protection through that date. The figures a protection records (the opening, 912.50 less nothing
here: 91,250, and the 95,200 it was verified against) must never be on any page.
"""

from __future__ import annotations

import html
import json
import re
import threading
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler
from obdi.verify.balance_anchors import record_stated_anchor
from test_account_pages import assert_tap_targets_are_thumb_sized
from test_balance_anchors import ACCOUNT, everyday
from test_ledger import land, txn

#: Every figure the protection holds or implies, in minor units and in pounds and pence.
SECRET_FIGURES = ("95200", "952.00", "91250", "912.50", "98000", "980.00", "100000", "1,000.00")


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def get(self, path: str = "/ledger", **params: str) -> httpx.Response:
        return httpx.get(
            f"{self.base}{path}", params={"ref": ACCOUNT, "month": "2026-03", **params}, timeout=20
        )

    def post(self, path: str, **fields: str) -> httpx.Response:
        headers = fields.pop("headers", None)  # type: ignore[arg-type]
        data = {"ref": ACCOUNT, "month": "2026-03", **fields}
        return httpx.post(
            f"{self.base}{path}", data=data, headers=headers, follow_redirects=False, timeout=20
        )

    def press(self, through: str = "2026-03-20") -> httpx.Response:
        return self.post("/protect", through=through, confirmed="yes")

    def record(self):
        with Store(self.db) as store:
            return store.protection_record(ACCOUNT)

    def events(self) -> list[str]:
        with Store(self.db) as store:
            return [str(e["event"]) for e in store.protection_events(ACCOUNT)]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    db = tmp_path / "protect.sqlite3"
    with Store(db) as store:
        everyday(store)
        for day, amount in (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"),
                            ("2026-03-20", "952.00")):
            record_stated_anchor(store, ACCOUNT, day, amount)
    account_map = tmp_path / "accounts.json"
    account_map.write_text(
        json.dumps({"actual": [{"canonical_id": ACCOUNT, "actual_account_id": "act-everyday"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


def said(page: str) -> str:
    """The words of the page: no tags (a date is set in a span that never breaks), entities read."""
    return html.unescape(re.sub(r"<[^>]+>", "", page))


def assert_no_secret(page: str) -> None:
    for figure in SECRET_FIGURES:
        assert figure not in page, figure


class TestOfferingAProtection:
    def test_Ledger_WhenInAgreement_OffersToLockInToTheLatestAgreedKnownBalanceSayingWhatItCovers(
        self, lab
    ):
        page = lab.get().text

        assert 'action="/protect"' in page
        words = said(page)
        assert "Lock in to 2026-03-20" in words
        assert "5 transactions, 2026-03, would be locked in." in words, "how many, which months"
        assert "Show values to read them first" in words
        assert "never applied quietly" in words, "what locking gives"
        assert "Adds up to the known balances to 2026-03-20." in words
        assert "not locked" not in words, "nothing is locked, and that is not said"

    def test_Ledger_WhenNoBalanceIsKnown_OffersNothingAndSaysWhy(self, tmp_path, lab):
        with Store(lab.db) as store:
            for day in ("2026-03-05", "2026-03-10", "2026-03-20"):
                from obdi.verify.balance_anchors import remove_stated_anchor

                remove_stated_anchor(store, ACCOUNT, day)

        page = lab.get().text

        assert 'action="/protect"' not in page
        assert "Nothing to check against." in page

    def test_Ledger_OffersEarlierKnownBalancesToo(self, lab):
        page = lab.get().text

        assert '<option value="2026-03-10">' in page, "an earlier agreed known balance"
        assert '<option value="2026-03-05">' not in page, "the defining balance tests nothing"


class TestPressingAndConfirming:
    def test_Post_WithoutConfirmation_AsksAreYouSureAndChangesNothing(self, lab):
        response = lab.post("/protect", through="2026-03-20")

        assert response.status_code == 200
        assert "Are you sure?" in response.text
        assert "Lock in this account to 2026-03-20?" in response.text
        assert lab.record() is None

    def test_Post_WhenConfirmed_LocksInAndAnswersWithTheMaskedLedger(self, lab):
        response = lab.press()

        assert response.status_code == 200
        assert "Locked in to 2026-03-20." in response.text
        assert re.search(
            r"Locked in to 2026-03-20, on \d{4}-\d{2}-\d{2}: 5 transactions, added up against "
            r"stated's balance of 2026-03-20\.",
            html.unescape(response.text),
        )
        assert_no_secret(response.text)
        assert lab.events() == ["pressed"]

    def test_Post_ForADateTheAccountIsNotInAgreementThrough_IsRefusedWithoutEchoingFigures(
        self, lab
    ):
        response = lab.press("2026-03-12")

        assert response.status_code == 400
        assert "Nothing was locked in." in response.text
        assert "have not been shown to add up to the known balances up to that date" in (
            response.text
        )
        assert lab.record() is None

    def test_Post_WithADateThatIsNotADate_IsRefusedAndTheTextIsNotEchoed(self, lab):
        response = lab.post("/protect", through="<b>1,000.00</b>", confirmed="yes")

        assert response.status_code == 400
        assert "<b>" not in response.text and "1,000.00" not in response.text

    def test_Get_OfTheProtectRoutes_ChangesNothingAndServesNothing(self, lab):
        for route in ("/protect", "/protect-accept", "/protect-withdraw"):
            response = httpx.get(f"{lab.base}{route}", params={"ref": ACCOUNT}, timeout=20)
            assert response.status_code == 404, route
        assert lab.record() is None

    @pytest.mark.parametrize("route", ["/protect", "/protect-accept", "/protect-withdraw"])
    def test_Post_DrivenByAnotherSite_IsRefused(self, lab, route):
        response = lab.post(
            route, through="2026-03-20", confirmed="yes",
            headers={"Origin": "https://evil.example"},  # type: ignore[arg-type]
        )

        assert response.status_code == 403
        assert lab.record() is None

    def test_Forms_AreThumbSizedTapTargets(self, lab):
        assert_tap_targets_are_thumb_sized(lab.get().text)
        assert_tap_targets_are_thumb_sized(lab.press().text)
        with Store(lab.db) as store:
            land(store, "d-new", txn(ACCOUNT, "src-a", "r6", date(2026, 3, 7), -111, "NEW"))
        assert_tap_targets_are_thumb_sized(lab.get().text)


class TestABreakOnThePage:
    def broken(self, lab: Lab) -> str:
        lab.press()
        with Store(lab.db) as store:
            land(store, "d-new", txn(ACCOUNT, "src-a", "r6", date(2026, 3, 7), -111, "NEW"))
        return lab.get().text

    def test_Ledger_WhenTheSpanChanged_SaysWhatChangedInCountsAndDates(self, lab):
        page = self.broken(lab)

        assert "The locked stretch, through 2026-03-20, has changed since" in page
        assert "1 row added to the protected period (dated 2026-03-07)" in page
        assert "Accept the change and lock in again" in page
        assert "Locked in to 2026-03-20, but that stretch has changed since." in said(page), (
            "the sentence says so too"
        )
        assert "Locked in to 2026-03-20." not in said(page), (
            "a changed stretch is not claimed intact"
        )
        assert_no_secret(page)

    def test_Ledger_WhenBroken_SaysSoInTheSentenceAndInAThingToDoThatLeadsToTheFold(self, lab):
        page = self.broken(lab)

        state = page[page.index('class="acct-state"') : page.index('class="acct-txns"')]
        assert 'class="trust bad"' in state
        assert 'href="#locking"' in state, "the control goes to the fold that holds the change"
        assert '<div id="locking">' in page

    def test_Ledger_WhenIntact_ShowsOneLineWithRemoveInsideTheLockingFold(self, lab):
        lab.press()

        page = lab.get().text

        assert re.search(
            r'<p class="protect-line">Locked in to 2026-03-20, on \d{4}-\d{2}-\d{2}: '
            r"5 transactions",
            page,
        )
        assert "Locked in to 2026-03-20." in said(page), "the sentence names the stretch"
        assert page.index("<summary>Locking in (to 2026-03-20)</summary>") < page.index(
            "Remove the lock"
        ), "the way out is in its fold, which is the only place that holds it"
        assert "The locked stretch, through" not in page

    def test_Ledger_WhenNotLocked_OffersTheLockAndNoRemoval(self, lab):
        page = lab.get().text

        assert "Lock in to 2026-03-20" in said(page)
        assert "Remove the lock" not in page, "there is nothing to remove"

    def test_Accept_AfterConfirmation_LocksTheNewStateAndRecordsIt(self, lab):
        self.broken(lab)

        asked = lab.post("/protect-accept")
        assert "Accept the change and lock in again?" in asked.text
        assert lab.events() == ["pressed"]

        accepted = lab.post("/protect-accept", confirmed="yes")

        assert accepted.status_code == 200
        assert "The locked stretch, through" not in accepted.text
        assert lab.events() == ["pressed", "accepted"]

    def test_Remove_AfterConfirmation_RemovesItAndRecordsIt(self, lab):
        lab.press()

        asked = lab.post("/protect-withdraw")
        assert "Remove this account's lock?" in html.unescape(asked.text)
        assert lab.record() is not None

        done = lab.post("/protect-withdraw", confirmed="yes")

        assert done.status_code == 200
        assert "Lock removed:" in done.text, "the result says the verb the button did"
        assert lab.record() is None
        assert lab.events() == ["pressed", "withdrawn"]

    def test_Accept_WhenNothingChanged_IsRefused(self, lab):
        lab.press()

        response = lab.post("/protect-accept", confirmed="yes")

        assert response.status_code == 400
        assert "nothing to accept" in response.text
