"""What the owner decides about who a payment was to is kept: entities, and the shapes under them.

KNOWN ANSWERS, decided before the first run. The owner gathers three shapes of one invented
grocer into one entity, splits one of them apart again, renames it, and the store is rebuilt from
raw: after the rebuild the entity, its two remaining shapes, and the detached one's history are
all still there. A write that is refused changes nothing at all: no entity, no half of a merge.
The decision about the last shape leaving an entity is that the entity is removed, because an
entity with no shape names nothing a page or the detector could read.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from obdi.entities import EntityRefused
from obdi.rebuild import rebuild_from_raw
from obdi.store import SCHEMA_VERSION, Store

GROCER = ("fernhollow grocers", "fernhollow grocers express", "fernhollow metro")
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def names_and_shapes(store: Store) -> dict[str, tuple[str, ...]]:
    return {e.name: e.shapes for e in store.entities_with_shapes()}


class TestGatheringShapesIntoAnEntity:
    def test_Entity_WhenCreatedFromThreeShapes_ListsThemUnderTheNameTheOwnerGave(self, store):
        entity = store.create_entity("Fernhollow", GROCER, now=NOW)

        assert names_and_shapes(store) == {"Fernhollow": tuple(sorted(GROCER))}
        assert store.shape_entities() == dict.fromkeys(GROCER, (entity, "Fernhollow"))

    def test_Entity_WhenCreatedWithNoShapes_IsRefusedAndNothingIsKept(self, store):
        with pytest.raises(EntityRefused, match="at least one shape"):
            store.create_entity("Fernhollow", (), now=NOW)

        assert names_and_shapes(store) == {}

    @pytest.mark.parametrize("name", ["", "   ", "\t\n"])
    def test_Entity_WhenNamedNothing_IsRefusedAndTheShapesStayFree(self, store, name):
        with pytest.raises(EntityRefused, match="needs a name"):
            store.create_entity(name, GROCER, now=NOW)

        assert names_and_shapes(store) == {}
        assert store.shape_entities() == {}

    def test_Entity_WhenOneShapeBelongsToAnotherEntity_IsRefusedWholeNotHalfMerged(self, store):
        store.create_entity("Fernhollow", GROCER[:1], now=NOW)

        with pytest.raises(EntityRefused, match="already belongs"):
            store.create_entity("Other", (GROCER[1], GROCER[0]), now=NOW)

        assert names_and_shapes(store) == {"Fernhollow": (GROCER[0],)}

    def test_Entity_WhenTheNameIsInUseByAnotherEntity_IsRefused(self, store):
        store.create_entity("Fernhollow", GROCER[:1], now=NOW)

        with pytest.raises(EntityRefused, match="already an entity"):
            store.create_entity("FERNHOLLOW", GROCER[1:2], now=NOW)

    def test_Entity_WhenTheSameShapeIsListedTwice_HoldsItOnce(self, store):
        store.create_entity("Fernhollow", (GROCER[0], GROCER[0]), now=NOW)

        assert names_and_shapes(store) == {"Fernhollow": (GROCER[0],)}


class TestAttachingLaterShapes:
    def test_Shape_WhenAttachedToAnExistingEntity_JoinsItsShapes(self, store):
        entity = store.create_entity("Fernhollow", GROCER[:1], now=NOW)

        store.attach_shapes(entity, GROCER[1:], now=NOW)

        assert names_and_shapes(store) == {"Fernhollow": tuple(sorted(GROCER))}

    def test_Shape_WhenItBelongsToAnotherEntity_IsRefusedAndStays(self, store):
        first = store.create_entity("Fernhollow", GROCER[:1], now=NOW)
        store.create_entity("Other", GROCER[1:2], now=NOW)

        with pytest.raises(EntityRefused, match="already belongs"):
            store.attach_shapes(first, (GROCER[2], GROCER[1]), now=NOW)

        assert names_and_shapes(store) == {"Fernhollow": (GROCER[0],), "Other": (GROCER[1],)}

    def test_Shape_WhenAttachedToAnEntityThatIsNotThere_IsRefused(self, store):
        with pytest.raises(EntityRefused, match="no such entity"):
            store.attach_shapes(99, GROCER[:1], now=NOW)

    def test_Shape_WhenAttachedToARemovedEntity_IsRefused(self, store):
        entity = store.create_entity("Fernhollow", GROCER[:1], now=NOW)
        store.detach_shape(GROCER[0], now=NOW)

        with pytest.raises(EntityRefused, match="no such entity"):
            store.attach_shapes(entity, GROCER[1:2], now=NOW)

    def test_Shape_WhenNoneIsGiven_IsRefused(self, store):
        entity = store.create_entity("Fernhollow", GROCER[:1], now=NOW)

        with pytest.raises(EntityRefused, match="at least one shape"):
            store.attach_shapes(entity, (), now=NOW)


class TestSplittingApart:
    def test_Shape_WhenSplitApart_LeavesTheEntityAndCanJoinAnother(self, store):
        store.create_entity("Fernhollow", GROCER, now=NOW)

        assert store.detach_shape(GROCER[2], now=NOW + timedelta(minutes=1)) is True

        assert names_and_shapes(store) == {"Fernhollow": tuple(sorted(GROCER[:2]))}
        other = store.create_entity("Metro", GROCER[2:], now=NOW + timedelta(minutes=2))
        assert store.shape_entities()[GROCER[2]] == (other, "Metro")

    def test_Shape_WhenSplitApart_IsStampedNotDeletedSoTheHistoryIsKept(self, store):
        store.create_entity("Fernhollow", GROCER, now=NOW)
        store.detach_shape(GROCER[2], now=NOW + timedelta(minutes=1))

        rows = store.connection.execute(
            "SELECT detached_at FROM entity_shapes WHERE shape = ?", (GROCER[2],)
        ).fetchall()

        assert [r["detached_at"] for r in rows] == [(NOW + timedelta(minutes=1)).isoformat()]

    def test_Shape_WhenItBelongsToNoEntity_SaysSoAndChangesNothing(self, store):
        store.create_entity("Fernhollow", GROCER[:1], now=NOW)

        assert store.detach_shape("never seen", now=NOW) is False
        assert names_and_shapes(store) == {"Fernhollow": (GROCER[0],)}

    def test_Entity_WhenItsLastShapeIsSplitApart_IsRemovedAndItsNameIsFreeAgain(self, store):
        store.create_entity("Fernhollow", GROCER[:1], now=NOW)

        store.detach_shape(GROCER[0], now=NOW)

        assert names_and_shapes(store) == {}
        store.create_entity("Fernhollow", GROCER[1:2], now=NOW)
        assert names_and_shapes(store) == {"Fernhollow": (GROCER[1],)}
        removed = store.connection.execute(
            "SELECT removed_at FROM entities WHERE removed_at IS NOT NULL"
        ).fetchall()
        assert len(removed) == 1


class TestRenaming:
    def test_Entity_WhenRenamed_KeepsItsShapesUnderTheNewName(self, store):
        entity = store.create_entity("Fernhollow", GROCER, now=NOW)

        store.rename_entity(entity, "  Fernhollow Grocers  ")

        assert names_and_shapes(store) == {"Fernhollow Grocers": tuple(sorted(GROCER))}

    @pytest.mark.parametrize("name", ["", "  "])
    def test_Entity_WhenRenamedToNothing_IsRefusedAndKeepsItsName(self, store, name):
        entity = store.create_entity("Fernhollow", GROCER, now=NOW)

        with pytest.raises(EntityRefused, match="needs a name"):
            store.rename_entity(entity, name)

        assert list(names_and_shapes(store)) == ["Fernhollow"]

    def test_Entity_WhenRenamedToAnotherEntitysName_IsRefused(self, store):
        store.create_entity("Fernhollow", GROCER[:1], now=NOW)
        second = store.create_entity("Other", GROCER[1:2], now=NOW)

        with pytest.raises(EntityRefused, match="already an entity"):
            store.rename_entity(second, "fernhollow")

    def test_Entity_WhenRenamedToItsOwnNameInAnotherCase_IsAllowed(self, store):
        entity = store.create_entity("Fernhollow", GROCER[:1], now=NOW)

        store.rename_entity(entity, "FERNHOLLOW")

        assert list(names_and_shapes(store)) == ["FERNHOLLOW"]

    def test_Entity_WhenItIsNotThere_IsRefused(self, store):
        with pytest.raises(EntityRefused, match="no such entity"):
            store.rename_entity(7, "Anything")


class TestSurvivingTheRebuildFromRaw:
    def test_Entities_WhenTheStoreIsRebuiltFromRaw_AreStillThere(self, store):
        entity = store.create_entity("Fernhollow", GROCER, now=NOW)
        store.detach_shape(GROCER[2], now=NOW)
        store.rename_entity(entity, "Fernhollow Grocers")

        rebuild_from_raw(store)

        assert names_and_shapes(store) == {"Fernhollow Grocers": tuple(sorted(GROCER[:2]))}
        assert (
            store.connection.execute("SELECT COUNT(*) FROM entity_shapes").fetchone()[0] == 3
        ), "the detached shape's history was lost"


class TestAStoreFromBeforeTheEntityTables:
    def test_Store_StampedVersion22WithoutTheTables_GrowsThemOnOpenAndTakesAnEntity(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path) as old:
            old.connection.execute("DROP TABLE entity_shapes")
            old.connection.execute("DROP TABLE entities")
            old.connection.execute(
                "UPDATE obdi_meta SET value = '22' WHERE key = 'schema_version'"
            )
            old.connection.commit()

        with Store(path) as opened:
            opened.create_entity("Fernhollow", GROCER, now=NOW)
            assert names_and_shapes(opened) == {"Fernhollow": tuple(sorted(GROCER))}

        connection = sqlite3.connect(path)
        stamped = connection.execute(
            "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        connection.close()
        assert stamped == str(SCHEMA_VERSION)
        assert SCHEMA_VERSION >= 23
