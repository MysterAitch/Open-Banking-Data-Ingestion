"""Anything keyed to a transaction should be counted when the transaction goes.

Six tables hang off a transaction's identity, and each holds something a person
or a process decided: a categorisation, a review verdict, a confirmed transfer
pair, an unsent event. A row in any of them whose transaction no longer exists is
invisible from every other angle - the transaction simply reads as uncategorised,
unflagged, unpaired - so nothing else would ever say the work was lost rather
than never done.

Only annotations were counted. That was not a judgement that the others did not
matter; it was where the first defect happened to be found, and the check was
written for that instance rather than for the class. The registry already knows
which tables hang off an entity id - it is what carries them across an account
rename - so one check reading it covers a table added tomorrow without anyone
remembering this file exists.

The fixtures here delete a transaction directly, which the application does not
do outside a rebuild. That is the point: the orphan state IS the subject, and a
check for a state nothing can produce still has to be shown working on it.
"""

from __future__ import annotations

import json

import pytest

from obdi.store import Store


def _one_transaction(store_path) -> str:
    from obdi.cli import replay_single_artefact
    from obdi.providers.truelayer import artefact_for

    body = json.dumps(
        {
            "results": [
                {
                    "transaction_id": "t-1",
                    "normalised_provider_transaction_id": "txn-aaa",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "amount": -12.34,
                    "currency": "GBP",
                    "description": "COFFEE SHOP",
                }
            ],
            "status": "Succeeded",
        }
    ).encode()
    with Store(store_path) as store:
        store.land_artefact(
            artefact_for(
                body,
                account_id="acc-1",
                kind="booked",
                requested="from=2026-06-01&to=2026-07-31",
                account_ref="halifax-current",
            )
        )
        artefact_id = int(
            store.connection.execute("SELECT rowid FROM raw_artefacts LIMIT 1").fetchone()[0]
        )
    replay_single_artefact(store_path, artefact_id)
    with Store(store_path) as store:
        return store.all_transactions()[0].entity_id


class TestCountingWorkLeftPointingAtNothing:
    def test_AHealthyStore_ReportsNoOrphansAnywhere(self, tmp_path):
        store_path = tmp_path / "store.sqlite3"
        entity = _one_transaction(store_path)
        with Store(store_path) as store:
            store.annotate(entity, "category", "Coffee", provenance="human")
            store.queue_for_review(entity, "possible duplicate")
            orphans = store.orphaned_entity_rows()

        assert sum(orphans.values()) == 0, f"a healthy store reports orphans: {orphans}"

    def test_EveryTableKeyedToATransaction_IsChecked(self, tmp_path):
        """Read out of the registry rather than listed here, so a table added
        later is covered without this file being edited."""
        from obdi.namespaces import ENTITY_KEYED_TABLES

        store_path = tmp_path / "store.sqlite3"
        _one_transaction(store_path)
        with Store(store_path) as store:
            checked = set(store.orphaned_entity_rows())

        expected = {
            f"{table}.{column}"
            for table, columns in ENTITY_KEYED_TABLES.items()
            for column in columns
            # A transaction's own id is what everything else is compared
            # AGAINST; it cannot be an orphan of itself.
            if not (table == "transactions" and column == "entity_id")
        }
        assert checked == expected, (
            f"the check and the registry disagree about what hangs off an entity "
            f"id: only in registry {sorted(expected - checked)}, only in check "
            f"{sorted(checked - expected)}"
        )

    def test_WhenTheTransactionGoes_TheWorkAttachedToItIsCountedAndNamed(self, tmp_path):
        store_path = tmp_path / "store.sqlite3"
        entity = _one_transaction(store_path)
        with Store(store_path) as store:
            store.annotate(entity, "category", "Coffee", provenance="human")
            store.queue_for_review(entity, "possible duplicate")
            store.resolve_review(entity)
            # The state under test: the row goes, its attachments stay.
            store.connection.execute(
                "DELETE FROM transactions WHERE entity_id = ?", (entity,)
            )
            store.connection.commit()
            orphans = store.orphaned_entity_rows()

        assert orphans["annotations.entity_id"] == 1
        assert orphans["review_queue.entity_id"] == 1, (
            "a review decision outlived its transaction and nothing counted it - "
            f"{orphans}"
        )

    def test_TheDoctor_NamesWhichTableHoldsTheOrphans(self, tmp_path):
        """A bare count sends the reader looking; the name says where to look."""
        from obdi.doctor import collision_checks

        store_path = tmp_path / "store.sqlite3"
        entity = _one_transaction(store_path)
        with Store(store_path) as store:
            store.queue_for_review(entity, "possible duplicate")
            store.connection.execute(
                "DELETE FROM transactions WHERE entity_id = ?", (entity,)
            )
            store.connection.commit()
            results = collision_checks(store)

        failed = [check for check in results if not check.ok]
        assert failed, "the doctor passed a store holding work that points at nothing"
        assert any("review_queue" in check.detail for check in failed), (
            f"the doctor reported orphans without saying where: {[c.detail for c in failed]}"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
