"""What the store keeps of who owns an account, and what happens to it across a rebuild and an
upgrade.

KNOWN ANSWERS, decided before the first run: an account with nothing declared has no owners
listed (the owner's alone); declaring a half share lists two owners with the larger first; declaring
again replaces them and keeps the old rows as history; clearing returns the account to the default;
a rebuild from raw leaves the declaration; a store made by the release before this one (schema 30)
opens, gains the table, and keeps its commitment. Every name is invented.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from landing import rebuild_from_raw
from obdi.ingest.ownership_records import OwnershipRefused
from obdi.ingest.store import SCHEMA_VERSION, Store

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "schema_history" / "26-ownership.sql"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def two(store: Store, mine: int = 50) -> tuple[int, int]:
    owner = store.owner_entity()
    other = store.create_empty_entity("Casey Wintermute")
    store.declare_ownership("current-main", [(owner, mine), (other, 100 - mine)])
    return owner, other


class TestOwnershipInTheStore:
    def test_AccountOwners_WhenNothingIsDeclared_ListsNoAccountAndSoTheOwnerAlone(self, store):
        assert store.account_owners() == {}

    def test_Declare_WhenSharedHalfAndHalf_ListsBothWithTheOwnerMarked(self, store):
        owner, other = two(store)

        owners = store.account_owners()["current-main"]

        assert sorted((o.entity_id, o.percent, o.owner) for o in owners) == sorted(
            [(owner, 50, True), (other, 50, False)]
        )

    def test_Declare_WhenSharesUnequal_ListsTheLargerFirst(self, store):
        owner, _ = two(store, mine=30)

        first, second = store.account_owners()["current-main"]

        assert (first.percent, second.percent) == (70, 30)
        assert second.entity_id == owner

    def test_Declare_WhenDeclaredAgain_ReplacesTheSharesAndKeepsTheOldRowsAsHistory(self, store):
        two(store, mine=50)
        two_again = store.account_owners()["current-main"]
        owner = next(o.entity_id for o in two_again if o.owner)
        other = next(o.entity_id for o in two_again if not o.owner)

        store.declare_ownership("current-main", [(owner, 25), (other, 75)])

        live = {o.entity_id: o.percent for o in store.account_owners()["current-main"]}
        assert live == {owner: 25, other: 75}
        rows = store.connection.execute("SELECT COUNT(*) FROM account_owners").fetchone()[0]
        assert rows == 4

    def test_Clear_WhenSharedThenMadeSole_ListsNothingAgainAndKeepsHistory(self, store):
        two(store)

        store.clear_ownership("current-main")

        assert store.account_owners() == {}
        assert store.connection.execute("SELECT COUNT(*) FROM account_owners").fetchone()[0] == 2

    def test_Clear_WhenNothingWasDeclared_SaysNothingAndChangesNothing(self, store):
        store.clear_ownership("current-main")

        assert store.connection.execute("SELECT COUNT(*) FROM account_owners").fetchone()[0] == 0

    def test_Declare_WhenRefused_LeavesTheEarlierDeclarationAsItWas(self, store):
        owner, other = two(store)

        with pytest.raises(OwnershipRefused):
            store.declare_ownership("current-main", [(owner, 90), (other, 20)])

        live = {o.entity_id: o.percent for o in store.account_owners()["current-main"]}
        assert live == {owner: 50, other: 50}

    def test_Irreplaceable_WhenAnAccountIsShared_CountsItSoAWipeCannotLoseItUnnoticed(self, store):
        before = store.irreplaceable()["accounts with a declared ownership"]
        two(store)

        assert store.irreplaceable()["accounts with a declared ownership"] == before + 1

    def test_Rebuild_WhenAnAccountIsShared_KeepsTheDeclaration(self, store):
        two(store)

        rebuild_from_raw(store)

        assert [o.percent for o in store.account_owners()["current-main"]] == [50, 50]

    def test_EntityNamed_WhenTheNameDiffersInCaseAndSpacing_FindsTheEntity(self, store):
        made = store.create_empty_entity("Casey Wintermute")

        assert store.entity_named("  casey   WINTERMUTE ") == made
        assert store.entity_named("") is None
        assert store.entity_named("Nobody Here") is None


class TestAStoreFromTheReleaseBeforeThisOne:
    def test_Store_WhenStampedSchema30_OpensGainsTheTableAndKeepsItsCommitment(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(SNAPSHOT.read_text(encoding="utf-8"))
        legacy.commit()
        legacy.close()

        with Store(path) as opened:
            assert opened.account_owners() == {}
            assert [c.name for c in opened.commitments()] == ["Hartsholme Gas"]
            two(opened)
            stamped = opened.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]

        assert stamped == str(SCHEMA_VERSION)
