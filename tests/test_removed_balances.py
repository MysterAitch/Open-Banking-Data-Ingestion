"""Removing a stated balance asks first, and keeps a record that can be read back.

"Remove the stated balance for the end of 2026-06-30" was a full-width button directly under the
save form, with no confirmation and no undo, and the figure was never shown, so a mis-tap
destroyed something that could not be read back. The press now asks "Are you sure?", naming the
date and the source and never the figure, and the removal is kept (date, when removed, and the
figure in the private store) so the values view can show it and state it again.
"""

from __future__ import annotations

import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor, removed_stated_anchors, stated_anchors
from obdi.web import AuthorisationSession, ConnectionHandler
from test_balance_anchors import ACCOUNT, everyday

#: Invented, and chosen so that no other figure on the page can contain it.
FIGURE = "7777.65"
MINOR = 777765
FORMATTED = "7,777.65"
DAY = "2026-03-26"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "removed.sqlite3"
    with Store(path) as store:
        everyday(store)
        store.declare_account(AccountRecord(ref=AccountRef(ACCOUNT), kind="current"))
        store.declare_account(AccountRecord(ref=AccountRef("other-acct"), kind="current"))
        record_stated_anchor(store, ACCOUNT, DAY, FIGURE)
    return path


@pytest.fixture
def served(db, tmp_path, monkeypatch):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
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
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


def post(served: str, path: str, **fields: str) -> httpx.Response:
    return httpx.post(f"{served}{path}", data=fields, timeout=60)


def press_remove(served: str, *, confirmed: bool, day: str = DAY, ref: str = ACCOUNT):
    fields = {"ref": ref, "month": "2026-03", "day": day}
    if confirmed:
        fields["confirmed"] = "yes"
    return post(served, "/ledger-anchor-remove", **fields)


def stated_days(db) -> list[str]:
    with Store(db) as store:
        return [anchor.day.isoformat() for anchor in stated_anchors(store, ACCOUNT)]


class TestThePressAsksFirst:
    def test_Remove_WhenFirstPressed_AsksAndRemovesNothing(self, served, db):
        asked = press_remove(served, confirmed=False)

        assert asked.status_code == 200
        assert "Are you sure?" in asked.text
        assert stated_days(db) == [DAY]

    def test_Remove_Question_NamesTheDateAndTheSourceButNeverTheFigure(self, served):
        asked = press_remove(served, confirmed=False)

        assert f"the known balance for the end of {DAY}, stated by you" in asked.text
        for leak in (FIGURE, FORMATTED, str(MINOR)):
            assert leak not in asked.text

    def test_Remove_Question_IsNotCachedAndOffersTheWayBack(self, served):
        asked = press_remove(served, confirmed=False)

        assert "no-store" in asked.headers.get("cache-control", "")
        assert "No, go back to the ledger" in asked.text

    def test_Remove_WhenConfirmed_RemovesIt(self, served, db):
        done = press_remove(served, confirmed=True)

        assert done.status_code == 200
        assert stated_days(db) == []
        assert f"Removed: the known balance for the end of {DAY}." in done.text

    def test_Remove_WhenTheDateIsMalformed_RefusesWithoutAsking(self, served, db):
        refused = press_remove(served, confirmed=False, day="not-a-day")

        assert refused.status_code == 400
        assert "Are you sure?" not in refused.text
        assert stated_days(db) == [DAY]

    def test_Remove_WhenConfirmedForADateNothingWasStatedFor_SaysSo(self, served, db):
        refused = press_remove(served, confirmed=True, day="2026-03-27")

        assert refused.status_code == 404
        assert "nothing was removed" in refused.text
        assert stated_days(db) == [DAY]


class TestTheRemovalIsKept:
    def test_Store_WhenAStatedBalanceIsRemoved_KeepsItsDateFigureAndTime(self, db):
        with Store(db) as store:
            from obdi.verify.balance_anchors import remove_stated_anchor

            remove_stated_anchor(store, ACCOUNT, DAY)
            kept = removed_stated_anchors(store, ACCOUNT)
            other = removed_stated_anchors(store, "other-acct")

        assert [(k.day.isoformat(), k.balance_minor, k.source) for k in kept] == [
            (DAY, MINOR, "stated")
        ]
        assert kept[0].removed_at
        assert other == []

    def test_Ledger_WhenMasked_ListsTheRemovalByDateAndNeverByFigure(self, served):
        press_remove(served, confirmed=True)

        page = httpx.get(
            f"{served}/ledger", params={"ref": ACCOUNT, "month": "2026-03"}, timeout=60
        ).text

        assert "Removed known balances" in page
        assert "The known balance for the end of <span" in page
        assert "Show values to read a removed balance back" in page
        for leak in (FIGURE, FORMATTED, str(MINOR)):
            assert leak not in page

    def test_Ledger_WhenValuesAreShown_ReadsTheFigureBackAndOffersToStateItAgain(self, served):
        press_remove(served, confirmed=True)

        page = post(served, "/ledger", ref=ACCOUNT, month="2026-03").text

        assert FORMATTED in page
        assert f"State the balance for the end of {DAY} again" in page

    def test_Ledger_AfterTheRemovedBalanceIsStatedAgain_NoLongerListsIt(self, served, db):
        press_remove(served, confirmed=True)
        post(served, "/ledger-anchor", ref=ACCOUNT, month="2026-03", day=DAY, amount=FIGURE)

        page = post(served, "/ledger", ref=ACCOUNT, month="2026-03").text

        assert stated_days(db) == [DAY]
        assert "Removed known balances" not in page

    def test_Ledger_WhenNothingWasEverRemoved_HasNoRemovedSection(self, served):
        page = httpx.get(
            f"{served}/ledger", params={"ref": ACCOUNT, "month": "2026-03"}, timeout=60
        ).text

        assert "Removed known balances" not in page

    def test_Ledger_OfAnotherAccount_DoesNotListThisAccountsRemoval(self, served):
        press_remove(served, confirmed=True)

        page = post(served, "/ledger", ref="other-acct", month="2026-03").text

        assert "Removed known balances" not in page
