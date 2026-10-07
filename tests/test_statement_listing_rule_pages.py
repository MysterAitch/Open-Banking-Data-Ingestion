"""The account page and Today say what the listing rule concluded, in plain sentences and no figure
(`agreement`, R1 to R4). Over the households of `test_statement_listing_rule_accounts`.

Decided before the first run, for the page of each account:

  r-lone          "tested by the 2 transactions its statement lists", and "1 of 1 statement adds
                  up by what it lists", and the verdict reads "adds up".
  sd-explained    the day is not offered as a conflict: "taken to have closed before", with how it
                  is known, and the several-sources day says why the figures differ.
  sd-unexplained  still "Two sources state different balances", and no "taken to have closed".
  r-unsummed      names the statement and the check: "do not add up at the statement closing on
                  2026-02-10: its opening balance and the amounts it states do not reach its
                  closing balance". (r-lone-missing's page names a movement fault from 25 January
                  instead, which is dated earlier and so is the hold: the earliest hold wins.)
  r-overlap       the measurement page says the two statements overlap and shared 2 transactions,
                  and never "money moved that neither statement lists".
Today: a statement that does not add up is an attention item at the fault band, from its first
day, naming the account and the statement.
"""

from __future__ import annotations

import re
import threading
from datetime import date
from html.parser import HTMLParser
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from obdi.account_names import AccountsShown
from obdi.balance_anchors import effective_opening
from obdi.cli import build_web_config
from obdi.core.models import TransactionStatus
from obdi.overview import NOW, standing_items_from
from obdi.standing_data import standings_for
from obdi.statement_listing_measure import StatementListingReport, statement_listing_report
from obdi.statement_listing_page import statement_listing_html
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_statement_listing_measure import FAMILIES
from test_statement_listing_rule_accounts import build
from test_statement_listing_rule_review_three import _feed, _lone

FORBIDDEN_WORDS = ("anchor", "in agreement", "held back")


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        self._skip = tag in ("style", "script")

    def handle_endtag(self, tag):
        self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.texts.append(data.strip())

    @property
    def said(self) -> str:
        return " ".join(self.texts)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("rulepages")
    path = root / "store.sqlite3"
    with Store(path) as store:
        build(store, Path(root))
        # Two lone statements whose balance by stored date is a different figure (round four).
        _lone(store, root, "y-late", date(2026, 1, 9))
        _feed(store, "y-late", date(2026, 1, 11), "Bravo y-late", 519)
        _lone(store, root, "y-pending", date(2026, 1, 5))
        _feed(
            store, "y-pending", date(2025, 12, 30), "Hold y-pending", 413,
            source="truelayer-pending", status=TransactionStatus.PENDING,
        )
        store.connection.commit()
    mp = pytest.MonkeyPatch()
    mp.setenv("OBDI_CONNECTION_STORE", str(root / "connections.json"))
    mp.delenv("OBDI_ACCOUNT_MAP", raising=False)
    config = build_web_config(path)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", path
    finally:
        httpd.shutdown()
        mp.undo()


def page_of(base: str, ref: str, month: str) -> Text:
    response = httpx.get(f"{base}/ledger", params={"ref": ref, "month": month}, timeout=60)
    assert response.status_code == 200
    parsed = Text()
    parsed.feed(response.text)
    return parsed


class TestAStatementBalanceThatPartsFromTheBalanceByDate:
    """Round four, finding 1: said on the balance's own line, in counts and dates only."""

    def test_Page_WhenAPendingTransactionIsDatedInsideItsDays_NamesItWithoutTheSize(self, served):
        said = page_of(served[0], "y-pending", "2025-12").said

        assert (
            "By date the balance at the end of 2026-01-10 is a different figure, because "
            "1 transaction dated on or before it is not listed by this statement "
            "(1 pending, 0 listed by a later statement). The statement is tested by what it "
            "lists, not by date."
        ) in said

    def test_Page_WhenAListedPurchaseIsHeldUnderALaterDate_NamesItWithoutTheSize(self, served):
        said = page_of(served[0], "y-late", "2026-01").said

        assert (
            "because 1 transaction it lists is dated after it. The statement is tested by what "
            "it lists, not by date."
        ) in said

    def test_Pages_ThatSayTheDifference_HoldNoFigureInTextOrAttributes(self, served):
        base, _ = served
        for ref, month in (("y-pending", "2025-12"), ("y-late", "2026-01")):
            html = httpx.get(f"{base}/ledger", params={"ref": ref, "month": month}, timeout=60).text
            for figure in ("117.56", "11756", "4.13", "413", "5.19", "519", "121.69", "12169"):
                assert figure not in html, (ref, figure)


class TestNoPageTheRuleTouchesHoldsAFigure:
    def test_Get_ForTodayAccountsChecksIdentityHealthAndStatementPeriods_HoldsNoStatedFigure(
        self, served
    ):
        base, _ = served
        figures = (
            "117.56", "11756", "121.69", "12169", "110.48", "11048", "118.25", "11825",
            "100.00", "10000", "7.77", "777", "4.13", "5.19", "12.37",
        )
        for route in ("/", "/accounts", "/checks", "/identity-health", "/period-reconciliation"):
            response = httpx.get(f"{base}{route}", timeout=60)
            assert response.status_code == 200, route
            visible = Text()
            visible.feed(response.text)
            attributes = " ".join(
                value
                for tag in re.findall(r"<[^>]+>", response.text)
                for value in re.findall(r'="([^"]*)"', tag)
            )
            for figure in figures:
                assert figure not in visible.said, (route, figure)
                assert figure not in attributes, (route, figure, "in an attribute")


class TestTheAccountPage:
    def test_Page_WhenALoneStatementAddsUpByWhatItLists_SaysHowItIsTested(self, served):
        said = page_of(served[0], "r-lone", "2026-01").said

        assert "tested by the 2 transactions its statement lists" in said
        assert "1 of 1 statement adds up by what it lists" in said
        assert "adds up" in said

    def test_Page_WhenALoneStatementAddsUp_TellsOneStoryAndNotTheOldWarnings(self, served):
        said = page_of(served[0], "r-lone", "2026-01").said

        assert "Known balances (1 add up, none differ)" in said
        assert "absorbs every missing or surplus transaction" not in said
        assert "is tested by its own statement" in said
        # The days its statement tests count as adding up, to its closing day; the trust
        # sentence says that, and the strip draws the days before the statement as held only.
        assert "Adds up to the known balances to 2026-01-10" in said

    def test_Page_WhenAStatementIsTakenToHaveClosedBeforeATransaction_SaysWhyAndHowItIsKnown(
        self, served
    ):
        said = page_of(served[0], "sd-explained", "2026-02").said

        assert "taken to have closed before" in said
        assert "strong evidence and not proof" in said
        assert "The figures differ, and the statement is taken to have closed before" in said
        assert "Two sources state different balances" not in said

    def test_Page_WhenNothingExplainsTheDifference_StillSaysTheSourcesConflict(self, served):
        said = page_of(served[0], "sd-unexplained", "2026-02").said

        assert "Two sources state different balances" in said
        assert "taken to have closed" not in said

    def test_Page_WhenAStatementDoesNotAddUp_NamesTheStatementAndTheCheck(self, served):
        # r-lone-missing: an earlier movement fault is the account's hold; the statement's own
        # fault is still said, with the way out.
        said = page_of(served[0], "r-lone-missing", "2026-02").said

        assert (
            "The statement closing on 2026-02-10 does not add up by what it lists: a "
            "transaction it lists is not held."
        ) in said
        assert "disregard it under known balances" in said

    def test_Page_WhenTheReaderRefusedAStatement_BlamesTheReadingAndNotTheTransactions(
        self, served
    ):
        said = page_of(served[0], "r-unsummed", "2026-02").said

        assert "could not supply a balance" in said
        assert "does not add up by what it lists" not in said

    def test_Pages_ForTheStatesTheRuleAdds_UseNoRetiredWordAndHoldNoClosingFigure(self, served):
        for ref, month in (
            ("r-lone", "2026-01"),
            ("sd-explained", "2026-02"),
            ("r-lone-missing", "2026-02"),
        ):
            said = page_of(served[0], ref, month).said.lower()
            for word in FORBIDDEN_WORDS:
                assert word not in said, (ref, word)
            # An opening of 100.00 owed and a typed balance 7.77 away: neither is on the page.
            for figure in ("100.00", "7.77", "10000", "777"):
                assert figure not in said, (ref, figure)


class TestTheMeasurementPage:
    def test_Page_WhenStatementsOverlap_SaysSoAndNeverThatMoneyMoved(self, served):
        _, path = served
        with Store(path) as store:
            listing = {
                a.account: a for a in statement_listing_report(store, FAMILIES).accounts
            }
        page = Text()
        page.feed(
            statement_listing_html(
                StatementListingReport([listing["r-overlap"]]), AccountsShown()
            )
        )

        assert "shares 2 transactions with the statement before it" in page.said
        assert "money moved that neither" not in page.said

    def test_Page_WhenTheRuleDoesNotTakeTheClaim_SaysTheDayIsStillHeldAsAConflict(
        self, tmp_path_factory
    ):
        # The reviewer's `v-cannot-say`: the other balance differs by exactly the unlisted
        # purchase, but the statement cannot say what it lists, so the account page keeps the
        # conflict and Identity health must not say the two balances do not contradict.
        from test_statement_listing_rule_review import build as review_build

        root = tmp_path_factory.mktemp("held-conflict")
        with Store(root / "s.sqlite3") as store:
            review_build(store, root)
            listing = {
                a.account: a for a in statement_listing_report(store, FAMILIES).accounts
            }
        (only,) = listing["v-cannot-say"].statements
        page = Text()
        page.feed(
            statement_listing_html(
                StatementListingReport([listing["v-cannot-say"]]), AccountsShown()
            )
        )

        assert only.day_conflict is not None and only.day_conflict.refused
        assert "still holds the day as a conflict" in page.said
        assert "do not contradict each other" not in page.said


class TestToday:
    def test_Today_WhenAStatementDoesNotAddUp_RaisesAFaultNamingTheStatement(self, served):
        _, path = served
        with Store(path) as store:
            standings = standings_for(
                store, ["r-lone-missing", "r-lone"], families=FAMILIES, movement=None
            )
            assert effective_opening(store, "r-lone", families=FAMILIES) is not None
        items = standing_items_from(
            standings, lambda ref: ref, lambda ref: False, __import__("datetime").date(2026, 3, 1)
        )

        faults = [i for i in items if i.kind == "statement-fault"]
        assert [i.accounts for i in faults] == [("r-lone-missing",)]
        assert faults[0].severity == NOW
        assert "statement closing on 2026-02-10" in faults[0].message
