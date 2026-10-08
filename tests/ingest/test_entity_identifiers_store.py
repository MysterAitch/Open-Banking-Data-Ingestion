"""An entity holds identifiers with kinds, and the attachments of the store before kinds survive.

KNOWN ANSWERS, decided before the first run. A store stamped 25 holds three attachments for one
invented grocer in `entity_shapes`: two live and one split apart. Opened by current code it has
no `entity_shapes`, two live description-kind identifiers declared by the owner, and the split
one's history as a stamped third. A stated name and a description-shape that print alike are two
identifiers, so both can be held (by one entity or by two); the same kind and value cannot be
held twice at once. Folding moves every identifier with its kind and source; splitting one apart
detaches the kind asked for and no other. A kind outside the four, or a basis that is neither
declared nor learned, is refused with nothing written.
"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from obdi.ingest.entity_records import (
    ACCOUNT,
    DECLARED,
    DESCRIPTION,
    LEARNED,
    SOURCE_ID,
    STATED_NAME,
    EntityRefused,
    Identifier,
)
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import SCHEMA_VERSION, Store

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "schema_history" / "22-entity-rules.sql"
SHAPE = "fernhollow grocers"
PRINTED = "fernhollow grocers express"
SPLIT_APART = "fernhollow metro"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def rows(store: Store, *, live_only: bool = False) -> list[sqlite3.Row]:
    everything = "SELECT * FROM entity_identifiers ORDER BY id"
    live = "SELECT * FROM entity_identifiers WHERE detached_at IS NULL ORDER BY id"
    return store.connection.execute(live if live_only else everything).fetchall()


class TestAStoreFromBeforeKinds:
    @pytest.fixture
    def old_path(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path) as fresh:
            fresh.connection.execute("DROP TABLE entity_identifiers")
            fresh.connection.executescript(SNAPSHOT.read_text(encoding="utf-8"))
            fresh.connection.execute(
                "INSERT INTO entities (name, created_at) VALUES ('Fernhollow', ?)",
                (NOW.isoformat(),),
            )
            fresh.connection.executemany(
                "INSERT INTO entity_shapes (of_entity, shape, attached_at, detached_at) "
                "VALUES (1, ?, ?, ?)",
                [
                    (SHAPE, NOW.isoformat(), None),
                    (PRINTED, NOW.isoformat(), None),
                    (SPLIT_APART, NOW.isoformat(), (NOW + timedelta(days=1)).isoformat()),
                ],
            )
            fresh.connection.execute(
                "UPDATE obdi_meta SET value = '25' WHERE key = 'schema_version'"
            )
            fresh.connection.commit()
        return path

    def test_Attachments_WhenAStoreStampedBeforeKindsIsOpened_MoveAsDeclaredDescriptions(
        self, old_path
    ):
        with Store(old_path) as opened:
            (entity,) = opened.entities_with_shapes()
            kept = rows(opened)

        assert entity.shapes == tuple(sorted((SHAPE, PRINTED)))
        assert [(i.kind, i.value, i.basis) for i in entity.identifiers] == [
            (DESCRIPTION, SHAPE, DECLARED),
            (DESCRIPTION, PRINTED, DECLARED),
        ]
        assert [(r["kind"], r["value"]) for r in kept] == [
            (DESCRIPTION, SHAPE),
            (DESCRIPTION, PRINTED),
            (DESCRIPTION, SPLIT_APART),
        ], "the split-apart shape's history must move too"
        assert [r["detached_at"] is None for r in kept] == [True, True, False]

    def test_OldTable_WhenTheMigrationHasRun_IsGoneAndTheStoreMatchesAFreshOne(self, old_path):
        with Store(old_path) as opened:
            tables = {
                r[0]
                for r in opened.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            stamped = opened.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]

        assert "entity_shapes" not in tables
        assert "entity_identifiers" in tables
        assert stamped == str(SCHEMA_VERSION)

    def test_Store_WhenOpenedTwice_DoesNotMoveTheAttachmentsAgain(self, old_path):
        with Store(old_path):
            pass
        with Store(old_path) as again:
            assert len(rows(again)) == 3

    def test_Attachments_WhenTheStoreIsRebuiltFromRaw_KeepTheirKinds(self, old_path):
        with Store(old_path) as opened:
            opened.attach_shapes(1, [Identifier(STATED_NAME, "tesco stores", "starling")], now=NOW)
            rebuild_from_raw(opened)
            kept = {(r["kind"], r["value"], r["source"]) for r in rows(opened)}

        assert (STATED_NAME, "tesco stores", "starling") in kept
        assert (DESCRIPTION, SHAPE, "") in kept


class TestHoldingIdentifiersOfSeveralKinds:
    def test_Identifier_WhenAttachedWithAKindAndSource_IsKeptAndReturnedWithBoth(self, store):
        store.create_entity(
            "Tesco",
            [
                Identifier(STATED_NAME, "tesco stores", "starling", DECLARED, 4),
                Identifier(DESCRIPTION, "tesco stores birmingham"),
                Identifier(ACCOUNT, "20000012345678"),
                Identifier(SOURCE_ID, "party-7"),
            ],
            now=NOW,
        )

        (entity,) = store.entities_with_shapes()

        kinds = {(i.kind, i.value, i.source, i.support) for i in entity.identifiers}
        assert kinds == {
            (STATED_NAME, "tesco stores", "starling", 4),
            (DESCRIPTION, "tesco stores birmingham", "", 0),
            (ACCOUNT, "20000012345678", "", 0),
            (SOURCE_ID, "party-7", "", 0),
        }
        assert set(entity.shapes) == {i.value for i in entity.identifiers}

    def test_ABareString_IsADescriptionShapeDeclaredByTheOwner(self, store):
        store.create_entity("Fernhollow", [SHAPE], now=NOW)

        (found,) = rows(store)

        assert (found["kind"], found["declared_or_learned"], found["source"]) == (
            DESCRIPTION,
            DECLARED,
            "",
        )

    def test_AStatedNameAndADescriptionThatPrintAlike_AreTwoIdentifiersHeldApart(self, store):
        first = store.create_entity("Fernhollow", [Identifier(STATED_NAME, SHAPE)], now=NOW)
        second = store.create_entity(
            "Fernhollow Cafe", [Identifier(DESCRIPTION, SHAPE)], now=NOW
        )

        by_kind = {i.kind: e.id for e in store.entities_with_shapes() for i in e.identifiers}

        assert by_kind == {STATED_NAME: first, DESCRIPTION: second}

    def test_TheSameKindAndValue_WhenAnotherEntityHoldsIt_IsRefusedWhole(self, store):
        store.create_entity("Fernhollow", [Identifier(STATED_NAME, SHAPE)], now=NOW)

        with pytest.raises(EntityRefused, match="already belongs"):
            store.create_entity(
                "Other", [Identifier(DESCRIPTION, "free"), Identifier(STATED_NAME, SHAPE)], now=NOW
            )

        assert [e.name for e in store.entities_with_shapes()] == ["Fernhollow"]

    @pytest.mark.parametrize(
        ("identifier", "said"),
        [
            (Identifier("nickname", "x"), "kind of identifier"),
            (Identifier(STATED_NAME, "x", basis="guessed"), "declared or learned"),
        ],
    )
    def test_Identifier_OfAnUnknownKindOrBasis_IsRefusedAndNothingIsKept(
        self, store, identifier, said
    ):
        with pytest.raises(EntityRefused, match=said):
            store.create_entity("Fernhollow", [identifier], now=NOW)

        assert store.entities_with_shapes() == []

    def test_Identifier_WhenLearned_IsKeptAsLearnedWithItsSupport(self, store):
        store.create_entity(
            "Fernhollow", [Identifier(STATED_NAME, SHAPE, "", LEARNED, 12)], now=NOW
        )

        (found,) = rows(store)

        assert (found["declared_or_learned"], found["support"]) == (LEARNED, 12)


class TestSplittingAndFoldingKeepKinds:
    def test_Split_WhenTwoKindsPrintAlike_DetachesTheKindAskedForAndNoOther(self, store):
        entity = store.create_entity(
            "Fernhollow", [Identifier(STATED_NAME, SHAPE), Identifier(DESCRIPTION, SHAPE)], now=NOW
        )

        assert store.detach_shape(SHAPE, exclude=False, kind=DESCRIPTION, now=NOW) is True

        (found,) = store.entities_with_shapes()
        assert found.id == entity
        assert [(i.kind, i.value) for i in found.identifiers] == [(STATED_NAME, SHAPE)]

    def test_Split_WhenNoKindIsSaid_DetachesTheStrongestKindHeld(self, store):
        store.create_entity(
            "Fernhollow",
            [
                Identifier(DESCRIPTION, SHAPE),
                Identifier(STATED_NAME, SHAPE),
                Identifier(ACCOUNT, "1"),
            ],
            now=NOW,
        )

        store.detach_shape(SHAPE, exclude=False, now=NOW)

        (found,) = store.entities_with_shapes()
        assert {(i.kind, i.value) for i in found.identifiers} == {
            (ACCOUNT, "1"),
            (DESCRIPTION, SHAPE),
        }

    def test_Split_WhenTheKindIsNotHeld_ChangesNothing(self, store):
        store.create_entity("Fernhollow", [Identifier(STATED_NAME, SHAPE)], now=NOW)

        assert store.detach_shape(SHAPE, exclude=False, kind=DESCRIPTION, now=NOW) is False

        assert len(rows(store, live_only=True)) == 1

    def test_Fold_MovesEveryIdentifierWithItsKindSourceAndBasis(self, store):
        first = store.create_entity(
            "Fernhollow",
            [
                Identifier(STATED_NAME, SHAPE, "starling", DECLARED, 3),
                Identifier(ACCOUNT, "20000012345678"),
            ],
            now=NOW,
        )
        second = store.create_entity(
            "Fernhollow Group", [Identifier(DESCRIPTION, "group")], now=NOW
        )

        moved, into = store.fold_entity(first, "Fernhollow Group", now=NOW)

        assert (moved, into) == (2, "Fernhollow Group")
        (target,) = store.entities_with_shapes()
        assert target.id == second
        assert {(i.kind, i.value, i.source, i.support) for i in target.identifiers} == {
            (STATED_NAME, SHAPE, "starling", 3),
            (ACCOUNT, "20000012345678", "", 0),
            (DESCRIPTION, "group", "", 0),
        }

    def test_ChildEntity_TakesEveryKindOfTheNameMovedToIt(self, store):
        parent = store.create_entity(
            "Fernhollow",
            [
                Identifier(STATED_NAME, PRINTED),
                Identifier(DESCRIPTION, PRINTED),
                Identifier(DESCRIPTION, SHAPE),
            ],
            now=NOW,
        )

        child = store.make_child_entity(parent, PRINTED, "Express", now=NOW)

        by_id = {
            e.id: {(i.kind, i.value) for i in e.identifiers}
            for e in store.entities_with_shapes()
        }
        assert by_id[child] == {(STATED_NAME, PRINTED), (DESCRIPTION, PRINTED)}
        assert by_id[parent] == {(DESCRIPTION, SHAPE)}
