""""I looked, and I cannot answer this yet" as a first-class outcome.

Ratified with the case that motivates it: a purchase from a shared account
may genuinely be undecidable in the moment, and splitting it properly costs
real effort. Every mature system in the survey grew this eventually -
holding categories, fixme keys - because without it a queue can only be
emptied by guessing, and a guess is worse than an open question.

Deferring is therefore NOT a category. It records that a person looked and
withheld judgement, which is information in its own right: the group stops
competing for attention with groups nobody has considered, while remaining
visible, because a hidden decision is indistinguishable from a forgotten
one. New rows arriving later are not deferred - a fresh instance deserves a
fresh look - so the mark fades as the group grows.
"""

from __future__ import annotations

from datetime import date

from obdi.categorise import defer_group, uncategorised_summary
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
            txn(1, -450, "AMAZON 204326778149", source_id="a-1"),
            txn(2, -960, "AMAZON 204326778150", source_id="a-2"),
            txn(3, -120, "COSTA COFFEE 101", source_id="c-1"),
            txn(4, -130, "COSTA COFFEE 202", source_id="c-2"),
            txn(5, -140, "COSTA COFFEE 303", source_id="c-3"),
        ],
        digest="d1",
    )


class TestDeferringIsNotAnswering:
    def test_ADeferredGroup_GetsNoCategory(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)

            deferred = defer_group(store, "AMAZON")

            assert deferred == 2
            assert store.annotations("category") == {}

    def test_ADeferredGroup_IsMarked_ButStillListed(self, tmp_path):
        # Visible, because a decision nobody can see is indistinguishable
        # from a decision nobody made.
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            defer_group(store, "AMAZON")

            worklist = uncategorised_summary(store)

            amazon = next(g for g in worklist.groups if g.label.startswith("AMAZON"))
            assert amazon.deferred == 2
            assert amazon.count == 2

    def test_DeferredGroups_SortAfterGroupsNobodyHasConsidered(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, -450, "BIG GROUP", source_id="b-1"),
                    txn(2, -450, "BIG GROUP", source_id="b-2"),
                    txn(3, -450, "BIG GROUP", source_id="b-3"),
                    txn(4, -450, "BIG GROUP", source_id="b-4"),
                    txn(5, -120, "SMALL GROUP", source_id="s-1"),
                ],
                digest="d1",
            )
            defer_group(store, "BIG GROUP")

            labels = [group.label for group in uncategorised_summary(store).groups]

            assert labels == ["SMALL GROUP", "BIG GROUP"], (
                "a bigger but deferred group must not out-rank an unconsidered one"
            )

    def test_ANewRow_IsNotDeferred_SoTheMarkFadesAsTheGroupGrows(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            defer_group(store, "AMAZON")

            reconcile_batch(
                store,
                [txn(9, -770, "AMAZON 204326778151", source_id="a-3")],
                digest="d2",
            )

            worklist = uncategorised_summary(store)
            amazon = next(g for g in worklist.groups if g.label.startswith("AMAZON"))
            assert (amazon.count, amazon.deferred) == (3, 2)

    def test_ADeferredRow_CanStillBeAnsweredLater(self, tmp_path):
        from obdi.categorise import apply_to_group

        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            defer_group(store, "AMAZON")

            answered = apply_to_group(store, "AMAZON", "True Expenses: Gifts")

            assert answered == 2
            assert len(store.annotations("category")) == 2

    def test_ARuleSweep_NeverUndefers(self, tmp_path):
        from obdi.categorise import apply_rules

        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            defer_group(store, "AMAZON")

            apply_rules(
                store,
                {"category_rules": [{"match": "AMAZON", "category": "Shopping"}]},
            )

            # The rule may categorise - deferring withholds a HUMAN answer,
            # it does not forbid a machine proposing one - but the record of
            # the person's hesitation survives.
            assert all(
                value == ("deferred", "human")
                for value in store.annotations("review").values()
            )
