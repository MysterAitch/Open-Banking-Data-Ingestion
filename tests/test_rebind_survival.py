"""Categorising an account and then naming it must not lose the work.

Found by review and verified there by execution. An entity id folds the
account into its material, so renaming an account re-mints every id under
it - and the rename used to move only the labels, leaving the ids to be
re-minted by the next rebuild instead. Anything keyed by the old id was
then pointing at a row that no longer existed: a person's categorisation
first among them.

Nothing reported it, because there was nothing to report. The rows came
back uncategorised, the annotations sat in their table addressing nobody,
and the store looked exactly like one belonging to someone who had never
categorised anything. A deploy alone triggers the rebuild that does it.

The rename now computes the new ids - the same values the next rebuild
mints from the same evidence - and moves every entity-keyed table
together, with the tables declared in one registry so a new one cannot be
forgotten.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from obdi.identity import artefact_digest
from obdi.models import RawArtefact
from obdi.namespaces import ENTITY_KEYED_TABLES
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store

FEED = (
    b'{"feedItems": [{"feedItemUid": "u-1", '
    b'"amount": {"currency": "GBP", "minorUnits": 1250}, '
    b'"direction": "OUT", "transactionTime": "2026-01-05T10:00:00.000Z", '
    b'"source": "MASTER_CARD", "status": "SETTLED", '
    b'"counterPartyName": "Shop", "reference": "SOME SHOP"}]}'
)


def _landed(store: Store) -> None:
    store.land_artefact(
        RawArtefact(
            source="starling-feed",
            account_ref="starling:acc-1",
            fetched_at=datetime.now().astimezone(),
            media_type="application/json",
            digest=artefact_digest(FEED),
            payload=FEED,
            origin="feed-1",
        )
    )
    rebuild_from_raw(store)


class TestWorkSurvivesBeingRenamed:
    def test_ACategoryEnteredByHand_SurvivesARebindAndARebuild(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _landed(store)
            original = store.all_transactions()[0]
            store.annotate(
                original.entity_id, "category", "Groceries", provenance="human"
            )

            store.rebind_account(original.account_id, "starling-personal")

            held = store.all_transactions()
            assert len(held) == 1
            assert held[0].account_id == "starling-personal"
            categories = store.annotations("category")
            assert categories.get(held[0].entity_id) == ("Groceries", "human"), (
                "the answer a person gave must still be attached to the row "
                "they gave it about"
            )
            assert store.dangling_annotations() == 0

    def test_TheRenamedIds_AreTheOnesTheNextRebuildMints(self, tmp_path):
        # The rename is only safe because it computes what the rebuild will
        # compute. If those two ever disagree the annotation detaches at the
        # next deploy instead of at the rename, which is worse: further from
        # the act that caused it.
        with Store(tmp_path / "s.sqlite3") as store:
            _landed(store)
            original = store.all_transactions()[0]
            store.annotate(
                original.entity_id, "category", "Groceries", provenance="human"
            )
            store.rebind_account(original.account_id, "starling-personal")
            after_rename = store.all_transactions()[0].entity_id

            rebuild_from_raw(store)

            rebuilt = store.all_transactions()[0]
            assert rebuilt.entity_id == after_rename, (
                "the rename anticipated the rebuild exactly"
            )
            assert store.annotations("category").get(rebuilt.entity_id) == (
                "Groceries",
                "human",
            )
            assert store.dangling_annotations() == 0

    def test_ADanglingAnnotation_IsCounted_RatherThanInvisible(self, tmp_path):
        # However it arises, an annotation addressing no row must be
        # findable: from every other angle it looks like a row nobody
        # categorised, which is indistinguishable from work never done.
        with Store(tmp_path / "s.sqlite3") as store:
            _landed(store)
            store.annotate("no-such-entity", "category", "Ghost", provenance="human")

            assert store.dangling_annotations() == 1


class TestTheRegistryDescribesTheSchema:
    def test_EveryTableWithAnEntityIdColumn_IsDeclared(self, tmp_path):
        # The backstop. A table that grows an entity-id column without
        # joining the registry would be silently left behind by the next
        # rebind - which is exactly how this defect arrived.
        schema = Path("src/obdi/store.py").read_text(encoding="utf-8")
        found: dict[str, set[str]] = {}
        for block in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, re.S
        ):
            columns = set(
                re.findall(r"^\s*(\w*entity_id)\s+TEXT", block.group(2), re.M)
            )
            if columns:
                found[block.group(1)] = columns

        assert set(found) == set(ENTITY_KEYED_TABLES), (
            "a table carries entity ids without being declared in "
            "ENTITY_KEYED_TABLES - a rebind would leave its rows behind"
        )
        for table, columns in found.items():
            assert columns == set(ENTITY_KEYED_TABLES[table]), (
                f"{table}: declared columns do not match the schema"
            )
