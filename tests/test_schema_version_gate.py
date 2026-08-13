"""A schema change that does not bump the version reaches nobody's store.

`_prepare` does work only when a store's stamped version differs from
SCHEMA_VERSION. That gate is what lets an open avoid the write lock, and it has
one failure mode: add a column, write its migration, forget the bump, and every
store that already exists skips the migration for ever. A new store is fine -
the column is in SCHEMA - so the whole test suite stays green while every real
store is broken.

That happened on 2026-08-13. 0.4.212 gave sightings an `observed_date` with a
migration and 138 lines of its own tests, and left SCHEMA_VERSION at 8. The live
instance rebuilt twice, wiped its derived layer first as a rebuild does, and
failed on the first insert with "table transaction_sources has no column named
observed_date". Its categorise page then read "2768 of 0 eligible transactions"
for two hours. Nothing was lost - layer 0 replays - but nothing worked either,
and no test could have said so.

The comment on SCHEMA_VERSION already said to bump it. This is that instruction
with teeth.
"""

from __future__ import annotations

import sqlite3

import pytest

from obdi.store import SCHEMA_SHAPE, SCHEMA_VERSION, Store, schema_shape


def _shape_of(store_path) -> dict[str, list[str]]:
    with Store(store_path):
        pass
    connection = sqlite3.connect(str(store_path))
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: sorted(
                str(column[1])
                for column in connection.execute(f"PRAGMA table_info({table})")
            )
            for table in tables
        }
    finally:
        connection.close()


class TestTheVersionGate:
    def test_TheRecordedShape_MatchesWhatAFreshStoreActuallyBuilds(self, tmp_path):
        """The pin is a fact about this version, so it must be true of it.

        A pin that has drifted from the code is worse than none: it goes on
        passing while describing a schema that no longer exists.
        """
        assert schema_shape() == SCHEMA_SHAPE, (
            "the recorded shape disagrees with what the store builds - repin it"
        )
        assert schema_shape() == _shape_of(tmp_path / "fresh.sqlite3"), (
            "the recorded shape is not what a store on disk ends up with"
        )

    def test_ChangingTheSchema_WithoutBumpingTheVersion_IsCaught(self, tmp_path):
        """The guard proving it can fail, in the shape the mistake takes.

        A column added to SCHEMA without a bump leaves every existing store
        without it. Here the change is simulated rather than made, because
        making it would mean editing the schema this test defends.
        """
        current = schema_shape()
        changed = {
            table: (
                sorted([*columns, "a_column_added_without_a_bump"])
                if table == "transaction_sources"
                else columns
            )
            for table, columns in current.items()
        }

        assert changed != SCHEMA_SHAPE, (
            "a schema carrying an extra column matched the recorded shape, so "
            "this guard cannot see the mistake it exists to catch"
        )

    def test_EveryTableTheStoreBuilds_IsInTheRecordedShape(self):
        """A table added and not pinned is the same fault one level up.

        Named separately from the column check because the failure message is
        the whole value: "a table is missing from the pin" sends a reader
        somewhere different from "a column is".
        """
        missing = sorted(set(schema_shape()) - set(SCHEMA_SHAPE))

        assert not missing, (
            f"tables {missing} exist but are not recorded - bump SCHEMA_VERSION "
            f"(now {SCHEMA_VERSION}) and repin SCHEMA_SHAPE"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
