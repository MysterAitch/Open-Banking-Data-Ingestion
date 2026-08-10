"""The category layer: annotations that survive everything.

The pain this exists to end is threefold - re-categorising per REBUILD, per
SYSTEM, per BATCH (exhibit: a 1,032-row review pile after a full-history
replay). Annotations are keyed by entity_id - deterministic across rebuilds
by design - and stored beside the raw layer, so a rebuild wipes the derived
tables and the annotations simply re-attach. Provenance makes every
assignment revisable and auditable, and gives precedence its rule: a
human's word is never overwritten by a machine's, and rules re-run
idempotently without trampling anything that outranks them.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from obdi.categorise import apply_rules, load_rules, uncategorised_summary
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


class TestTheAnnotationStore:
    def test_AnAnnotation_RoundTrips_WithItsProvenance(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn(5, -450, "NETFLIX.COM", source_id="s-1")], digest="d1")
            entity = store.all_transactions()[0].entity_id

            assert store.annotate(entity, "category", "Subscriptions", provenance="human")

            held = store.annotations("category")
            assert held[entity] == ("Subscriptions", "human")

    def test_ARule_NeverOverwritesAHuman_ButAHumanOverwritesAnything(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn(5, -450, "NETFLIX.COM", source_id="s-1")], digest="d1")
            entity = store.all_transactions()[0].entity_id

            assert store.annotate(entity, "category", "Telly", provenance="human")
            assert not store.annotate(
                entity, "category", "Subscriptions", provenance="rule:streaming"
            )
            assert store.annotations("category")[entity] == ("Telly", "human")

            assert store.annotate(entity, "category", "Film", provenance="human")
            assert store.annotations("category")[entity] == ("Film", "human")

    def test_ARule_MayRevisitItsOwnWork(self, tmp_path):
        # Rules evolve; a rule-made assignment is a rule's to change. Only
        # higher provenance is protected.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn(5, -450, "NETFLIX.COM", source_id="s-1")], digest="d1")
            entity = store.all_transactions()[0].entity_id

            assert store.annotate(entity, "category", "Media", provenance="rule:v1")
            assert store.annotate(entity, "category", "Subscriptions", provenance="rule:v2")
            assert store.annotations("category")[entity] == ("Subscriptions", "rule:v2")

    def test_Annotations_SurviveARebuild_AndReattach(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw

        with Store(tmp_path / "s.sqlite3") as store:
            # Land through a real artefact so the rebuild has raw to replay.
            payload = (
                b'{"feedItems": [{"feedItemUid": "u-1", '
                b'"amount": {"currency": "GBP", "minorUnits": 450}, '
                b'"direction": "OUT", "transactionTime": "2026-01-05T10:00:00.000Z", '
                b'"source": "MASTER_CARD", "status": "SETTLED", '
                b'"counterPartyName": "Netflix", "reference": "NETFLIX.COM"}]}'
            )
            from datetime import datetime

            from obdi.identity import artefact_digest
            from obdi.models import RawArtefact

            store.land_artefact(
                RawArtefact(
                    source="starling-feed",
                    account_ref="starling:acc-1",
                    fetched_at=datetime.now().astimezone(),
                    media_type="application/json",
                    digest=artefact_digest(payload),
                    payload=payload,
                    origin=(
                        "https://api.starlingbank.com/api/v2/feed/account/"
                        "acc-1/category/cat-1?changesSince=x"
                    ),
                )
            )
            rebuild_from_raw(store)
            first = store.all_transactions()
            assert len(first) == 1
            entity = first[0].entity_id
            store.annotate(entity, "category", "Subscriptions", provenance="human")

            rebuild_from_raw(store)

            rebuilt = store.all_transactions()
            assert len(rebuilt) == 1
            assert rebuilt[0].entity_id == entity, "entity identity is deterministic"
            assert store.annotations("category")[entity] == ("Subscriptions", "human")


class TestTheRuleSweep:
    RULES: ClassVar[dict[str, list[dict[str, str]]]] = {
        "payee_rules": [
            {"match": "TESCO STORES", "payee": "Tesco"},
        ],
        "category_rules": [
            {"match": "tesco", "category": "Groceries"},
            {"match": "NETFLIX", "category": "Subscriptions"},
        ],
    }

    def _seed(self, store: Store) -> None:
        reconcile_batch(
            store,
            [
                txn(5, -450, "NETFLIX.COM AMSTERDAM", source_id="s-1"),
                txn(6, -1230, "TESCO STORES 5223 BIRMINGHAM", source_id="s-2"),
                txn(7, -999, "MYSTERY MERCHANT", source_id="s-3"),
            ],
            digest="d1",
        )

    def test_TheSweep_CategorisesAndNormalises_WithCounts(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            self._seed(store)

            summary = apply_rules(store, self.RULES)

            assert summary.categorised == 2
            assert summary.payees_normalised == 1
            assert summary.considered == 3
            categories = {
                value for value, _ in store.annotations("category").values()
            }
            assert categories == {"Groceries", "Subscriptions"}
            payees = {value for value, _ in store.annotations("payee").values()}
            assert payees == {"Tesco"}

    def test_TheSweep_LeavesHumanWorkAlone(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            self._seed(store)
            netflix = next(
                t.entity_id
                for t in store.all_transactions()
                if "NETFLIX" in t.description
            )
            store.annotate(netflix, "category", "Telly", provenance="human")

            apply_rules(store, self.RULES)

            assert store.annotations("category")[netflix] == ("Telly", "human")

    def test_ADryRun_ReportsButWritesNothing(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            self._seed(store)

            summary = apply_rules(store, self.RULES, dry_run=True)

            assert summary.categorised == 2
            assert store.annotations("category") == {}

    def test_TheUncategorisedReport_GroupsByFrequency_ForRuleWriting(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, -100, "COSTA COFFEE 101", source_id="c-1"),
                    txn(2, -110, "COSTA COFFEE 202", source_id="c-2"),
                    txn(3, -120, "COSTA COFFEE 303", source_id="c-3"),
                    txn(4, -999, "ONE OFF SHOP", source_id="o-1"),
                ],
                digest="d1",
            )

            worklist = uncategorised_summary(store, limit=5)

            assert worklist.groups[0][0].startswith("COSTA COFFEE")
            assert worklist.groups[0][1] == 3

    def test_RulesLoad_FromAJsonFile(self, tmp_path):
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(
            '{"category_rules": [{"match": "netflix", "category": "Subs"}]}',
            encoding="utf-8",
        )

        rules = load_rules(rules_path)

        assert rules["category_rules"][0]["category"] == "Subs"
