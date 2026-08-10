"""Categorising a whole group in one gesture, at human rank.

The survey of how other systems do this was blunt about what works:
confirmation must be CHEAP (one action, group-wise, not row by row), it
must produce a durable artefact, and automation must be unable to undo it.
The provenance ladder already provides the third, so the surface only has
to supply the first two - and it must show the evidence a person needs to
decide, since half the groups here are bank reference codes where a rule
would be guesswork.

The page writes at HUMAN rank, which is what makes it different from the
rules file: a sweep may propose, but what is confirmed here outranks every
later sweep and survives every rebuild.
"""

from __future__ import annotations

from datetime import date

from obdi.categorise import apply_to_group, group_members
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(day: int, amount: int, desc: str, *, source_id: str) -> Transaction:
    return Transaction(
        account_id="starling-personal",
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, 1, day),
        booking_date=date(2026, 1, day),
        description=desc,
        source="starling",
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
    )


def _seed(store: Store) -> None:
    reconcile_batch(
        store,
        [
            txn(1, -450, "DAP90481679", source_id="d-1"),
            txn(2, -450, "DAP90481679", source_id="d-2"),
            txn(3, -450, "DAP90481679", source_id="d-3"),
            txn(4, -999, "SOMETHING ELSE", source_id="x-1"),
        ],
        digest="d1",
    )


class TestFindingAGroupsMembers:
    def test_AGroup_GathersEveryRowSharingItsStrippedLabel(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)

            members = group_members(store, "DAP")

            assert len(members) == 3

    def test_AGroup_ExcludesRowsThatAlreadyCarryAHumanAnswer(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            first = group_members(store, "DAP")[0]
            store.annotate(first, "category", "Mine", provenance="human")

            remaining = group_members(store, "DAP")

            assert first not in remaining
            assert len(remaining) == 2

    def test_AGroup_ExcludesConfirmedTransferLegs(self, tmp_path):
        from obdi.ingest import pair_transfers_across_store

        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store, [txn(5, -5000, "MOVE 1", source_id="m-1")], digest="d1"
            )
            reconcile_batch(
                store,
                [
                    Transaction(
                        account_id="starling-savings",
                        amount_minor=5000,
                        currency="GBP",
                        value_date=date(2026, 1, 5),
                        booking_date=date(2026, 1, 5),
                        description="MOVE IN",
                        source="starling",
                        source_id="m-2",
                        tier=SourceTier.AUTHORITATIVE,
                    )
                ],
                digest="d2",
            )
            pair_transfers_across_store(store)

            assert group_members(store, "MOVE") == []

    def test_AnUnknownGroup_IsEmptyRatherThanAnError(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)

            assert group_members(store, "NOT A GROUP") == []


class TestApplyingToAWholeGroup:
    def test_OneAction_CategorisesEveryMember_AtHumanRank(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)

            written = apply_to_group(store, "DAP", "Home Bills: Water")

            assert written == 3
            held = store.annotations("category")
            assert len(held) == 3
            assert all(
                value == ("Home Bills: Water", "human") for value in held.values()
            )

    def test_WhatIsConfirmedHere_OutranksALaterRuleSweep(self, tmp_path):
        # The point of writing at human rank: a rules file edit cannot
        # silently change an answer a person gave.
        from obdi.categorise import apply_rules

        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_to_group(store, "DAP", "Home Bills: Water")

            apply_rules(
                store,
                {"category_rules": [{"match": "DAP", "category": "Something Else"}]},
            )

            held = store.annotations("category")
            assert all(value[0] == "Home Bills: Water" for value in held.values())

    def test_AnEmptyCategory_WritesNothing(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)

            assert apply_to_group(store, "DAP", "   ") == 0
            assert store.annotations("category") == {}

    def test_AnyKind_MayBeApplied_SoNotesRideTheSameGesture(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)

            written = apply_to_group(
                store, "DAP", "still to identify", kind="comment"
            )

            assert written == 3
            assert all(
                value == ("still to identify", "human")
                for value in store.annotations("comment").values()
            )

    def test_ARerun_IsHarmless(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_to_group(store, "DAP", "Home Bills: Water")

            again = apply_to_group(store, "DAP", "Home Bills: Water")

            assert again == 0, "already answered rows are no longer members"
            assert len(store.annotations("category")) == 3
