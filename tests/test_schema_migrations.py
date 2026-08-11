"""A store made by an older release must open, and then WORK.

The rule this file enforces is already written in the store's own
migration docstrings: CREATE TABLE IF NOT EXISTS never alters an existing
table, so a schema change without a migration reaches nobody who already
has a store. It reaches them at the write door instead - the first pull
after an upgrade failing on a column that is not there - which is as far
as possible from where a person would look.

So the shapes earlier releases actually shipped are checked in under
tests/schema_history, and every one of them is built, opened by current
code, and compared table by table against a store this code made from
scratch. The table list is read out of the schema itself, so a table
added tomorrow is covered without anyone remembering to add it here.

The newest snapshot is the shape currently in the wild, so a change to
SCHEMA without a migration fails against it. Append a snapshot when a
shape ships; never edit one to make a test pass.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from datetime import UTC, date, datetime

import pytest

from obdi.ingest import reconcile_batch
from obdi.models import RawArtefact, SourceTier, Transaction
from obdi.store import TABLE_NAMES, Store

SCHEMA_HISTORY = pathlib.Path(__file__).resolve().parent / "schema_history"

SHIPPED_SHAPES = sorted(SCHEMA_HISTORY.glob("*.sql"))


def _store_from(snapshot: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """A database carrying exactly the shape one release shipped."""
    legacy = sqlite3.connect(path)
    legacy.executescript(snapshot.read_text(encoding="utf-8"))
    legacy.commit()
    legacy.close()
    return path


def _shape(connection: sqlite3.Connection, table: str) -> dict[str, tuple[object, ...]]:
    """One table's shape, keyed by column NAME rather than position.

    Position is deliberately not compared: SQLite gives column order no
    meaning beyond SELECT *, which no query in the store relies on, and a
    column added by ALTER lands at the end while the same column sits
    mid-table in a fresh store. Type, nullability, default and the
    primary key are what a write door actually depends on.
    """
    return {
        str(row[1]): (str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


@pytest.fixture(scope="module")
def reference_shapes(tmp_path_factory) -> dict[str, dict[str, tuple[object, ...]]]:
    path = tmp_path_factory.mktemp("reference") / "fresh.sqlite3"
    with Store(path) as store:
        return {table: _shape(store.connection, table) for table in TABLE_NAMES}


class TestAStoreFromAnEarlierRelease:
    def test_ThereIsAtLeastOneShippedShapeToTestAgainst(self):
        """A guard that reads an empty directory passes silently, which is
        the failure mode this whole file exists to prevent."""
        assert SHIPPED_SHAPES, f"no schema snapshots under {SCHEMA_HISTORY}"

    @pytest.mark.parametrize("snapshot", SHIPPED_SHAPES, ids=lambda p: p.stem)
    def test_Store_WhenOpenedOnAShapeAnEarlierReleaseShipped_MatchesAFreshStore(
        self, snapshot, tmp_path, reference_shapes
    ):
        """Every table in the schema, not a hand-kept list of the ones
        somebody remembered to migrate."""
        path = _store_from(snapshot, tmp_path / "old.sqlite3")

        with Store(path) as store:
            migrated = {table: _shape(store.connection, table) for table in TABLE_NAMES}

        divergent = {
            table: {
                column: (reference_shapes[table].get(column), migrated[table].get(column))
                for column in set(reference_shapes[table]) | set(migrated[table])
                if reference_shapes[table].get(column) != migrated[table].get(column)
            }
            for table in TABLE_NAMES
            if reference_shapes[table] != migrated[table]
        }
        assert not divergent, (
            f"{snapshot.name} opens into a shape a fresh store would not have "
            f"(expected, found): {divergent}. A schema change needs a "
            "migration in Store._prepare; CREATE TABLE IF NOT EXISTS reaches "
            "nobody who already has a store"
        )


class TestTheFoldStillWritesToAnUpgradedStore:
    """The shape comparison says the columns are there; these say the
    write doors that need them actually work, which is where a missing
    migration surfaces in real life."""

    def _txn(self, source_id: str) -> Transaction:
        return Transaction(
            account_id="current",
            amount_minor=-2500,
            currency="GBP",
            value_date=date(2026, 3, 5),
            booking_date=date(2026, 3, 5),
            description="RENT",
            source="truelayer",
            source_id=source_id,
            tier=SourceTier.AUTHORITATIVE,
            content_key="key-rent",
        )

    def test_Sightings_WhenTheStorePredatesArtefactLinks_StillRecordEveryWitness(
        self, tmp_path
    ):
        """Two artefacts seeing one payment is the case the widened key
        exists for. On the old key the second sighting was a conflict, so
        the walk back to the raw bytes of the earlier one was lost."""
        snapshot = SCHEMA_HISTORY / "05-transaction-sources.sql"
        path = _store_from(snapshot, tmp_path / "old.sqlite3")
        legacy = sqlite3.connect(path)
        legacy.execute(
            "INSERT INTO transaction_sources "
            "(entity_id, source, source_id, first_seen_at) "
            "VALUES ('old-entity', 'halifax-qif', NULL, '2026-01-01T00:00:00')"
        )
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            reconcile_batch(store, [self._txn("tl-1")], digest="digest-api")
            reconcile_batch(store, [self._txn("tl-1")], digest="digest-repull")
            entity = store.all_transactions()[0].entity_id

            assert set(store.sightings_for(entity)) == {
                ("truelayer", "digest-api"),
                ("truelayer", "digest-repull"),
            }
            kept = store.connection.execute(
                "SELECT entity_id, artefact_digest FROM transaction_sources "
                "WHERE entity_id = 'old-entity'"
            ).fetchone()

        # The pre-existing sighting survives the rebuild, with an empty
        # digest - honest, because nobody recorded one at the time.
        assert tuple(kept) == ("old-entity", "")

    def test_Valuations_WhenTheStorePredatesIncomeEntitlements_RecordOneWithNoPot(
        self, tmp_path
    ):
        """A defined-benefit pension has an income and no capital value.
        On the old table value_minor was mandatory, so recording one
        failed at the write door."""
        snapshot = SCHEMA_HISTORY / "03-source-tiers.sql"
        path = _store_from(snapshot, tmp_path / "old.sqlite3")
        legacy = sqlite3.connect(path)
        legacy.execute(
            "INSERT INTO valuations (asset_id, observed_at, value_minor, currency, "
            "source, ingested_at) VALUES "
            "('sipp', '2026-01-01', 1234500, 'GBP', 'statement', '2026-01-02')"
        )
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            store.record_valuation_row(
                asset_id="db-pension",
                kind="income",
                observed_at=date(2026, 2, 1),
                source="statement",
                annual_income_minor=980000,
            )

            entitlement = store.valuations_for("db-pension")[0]
            pot = store.valuations_for("sipp")[0]

        assert entitlement["value_minor"] is None
        assert entitlement["annual_income_minor"] == 980000
        # The pot observation survives the rebuild, with the default kind.
        assert pot["value_minor"] == 1234500
        assert pot["kind"] == "other"


class TestAnInterruptedMigrationRunsAgain:
    """A migration gated on the shape it produces cannot retry.

    ALTER TABLE is DDL and commits by itself, so a process killed between
    the ALTER and the rows it was about to fill leaves a store that looks
    migrated from every angle and carries none of the work. Gated on the
    column, the ladder would never run again on that store; gated on a
    completion marker, the next open finishes it.
    """

    @staticmethod
    def _land(legacy: sqlite3.Connection, digest: str, meta: dict[str, str]) -> None:
        legacy.execute(
            "INSERT INTO raw_artefacts (digest, source, account_ref, media_type, "
            "origin, fetched_at, payload, request_meta) VALUES (?,?,?,?,?,?,?,?)",
            (
                digest,
                "truelayer-booked",
                "halifax-current",
                "application/json",
                "https://api/transactions",
                "2026-02-01T00:00:00+00:00",
                b"{}",
                json.dumps(meta),
            ),
        )

    def _killed_after_the_alter(self, tmp_path) -> pathlib.Path:
        path = _store_from(
            SCHEMA_HISTORY / "13-rebuild-runs.sql", tmp_path / "old.sqlite3"
        )
        legacy = sqlite3.connect(path)
        self._land(legacy, "d-1", {"connection_id": "halifax"})
        self._land(legacy, "d-2", {"connection_id": "halifax"})
        # Exactly what a kill mid-migration leaves behind: the column
        # committed by its own DDL, and not one row populated.
        legacy.execute(
            "ALTER TABLE raw_artefacts "
            "ADD COLUMN connection_id TEXT NOT NULL DEFAULT ''"
        )
        legacy.commit()
        legacy.close()
        return path

    def test_Attribution_WhenAKilledUpgradeLeftTheColumnEmpty_TheNextOpenFinishesIt(
        self, tmp_path
    ):
        path = self._killed_after_the_alter(tmp_path)

        with Store(path) as store:
            attributed = store.connection.execute(
                "SELECT digest, connection_id FROM raw_artefacts ORDER BY digest"
            ).fetchall()

        assert [tuple(row) for row in attributed] == [
            ("d-1", "halifax"),
            ("d-2", "halifax"),
        ]

    def test_Attribution_WhenItHasFinished_DoesNotRunAgainOnEveryOpen(self, tmp_path):
        """The completion marker is what keeps opening the store
        read-only, which is what lets the page render while a pull holds
        the write lock."""
        path = self._killed_after_the_alter(tmp_path)
        with Store(path):
            pass

        with Store(path) as store:
            store.connection.execute(
                "UPDATE raw_artefacts SET connection_id = 'renamed'"
            )
            store.connection.commit()

        with Store(path) as store:
            still = store.connection.execute(
                "SELECT DISTINCT connection_id FROM raw_artefacts"
            ).fetchall()

        assert [tuple(row) for row in still] == [("renamed",)]


class TestTheUnattributedArtefactBackstop:
    """A half-attributed store answers every per-connection question with
    nothing, which reads exactly like a store that has no connections
    yet. The count says which it is - against its denominator, and with
    the artefacts themselves rather than a bare number.
    """

    @staticmethod
    def _artefact(digest: str, connection_id: str = "") -> RawArtefact:
        return RawArtefact(
            source="truelayer-booked",
            account_ref="halifax-current",
            fetched_at=datetime(2026, 2, 1, tzinfo=UTC),
            media_type="application/json",
            digest=digest,
            payload=b"{}",
            origin=f"https://api/transactions#{digest}",
            connection_id=connection_id,
        )

    def test_Backstop_WhenNoArtefactNamesItsConnection_SaysSoAgainstTheTotal(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(self._artefact("d-1"))
            store.land_artefact(self._artefact("d-2"))

            report = store.unattributed_api_artefacts()

        assert report["unattributed"] == 2
        assert report["total"] == 2
        assert report["attributed"] == 0
        # Evidence, not a bare integer: a person can go and look at these.
        assert [entry["origin"] for entry in report["sample"]] == [
            "https://api/transactions#d-2",
            "https://api/transactions#d-1",
        ]
        assert report["sample_of"] == 2

    def test_Backstop_WhenTheSampleIsCapped_StillSaysHowManyItIsOutOf(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            for index in range(7):
                store.land_artefact(self._artefact(f"d-{index}"))

            report = store.unattributed_api_artefacts(sample_limit=2)

        assert len(report["sample"]) == 2
        assert report["sample_of"] == 7
        assert report["unattributed"] == 7

    def test_Backstop_WhenEveryArtefactNamesItsConnection_ReportsNoneOutstanding(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(self._artefact("d-1", connection_id="halifax"))

            report = store.unattributed_api_artefacts()

        assert report["unattributed"] == 0
        assert report["total"] == 1
        assert report["sample"] == []
        assert report["migration_completed"] is True

    def test_Backstop_IgnoresFileImports_WhichHaveNoConnectionToName(self, tmp_path):
        """An imported statement arrived through no connection at all, so
        an empty value there is the truth rather than a gap."""
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                RawArtefact(
                    source="qif",
                    account_ref="halifax-current",
                    fetched_at=datetime(2026, 2, 1, tzinfo=UTC),
                    media_type="text/plain",
                    digest="d-file",
                    payload=b"!Type:Bank",
                )
            )

            report = store.unattributed_api_artefacts()

        assert report["total"] == 0
        assert report["unattributed"] == 0
