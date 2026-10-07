"""What the store holds and refuses where entities are concerned, with no rule engine in sight.

The store keeps the owner's rows and hands them back as records; what a rule matches, and whether
its words are fit to keep, are the analysis's to say (`tests/analysis/test_entities_assembly.py`).
These tests drive the store alone, over invented names.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from obdi.ingest.entity_records import BEGINS, CONTAINS, RULE_KINDS, EntityRefused
from obdi.ingest.store import Store

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
HAND = "fernhollow grocers"
OTHER = "fernhollow express"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


class TestTheStoreHoldsRowsAndAppliesNoRule:
    def test_Entities_WithALiveRule_AreReturnedWithOnlyTheirHandAttachedNames(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)
        store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)

        (found,) = store.entities_with_shapes()

        assert found.shapes == (HAND,)
        assert found.by_rule == ()

    def test_Rules_WhenAskedFor_AreReturnedAsKeptAndExclusionsAsRows(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)
        store.add_entity_rule(entity, CONTAINS, "express", now=NOW)
        store.exclude_shape(entity, OTHER, now=NOW)

        assert [(r.entity_id, r.kind, r.words) for r in store.entity_rules()] == [
            (entity, CONTAINS, "express")
        ]
        assert store.entity_exclusions() == {(entity, OTHER)}

    def test_Rule_KeptWithWordsAsGiven_IsKeptAsGivenNotRespelt(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)

        store.add_entity_rule(entity, BEGINS, "Fernhollow 1041", now=NOW)

        assert [r.words for r in store.entity_rules()] == ["Fernhollow 1041"]


class TestTheStoreKeepsItsOwnInvariants:
    def test_Rule_OfAKindOutsideTheTwoKnown_IsRefusedAndKeptNowhere(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)

        with pytest.raises(EntityRefused, match="begins with some words or contains them"):
            store.add_entity_rule(entity, "ends", "fernhollow", now=NOW)

        assert store.entity_rules() == []

    def test_Rule_OnAnEntityThatWasRemoved_IsRefused(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)
        store.detach_shape(HAND, exclude=False, now=NOW)

        with pytest.raises(EntityRefused, match="no such entity"):
            store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)

    def test_Rule_KeptTwiceInTheSameWords_IsRefusedTheSecondTime(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)
        store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)

        with pytest.raises(EntityRefused, match="already has that rule"):
            store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)

    def test_Shape_AlreadyHeldByAnotherEntity_IsRefusedFromTheStore(self, store):
        store.create_entity("Fernhollow", [HAND], now=NOW)

        with pytest.raises(EntityRefused, match="already belongs to an entity"):
            store.create_entity("Other", [HAND], now=NOW)

    def test_RuleKinds_AreTheTwoTheStoreKnows(self):
        assert RULE_KINDS == (BEGINS, CONTAINS)


class TestSplittingApartIsToldWhetherToRecordAnExclusion:
    def test_Detach_WhenToldToExclude_RecordsTheExclusionInTheSameCommit(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)
        store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)

        assert store.detach_shape(HAND, exclude=True, now=NOW) is True

        assert store.entity_exclusions() == {(entity, HAND)}

    def test_Detach_WhenNotToldToExclude_RecordsNoExclusion(self, store):
        entity = store.create_entity("Fernhollow", [HAND], now=NOW)
        store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)

        store.detach_shape(HAND, exclude=False, now=NOW)

        assert store.entity_exclusions() == set()

    def test_Detach_WithoutSayingWhetherToExclude_IsNotOffered(self):
        # A default of False would let a rule attach a name again that the owner split apart.
        parameter = inspect.signature(Store.detach_shape).parameters["exclude"]

        assert parameter.default is inspect.Parameter.empty

    def test_Detach_OfANameNoEntityHolds_ReturnsFalseAndWritesNothing(self, store):
        assert store.detach_shape("never seen", exclude=True, now=NOW) is False
        assert store.entity_exclusions() == set()
