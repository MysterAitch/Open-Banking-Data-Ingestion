"""Where a person looking at a wrong Starling balance finds the family walk.

The household and its hand working are in test_family_anchors: one main account,
the Bills Space, and a certified statement that prints the WHOLE account's
balance. Here the same household is served by the application's own
configuration (`build_web_config`), so the hooks under test are the ones a
browser reaches.

Two worlds. In the healthy one every row is right. In the faulted one the
garage payment of 90.00 (9000) on the 22nd has been lost from the store, so the
family's rows stop reproducing the stated balance at the end of the 22nd, the
last day they agreed is the 20th, and the difference (-9000) is the same at
every later balance because one movement is missing.

The rule of both pages: every GET is masked, whatever its query string, and a
figure appears only in the direct response to a POST of the "Show values" form,
which is marked not to be kept.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.actual_push import opening_balances
from obdi.balance_anchors import record_stated_anchor
from obdi.cli import build_web_config
from obdi.family_anchors import families_of
from obdi.replay import ActualAccountBinding
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_family_anchors import (
    STATEMENT,
    drop_row,
    feed_rows,
    import_statement,
)
from test_ledger import land, txn
from test_space_attribution import BILLS, HOLIDAY, MAIN, MAP, Household

#: Every figure the world makes that a masked page must not show, in pounds and
#: pence and in minor units: the family's balances, main's, the Space's, main's
#: opening, and the difference.
SECRET_FIGURES = (
    "3,775.00", "377500", "3,725.00", "372500", "3,655.00", "365500",
    "3,625.00", "362500", "3,535.00", "353500", "4,735.00", "473500",
    "3,375.00", "337500", "4,395.00", "439500", "800.00", "80000",
    "340.00", "34000", "90.00", "9000",
)


def serve(tmp_path, monkeypatch, *, faulted: bool):
    db = tmp_path / "family.sqlite3"
    with Store(db) as store:
        for space in (BILLS, HOLIDAY):
            store.declare_account(
                AccountRecord(ref=AccountRef(space), kind="starling-space", parent=AccountRef(MAIN))
            )
        Household(store, MAP).arrive(*feed_rows())
        import_statement(store, tmp_path, STATEMENT)
        if faulted:
            drop_row(store, MAIN, -9000, 22)
    account_map = tmp_path / "accounts.json"
    account_map.write_text(
        json.dumps(
            {
                "bindings": [
                    {"canonical_id": MAIN, "source": "starling", "provider_account_id": "acc-main"},
                    {
                        "canonical_id": BILLS,
                        "source": "starling",
                        "provider_account_id": "cat-bills",
                    },
                    {
                        "canonical_id": HOLIDAY,
                        "source": "starling",
                        "provider_account_id": "cat-holiday",
                    },
                    {"canonical_id": MAIN, "source": "truelayer", "provider_account_id": "tl-main"},
                ],
                "actual": [{"canonical_id": MAIN, "actual_account_id": "act-main"}],
            }
        ),
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
    return httpd, db


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def ledger(self, ref: str = MAIN) -> httpx.Response:
        return httpx.get(f"{self.base}/ledger", params={"ref": ref, "month": "2026-09"}, timeout=30)

    def ledger_values(self, ref: str = MAIN) -> httpx.Response:
        return httpx.post(
            f"{self.base}/ledger", data={"ref": ref, "month": "2026-09"}, timeout=30
        )

    def position(self) -> httpx.Response:
        return httpx.get(f"{self.base}/position", timeout=30)


@pytest.fixture
def healthy(tmp_path, monkeypatch):
    httpd, db = serve(tmp_path, monkeypatch, faulted=False)
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


@pytest.fixture
def faulted(tmp_path, monkeypatch):
    httpd, db = serve(tmp_path, monkeypatch, faulted=True)
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


def assert_no_secret(page: str) -> None:
    for figure in SECRET_FIGURES:
        assert figure not in page, figure


class TestTheMainAccountsLedger:
    def test_Section_SaysWhatAFamilyBalanceIsInOneSentence(self, healthy):
        page = healthy.ledger().text

        assert "The whole account's stated balances" in page
        assert (
            "A family balance is one stated by a source that cannot see this account's "
            "Spaces" in page.replace("&#x27;", "'")
        )

    def test_Section_StatesTheAssumptionThatEverySpaceStartsFromNil(self, healthy):
        page = healthy.ledger().text.replace("&#x27;", "'")

        assert "assumes every Space's rows start from nil" in page

    def test_Section_WhenEveryRowIsRight_SaysTheRowsReproduceEveryBalance(self, healthy):
        page = healthy.ledger().text

        assert "reproduce every later whole-account balance stated" in page
        assert "first stop reproducing" not in page
        assert "starling-statement-pdf" in page

    def test_Section_WhenARowIsMissing_NamesTheTwoDatesAndSaysTheDifferenceIsConstant(
        self, faulted
    ):
        page = faulted.ledger().text

        assert "first stop reproducing the stated balance at the end of" in page
        assert "2026-09-22" in page
        assert "they last agreed at the end of" in page
        assert "2026-09-20" in page
        assert "same at every later balance, so one movement is missing or surplus" in page

    def test_MaskedPage_ShowsNoFigureOfTheFamilyOrTheDifference(self, faulted):
        assert_no_secret(faulted.ledger().text)

    def test_MaskedPage_StaysMaskedWhateverTheQueryStringAsks(self, faulted):
        page = httpx.get(
            f"{faulted.base}/ledger",
            params={"ref": MAIN, "month": "2026-09", "values": "1", "unmasked": "true"},
            timeout=30,
        ).text

        assert_no_secret(page)

    def test_ShowValues_AnswersWithTheDifferenceAndIsNotToBeKept(self, faulted):
        response = faulted.ledger_values()

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert "£90.00" in response.text

    def test_Section_IsAbsentFromAnAccountWithNoKnownSpaces(self, healthy):
        # The Bills Space is itself a Space: it has none of its own.
        page = healthy.ledger(BILLS).text

        assert "The whole account's stated balances" not in page

    def test_Anchors_ListMainsOwnFamilyDerivedBalancesNotTheStatementsClosingFigure(
        self, healthy
    ):
        page = healthy.ledger().text.replace("&#x27;", "'")

        assert "the whole account's stated balance, less its Spaces' own rows" in page
        assert "a held statement's closing balance" not in page


class TestThePositionPage:
    def test_LaterBalanceChecks_WhenARowIsMissing_NamesTheFirstDifferingDay(self, faulted):
        page = faulted.position().text.replace("&#x27;", "'")

        assert "first stop matching the rows on 2026-09-22" in page
        assert "the difference is constant after that" in page

    def test_LaterBalanceChecks_WhenEveryRowIsRight_SaysTheWholeAccountWasReproduced(
        self, healthy
    ):
        page = healthy.position().text

        assert "7 stated balances, all reproduced" in page
        assert "first stop matching" not in page

    def test_MaskedPage_ShowsNoFigureOfTheFamilyOrTheDifference(self, faulted):
        assert_no_secret(faulted.position().text)


class TestThePushToActual:
    """Item 6 of the brief: nothing changes for any other account."""

    HALIFAX = "halifax-current"

    def test_MainsOpening_IsDerivedFromTheFamily_AndOtherAccountsAreUnmoved(
        self, tmp_path
    ):
        with Store(tmp_path / "push.sqlite3") as store:
            home = Household(store, MAP)
            home.arrive(*feed_rows())
            import_statement(store, tmp_path, STATEMENT)
            land(
                store,
                "digest-halifax",
                txn(self.HALIFAX, "src-a", "h1", date(2026, 9, 2), -100, "X"),
            )
            record_stated_anchor(store, self.HALIFAX, "2026-09-10", "250.00")
            bindings = [
                ActualAccountBinding(MAIN, "act-main"),
                ActualAccountBinding(self.HALIFAX, "act-halifax"),
            ]

            without = {o.canonical_id: o.amount_minor for o in opening_balances(store, bindings)}
            with_families = {
                o.canonical_id: o.amount_minor
                for o in opening_balances(
                    store, bindings, families=families_of(store, MAP)
                )
            }

        # Measured before the change: main's opening was the true 80000 plus
        # the 34000 the Bills Space held at the statement's closing.
        assert without[MAIN] == 80000 + 34000
        assert with_families[MAIN] == 80000
        assert with_families[self.HALIFAX] == without[self.HALIFAX] == 25000 + 100
