"""How often each exact rule holds, as the Identity health page states it.

The household is the late-settlement corpus with an aggregator that states the feed's
own uid, as a real one does (58 of 58 items of a real month did).
Every name and amount is invented, and each known answer was worked out by hand
before the first run.

KNOWN ANSWERS, the household landed feed, then export, then aggregator:

    six aggregator items
        four state the uid of a feed item and agree with it on size and direction:
            one to the second, one 40 seconds off, one three hours off, one two days off
        one states an id no feed item has, and one states none
    twelve export rows
        three have exactly one feed item of their size that settled on their day,
        two (a pair of equal payments settled together) have two, and seven have none
    the store, matching by amount and window as it did before these rules, holds
        all four joined pairs as one row each, and all three single-candidate export rows
        as one row each

THE PLANTED FAULTS. The matcher cannot be made to produce a wrong or a missed join
over invented data without also being the thing under test, so these are PLANTED: the
payloads are landed and parsed by the real providers, and the rows are written through
the store's own write methods, with the sightings put where the fault would have put them.
    two payments of one size to one payee, minutes apart, whose aggregator sightings are
    swapped between their rows (planted): the id proves both are on the wrong row
    a payment whose feed row and aggregator row were never joined (planted)
    a payment joined correctly, as the control
"""

from __future__ import annotations

import json
import pathlib
import threading
from dataclasses import replace
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from late_settlement_corpus import Payment, aggregator_item, household
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import parse_artefact_transactions
from obdi.ingest.store import Store
from obdi.verify.exact_rule_measure import exact_rule_report
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP

FEED_FIRST = ("feed", "export", "aggregator")

NO_ID = {"provider_transaction_id": None, "meta": None}
UNKNOWN_ID = {"provider_transaction_id": "f-nowhere", "meta": {"provider_id": "f-nowhere"}}


def household_payments() -> list[Payment]:
    settled = "2026-09-15T03:00:00.000Z"
    day = date(2026, 9, 15)
    return [
        Payment("f-bakery", "Bakery", 520, "2026-09-14T10:20:00.000Z", settled, day, 14,
                {"timestamp": "2026-09-14T10:20:00Z"}),
        Payment("f-tailor", "Tailor", 640, "2026-09-14T10:21:00.000Z", settled, day, 14,
                {"timestamp": "2026-09-14T10:21:40Z"}),
        Payment("f-florist", "Florist", 1150, "2026-09-14T10:22:00.000Z", settled, day, 14,
                {"timestamp": "2026-09-14T13:22:00Z"}),
        Payment("f-cobbler", "Cobbler", 905, "2026-09-14T10:23:00.000Z", None, None, 16,
                {"timestamp": "2026-09-16T10:23:00Z"}),
        Payment("f-hatter", "Hatter", 777, "2026-09-14T10:24:00.000Z", None, None, 14,
                UNKNOWN_ID, in_feed=False),
        Payment("f-locksmith", "Locksmith", 410, "2026-09-14T10:25:00.000Z", None, None, 14,
                NO_ID, in_feed=False),
        Payment("f-twin-1", "Twin", 2000, "2026-09-14T10:30:00.000Z",
                "2026-09-19T03:00:00.000Z", date(2026, 9, 19), None),
        Payment("f-twin-2", "Twin", 2000, "2026-09-14T10:31:00.000Z",
                "2026-09-19T03:00:00.000Z", date(2026, 9, 19), None),
    ]


@pytest.fixture
def made(tmp_path):
    opened: list[Store] = []

    def build(*args, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        opened.append(household(directory, *args, **kwargs))
        return opened[-1]

    yield build
    for store in opened:
        store.close()


def figures_of(store: Store, account: str = MAIN):
    (found,) = [f for f in exact_rule_report(store, MAP).accounts if f.account == account]
    return found


class TestTheHousehold:
    def test_AggregatorItems_WhenTheyStateTheFeedsUid_AreCountedByHowTheyAgree(self, made):
        figures = figures_of(made(FEED_FIRST, household_payments(), linked=True))

        assert figures.aggregator_items == 6
        assert figures.linked == 4
        assert figures.agree_on_size == 4
        assert (
            figures.same_second,
            figures.within_a_minute,
            figures.within_a_day,
            figures.further_apart,
        ) == (1, 1, 1, 1)
        assert figures.unknown_id == 1
        assert figures.no_id == 1

    def test_AggregatorItems_WhenMostStateNoUid_AreCountedAsHavingNone(self, made):
        figures = figures_of(made(FEED_FIRST, household_payments()))

        # The hatter's item states an id of its own whatever the household does,
        # so it is the one that is not "none".
        assert figures.aggregator_items == 6
        assert figures.linked == 0
        assert (figures.no_id, figures.unknown_id) == (5, 1)

    def test_Store_WhenMatchedByAmountAndWindow_HoldsEveryJoinedPairAsOneRow(self, made):
        figures = figures_of(made(FEED_FIRST, household_payments(), linked=True))

        assert (figures.held_as_one_row, figures.held_as_two_rows) == (4, 0)
        assert figures.on_another_feed_uid == 0

    def test_ExportRows_WhenTheFeedStatesSettlement_AreCountedByHowManyItsDayNames(self, made):
        figures = figures_of(made(FEED_FIRST, household_payments(), linked=True))

        assert figures.export_rows == 12
        assert (figures.settled_one, figures.settled_several, figures.settled_none) == (3, 2, 7)
        assert figures.settled_one_held_as_one_row == 3

    def test_Report_WhenAnAccountHasNoAggregator_ListsNothingForIt(self, made):
        report = exact_rule_report(made(FEED_FIRST, [Payment(
            "f-solo", "Solo", 300, "2026-09-14T10:20:00.000Z", None, None, None
        )]), MAP)

        assert report.accounts == []
        assert "No account is fed by both" in report.describe()

    def test_Report_ReadsTheArtefactsAndNotTheRows_SoARowlessStoreStillCountsThem(
        self, made
    ):
        store = made(FEED_FIRST, household_payments(), linked=True)
        rows_before = len(store.all_transactions())

        figures = figures_of(store)

        assert rows_before > 0
        assert figures.linked == 4


def land(store: Store, feed_items, aggregator_items):
    feed = starling.artefact_for(
        json.dumps({"feedItems": feed_items}).encode(),
        account_id="starling:cat-main",
        kind="feed",
        origin=f"{FEED_ORIGIN}?changesSince=2026-09-02T00:00:00Z",
    )
    aggregator = truelayer.artefact_for(
        json.dumps({"results": aggregator_items}).encode(), account_id="tl-main", kind="booked"
    )
    for artefact in (feed, aggregator):
        store.land_artefact(artefact)
    read = {
        artefact.source: {
            t.source_id: t
            for t in parse_artefact_transactions(
                artefact.source, artefact.payload, MAIN, artefact.digest
            )
        }
        for artefact in (feed, aggregator)
    }
    return read["starling-feed"], read["truelayer-booked"]


def hold(store: Store, transaction, entity: str, *, founding: bool) -> None:
    placed = replace(transaction, entity_id=entity)
    if founding:
        store.upsert_transaction(placed, match_tier="unresolved")
    store.record_source(placed)


@pytest.fixture
def planted(tmp_path):
    store = Store(tmp_path / "planted.sqlite3")
    land_evidence(store)
    payments = [
        Payment("f-a", "Garage", 3000, "2026-09-14T10:00:00.000Z", None, None, 14),
        Payment("f-b", "Garage", 3000, "2026-09-14T10:05:00.000Z", None, None, 14),
        Payment("f-c", "Printer", 1500, "2026-09-15T10:00:00.000Z", None, None, 15),
        Payment("f-d", "Kettle", 800, "2026-09-16T10:00:00.000Z", None, None, 16),
    ]
    feed, aggregator = land(
        store,
        [p.feed_item() for p in payments],
        [aggregator_item(p, link=True) for p in payments],
    )
    for uid in ("f-a", "f-b", "f-c", "f-d"):
        hold(store, feed[uid], f"e-{uid}", founding=True)
    # Planted: each aggregator sighting sits on the OTHER equal payment's row.
    hold(store, aggregator["tl-f-b"], "e-f-a", founding=False)
    hold(store, aggregator["tl-f-a"], "e-f-b", founding=False)
    # Planted: never joined, so the aggregator's own row stands beside the feed's.
    hold(store, aggregator["tl-f-c"], "e-agg-c", founding=True)
    # The control: joined correctly.
    hold(store, aggregator["tl-f-d"], "e-f-d", founding=False)
    store.connection.commit()
    yield store
    store.close()


class TestPlantedFaults:
    def test_Pair_WhenTheAggregatorSightingsAreSwappedBetweenEqualPayments_BothAreOnTheWrongRow(
        self, planted
    ):
        figures = figures_of(planted)

        assert figures.on_another_feed_uid == 2

    def test_Pair_WhenTheTwoReportsWereNeverJoined_IsHeldAsTwoRowsWithoutBeingWrong(
        self, planted
    ):
        figures = figures_of(planted)

        assert figures.aggregator_items == 4
        assert figures.linked == 4
        assert figures.held_as_two_rows == 3
        assert figures.on_another_feed_uid == 2

    def test_Pair_WhenJoinedCorrectly_IsTheOnlyOneHeldAsOneRow(self, planted):
        assert figures_of(planted).held_as_one_row == 1


def serve(**hooks) -> HTTPServer:
    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(pathlib.Path("unused.json")),
        **hooks,
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class TestThePage:
    def test_Page_WhenTheHouseholdIsMeasured_StatesEachFigureAndShowsNoMoneyOrPayee(self, made):
        store = made(FEED_FIRST, household_payments(), linked=True)
        text = exact_rule_report(store, MAP).describe()
        httpd = serve(identity_health_text=lambda: "identity report", exact_rules_text=lambda: text)
        try:
            page = httpx.get(f"http://127.0.0.1:{httpd.server_port}/identity-health").text
        finally:
            httpd.shutdown()

        assert f"{MAIN}:" in page
        assert "The aggregator listed 6 items in all." in page
        assert "4 carry a provider id that is the own id of a landed feed item." in page
        assert "Of those, 1 agree on the instant to the second." in page
        assert "Of the 4 joined pairs, the store already holds 4 as one stored row." in page
        assert "The export listed 12 rows in all." in page
        for hidden in ("Bakery", "Hatter", "Locksmith", "5.20", "520", "f-bakery"):
            assert hidden not in page

    def test_WebHook_BuiltFromTheRealConfiguration_ReportsAnEmptyStoreInWords(
        self, tmp_path, monkeypatch
    ):
        from obdi.cli import build_web_config

        db = tmp_path / "hook.sqlite3"
        with Store(db):
            pass
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            monkeypatch.delenv(variable, raising=False)

        config = build_web_config(db)

        assert config is not None
        assert config.exact_rules_text is not None
        assert "No account is fed by both" in config.exact_rules_text()

    def test_Page_WhenNoExactRuleHookIsWired_HasNoSuchSection(self):
        httpd = serve(identity_health_text=lambda: "identity report")
        try:
            page = httpx.get(f"http://127.0.0.1:{httpd.server_port}/identity-health").text
        finally:
            httpd.shutdown()

        assert "Exact rules" not in page
