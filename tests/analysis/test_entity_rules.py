"""An entity keeps rules, so a payee's next variant is under it on sight.

KNOWN ANSWERS, decided before the first run. An invented retailer, Sainsburys, is an entity with
one name attached by hand ("sainsburys southampton") and one rule, begins with "sainsburys". The
names the transactions hold are:

  sainsburys southampton        attached by hand (matched by the rule too, but listed once)
  sainsburys scotland           a new variant: under the entity BY RULE
  sainsburys local edinburgh    likewise
  sainsbury high street         under it by rule as well: a plural s is not a different word
  sainsburyville bank           NOT under it: a different word that begins alike
  tesco express                 a different payee

So the entity holds four names, three of them by rule. A name split apart from the rule is held
by neither. A rule that contains words matches them in whatever order the bank printed them, and
two rules of one entity that match one name list it once. A rule is refused when it is empty,
holds only a payment method, has a kind that is not offered, or is on an entity that has gone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from landing import rebuild_from_raw
from obdi.analysis.entities import (
    clean_rule,
    detach_shape,
    entities_of,
    exclude_shape,
    rule_matches,
    trial_rule,
    with_rules,
)
from obdi.analysis.entity_actions import SPLIT, apply_action
from obdi.ingest.entity_records import BEGINS, CONTAINS, EntityRefused, EntityRule
from obdi.ingest.store import Store

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
HAND = "sainsburys southampton"
SCOTLAND = "sainsburys scotland"
EDINBURGH = "sainsburys local edinburgh"
SINGULAR = "sainsbury high street"
BANK = "sainsburyville bank"
TESCO = "tesco express"
KNOWN = (HAND, SCOTLAND, EDINBURGH, SINGULAR, BANK, TESCO)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def sainsburys(store: Store) -> int:
    entity = store.create_entity("Sainsburys", [HAND], now=NOW)
    store.add_entity_rule(entity, BEGINS, "sainsburys", now=NOW)
    return entity


def names(store: Store, entity: int, known=KNOWN) -> tuple[str, ...]:
    (found,) = [e for e in entities_of(store, known) if e.id == entity]
    return found.shapes


class TestAnEntityHoldsTheNamesItsRulesMatch:
    def test_Entity_WithABeginsRule_HoldsEveryNameBeginningWithThoseWords(self, store):
        entity = sainsburys(store)

        assert names(store, entity) == tuple(sorted([HAND, SCOTLAND, EDINBURGH, SINGULAR]))

    def test_Entity_WithABeginsRule_ListsRuleMatchedNamesAsByRuleAndHandAttachedOnesNot(
        self, store
    ):
        entity = sainsburys(store)

        (found,) = [e for e in entities_of(store, KNOWN) if e.id == entity]

        assert set(found.by_rule) == {SCOTLAND, EDINBURGH, SINGULAR}
        assert HAND not in found.by_rule

    def test_Entity_WhenAVariantAppearsTomorrow_HoldsItWithNoPress(self, store):
        entity = sainsburys(store)
        before = names(store, entity, KNOWN)

        after = names(store, entity, (*KNOWN, "sainsburys glasgow central"))

        assert set(after) - set(before) == {"sainsburys glasgow central"}

    def test_Entity_AskedWithNoNamesKnown_HoldsOnlyWhatWasAttachedByHand(self, store):
        entity = sainsburys(store)

        assert names(store, entity, ()) == (HAND,)

    def test_Entity_WithAContainsRule_HoldsNamesWithThoseWordsInAnyOrder(self, store):
        entity = store.create_entity("Fernhollow", ["fernhollow grocers"], now=NOW)
        store.add_entity_rule(entity, CONTAINS, "grocers fernhollow", now=NOW)
        known = ("fernhollow grocers", "grocers fernhollow london", "fernhollow express")

        assert names(store, entity, known) == ("fernhollow grocers", "grocers fernhollow london")

    def test_Entity_WithTwoRulesMatchingOneName_ListsItOnce(self, store):
        entity = sainsburys(store)
        store.add_entity_rule(entity, CONTAINS, "scotland sainsburys", now=NOW)

        assert names(store, entity).count(SCOTLAND) == 1

    def test_Entity_WhenARuleMatchesNothingYet_IsKeptAndHoldsWhatItDid(self, store):
        entity = store.create_entity("Fernhollow", ["fernhollow grocers"], now=NOW)

        store.add_entity_rule(entity, BEGINS, "zephyr", now=NOW)

        assert names(store, entity) == ("fernhollow grocers",)
        assert [r.words for r in store.entity_rules()] == ["zephyr"]


class TestWhoWinsAName:
    def test_Name_AttachedByHandToAnotherEntity_StaysThereWhateverARuleSays(self, store):
        entity = sainsburys(store)
        other = store.create_entity("Scottish Branch", [SCOTLAND], now=NOW)

        assert SCOTLAND not in names(store, entity)
        assert names(store, other) == (SCOTLAND,)

    def test_Name_MatchedByTwoEntitiesRules_GoesToTheRuleOfMoreWords(self, store):
        broad = sainsburys(store)
        narrow = store.create_entity("Sainsburys Local", ["sainsburys local croydon"], now=NOW)
        store.add_entity_rule(narrow, BEGINS, "sainsburys local", now=NOW)

        assert EDINBURGH in names(store, narrow)
        assert EDINBURGH not in names(store, broad)

    def test_Name_MatchedByTwoEntitiesRulesOfEqualLength_GoesToTheOlderRule(self, store):
        first = store.create_entity("First", ["alpha one"], now=NOW)
        second = store.create_entity("Second", ["beta one"], now=NOW)
        store.add_entity_rule(first, CONTAINS, "shared", now=NOW)
        store.add_entity_rule(second, CONTAINS, "shared", now=NOW)

        known = ("shared thing",)

        assert names(store, first, known) == ("alpha one", "shared thing")
        assert names(store, second, known) == ("beta one",)


class TestSplittingANameApart:
    def test_Split_OfANameAHeldByRule_RecordsAnExclusionAndTheRuleLetsItGo(self, store):
        entity = sainsburys(store)

        message = apply_action(store, dict.fromkeys(KNOWN, 1), SPLIT, {"shape": [SCOTLAND]})

        assert SCOTLAND not in names(store, entity)
        assert store.entity_exclusions() == {(entity, SCOTLAND)}
        assert "will not attach that name again" in message
        assert names(store, entity, (*KNOWN, "sainsburys glasgow central")).count(
            "sainsburys glasgow central"
        ) == 1

    def test_Split_OfAHandAttachedNameNoRuleMatches_IsAPlainDetach(self, store):
        entity = store.create_entity(
            "Fernhollow", ["fernhollow grocers", "fernhollow metro"], now=NOW
        )

        apply_action(store, {}, SPLIT, {"shape": ["fernhollow metro"]})

        assert names(store, entity, ("fernhollow grocers",)) == ("fernhollow grocers",)
        assert store.entity_exclusions() == set()

    def test_Split_OfAHandAttachedNameARuleAlsoMatches_DetachesAndExcludesSoItDoesNotReturn(
        self, store
    ):
        entity = sainsburys(store)

        apply_action(store, dict.fromkeys(KNOWN, 1), SPLIT, {"shape": [HAND]})

        assert HAND not in names(store, entity)
        assert store.entity_exclusions() == {(entity, HAND)}

    def test_Exclude_OfANameNoRuleMatches_IsRefused(self, store):
        entity = sainsburys(store)

        with pytest.raises(EntityRefused, match="No rule of this entity matches"):
            exclude_shape(store, entity, TESCO, now=NOW)

        assert store.entity_exclusions() == set()

    def test_Split_OfTheLastHandAttachedName_KeepsAnEntityThatStillHasARule(self, store):
        entity = store.create_entity("Seed", ["seed pod"], now=NOW)
        store.add_entity_rule(entity, BEGINS, "sainsburys", now=NOW)
        known = {SCOTLAND: 1}

        apply_action(store, known, SPLIT, {"shape": ["seed pod"]})

        assert [e.name for e in entities_of(store, known)] == ["Seed"]

    def test_Split_OfTheLastHandAttachedNameOfAnEntityWithNoRule_RemovesTheEntity(self, store):
        store.create_entity("Fernhollow", ["fernhollow grocers"], now=NOW)

        message = apply_action(store, {}, SPLIT, {"shape": ["fernhollow grocers"]})

        assert store.entities_with_shapes() == []
        assert "so it is gone" in message


class TestARuleIsRefusedWhenItCouldMatchEveryone:
    @pytest.mark.parametrize("words", ["", "   ", "faster payment", "a", "12345", "card payment"])
    def test_Rule_WithNothingThatTellsAPayeeApart_IsRefusedAndKeptNowhere(self, store, words):
        entity = store.create_entity("Fernhollow", ["fernhollow grocers"], now=NOW)

        with pytest.raises(EntityRefused, match="at least one word"):
            store.add_entity_rule(entity, *clean_rule(BEGINS, words), now=NOW)

        assert store.entity_rules() == []

    def test_Rule_OfAKindThatIsNotOffered_IsRefused(self, store):
        entity = store.create_entity("Fernhollow", ["fernhollow grocers"], now=NOW)

        with pytest.raises(EntityRefused, match="begins with some words or contains them"):
            store.add_entity_rule(entity, *clean_rule("ends", "fernhollow"), now=NOW)

    def test_Rule_OnAnEntityThatWasRemoved_IsRefused(self, store):
        entity = store.create_entity("Fernhollow", ["fernhollow grocers"], now=NOW)
        detach_shape(store, "fernhollow grocers", now=NOW)

        with pytest.raises(EntityRefused, match="no such entity"):
            store.add_entity_rule(entity, BEGINS, "fernhollow", now=NOW)

    def test_Rule_KeptTwice_IsRefusedTheSecondTime(self, store):
        entity = sainsburys(store)

        with pytest.raises(EntityRefused, match="already has that rule"):
            store.add_entity_rule(entity, *clean_rule(BEGINS, "Sainsburys"), now=NOW)

        assert len(store.entity_rules()) == 1

    def test_Rule_WordsWithANumberAndADate_AreKeptAsANameIs(self, store):
        entity = store.create_entity("Fernhollow", ["fernhollow grocers"], now=NOW)

        store.add_entity_rule(entity, *clean_rule(BEGINS, "FERNHOLLOW 1041 on 12 apr"), now=NOW)

        assert [r.words for r in store.entity_rules()] == ["fernhollow"]


class TestRemovingARule:
    def test_Remove_OfARule_ReleasesTheNamesOnlyItHeld(self, store):
        entity = sainsburys(store)
        (rule,) = store.entity_rules()

        store.remove_entity_rule(rule.id, now=NOW)

        assert names(store, entity) == (HAND,)
        assert store.entity_rules() == []

    def test_Remove_OfARuleAlreadyRemoved_IsRefused(self, store):
        sainsburys(store)
        (rule,) = store.entity_rules()
        store.remove_entity_rule(rule.id, now=NOW)

        with pytest.raises(EntityRefused, match="no such rule"):
            store.remove_entity_rule(rule.id, now=NOW + timedelta(minutes=1))

    def test_Remove_KeepsTheRulesHistoryAsAStamp(self, store):
        sainsburys(store)
        (rule,) = store.entity_rules()
        store.remove_entity_rule(rule.id, now=NOW)

        stamped = store.connection.execute(
            "SELECT removed_at FROM entity_rules WHERE id = ?", (rule.id,)
        ).fetchone()[0]

        assert stamped == NOW.isoformat()


class TestFoldingAnEntityKeepsItsRules:
    def test_Fold_OfAnEntityWithARule_MovesTheRuleToTheTarget(self, store):
        sainsburys(store)
        target = store.create_entity("Groceries", ["fernhollow grocers"], now=NOW)
        (entity,) = [e.id for e in store.entities_with_shapes() if e.name == "Sainsburys"]

        store.fold_entity(entity, "Groceries", now=NOW)

        assert SCOTLAND in names(store, target)
        assert [r.entity_id for r in store.entity_rules()] == [target]

    def test_Fold_WhenTheTargetKeepsTheSameRule_KeepsOneOfIt(self, store):
        sainsburys(store)
        target = store.create_entity("Groceries", ["fernhollow grocers"], now=NOW)
        store.add_entity_rule(target, BEGINS, "sainsburys", now=NOW)
        (entity,) = [e.id for e in store.entities_with_shapes() if e.name == "Sainsburys"]

        store.fold_entity(entity, "Groceries", now=NOW)

        assert [r.words for r in store.entity_rules()] == ["sainsburys"]


class TestTheDecisionsSurviveTheRebuildFromRaw:
    def test_RulesAndExclusions_WhenTheStoreIsRebuiltFromRaw_AreStillThere(self, store):
        entity = sainsburys(store)
        exclude_shape(store, entity, SCOTLAND, now=NOW)

        rebuild_from_raw(store)

        assert [(r.kind, r.words) for r in store.entity_rules()] == [(BEGINS, "sainsburys")]
        assert store.entity_exclusions() == {(entity, SCOTLAND)}

    def test_Irreplaceable_CountsARuleAndAnExclusionAsHandWork(self, store):
        entity = sainsburys(store)
        exclude_shape(store, entity, SCOTLAND, now=NOW)

        assert store.irreplaceable()["entity rules and names split from them"] == 2


class TestTryingARuleWritesNothing:
    def entities(self, store):
        return store.entities_with_shapes()

    def test_Trial_ListsTheNamesTheRuleWouldAttachAndKeepsNothing(self, store):
        entity = store.create_entity("Sainsburys", [HAND], now=NOW)

        trial = trial_rule(
            self.entities(store), store.entity_rules(), store.entity_exclusions(),
            KNOWN, entity, BEGINS, "sainsburys",
        )

        assert trial.attach == tuple(sorted([SCOTLAND, EDINBURGH, SINGULAR]))
        assert trial.already == 1 and trial.elsewhere == 0
        assert store.entity_rules() == []

    def test_Trial_CountsNamesAnotherEntityHoldsAsElsewhereAndDoesNotAttachThem(self, store):
        entity = store.create_entity("Sainsburys", [HAND], now=NOW)
        store.create_entity("Scottish Branch", [SCOTLAND], now=NOW)

        trial = trial_rule(
            self.entities(store), store.entity_rules(), store.entity_exclusions(),
            KNOWN, entity, BEGINS, "sainsburys",
        )

        assert SCOTLAND not in trial.attach and trial.elsewhere == 1

    def test_Trial_OfARuleMatchingNothing_AttachesNothing(self, store):
        entity = store.create_entity("Sainsburys", [HAND], now=NOW)

        trial = trial_rule(
            self.entities(store), store.entity_rules(), store.entity_exclusions(),
            KNOWN, entity, BEGINS, "zephyr",
        )

        assert trial.attach == () and trial.already == 0

    def test_Trial_OfAnEmptyRule_IsRefusedAsKeepingItWouldBe(self, store):
        entity = store.create_entity("Sainsburys", [HAND], now=NOW)

        with pytest.raises(EntityRefused, match="at least one word"):
            trial_rule(
                self.entities(store), [], set(), KNOWN, entity, BEGINS, "  ",
            )

    def test_Trial_OnAMissingEntity_IsRefused(self, store):
        with pytest.raises(EntityRefused, match="no such entity"):
            trial_rule([], [], set(), KNOWN, 99, BEGINS, "sainsburys")


class TestHowAWordIsCompared:
    @pytest.mark.parametrize(
        ("kind", "words", "shape", "expected"),
        [
            (BEGINS, "sainsburys", "sainsburys local", True),
            (BEGINS, "sainsburys", "sainsbury local", True),
            (BEGINS, "sainsburys", "direct debit sainsburys local", True),
            (BEGINS, "sainsburys local", "sainsburys", False),
            (BEGINS, "local", "sainsburys local", False),
            (CONTAINS, "local sainsburys", "sainsburys local edinburgh", True),
            (CONTAINS, "local sainsburys", "sainsburys edinburgh", False),
        ],
    )
    def test_Rule_ComparesWordsAsNamesAreComparedElsewhere(self, kind, words, shape, expected):
        assert rule_matches(kind, words, shape) is expected

    def test_WithRules_OnARuleOfAnEntityNotAmongThoseGiven_IsIgnored(self):
        assert with_rules([], [EntityRule(1, 7, BEGINS, "sainsburys")], set(), KNOWN) == []
