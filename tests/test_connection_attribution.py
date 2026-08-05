"""Artefacts know which connection fetched them.

The witness instance, distinct from the source pipe: "starling-api" and
a TrueLayer connection to the same bank are different witnesses, and
once a second connection to one bank exists this column is the only
thing that tells their evidence apart. Attributed NOW, while history is
unambiguous - later it becomes archaeology.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from obdi.providers import starling, truelayer
from obdi.store import Store


class TestNewLandingsCarryTheirConnection:
    def test_ATrueLayerArtefact_RecordsTheConnectionFromItsRequestMeta(self):
        meta = json.dumps({"trigger": "scheduled", "connection_id": "halifax"})
        artefact = truelayer.artefact_for(
            b'{"results": []}',
            account_id="acc-1",
            kind="booked",
            requested="from=x&to=y",
            request_meta=meta,
        )
        assert artefact.connection_id == "halifax"

    def test_AStarlingArtefact_RecordsItsConnection(self):
        meta = json.dumps({"trigger": "scheduled", "connection_id": "starling-api"})
        artefact = starling.artefact_for(
            b'{"feedItems": []}',
            account_id="starling:cat-1",
            kind="feed",
            origin="https://api.example.com/feed",
            request_meta=meta,
        )
        assert artefact.connection_id == "starling-api"

    def test_NoMetaMeansNoConnection_HonestlyEmpty(self):
        artefact = truelayer.artefact_for(
            b"{}", account_id="a", kind="accounts", request_meta=""
        )
        assert artefact.connection_id == ""

    def test_TheColumnRoundTripsThroughTheStore(self, tmp_path):
        meta = json.dumps({"connection_id": "nationwide"})
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                truelayer.artefact_for(
                    b'{"results": []}',
                    account_id="acc-9",
                    kind="booked",
                    requested="from=x&to=y",
                    request_meta=meta,
                )
            )
            stored = store.connection.execute(
                "SELECT connection_id FROM raw_artefacts"
            ).fetchone()[0]
        assert stored == "nationwide"


class TestTheMigrationLadder:
    """recorded (request_meta) -> recovered (origin via enumeration) ->
    defaulted (starling) -> honestly empty."""

    def _old_store(self, path) -> None:
        """A pre-attribution store: the table without the column, marked
        schema version 2, holding one row per ladder rung."""
        db = sqlite3.connect(path)
        db.executescript(
            """
            CREATE TABLE raw_artefacts (
                digest TEXT NOT NULL, source TEXT NOT NULL,
                account_ref TEXT NOT NULL, media_type TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL,
                payload BLOB NOT NULL, request_meta TEXT NOT NULL DEFAULT '',
                record_count INTEGER,
                PRIMARY KEY (digest, account_ref, origin)
            );
            CREATE TABLE obdi_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO obdi_meta VALUES ('schema_version', '2');
            """
        )
        now = datetime.now(UTC).isoformat()
        rows = [
            # Rung 1: request_meta names the connection.
            ("d1", "truelayer-booked", "halifax-current", "o1",
             json.dumps({"connection_id": "halifax"}), b'{"results": []}'),
            # Rung 2: no meta, but the origin carries the provider account
            # id that halifax's own accounts enumeration lists.
            ("d2", "truelayer-booked", "halifax-current",
             "https://api.truelayer.com/data/v1/accounts/acc-77/transactions?from=x",
             "", b'{"results": []}'),
            # The enumeration that licenses rung 2.
            ("d3", "truelayer-accounts", "halifax", "o3", "",
             json.dumps({"results": [{"account_id": "acc-77"}]}).encode()),
            # Rung 3: starling defaults to the only first-party connection.
            ("d4", "starling-feed", "starling-personal", "o4", "",
             b'{"feedItems": []}'),
            # Rung 4: a file import - no connection exists, stays empty.
            ("d5", "csv", "halifax-current", "export.csv", "", b"a,b"),
        ]
        for digest, source, ref, origin, meta, payload in rows:
            db.execute(
                "INSERT INTO raw_artefacts (digest, source, account_ref, "
                "media_type, origin, fetched_at, payload, request_meta) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (digest, source, ref, "application/json", origin, now,
                 payload, meta),
            )
        db.commit()
        db.close()

    def test_EveryRung_ResolvesToItsBestEvidence(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        self._old_store(path)

        with Store(path) as store:
            attributed = {
                row[0]: row[1]
                for row in store.connection.execute(
                    "SELECT digest, connection_id FROM raw_artefacts"
                )
            }

        assert attributed["d1"] == "halifax", "recorded in request_meta"
        assert attributed["d2"] == "halifax", "recovered via the enumeration"
        assert attributed["d3"] == "halifax", "the enumeration itself is halifax's"
        assert attributed["d4"] == "starling-api", "starling default"
        assert attributed["d5"] == "", "a file import honestly has none"

    def test_ReopeningTheStore_ChangesNothing(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        self._old_store(path)
        with Store(path):
            pass
        with Store(path) as store:
            count = store.connection.execute(
                "SELECT COUNT(*) FROM raw_artefacts WHERE connection_id = 'halifax'"
            ).fetchone()[0]
        assert count == 3


class TestTheDisplayHalf:
    """The roster names witnesses; corroboration still counts classes."""

    def _seeded(self, tmp_path):
        """One account fed by two witnesses over different pipes."""
        from datetime import date

        from obdi.ingest import reconcile_batch
        from obdi.models import SourceTier, Transaction, TransactionStatus

        store = Store(tmp_path / "s.sqlite3")
        for source, connection, digest in (
            ("starling", "starling-api", "d-s"),
            ("truelayer", "starling-truelayer", "d-t"),
        ):
            meta = json.dumps({"connection_id": connection})
            artefact = (
                starling.artefact_for(
                    b'{"feedItems": []}',
                    account_id="starling-personal",
                    kind="feed",
                    origin=f"https://x/{connection}",
                    request_meta=meta,
                )
                if source == "starling"
                else truelayer.artefact_for(
                    b'{"results": []}',
                    account_id="starling-personal",
                    kind="booked",
                    requested="from=x&to=y",
                    request_meta=meta,
                )
            )
            artefact = type(artefact)(**{**artefact.__dict__, "digest": digest})
            store.land_artefact(artefact)
            reconcile_batch(
                store,
                [
                    Transaction(
                        entity_id=f"e-{source}",
                        account_id="starling-personal",
                        amount_minor=-1234,
                        currency="GBP",
                        description="COFFEE",
                        value_date=date(2026, 8, 1),
                        booking_date=date(2026, 8, 1),
                        source=source,
                        source_id=f"id-{source}",
                        content_key="ck-1",
                        tier=SourceTier.AUTHORITATIVE,
                        status=TransactionStatus.BOOKED,
                        artefact_digest=digest,
                    )
                ],
                digest=digest,
            )
        return store

    def test_TheWitnessMap_NamesEveryConnectionThatFedAnAccount(self, tmp_path):
        store = self._seeded(tmp_path)
        mapping = store.source_connections()
        store.close()

        assert mapping[("starling-personal", "starling")] == ["starling-api"]
        assert mapping[("starling-personal", "truelayer")] == ["starling-truelayer"]

    def test_TheBreakdown_CarriesConnectionsPerFeeder(self, tmp_path):
        store = self._seeded(tmp_path)
        breakdown = store.source_breakdown("starling-personal")
        store.close()

        by_feeder = {
            (entry["source"]): entry["connections"]
            for entry in breakdown["by_feeder"]
        }
        assert by_feeder["starling"] == ["starling-api"]
        assert by_feeder["truelayer"] == ["starling-truelayer"]

    def test_TheViaLabel_PrefersTheWitnessName_FallsBackToThePipe(self):
        from obdi.web import _via_label

        assert _via_label("truelayer", ["halifax"]) == "halifax"
        assert _via_label("truelayer", ["halifax", "tink-halifax"]) == (
            "halifax, tink-halifax"
        )
        assert _via_label("truelayer", None) == "truelayer"
        assert _via_label("truelayer", []) == "truelayer"

    def test_CorroborationStillCountsClasses_NotInstances(self, tmp_path):
        """Two witnesses of the SAME payment via different pipes = one
        corroborated transaction. The witness names must not change what
        corroboration means."""
        store = self._seeded(tmp_path)
        breakdown = store.source_breakdown("starling-personal")
        store.close()

        assert breakdown["transactions"] == 1
        assert breakdown["corroborated"] == 1
