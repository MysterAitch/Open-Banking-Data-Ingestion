"""Does every payment a provider reported still have a row of its own?

Two faults leave no trace a reader of the merged layer can see: two rows
sharing one identity, and two payments folded into one row. Both are counted
here, and only counted - the report names accounts and numbers, never an
amount, a payee or a description, so it can be read by anybody who may see
that a fault exists without seeing the money it concerns.

Every scenario fixes its counts before the first run. The folded states are
planted through the sighting door rather than produced by the matcher, so
these stay true whatever the matcher is later taught.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import date
from http.server import HTTPServer

import httpx

from landing import rebuild_from_raw
from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.identity_health import ProviderIdTally, SharedIdentity, identity_health
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig

ACCOUNT = "starling:cat-1"

#: Chosen so that neither figure can appear in a report by coincidence.
PRIVATE_MINOR = 73913
PRIVATE_PAYEE = "Zebra Crossing Cafe"
PRIVATE_REFERENCE = "QUAGGA-REFERENCE"


def _feed(store: Store, items: list[dict], cycle: int) -> None:
    store.land_artefact(
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id=ACCOUNT,
            kind="feed",
            origin=f"https://api.example.com/feed/account/a/category/cat-1?c={cycle}",
        )
    )


def _payment(uid: str, day: int) -> dict:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": PRIVATE_MINOR},
        "direction": "OUT",
        "transactionTime": f"2026-{day:02}-01T09:15:00.000Z",
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": PRIVATE_PAYEE,
        "reference": PRIVATE_REFERENCE,
    }


def _three_payments_months_apart(store: Store) -> None:
    """Far enough apart that nothing could take one for another."""
    _feed(store, [_payment("pay-1", 1), _payment("pay-2", 3), _payment("pay-3", 5)], cycle=0)
    rebuild_from_raw(store)


def _row(store: Store, source_id: str) -> Transaction:
    return next(t for t in store.all_transactions() if t.source_id == source_id)


def _fold_into(store: Store, survivor: str, absorbed_id: str) -> None:
    """Record that the survivor's row was also sighted under another provider id."""
    store.record_source(
        replace(
            _row(store, survivor),
            source_id=absorbed_id,
            artefact_digest=f"digest-of-the-response-carrying-{absorbed_id}",
        )
    )
    store.connection.commit()


class TestProviderIdsAgainstTheRowsThatHoldThem:
    def test_Report_WhenEveryPaymentHasItsOwnRow_ReportsNothingFolded(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            report = identity_health(store)

        assert report.tallies == [
            ProviderIdTally(
                account_id=ACCOUNT, source="starling", reported=3, held=3, absorbing_rows=0
            )
        ]
        assert report.folded == 0

    def test_Report_WhenARowAbsorbedAnotherPayment_CountsOneFolded(self, tmp_path):
        """Four provider ids, three rows: one payment has no row of its own."""
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _fold_into(store, "pay-1", "pay-lost")
            report = identity_health(store)

        assert report.tallies == [
            ProviderIdTally(
                account_id=ACCOUNT,
                source="starling",
                reported=4,
                held=3,
                absorbing_rows=1,
                folded=1,
            )
        ]
        assert report.folded == 1
        assert report.surplus == 0

    def test_Report_WhenTheProviderListedBothIdsInOneResponse_TheFoldIsProvenTwoPayments(
        self, tmp_path
    ):
        """One response naming both ids cannot be one payment named twice."""
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _feed(store, [_payment("pay-1", 1), _payment("pay-lost", 2)], cycle=1)
            _fold_into(store, "pay-1", "pay-lost")
            report = identity_health(store)

        (tally,) = report.tallies
        assert (tally.folded, tally.folded_listed_together) == (1, 1)
        assert report.folded_listed_together == 1
        assert "1 listed in one response beside the id that holds the row" in report.describe()

    def test_Report_WhenTheIdsWereNeverListedTogether_TheFoldMayBeOnePaymentRenumbered(
        self, tmp_path
    ):
        """Each id arrived in a response of its own: what a provider giving a
        payment a new id looks like, and not proof of a missing payment."""
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _feed(store, [_payment("pay-renumbered", 1)], cycle=1)
            _fold_into(store, "pay-1", "pay-renumbered")
            report = identity_health(store)

        (tally,) = report.tallies
        assert (tally.folded, tally.folded_listed_together) == (1, 0)
        assert "1 never listed beside it" in report.describe()
        assert "renumber" in report.describe()

    def test_Report_WhenTheAbsorbedIdIsInNoResponseHeld_CountsItAsNeverListedTogether(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _fold_into(store, "pay-1", "pay-lost")
            report = identity_health(store)

        (tally,) = report.tallies
        assert (tally.folded, tally.folded_listed_together) == (1, 0)

    def test_Report_WhenARowsTwoIdsWereListedTogetherButBothHaveRows_ProvesNothingFolded(
        self, tmp_path
    ):
        """History, not loss: the opposite case must not be counted as proof."""
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _fold_into(store, "pay-1", "pay-2")
            report = identity_health(store)

        (tally,) = report.tallies
        assert (tally.folded, tally.folded_listed_together) == (0, 0)

    def test_Report_WhenAFoldedPaymentLaterRegainedItsOwnRow_CountsNothingFolded(
        self, tmp_path
    ):
        """The id is still recorded against the row that once absorbed it, and
        is also held by its own row. Nothing is missing now, and the report
        must say so rather than counting history as loss."""
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _fold_into(store, "pay-1", "pay-2")
            report = identity_health(store)

        assert report.tallies == [
            ProviderIdTally(
                account_id=ACCOUNT, source="starling", reported=3, held=3, absorbing_rows=1
            )
        ]
        assert report.folded == 0

    def test_Report_WhenAPendingRecordSettledUnderANewId_CountsNothingFolded(
        self, tmp_path
    ):
        """One payment, two ids, by the provider's own design: the pending
        snapshot names it one way and the settled record another."""
        record = {
            "timestamp": "2026-07-01T00:00:00Z",
            "amount": -12.34,
            "currency": "GBP",
            "description": PRIVATE_REFERENCE,
        }
        with Store(tmp_path / "s.sqlite3") as store:
            for kind, provider_id in (("pending", "tl-pending-1"), ("booked", "tl-booked-1")):
                body = {
                    "results": [
                        {
                            **record,
                            "transaction_id": provider_id,
                            "normalised_provider_transaction_id": provider_id,
                        }
                    ],
                    "status": "Succeeded",
                }
                store.land_artefact(
                    truelayer.artefact_for(
                        json.dumps(body).encode(),
                        account_id="tl-1",
                        kind=kind,
                        requested=kind,
                    )
                )
            rebuild_from_raw(store)
            rows = store.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            report = identity_health(store)

        assert rows == 1
        assert report.tallies == [
            ProviderIdTally(
                account_id="truelayer:tl-1",
                source="truelayer",
                reported=1,
                held=1,
                absorbing_rows=0,
            )
        ]

    def test_Report_WhenOnePaymentIsHeldByTwoRows_CountsOneRowTooMany(self, tmp_path):
        """Three provider ids, four rows carrying them: one id is the only id
        of two different rows, so one payment is held twice.

        The fourth row begins as an export's row with no provider id, and is
        then sighted by the feed under an id another row already holds - the
        shape left when a row's provider id is lost to a second source and the
        first source reports the payment again."""
        exported = Transaction(
            account_id=ACCOUNT,
            amount_minor=-PRIVATE_MINOR,
            currency="GBP",
            description=PRIVATE_REFERENCE,
            value_date=date(2026, 11, 20),
            booking_date=date(2026, 11, 20),
            source="qif",
            source_id=None,
            content_key="ck-held-twice",
            tier=SourceTier.SYNTHETIC,
            status=TransactionStatus.BOOKED,
        )
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            reconcile_batch(store, [exported], digest="d-export")
            second_row = next(t for t in store.all_transactions() if t.source == "qif")
            store.record_source(
                replace(
                    second_row,
                    source="starling",
                    source_id="pay-1",
                    artefact_digest="digest-of-the-response-re-reporting-pay-1",
                )
            )
            store.connection.commit()
            report = identity_health(store)
            text = report.describe()

        tally = next(t for t in report.tallies if t.source == "starling")
        assert (tally.reported, tally.held) == (3, 4)
        assert tally.surplus == 1
        assert tally.folded == 0
        assert report.surplus == 1
        assert "1 more row than ids" in text
        assert "held by more than one row" in text

    def test_Report_WhenOnePaymentIsFoldedAndAnotherHeldTwice_NeitherHidesTheOther(
        self, tmp_path
    ):
        """Four ids and four rows: the totals agree, and both faults are there.
        One row absorbed a payment that has no row of its own, and elsewhere
        one payment is held by two rows."""
        exported = Transaction(
            account_id=ACCOUNT,
            amount_minor=-PRIVATE_MINOR,
            currency="GBP",
            description=PRIVATE_REFERENCE,
            value_date=date(2026, 11, 20),
            booking_date=date(2026, 11, 20),
            source="qif",
            source_id=None,
            content_key="ck-held-twice",
            tier=SourceTier.SYNTHETIC,
            status=TransactionStatus.BOOKED,
        )
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _fold_into(store, "pay-1", "pay-lost")
            reconcile_batch(store, [exported], digest="d-export")
            second_row = next(t for t in store.all_transactions() if t.source == "qif")
            store.record_source(
                replace(
                    second_row,
                    source="starling",
                    source_id="pay-3",
                    artefact_digest="digest-of-the-response-re-reporting-pay-3",
                )
            )
            store.connection.commit()
            report = identity_health(store)

        tally = next(t for t in report.tallies if t.source == "starling")
        assert (tally.reported, tally.held) == (4, 4)
        assert (tally.folded, tally.surplus) == (1, 1)

    def test_Report_WhenRowsAndIdsMatch_ReportsNoRowTooMany(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            report = identity_health(store)

        assert report.surplus == 0
        assert "more row" not in report.describe()

    def test_Report_ForASourceThatCarriesNoIds_SaysNothingAboutIt(self, tmp_path):
        """An export with no provider ids offers nothing to count, and a row
        of zeroes would read as a clean bill of health it has not earned."""
        exported = Transaction(
            account_id="savings",
            amount_minor=-PRIVATE_MINOR,
            currency="GBP",
            description=PRIVATE_REFERENCE,
            value_date=date(2026, 3, 6),
            booking_date=date(2026, 3, 6),
            source="qif",
            source_id=None,
            content_key="ck-export",
            tier=SourceTier.SYNTHETIC,
            status=TransactionStatus.BOOKED,
        )
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [exported], digest="d-export")
            report = identity_health(store)

        assert report.tallies == []


class TestRowsSharingAnIdentity:
    def test_Report_WhenIdentitiesAreDistinct_ListsNone(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            report = identity_health(store)

        assert report.shared == []

    def test_Report_WhenTwoRowsShareAnIdentity_CountsOneIdentityAndTwoRows(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _feed(store, [_payment("pay-1", 1), _payment("pay-2", 1)], cycle=0)
            rebuild_from_raw(store)
            # The state ingest prevents: both rows on one occurrence.
            store.connection.execute("UPDATE transactions SET occurrence = 0")
            store.connection.commit()
            report = identity_health(store)

        assert report.shared == [SharedIdentity(account_id=ACCOUNT, identities=1, rows=2)]


class TestTheReportShowsCountsAndNothingElse:
    def test_Describe_NamesAccountsAndCounts_AndNoAmountPayeeOrDescription(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            _fold_into(store, "pay-1", "pay-lost")
            text = identity_health(store).describe()

        assert ACCOUNT in text
        assert "4 reported" in text and "3 held" in text
        for private in (
            str(PRIVATE_MINOR),
            "739.13",
            PRIVATE_PAYEE,
            PRIVATE_REFERENCE,
            "pay-lost",
        ):
            assert private not in text

    def test_Describe_WhenNothingIsWrong_SaysSoInWords(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _three_payments_months_apart(store)
            text = identity_health(store).describe()

        assert "no transactions share an identity" in text
        assert "every provider id reported has a row of its own" in text

    def test_Describe_OnAnEmptyStore_SaysThereIsNothingToCount(self, tmp_path):
        """Silence must not read as a pass."""
        with Store(tmp_path / "s.sqlite3") as store:
            text = identity_health(store).describe()

        assert "no provider ids are held" in text


def _serve(config: WebConfig) -> HTTPServer:
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _config(tmp_path, **hooks) -> WebConfig:
    return WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        **hooks,
    )


class TestIdentityHealthPage:
    def test_Page_WhenWired_ShowsTheReport(self, tmp_path):
        httpd = _serve(
            _config(tmp_path, identity_health_text=lambda: "example: 4 reported, 3 held")
        )
        try:
            response = httpx.get(f"http://127.0.0.1:{httpd.server_port}/identity-health")
        finally:
            httpd.shutdown()

        assert response.status_code == 200
        assert "example: 4 reported, 3 held" in response.text

    def test_Page_WhenNotWired_IsNotFound(self, tmp_path):
        httpd = _serve(_config(tmp_path))
        try:
            response = httpx.get(f"http://127.0.0.1:{httpd.server_port}/identity-health")
        finally:
            httpd.shutdown()

        assert response.status_code == 404

    def test_Page_WhenTheReportFails_SaysSoRatherThanShowingAnEmptyPass(self, tmp_path):
        def broken() -> str:
            raise RuntimeError("the store would not open")

        httpd = _serve(_config(tmp_path, identity_health_text=broken))
        try:
            response = httpx.get(f"http://127.0.0.1:{httpd.server_port}/identity-health")
        finally:
            httpd.shutdown()

        assert response.status_code == 500
        assert "the store would not open" in response.text

    def test_ReportsIndex_LinksToTheReport(self, tmp_path):
        httpd = _serve(_config(tmp_path, identity_health_text=lambda: "report"))
        try:
            home = httpx.get(f"http://127.0.0.1:{httpd.server_port}/reports").text
        finally:
            httpd.shutdown()

        assert 'href="/identity-health"' in home


class TestIdentityHealthCommand:
    def test_Command_PrintsTheReport_AndExitsZeroBecauseItOnlyReports(
        self, tmp_path, capsys, monkeypatch
    ):
        from obdi import cli

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _three_payments_months_apart(store)
            _fold_into(store, "pay-1", "pay-lost")

        monkeypatch.setenv("OBDI_DB_PATH", str(db))
        exit_code = cli.main(["identity-health"])

        printed = capsys.readouterr().out
        assert exit_code == 0
        assert "4 reported" in printed and "3 held" in printed

    def test_WebHook_BuiltFromTheRealConfiguration_ReturnsTheSameReport(
        self, tmp_path, monkeypatch
    ):
        from obdi.cli import build_web_config

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _three_payments_months_apart(store)
            expected = identity_health(store).describe()

        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            monkeypatch.delenv(variable, raising=False)
        config = build_web_config(db)

        assert config is not None, "the builder refused a store it should have accepted"
        assert config.identity_health_text is not None
        assert config.identity_health_text() == expected
