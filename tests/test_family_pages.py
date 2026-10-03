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
from contextlib import contextmanager
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
    land_evidence,
    leg,
    opened_feed_rows,
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
    "47.00", "4700", "58.00", "5800", "11.00", "1100", "25.00", "2500",
)


def serve(
    tmp_path,
    monkeypatch,
    *,
    faulted: bool,
    evidence: dict[str, str | None] | None = None,
    drop: tuple[tuple[str, int, int], ...] = (),
    extra: tuple = (),
):
    """The household over the application's own configuration.

    `evidence` lands the provider's creation date and feed requests (the
    household then has its opening deposit, so the family really opened at nil);
    `drop` loses rows; `extra` adds rows."""
    db = tmp_path / "family.sqlite3"
    with Store(db) as store:
        for space in (BILLS, HOLIDAY):
            store.declare_account(
                AccountRecord(ref=AccountRef(space), kind="starling-space", parent=AccountRef(MAIN))
            )
        Household(store, MAP).arrive(
            *(opened_feed_rows() if evidence is not None else feed_rows()), *extra
        )
        import_statement(store, tmp_path, STATEMENT)
        if evidence is not None:
            land_evidence(store, **evidence)
        if faulted:
            drop_row(store, MAIN, -9000, 22)
        for account, minor, day in drop:
            drop_row(store, account, minor, day)
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


@contextmanager
def lab(tmp_path, monkeypatch, **world):
    httpd, db = serve(tmp_path, monkeypatch, **world)
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


@pytest.fixture
def healthy(tmp_path, monkeypatch):
    with lab(tmp_path, monkeypatch, faulted=False) as served:
        yield served


@pytest.fixture
def opened(tmp_path, monkeypatch):
    """The family opened at nil: creation date held, feed reaching back to it."""
    with lab(tmp_path, monkeypatch, faulted=False, evidence={}) as served:
        yield served


@pytest.fixture
def opened_with_an_early_fault(tmp_path, monkeypatch):
    """The cafe on the 4th is lost: before the statement's first balance."""
    with lab(
        tmp_path, monkeypatch, faulted=False, evidence={}, drop=((MAIN, -2500, 4),)
    ) as served:
        yield served


@pytest.fixture
def feed_starts_late(tmp_path, monkeypatch):
    with lab(
        tmp_path,
        monkeypatch,
        faulted=False,
        evidence={"asked_from": "2026-09-05T00:00:00Z"},
    ) as served:
        yield served


@pytest.fixture
def no_creation_date(tmp_path, monkeypatch):
    with lab(tmp_path, monkeypatch, faulted=False, evidence={"created": None}) as served:
        yield served


@pytest.fixture
def unheld_space(tmp_path, monkeypatch):
    """Two transfer legs to a Space the store holds no rows for."""
    extra = (
        leg(MAIN, -4700, 8, "cat-closed", "f-gone-1"),
        leg(MAIN, -1100, 18, "cat-closed", "f-gone-2"),
    )
    with lab(tmp_path, monkeypatch, faulted=False, evidence={}, extra=extra) as served:
        yield served


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


ABSORBED = "is absorbed into the opening and cannot be seen"
NIL_SENTENCE = (
    "The account's history is held from its opening on 2026-09-01, so the opening is nil "
    "and every stated balance is tested."
)


def ledger_text(served: Lab) -> str:
    return served.ledger().text.replace("&#x27;", "'")


def position_text(served: Lab) -> str:
    return served.position().text.replace("&#x27;", "'")


class TestWhetherTheOpeningIsKnownToBeNil:
    def test_Ledger_WhenCreationAndFeedReachAreHeld_SaysTheOpeningIsNilAndNeverSaysAbsorbed(
        self, opened
    ):
        page = ledger_text(opened)

        assert NIL_SENTENCE in page
        assert ABSORBED not in page
        assert "Opened, with a nil balance at the end of" in page
        assert "2026-08-31" in page

    def test_Ledger_WhenCreationAndFeedReachAreHeld_AnEarlyFaultIsCaughtAndDated(
        self, opened_with_an_early_fault
    ):
        page = ledger_text(opened_with_an_early_fault)

        assert "first stop reproducing the stated balance at the end of" in page
        assert "2026-09-11" in page
        assert "they last agreed at the end of" in page
        assert "2026-08-31" in page
        assert ABSORBED not in page

    def test_Ledger_WhenTheFeedStartsAfterTheCreation_SaysTheFeedDoesNotReachBack(
        self, feed_starts_late
    ):
        page = ledger_text(feed_starts_late)

        assert (
            "the feed does not reach back to the opening: the earliest request held asked "
            "from 2026-09-05 and the account opened on 2026-09-01"
        ) in page
        assert "A fault dated before 2026-09-11 " + ABSORBED in page
        assert NIL_SENTENCE not in page

    def test_Ledger_WhenNoCreationDateIsHeld_KeepsTheAbsorbedSentenceAndSaysWhy(
        self, no_creation_date
    ):
        page = ledger_text(no_creation_date)

        assert "no creation date is held for the account" in page
        assert "A fault dated before 2026-09-11 " + ABSORBED in page
        assert NIL_SENTENCE not in page

    def test_Ledger_WithNoEvidenceLanded_KeepsTheAbsorbedSentence(self, healthy):
        page = ledger_text(healthy)

        assert "A fault dated before 2026-09-11 " + ABSORBED in page

    def test_Ledger_WhenOpenedAtNil_ListsTheOpeningAnchorWithoutTheSingleAnchorWarning(
        self, opened
    ):
        page = ledger_text(opened)

        assert "The account opened with nothing" in page
        assert "An opening derived from a single anchor" not in page
        assert "the day before the account was created" in page

    def test_Position_WhenOpenedAtNil_SaysTheOpeningIsNil(self, opened):
        page = position_text(opened)

        assert NIL_SENTENCE in page
        assert "7 stated balances, all reproduced" in page
        assert ABSORBED not in page

    def test_Position_WithoutTheAnchor_SaysAFaultBeforeTheFirstBalanceIsAbsorbed(self, healthy):
        page = position_text(healthy)

        assert "A fault dated before 2026-09-11 " + ABSORBED in page
        assert NIL_SENTENCE not in page

    @pytest.mark.parametrize(
        "world",
        ["opened", "opened_with_an_early_fault", "feed_starts_late", "no_creation_date"],
    )
    def test_MaskedPages_InEveryWorld_ShowNoFigure(self, request, world):
        served = request.getfixturevalue(world)

        assert_no_secret(served.ledger().text)
        assert_no_secret(served.position().text)


class TestSpaceTransfersWithNoHeldOtherLeg:
    def test_Ledger_WhenLegsGoToAnUnheldSpace_SaysHowManyWhenFirstAndTheRemedy(
        self, unheld_space
    ):
        page = ledger_text(unheld_space)

        assert "2 transfer leg(s) go to or from a Space whose own rows are not held" in page
        assert "the first on" in page
        assert "2026-09-08" in page
        assert "The whole account cannot balance until that Space is recovered" in page
        assert "recover-spaces" in page
        assert 'href="/spaces"' in page

    def test_Ledger_WhenLegsGoToAnUnheldSpace_TheWalkDiffersFromTheFirstAnchorAfterThem(
        self, unheld_space
    ):
        page = ledger_text(unheld_space)

        assert "first stop reproducing the stated balance at the end of" in page
        assert "2026-09-11" in page

    def test_Ledger_WhenEveryLegIsHeld_SaysNothingOfUnheldSpaces(self, opened):
        page = ledger_text(opened)

        assert "go to or from a Space whose own rows are not held" not in page

    def test_MaskedLedger_WhenLegsAreUnheld_ShowsNoFigure(self, unheld_space):
        assert_no_secret(unheld_space.ledger().text)
        assert_no_secret(unheld_space.position().text)
