"""What a first merge teaches the page: free names that share a distinctive word with an entity.

KNOWN ANSWERS, decided before the first run.

  - An entity "Fernhollow" holds "fernhollow grocers london" and "fernhollow grocers york". The
    free name "fernhollow marketplace seller" shares the distinctive word "fernhollow", so it is
    offered under that entity; "zephyr water board" shares nothing and is not.
  - The same shared word is NOT enough when it is among the commonest words of the store (here
    "store", which the filler names all print), when it is a payment-method word, a country or
    company code, or a single letter.
  - A name that already sits in a proposed group is offered there and nowhere else; the owner's own
    entity is never a target; with two entities the one sharing more words wins; with none, nothing
    is offered.
"""

from __future__ import annotations

import pytest

from obdi.analysis import entities
from obdi.analysis.entities import view_of
from obdi.ingest.entity_records import OWNER_ROLE, Entity


@pytest.fixture(autouse=True)
def no_common_words(monkeypatch):
    """In a store of a dozen names a word that follows three different openings is common, so the
    scenarios below, which are about what a shared word means, set that rule aside; the tests of
    the rule itself set their own figure."""
    monkeypatch.setattr(entities, "COMMON_AFTER_OPENINGS", 1000)


def held(name: str, *shapes: str, role: str | None = None, ident: int = 7) -> Entity:
    return Entity(ident, name, None, tuple(shapes), role)


class TestAFreeNameSharingADistinctiveWord:
    def test_Suggestion_WhenAFreeNameSharesADistinctiveToken_IsOfferedUnderTheEntity(self):
        entity = held("Fernhollow", "fernhollow grocers london", "fernhollow grocers york")
        counts = {
            "fernhollow grocers london": 3,
            "fernhollow grocers york": 2,
            "fernhollow marketplace seller": 1,
            "zephyr water board": 4,
        }

        view = view_of(counts, [entity])

        (suggestion,) = view.suggestions
        assert suggestion.entity.name == "Fernhollow"
        assert suggestion.shapes == ("fernhollow marketplace seller",)
        assert suggestion.tokens == ("fernhollow",)

    def test_Suggestion_WhenTheNameAloneSharesTheToken_IsOffered(self):
        entity = held("Fernhollow", "pnx")
        counts = {"pnx": 3, "fernhollow outlet": 2}

        view = view_of(counts, [entity])

        assert [s.shapes for s in view.suggestions] == [("fernhollow outlet",)]

    def test_Suggestion_WhenTheTokenIsAPluralOrSpacedFormOfTheEntitys_IsStillShared(self):
        entity = held("W M Morrison", "wm morrison")
        counts = {"wm morrison": 3, "morrisons petrol": 2, "zephyr water board": 1}

        view = view_of(counts, [entity])

        assert [s.shapes for s in view.suggestions] == [("morrisons petrol",)]

    def test_Suggestion_WhenNoEntityExists_OffersNothing(self):
        assert view_of({"a b": 3, "a c": 2}, []).suggestions == ()


class TestWhenTheSharedWordSaysNothingAboutWho:
    def corpus(self):
        fillers = {f"filler{n} store": 2 for n in range(5)}
        return {**fillers, "fernhollow store": 1, "zephyr store": 1, "fernhollow online": 1}

    def test_Suggestion_WhenTheSharedWordFollowsManyBrands_IsNotOffered(self, monkeypatch):
        # "store" follows the five fillers, "fernhollow", and "zephyr": seven different openings.
        monkeypatch.setattr(entities, "COMMON_AFTER_OPENINGS", 3)
        entity = held("Fernhollow Store", "fernhollow store")

        view = view_of(self.corpus(), [entity])

        offered = {s for suggestion in view.suggestions for s in suggestion.shapes}
        assert "zephyr store" not in offered
        assert "fernhollow online" in offered

    def test_Suggestion_WhenTheSameWordFollowsFewerBrandsThanTheFloor_IsOffered(self, monkeypatch):
        monkeypatch.setattr(entities, "COMMON_AFTER_OPENINGS", 8)
        entity = held("Fernhollow Store", "fernhollow store")

        view = view_of(self.corpus(), [entity])

        offered = {s for suggestion in view.suggestions for s in suggestion.shapes}
        assert "zephyr store" in offered

    def test_Suggestion_WhenTheSharedWordIsAMethodWord_IsNotOffered(self):
        entity = held("Savings Pot", "direct debit savings pot")
        counts = {"direct debit savings pot": 3, "direct debit gym": 2}

        view = view_of(counts, [entity])

        assert view.suggestions == ()

    def test_Suggestion_WhenTheSharedWordIsACountryOrCompanyCode_IsNotOffered(self):
        entity = held("Arden", "arden uk")
        counts = {"arden uk": 3, "bramley uk": 2, "cormorant ltd": 1}

        assert view_of(counts, [entity]).suggestions == ()

    def test_Suggestion_WhenTheSharedWordIsOneLetter_IsNotOffered(self):
        entity = held("Arden", "arden x")
        counts = {"arden x": 3, "bramley x": 2}

        assert view_of(counts, [entity]).suggestions == ()


class TestWhereAFreeNameIsOffered:
    def test_Suggestion_WhenTheNameIsAlreadyInAProposedGroup_IsOfferedThereOnly(self):
        entity = held("Fernhollow", "fernhollow grocers london")
        counts = {
            "fernhollow grocers london": 3,
            "marlowe bakery leeds": 2,
            "marlowe bakery york": 2,
            "fernhollow marlowe": 1,
        }

        view = view_of(counts, [entity])

        in_groups = {s for g in view.proposals.groups for s in g.shapes}
        offered = {s for suggestion in view.suggestions for s in suggestion.shapes}
        assert not in_groups & offered

    def test_Suggestion_WhenTwoEntitiesShareWords_TheOneSharingMostWins(self):
        first = held("Alpha Beta", "alpha beta", ident=1)
        second = held("Alpha", "alpha", ident=2)
        counts = {"alpha beta": 3, "alpha": 3, "alpha beta gamma": 1}

        view = view_of(counts, [first, second])

        assert [(s.entity.name, s.shapes) for s in view.suggestions] == [
            ("Alpha Beta", ("alpha beta gamma",))
        ]

    def test_Suggestion_WhenTheOnlyEntityIsTheOwner_IsNeverOffered(self):
        owner = held("Me", "transfer to savings", role=OWNER_ROLE)
        counts = {"transfer to savings": 3, "savings club": 2}

        assert view_of(counts, [owner]).suggestions == ()

    def test_Suggestion_WhenTheNameIsAlreadyUnderAnotherEntity_IsNotOffered(self):
        first = held("Fernhollow", "fernhollow grocers", ident=1)
        second = held("Marketplace", "fernhollow marketplace", ident=2)
        counts = {"fernhollow grocers": 3, "fernhollow marketplace": 2}

        assert view_of(counts, [first, second]).suggestions == ()

    @pytest.mark.parametrize("order", [1, 2])
    def test_Suggestion_WhateverTheOrderOfEntities_IsTheSame(self, order):
        entities_in = [held("Alpha", "alpha", ident=1), held("Beta", "beta", ident=2)]
        if order == 2:
            entities_in.reverse()
        counts = {"alpha": 2, "beta": 2, "alpha beta gamma": 1}

        view = view_of(counts, entities_in)

        assert [(s.entity.name, s.shapes) for s in view.suggestions] == [
            ("Alpha", ("alpha beta gamma",))
        ]
