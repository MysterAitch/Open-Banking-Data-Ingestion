"""The movement checks, as the Identity health page and the Overview show them.

The household is the round-up corpus with a Space-blind export that lists a
payment of an invented, unmistakable size and payee. One of the export's
sightings is then removed, which leaves every balance agreeing and the record
one row short: the case a balance cannot see.

KNOWN ANSWERS:

    (i)   the household as landed
          the page names no fault, and the Overview raises nothing for it
    (ii)  the planted payment's sighting removed
          the page says "1 row of one size and direction listed, 0 held" with
          the date, account, and source, and shows neither the size nor the payee
          the Overview raises one item of the first band, linked to the page
    (iii) the check raising an error
          the page says so (a 500) instead of showing a pass
    (iv)  no movement hook wired
          the page is the identity report alone, as before
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from http.server import HTTPServer

import httpx
import pytest

from obdi.ingest.connections import ConnectionStore
from obdi.ingest.store import Store
from obdi.overview import NOW, build_overview
from obdi.verify.movement_completeness import (
    ChainFault,
    MovementCompleteness,
    movement_completeness,
)
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from round_up_corpus import main_feed, space_feed
from test_export_cuts import Row
from test_movement_rows_listed import EXPORT, drop_sighting, export_sightings
from test_space_attribution import MAP
from test_space_blind_rows_and_internal_legs import BASE_EXPORT, ORDERS, corpus

PRIVATE_MINOR = 4217
PRIVATE_PAYEE = "Zebra Crossing Cafe"
DAY = 9


def canonical(ref: str) -> str:
    return str(MAP.resolve(*ref.split(":", 1))) if ":" in ref else ref


@pytest.fixture
def store(tmp_path):
    with corpus(
        tmp_path,
        ORDERS[0],
        main=main_feed(),
        space=space_feed(),
        export_rows=[*BASE_EXPORT, Row(PRIVATE_PAYEE, -PRIVATE_MINOR, DAY, DAY)],
        aggregator=[],
    ) as opened:
        yield opened


def serve(tmp_path, **hooks) -> HTTPServer:
    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        **hooks,
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def fetch(httpd: HTTPServer) -> httpx.Response:
    try:
        return httpx.get(f"http://127.0.0.1:{httpd.server_port}/identity-health")
    finally:
        httpd.shutdown()


def plant_the_fault(store: Store) -> None:
    (sighting,) = export_sightings(store, -PRIVATE_MINOR, DAY)
    drop_sighting(store, sighting["entity_id"])


def overview_of(store: Store):
    return build_overview(
        store,
        now=datetime(2026, 10, 1, 14, 2, tzinfo=UTC),
        findings=lambda: [],
        canonical_for_ref=canonical,
        watched=set(),
        actual_bound=None,
        rebuild_status={},
    )


class TestThePage:
    def test_Page_WhenARowIsMissing_NamesTheDayAccountAndSourceAndNoFigureOrPayee(
        self, tmp_path, store
    ):
        plant_the_fault(store)
        report = movement_completeness(store, canonical).describe()

        page = fetch(
            serve(
                tmp_path,
                identity_health_text=lambda: "identity report",
                movement_completeness_text=lambda: report,
            )
        ).text

        assert (
            f"2026-09-{DAY:02} starling-personal via {EXPORT} (out): "
            "1 row of one size and direction listed, 0 held"
        ) in page
        for hidden in (str(PRIVATE_MINOR), "42.17", PRIVATE_PAYEE, "Zebra"):
            assert hidden not in page

    def test_Page_WhenEveryMovementIsAccountedFor_SaysEachCheckPassed(self, tmp_path, store):
        report = movement_completeness(store, canonical).describe()

        page = fetch(
            serve(
                tmp_path,
                identity_health_text=lambda: "identity report",
                movement_completeness_text=lambda: report,
            )
        ).text

        assert "every listed transaction is held once" in page
        assert "every leg has its partner" in page
        assert "both sides match every day" in page

    def test_Page_WhenTheMovementCheckFails_SaysSoRatherThanShowingAPass(self, tmp_path):
        def broken() -> str:
            raise RuntimeError("the store would not open")

        response = fetch(
            serve(
                tmp_path,
                identity_health_text=lambda: "identity report",
                movement_completeness_text=broken,
            )
        )

        assert response.status_code == 500
        assert "the store would not open" in response.text

    def test_Page_WhenNoMovementHookIsWired_IsTheIdentityReportAlone(self, tmp_path):
        page = fetch(serve(tmp_path, identity_health_text=lambda: "identity report")).text

        assert "identity report" in page
        assert "Every transaction a source lists" not in page

    def test_WebHook_BuiltFromTheRealConfiguration_ReturnsTheSameReport(
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
        assert config.movement_completeness_text is not None
        assert (
            "Every transaction a source lists is held once"
            in config.movement_completeness_text()
        )

    def test_Report_WhenMoreThanTwentyChainDaysDisagree_NamesTwentyAndCountsTheRest(self):
        report = MovementCompleteness(
            chain_faults=[
                ChainFault("a", "b", date(2026, 9, 1) + timedelta(days=n), 1, 0, 1, 0)
                for n in range(25)
            ]
        )

        text = report.describe()

        assert text.count("a to b:") == 20
        assert "... and 5 more" in text


class TestTheOverview:
    def test_Overview_WhenEveryMovementIsAccountedFor_RaisesNoMovementItem(self, store):
        items = [i for i in overview_of(store).items if i.kind == "movement-completeness"]

        assert items == []

    def test_Overview_WhenARowIsMissingAndEveryBalanceAgrees_RaisesOneItemOfTheFirstBand(
        self, store
    ):
        plant_the_fault(store)

        (item,) = [i for i in overview_of(store).items if i.kind == "movement-completeness"]

        assert item.severity == NOW
        assert item.href == "/identity-health"
        assert item.message == (
            "Movement completeness: 1 day where a source lists more or fewer rows than "
            "the store holds from it."
        )
        assert "starling-personal" in item.accounts
        for hidden in (str(PRIVATE_MINOR), PRIVATE_PAYEE):
            assert hidden not in item.message
