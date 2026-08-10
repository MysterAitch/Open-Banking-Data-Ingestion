"""Propagation: annotate one instance, the machine finds its siblings.

The workflow this pins is example-first: a person annotates a single 4.50
generic transaction (or one month of a subscription) and the system detects
the recurring pattern and proposes the same annotation forward - including
subscriptions billed in a foreign currency, whose GBP amounts drift with
exchange rates, and bulk comments over a detected family. Proposals write at
model rank: above rules, forever below the human whose example seeded them.
"""

from __future__ import annotations

from datetime import date

from obdi.categorise import apply_propagation, propagation_proposals
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(
    month: int,
    day: int,
    amount: int,
    desc: str,
    *,
    source_id: str,
    currency: str = "GBP",
) -> Transaction:
    return Transaction(
        account_id="starling-personal",
        amount_minor=amount,
        currency=currency,
        value_date=date(2026, month, day),
        booking_date=date(2026, month, day),
        description=desc,
        source="starling",
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
    )


def _entity_for(store: Store, source_id: str) -> str:
    return next(
        t.entity_id for t in store.all_transactions() if t.source_id == source_id
    )


class TestFindingTheSiblings:
    def test_OneAnnotatedMonthlyCharge_ProposesTheOtherMonths(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-1"),
                    txn(2, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-2"),
                    txn(3, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-3"),
                    txn(3, 9, -2200, "GROCERIES", source_id="g-1"),
                ],
                digest="d1",
            )
            seed = _entity_for(store, "m-1")
            store.annotate(seed, "category", "Music", provenance="human")

            report = propagation_proposals(store)

            assert len(report.proposals) == 1
            proposal = report.proposals[0]
            assert proposal.value == "Music"
            assert len(proposal.targets) == 2
            assert seed not in proposal.targets
            assert report.contested == 0

    def test_ForeignCurrencyBilling_AmountsDriftButTheSeriesStillGroups(
        self, tmp_path
    ):
        # A USD-billed subscription lands in GBP at the day's rate: the
        # amounts wobble a few percent but it is plainly one series.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 3, -799, "HELLOFRESH USD SUB", source_id="fx-1"),
                    txn(2, 3, -812, "HELLOFRESH USD SUB", source_id="fx-2"),
                    txn(3, 3, -785, "HELLOFRESH USD SUB", source_id="fx-3"),
                    txn(4, 3, -820, "HELLOFRESH USD SUB", source_id="fx-4"),
                ],
                digest="d1",
            )
            seed = _entity_for(store, "fx-1")
            store.annotate(seed, "category", "Meal kits", provenance="human")

            report = propagation_proposals(store)

            assert len(report.proposals) == 1
            assert len(report.proposals[0].targets) == 3

    def test_SameDescriptionButFarApartAmounts_AreLeftAlone(self, tmp_path):
        # The identical-triad transfer case: a 12.50 utility share and a
        # 340.00 holiday payment to the same payee are NOT one series.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 10, -1250, "TRANSFER TO ALEX", source_id="a-1"),
                    txn(2, 10, -1250, "TRANSFER TO ALEX", source_id="a-2"),
                    txn(2, 20, -34000, "TRANSFER TO ALEX", source_id="a-3"),
                ],
                digest="d1",
            )
            seed = _entity_for(store, "a-1")
            store.annotate(seed, "category", "Bills share", provenance="human")

            report = propagation_proposals(store)

            assert len(report.proposals) == 1
            targets = report.proposals[0].targets
            assert _entity_for(store, "a-2") in targets
            assert _entity_for(store, "a-3") not in targets

    def test_TwoHumansDisagreeInOneBand_NothingProposed_ConflictCounted(
        self, tmp_path
    ):
        # Equal-amount transfers to the same payee mean different things on
        # different days; when the person's own examples disagree, the
        # machine must not pick a winner.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 10, -1250, "TRANSFER TO ALEX", source_id="a-1"),
                    txn(2, 10, -1250, "TRANSFER TO ALEX", source_id="a-2"),
                    txn(3, 10, -1250, "TRANSFER TO ALEX", source_id="a-3"),
                ],
                digest="d1",
            )
            store.annotate(
                _entity_for(store, "a-1"), "category", "Bills share", provenance="human"
            )
            store.annotate(
                _entity_for(store, "a-2"), "category", "Meals out", provenance="human"
            )

            report = propagation_proposals(store)

            assert all(not p.targets for p in report.proposals)
            assert report.contested == 1

    def test_RuleMadeAnnotations_AreNotSeeds(self, tmp_path):
        # Propagation generalises a PERSON'S example; a rule's output is
        # already generalised and seeds nothing.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-1"),
                    txn(2, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-2"),
                ],
                digest="d1",
            )
            store.annotate(
                _entity_for(store, "m-1"),
                "category",
                "Music",
                provenance="rule:sweep",
            )

            report = propagation_proposals(store)

            assert report.proposals == []

    def test_DifferentCurrencies_NeverShareASeries(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 5, -450, "SPOTIFY", source_id="m-1"),
                    txn(2, 5, -450, "SPOTIFY", source_id="m-2", currency="EUR"),
                ],
                digest="d1",
            )
            store.annotate(
                _entity_for(store, "m-1"), "category", "Music", provenance="human"
            )

            report = propagation_proposals(store)

            assert all(not p.targets for p in report.proposals)


class TestApplyingProposals:
    def _seeded_report(self, store: Store):
        reconcile_batch(
            store,
            [
                txn(1, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-1"),
                txn(2, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-2"),
                txn(3, 5, -450, "SUBSCRIPTION PAYMENT", source_id="m-3"),
            ],
            digest="d1",
        )
        seed = _entity_for(store, "m-1")
        store.annotate(seed, "category", "Music", provenance="human")
        return propagation_proposals(store)

    def test_Application_WritesModelProvenance_BelowTheHumanSeed(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            report = self._seeded_report(store)

            written = apply_propagation(store, report)

            assert written == 2
            held = store.annotations("category")
            assert held[_entity_for(store, "m-2")] == ("Music", "model:propagation")
            assert held[_entity_for(store, "m-1")] == ("Music", "human")
            # A later rule sweep cannot overwrite what propagation wrote.
            assert not store.annotate(
                _entity_for(store, "m-2"), "category", "Other", provenance="rule:sweep"
            )

    def test_ADryRun_CountsButWritesNothing(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            report = self._seeded_report(store)

            written = apply_propagation(store, report, dry_run=True)

            assert written == 2
            held = store.annotations("category")
            assert len(held) == 1  # only the human seed

    def test_AnyAnnotationKindPropagates_IncludingBulkComments(self, tmp_path):
        # Roger's bulk-comment workflow: a note on one member of a detected
        # family rides the same machinery as categories.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 10, -1250, "TRANSFER TO ALEX", source_id="a-1"),
                    txn(2, 10, -1250, "TRANSFER TO ALEX", source_id="a-2"),
                ],
                digest="d1",
            )
            store.annotate(
                _entity_for(store, "a-1"),
                "comment",
                "shared utility bill",
                provenance="human",
            )

            report = propagation_proposals(store, kind="comment")
            apply_propagation(store, report)

            held = store.annotations("comment")
            assert held[_entity_for(store, "a-2")] == (
                "shared utility bill",
                "model:propagation",
            )
