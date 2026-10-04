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

from obdi.balance_anchors import record_stated_anchor
from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
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


def assert_no_secret(page: str) -> None:
    for figure in SECRET_FIGURES:
        assert figure not in page, figure


class TestOfferingAProtection:
    def test_Ledger_WhenInAgreement_OffersToProtectThroughTheLatestAgreedKnownBalance(self, lab):
        page = lab.get().text

        assert "Protect through 2026-03-20" in page
        assert 'action="/protect"' in page
        assert "in agreement through 2026-03-20; not protected." in page
        assert "protected through nowhere" not in page, "nothing protects it, so no place"

    def test_Ledger_WhenNoBalanceIsKnown_OffersNothingAndSaysWhy(self, tmp_path, lab):
        with Store(lab.db) as store:
            for day in ("2026-03-05", "2026-03-10", "2026-03-20"):
                from obdi.balance_anchors import remove_stated_anchor

                remove_stated_anchor(store, ACCOUNT, day)

        page = lab.get().text

        assert 'action="/protect"' not in page
        assert "No known balance: these rows cannot be verified." in page

    def test_Ledger_OffersEarlierKnownBalancesToo(self, lab):
        page = lab.get().text

        assert '<option value="2026-03-10">' in page, "an earlier agreed known balance"
        assert '<option value="2026-03-05">' not in page, "the defining balance tests nothing"


class TestPressingAndConfirming:
    def test_Post_WithoutConfirmation_AsksAreYouSureAndChangesNothing(self, lab):
        response = lab.post("/protect", through="2026-03-20")

        assert response.status_code == 200
        assert "Are you sure?" in response.text
        assert "Protect this account through 2026-03-20?" in response.text
        assert lab.record() is None

    def test_Post_WhenConfirmed_ProtectsAndAnswersWithTheMaskedLedger(self, lab):
        response = lab.press()

        assert response.status_code == 200
        assert "Protected through 2026-03-20." in response.text
        assert re.search(
            r"Protected through 2026-03-20: 5 rows, verified against stated's balance of "
            r"2026-03-20 on \d{4}-\d{2}-\d{2}\.",
            html.unescape(response.text),
        )
        assert_no_secret(response.text)
        assert lab.events() == ["pressed"]

    def test_Post_ForADateTheAccountIsNotInAgreementThrough_IsRefusedWithoutEchoingFigures(
        self, lab
    ):
        response = lab.press("2026-03-12")

        assert response.status_code == 400
        assert "Nothing was protected." in response.text
        assert "not in agreement through that date" in response.text
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

        assert "The protection is broken" in page
        assert "1 row added to the span (dated 2026-03-07)" in page
        assert "Accept the change and protect again" in page
        assert "the protection through 2026-03-20 is broken" in page, "the verdict says so too"
        assert "; protected through 2026-03-20" not in page, "a broken span is not claimed"
        assert_no_secret(page)

    def test_Ledger_WhenBrokenAndIntact_NeverHidesTheBreakInACollapsedBlock(self, lab):
        page = self.broken(lab)

        before = page[: page.index("The protection is broken")]
        assert before.count("<details") == before.count("</details>"), (
            "the break is inside a collapsed block"
        )

    def test_Ledger_WhenIntact_ShowsOneLineWithWithdrawVisibleAndTheDetailBehindIt(self, lab):
        lab.press()

        page = lab.get().text

        assert re.search(
            r'<p class="protect-line"><strong>Protected through 2026-03-20: 5 rows', page
        )
        assert "protected through 2026-03-20." in page, "the verdict names the span"
        before = page[: page.index("Withdraw protection")]
        assert before.count("<details") == before.count("</details>"), (
            "the way out is inside a collapsed block"
        )
        assert re.search(r"<summary>About this protection</summary>", page)
        assert "The protection is broken" not in page

    def test_Ledger_WhenNotProtected_OffersTheProtectionAndNoWithdrawal(self, lab):
        page = lab.get().text

        assert "Protect through 2026-03-20" in page
        assert "Withdraw protection" not in page, "there is nothing to withdraw"

    def test_Accept_AfterConfirmation_ProtectsTheNewStateAndRecordsIt(self, lab):
        self.broken(lab)

        asked = lab.post("/protect-accept")
        assert "Accept the change and protect again?" in asked.text
        assert lab.events() == ["pressed"]

        accepted = lab.post("/protect-accept", confirmed="yes")

        assert accepted.status_code == 200
        assert "The protection is broken" not in accepted.text
        assert lab.events() == ["pressed", "accepted"]

    def test_Withdraw_AfterConfirmation_RemovesItAndRecordsIt(self, lab):
        lab.press()

        asked = lab.post("/protect-withdraw")
        assert "Withdraw this account's protection?" in html.unescape(asked.text)
        assert lab.record() is not None

        done = lab.post("/protect-withdraw", confirmed="yes")

        assert done.status_code == 200
        assert lab.record() is None
        assert lab.events() == ["pressed", "withdrawn"]

    def test_Accept_WhenNothingChanged_IsRefused(self, lab):
        lab.press()

        response = lab.post("/protect-accept", confirmed="yes")

        assert response.status_code == 400
        assert "nothing to accept" in response.text
