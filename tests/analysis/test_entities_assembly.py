"""The owner's entities, assembled from the store's rows by the analysis.

The store holds an entity's hand-attached names, its rules, and the names split apart from them;
`entities_of` is the one function that puts them together, and `detach_shape` and `exclude_shape`
are where the analysis tells the store whether a rule matches. Names are invented.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from obdi.analysis.entities import (
    clean_rule,
    detach_shape,
    entities_of,
    exclude_shape,
    shape_entities,
    with_rules,
)
from obdi.ingest.entity_records import BEGINS, EntityRefused
from obdi.ingest.store import Store

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
HAND = "fernhollow grocers"
VARIANT = "fernhollow express"
UNRELATED = "marlowe bakery"
KNOWN = (HAND, VARIANT, UNRELATED)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def fernhollow(store: Store) -> int:
    entity = store.create_entity("Fernhollow", [HAND], now=NOW)
    store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)
    return entity


class TestEntitiesOfPutsTheStoresRowsTogether:
    def test_EntitiesOf_WithARuleAndKnownNames_HoldsTheNamesTheRuleMatches(self, store):
        fernhollow(store)

        (found,) = entities_of(store, KNOWN)

        assert found.shapes == tuple(sorted([HAND, VARIANT]))
        assert found.by_rule == (VARIANT,)

    def test_EntitiesOf_GivesWhatWithRulesGivesOverTheStoresThreeRowSets(self, store):
        entity = fernhollow(store)
        store.exclude_shape(entity, VARIANT, now=NOW)

        assembled = entities_of(store, KNOWN)

        assert assembled == with_rules(
            store.entities_with_shapes(),
            store.entity_rules(),
            store.entity_exclusions(),
            KNOWN,
        )
        assert assembled[0].shapes == (HAND,)

    def test_EntitiesOf_WithNoKnownNames_IsTheHandAttachedNamesAlone(self, store):
        fernhollow(store)

        (found,) = entities_of(store)

        assert found.shapes == (HAND,)

    def test_EntitiesOf_WhenNoEntityKeepsARule_AsksForNoExclusions(self, store):
        store.create_entity("Marlowe", [UNRELATED], now=NOW)
        statements: list[str] = []
        store.connection.set_trace_callback(statements.append)

        entities_of(store, KNOWN)

        store.connection.set_trace_callback(None)
        assert not any("entity_exclusions" in sql for sql in statements)

    def test_EntitiesOf_WithNoEntities_ReadsNeitherRulesNorExclusions(self, store):
        statements: list[str] = []
        store.connection.set_trace_callback(statements.append)

        assert entities_of(store, KNOWN) == []

        store.connection.set_trace_callback(None)
        assert not any("entity_rules" in sql or "entity_exclusions" in sql for sql in statements)

    def test_ShapeEntities_WithARule_NamesTheEntityOfARuleAttachedName(self, store):
        entity = fernhollow(store)

        assert shape_entities(store, KNOWN) == {
            HAND: (entity, "Fernhollow"),
            VARIANT: (entity, "Fernhollow"),
        }
        assert store.shape_entities() == {HAND: (entity, "Fernhollow")}


class TestSplittingApartKnowsWhetherARuleWouldAttachItAgain:
    def test_Detach_OfAHandNameARuleAlsoMatches_KeepsTheRuleFromAttachingItAgain(self, store):
        entity = fernhollow(store)

        assert detach_shape(store, HAND, now=NOW) is True

        assert store.entity_exclusions() == {(entity, HAND)}
        assert HAND not in entities_of(store, KNOWN)[0].shapes

    def test_Detach_OfAHandNameNoRuleMatches_RecordsNoExclusion(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)
        store.add_entity_rule(entity, BEGINS, "zephyr", now=NOW)

        detach_shape(store, HAND, now=NOW)

        assert store.entity_exclusions() == set()

    def test_Detach_OfANameNoEntityHolds_ReturnsFalse(self, store):
        fernhollow(store)

        assert detach_shape(store, UNRELATED, now=NOW) is False

    def test_Exclude_OfARuleAttachedName_RemovesItFromTheEntity(self, store):
        entity = fernhollow(store)

        exclude_shape(store, entity, VARIANT, now=NOW)

        assert entities_of(store, KNOWN)[0].shapes == (HAND,)

    def test_Exclude_OfANameNoRuleMatches_IsRefusedWithNothingWritten(self, store):
        entity = fernhollow(store)

        with pytest.raises(EntityRefused, match="No rule of this entity matches"):
            exclude_shape(store, entity, UNRELATED, now=NOW)

        assert store.entity_exclusions() == set()

    def test_Exclude_OnAnEntityThatIsMissing_IsRefusedAsMissingNotAsUnmatched(self, store):
        with pytest.raises(EntityRefused, match="no such entity"):
            exclude_shape(store, 99, UNRELATED, now=NOW)


class TestRuleWordsAreCheckedBeforeTheStoreIsAsked:
    def test_CleanRule_ForWordsOfOnlyAPaymentMethod_IsRefusedBeforeAnythingIsKept(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)

        with pytest.raises(EntityRefused, match="at least one word"):
            store.add_entity_rule(entity, *clean_rule(BEGINS, "faster payment"), now=NOW)

        assert store.entity_rules() == []

    def test_CleanRule_ForWordsWithADate_KeepsThemAsANameIsHeld(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)

        store.add_entity_rule(entity, *clean_rule(BEGINS, "FERNHOLLOW 1041 on 12 apr"), now=NOW)

        assert [r.words for r in store.entity_rules()] == ["fernhollow"]
