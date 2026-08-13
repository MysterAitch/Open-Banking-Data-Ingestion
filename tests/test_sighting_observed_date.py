"""Upgrading a store that predates the observed-date column.

Every other test builds a store through the current schema, so none of them can
reach the migration - the shape it exists to repair cannot be produced by any
write door, because the doors produce the new shape. That is precisely the class
of migration that fails on a real store at upgrade time and nowhere else.

The failure it prevents is not subtle: the sighting INSERT names a column that is
not there, so the first import after an upgrade dies at the write door rather
than anywhere a person would think to look. The artefact-digest column had the
same problem and its migration says so.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from obdi.models import SourceTier, Transaction
from obdi.store import Store

OLD_SCHEMA = """
CREATE TABLE transaction_sources (
    entity_id  TEXT NOT NULL,
    source     TEXT NOT NULL,
    source_id  TEXT,
    artefact_digest TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, source, artefact_digest)
);
"""


@pytest.fixture
def store_predating_the_column(tmp_path):
    """A store whose sighting table has no observed_date.

    Built with raw SQL deliberately, and it is the one fixture here that has to
    be: this is the shape the CURRENT code cannot create, which is what makes it
    worth testing. Recording a sighting through the door would produce the new
    table and the migration would have nothing to do.
    """
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(OLD_SCHEMA)
    connection.execute(
        "INSERT INTO transaction_sources "
        "(entity_id, source, source_id, artefact_digest, first_seen_at) "
        "VALUES (?,?,?,?,?)",
        ("entity-1", "starling-csv", "src-1", "digest-1", "2026-01-01T00:00:00Z"),
    )
    connection.commit()
    connection.close()
    return path


def _transaction(entity_id: str, when: date) -> Transaction:
    return Transaction(
        account_id="an-account",
        amount_minor=-1234,
        value_date=when,
        booking_date=when,
        description="A PAYMENT",
        source="starling-csv",
        source_id="src-2",
        tier=SourceTier.AUTHORITATIVE,
        entity_id=entity_id,
    )


class TestUpgradingAStoreThatPredatesTheColumn:
    def test_OpeningIt_AddsTheColumnAndKeepsTheExistingSighting(
        self, store_predating_the_column
    ):
        """The row is evidence of an observation and must survive. A migration
        that repairs the schema by losing history is not a repair."""
        with Store(store_predating_the_column) as store:
            columns = {
                row["name"]
                for row in store.connection.execute(
                    "PRAGMA table_info(transaction_sources)"
                )
            }
            rows = store.connection.execute(
                "SELECT entity_id, source, artefact_digest, observed_date "
                "FROM transaction_sources"
            ).fetchall()

        assert "observed_date" in columns
        assert len(rows) == 1, "the migration lost the sighting it was repairing"
        assert rows[0]["source"] == "starling-csv"
        assert rows[0]["artefact_digest"] == "digest-1"
        # Empty rather than invented. The sighting really was recorded before
        # anyone kept the date it carried, and saying so is the honest value -
        # the same choice the artefact-digest migration made for the same reason.
        assert rows[0]["observed_date"] == ""

    def test_ASightingRecordedAfterwards_CarriesItsOwnDate(
        self, store_predating_the_column
    ):
        """The write door works after the upgrade, which is the failure this
        migration exists to prevent: the INSERT names a column that is not
        there, so the first import after an upgrade dies at the door."""
        with Store(store_predating_the_column) as store:
            store.record_source(_transaction("entity-2", date(2026, 3, 4)))
            observed = store.connection.execute(
                "SELECT observed_date FROM transaction_sources WHERE entity_id = ?",
                ("entity-2",),
            ).fetchone()

        assert observed["observed_date"] == "2026-03-04"

    def test_AnUnmigratedSighting_FallsBackToTheMergedDate(
        self, store_predating_the_column
    ):
        """Old rows degrade to the previous behaviour rather than vanishing.

        A sighting with no recorded date must not drop out of the coverage
        report, and must not be given a date nobody observed. Falling back to
        the stored row's date is exactly what the report did before this column
        existed, so an unmigrated history reads as it always did.
        """
        with Store(store_predating_the_column) as store:
            store.upsert_transaction(
                _transaction("entity-1", date(2026, 5, 6)), match_tier="new"
            )
            sighted = store.transactions_by_sighting()

        assert [row.value_date for row in sighted] == [date(2026, 5, 6)], (
            "a sighting with no observed date should fall back to the stored "
            "row's date, not disappear and not invent one"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
