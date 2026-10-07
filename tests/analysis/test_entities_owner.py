"""The owner is an entity: the shapes that are legs of his own transfers are his, not a payee's.

KNOWN ANSWERS, decided before the first run.

  - Of the shapes "transfer to savings" (six transactions, all transfer legs), "from joint account"
    (four, all legs), "fernhollow grocers" (nine, one a leg), and "ardent landlord" (two, one a
    leg), only the first two are the owner's: more than half of their transactions are legs. The
    grocer and the landlord are payees, however one of each was a leg.
  - Exactly half is not enough; no leg at all offers nothing; a shape already under an entity is
    not offered again.
  - The owner group is never also a payee group: its shapes are out of the proposals.
  - Attaching those shapes to the owner entity leaves every series the detector finds as it was,
    because a transfer leg is found by its pair and never by its name; a shape's rows that are NOT
    legs are the one place the entity name is read.
  - The owner entity is made on first use as "Me" and then reused; where an entity of that name is
    already there it is taken, and a second press adds to it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from obdi.analysis.entities import detach_shape, view_of
from obdi.analysis.recurring import Series, find_recurring
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.entity_records import (
    DESCRIPTION,
    OWNER_NAME,
    OWNER_ROLE,
    Entity,
    EntityRefused,
)
from obdi.ingest.store import Store

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 7)

COUNTS = {
    "transfer to savings": 6,
    "from joint account": 4,
    "fernhollow grocers": 9,
    "ardent landlord": 2,
}
LEGS = {
    "transfer to savings": 6,
    "from joint account": 4,
    "fernhollow grocers": 1,
    "ardent landlord": 1,
}


class TestWhichShapesAreTheOwners:
    def test_Owner_WhenShapesAreMostlyTransferLegs_IsProposedAndNotAsAPayee(self):
        view = view_of(COUNTS, [], LEGS)

        assert view.owner is not None
        assert set(view.owner.shapes) == {"transfer to savings", "from joint account"}
        assert (view.owner.transactions, view.owner.legs) == (10, 10)
        for group in view.proposals.groups:
            assert not set(group.shapes) & set(view.owner.shapes)

    def test_Owner_WhenTheOwnersShapesShareWordsWithAPayee_TheyAreStillNotOfferedAsAPayee(self):
        counts = {"transfer to savings": 3, "transfer to savings pot": 2, "transfer to bob": 2}
        legs = {"transfer to savings": 3, "transfer to savings pot": 2}

        view = view_of(counts, [], legs)

        assert view.owner is not None
        assert view.owner.shapes == ("transfer to savings", "transfer to savings pot")
        assert view.proposals.groups == ()

    def test_Owner_WhenLegsAreExactlyHalf_IsNotProposed(self):
        assert view_of({"a payee": 4}, [], {"a payee": 2}).owner is None

    def test_Owner_WhenNoShapeIsMostlyLegs_ProposesNothing(self):
        assert view_of(COUNTS, [], {"fernhollow grocers": 2}).owner is None

    def test_Owner_WhenNoLegsAreKnown_ProposesNothing(self):
        assert view_of(COUNTS, []).owner is None

    def test_Owner_WhenTheShapesAreAlreadyAttached_IsNotProposedAgain(self):
        held = Entity(1, "Me", None, ("transfer to savings", "from joint account"), OWNER_ROLE)

        assert view_of(COUNTS, [held], LEGS).owner is None

    def test_Owner_WhenOneShapeIsAttachedElsewhere_OnlyTheFreeOneIsProposed(self):
        other = Entity(2, "Savings", None, ("transfer to savings",))

        view = view_of(COUNTS, [other], LEGS)

        assert view.owner is not None
        assert view.owner.shapes == ("from joint account",)


def row(day: int, minor: int, description: str, account: str, name: str) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=date(2026, 5, day),
        booking_date=date(2026, 5, day),
        description=description,
        source="synthetic",
        tier=SourceTier.SYNTHETIC,
        entity_id=name,
    )


class TestTheDetectorIsUnchangedByTheOwnerEntity:
    def world(self) -> tuple[list[Transaction], list[tuple[str, str]]]:
        rows: list[Transaction] = []
        pairs: list[tuple[str, str]] = []
        for month in range(1, 7):
            when = date(2026, month, 3)
            out = Transaction(
                account_id="current", amount_minor=-5000, value_date=when, booking_date=when,
                description="TRANSFER TO SAVINGS", source="synthetic",
                tier=SourceTier.SYNTHETIC, entity_id=f"out{month}",
            )
            arrive = Transaction(
                account_id="savings", amount_minor=5000, value_date=when, booking_date=when,
                description="FROM CURRENT", source="synthetic",
                tier=SourceTier.SYNTHETIC, entity_id=f"in{month}",
            )
            rows += [out, arrive]
            pairs.append((out.entity_id, arrive.entity_id))
            rows.append(
                Transaction(
                    account_id="current", amount_minor=-999, value_date=date(2026, month, 15),
                    booking_date=date(2026, month, 15), description="ZEPHYR WATER BOARD",
                    source="synthetic", tier=SourceTier.SYNTHETIC, entity_id=f"w{month}",
                )
            )
        return rows, pairs

    @staticmethod
    def summary(found: list[Series]) -> list[tuple[str, str, str, int]]:
        return sorted((s.account, s.label.casefold(), s.cadence, s.count) for s in found)

    def test_Detector_WhenTheLegShapesAreUnderTheOwner_FindsTheSameSeries(self):
        rows, pairs = self.world()
        owner = {
            (DESCRIPTION, "transfer to savings"): OWNER_NAME,
            (DESCRIPTION, "from current"): OWNER_NAME,
        }

        before = find_recurring(rows, pairs, TODAY)
        after = find_recurring(rows, pairs, TODAY, entities=owner)

        assert self.summary(before) == self.summary(after)
        assert len(before) == 2, "the transfer, found once by its two legs, and the water bill"

    def test_Detector_WhenALegShapeAlsoHasPaymentsThatAreNotLegs_ThoseJoinTheOwnersSeries(self):
        rows, pairs = self.world()
        rows += [
            row(day, -2500, "TRANSFER TO SAVINGS", "current", f"loose{day}")
            for day in (4, 11, 18, 25)
        ]
        owner = {(DESCRIPTION, "transfer to savings"): OWNER_NAME}

        before = find_recurring(rows, pairs, TODAY)
        after = find_recurring(rows, pairs, TODAY, entities=owner)

        assert [s for s in after if s.shape == OWNER_NAME]
        assert len(after) == len(before), "the unpaired rows were a series by their shape too"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def roles(store: Store) -> dict[str, str | None]:
    return {e.name: e.role for e in store.entities_with_shapes()}


class TestTheOwnerEntityInTheStore:
    def test_Owner_WhenFirstUsed_IsMadeAsMeWithTheOwnerRole(self, store):
        entity, name, made = store.gather_into_owner(["transfer to savings"], OWNER_NAME, now=NOW)

        assert (name, made) == ("Me", True)
        assert roles(store) == {"Me": OWNER_ROLE}
        assert store.owner_entity() == (entity, "Me")

    def test_Owner_WhenUsedAgain_AddsToTheSameEntityEvenAfterARename(self, store):
        entity, _, _ = store.gather_into_owner(["a"], OWNER_NAME, now=NOW)
        store.rename_entity(entity, "Roger")

        again, name, made = store.gather_into_owner(["b"], OWNER_NAME, now=NOW)

        assert (again, name, made) == (entity, "Roger", False)
        assert {e.name: e.shapes for e in store.entities_with_shapes()} == {"Roger": ("a", "b")}

    def test_Owner_WhenAnEntityIsAlreadyCalledThatName_ItIsTakenAsTheOwner(self, store):
        entity = store.create_entity("me", ["a"], now=NOW)

        again, name, made = store.gather_into_owner(["b"], "Me", now=NOW)

        assert (again, name, made) == (entity, "me", False)
        assert roles(store) == {"me": OWNER_ROLE}

    def test_Owner_WhenAShapeIsUnderAnotherEntity_IsRefusedAndNothingIsMade(self, store):
        store.create_entity("Savings", ["a"], now=NOW)

        with pytest.raises(EntityRefused, match="already belongs"):
            store.gather_into_owner(["a", "b"], "Me", now=NOW)

        assert roles(store) == {"Savings": None}

    def test_Owner_WhenNoShapeIsGiven_IsRefusedAndNoOwnerIsMade(self, store):
        with pytest.raises(EntityRefused, match="at least one shape"):
            store.gather_into_owner([], "Me", now=NOW)

        assert store.owner_entity() is None

    def test_Owner_WhenItsLastShapeIsSplitApart_IsGoneAndTheNextPressMakesItAgain(self, store):
        store.gather_into_owner(["a"], "Me", now=NOW)
        detach_shape(store, "a", now=NOW)

        assert store.owner_entity() is None
        _entity, _name, made = store.gather_into_owner(["b"], "Me", now=NOW)
        assert made is True

    def test_Fold_WhenTheOwnerIsFoldedIntoAnEntityWithNoRole_TheTargetBecomesTheOwner(self, store):
        owner, _, _ = store.gather_into_owner(["a"], "Me", now=NOW)
        other = store.create_entity("Roger", ["b"], now=NOW)

        store.fold_entity(owner, "Roger", now=NOW)

        assert store.owner_entity() == (other, "Roger")
