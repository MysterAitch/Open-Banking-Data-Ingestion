"""A version 19 store moves to 20 keeping its data, and a store newer than the code is refused.

THE STORE. `held` is built by the code under test, then stamped as version 19 with the table the
new version adds dropped, which is what a store last opened by the release before looks like. It
holds one account with a typed known balance, so "keeping its data" is something that can be read.

Decided before the first run:
  - opening it under this code, twice, grows the table once and stamps 20; the typed balance is
    still read, and a disregard written after the first open is still there after the second;
  - a store stamped one version NEWER than the code is refused at open with `StoreIsNewer`, the
    stamp is still the newer one afterwards, and no table the newer release may have changed was
    touched (the stamp is the observable: a restamp is what hid a rolled-back store before).
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from obdi.balance_anchors import record_stated_anchor, stated_anchors
from obdi.store import SCHEMA_VERSION, Store, StoreIsNewer
from test_balance_anchors import ACCOUNT, everyday

DAY = date(2026, 3, 10)


def stamp(path, version: int) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'", (str(version),)
    )
    connection.commit()
    connection.close()


def stamped(path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(
            connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        )
    finally:
        connection.close()


@pytest.fixture
def held(tmp_path):
    path = tmp_path / "held.sqlite3"
    with Store(path) as store:
        everyday(store)
        record_stated_anchor(store, ACCOUNT, DAY.isoformat(), "1000.00")
        store.connection.execute("DROP TABLE disregarded_balances")
        store.connection.commit()
    stamp(path, 19)
    return path


class TestAStoreLeftAtVersionNineteen:
    def test_Store_OpenedTwiceUnderTheNewCode_GrowsTheTableOnceAndKeepsItsData(self, held):
        with Store(held) as store:
            assert stamped(held) == str(SCHEMA_VERSION) == "20"
            assert [a.balance_minor for a in stated_anchors(store, ACCOUNT)] == [100000]
            assert store.disregard_balance(ACCOUNT, DAY, "stated", "stated", 100000)

        with Store(held) as store:
            assert stamped(held) == "20"
            assert store.disregarded_balance_keys(ACCOUNT) == [(DAY, "stated", "stated")]
            assert [a.balance_minor for a in stated_anchors(store, ACCOUNT)] == [100000]


class TestAStoreWrittenByANewerRelease:
    def test_Store_StampedNewerThanTheCode_IsRefusedAndStaysStampedNewer(self, held):
        stamp(held, SCHEMA_VERSION + 1)

        with pytest.raises(StoreIsNewer) as refused:
            Store(held)

        assert str(SCHEMA_VERSION + 1) in str(refused.value)
        assert stamped(held) == str(SCHEMA_VERSION + 1), "an older release restamped it"

    def test_Store_StampedNewer_IsNotMigratedBeforeItIsRefused(self, held):
        stamp(held, SCHEMA_VERSION + 1)

        with pytest.raises(StoreIsNewer):
            Store(held)

        connection = sqlite3.connect(held)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        connection.close()
        assert "disregarded_balances" not in tables, "the migrations ran before the refusal"

    def test_Store_StampedTheCurrentVersion_OpensWithoutRefusal(self, tmp_path):
        path = tmp_path / "current.sqlite3"
        with Store(path):
            pass

        with Store(path):
            pass

        assert stamped(path) == str(SCHEMA_VERSION)
