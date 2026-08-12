"""Correcting where a statement was filed must not strand what was derived from it.

Refiling is the recovery for an import that went to the wrong account. The artefact
keeps its bytes and changes its filing, and the caller then replays the payload
under the corrected destination - so rows appear beneath the right account.

The question these settle is what happens to the rows that were derived under the
WRONG one. They are keyed by entity ids that fold in the account, so the replay
mints new ids rather than moving the old ones; nothing in the refile itself removes
the originals. If they survive, the store holds the same money twice and any
hand-applied categorisation is attached to the copy that is now wrong.

Written as an investigation rather than from the answer: the sibling operation
`rebind_account` moves every entity-keyed row deliberately, and the durability panel
asked for refile to be given the same treatment. Whether it needed it depended on
what the replay already does, which was a question for a run rather than for reading.

The run answered it. Both accounts held the payment afterwards, so refiling now
carries the derived rows with the filing. The category test is the reason to keep
reading: before the fix it passed, because the annotation was still attached to a
surviving row - the WRONG one, under the old account. A count of stranded
annotations cannot tell those apart, so it asserts which account the categorised
payment sits in.
"""

from __future__ import annotations

import json

import pytest

from obdi.store import Store


def _land_under(store: Store, account_ref: str) -> None:
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
    store.land_artefact(
        artefact_for(
            body,
            account_id="acc-1",
            kind="booked",
            requested="from=2026-06-01&to=2026-07-31",
            account_ref=account_ref,
        )
    )


def _replay(store_path, artefact_id: int) -> str:
    """The second press. The web page offers refiling and replaying as two
    separate doors, so this is the flow a person actually performs: correct
    where the artefact is filed, then read its bytes again under the
    correction."""
    from obdi.cli import replay_single_artefact

    return replay_single_artefact(store_path, artefact_id)


def _artefact_id(store: Store, account_ref: str = "") -> int:
    if account_ref:
        row = store.connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE account_ref = ?", (account_ref,)
        ).fetchone()
    else:
        row = store.connection.execute("SELECT rowid FROM raw_artefacts LIMIT 1").fetchone()
    return int(row[0])


class TestWhatSurvivesARefile:
    def test_Refiling_ThenReplaying_LeavesTheMoneyCountedOnce(self, tmp_path):
        store_path = tmp_path / "store.sqlite3"
        with Store(store_path) as store:
            _land_under(store, "halifax-current")
            artefact_id = _artefact_id(store)
        _replay(store_path, artefact_id)

        with Store(store_path) as store:
            store.refile_artefact(artefact_id, "halifax-reward")
        _replay(store_path, artefact_id)

        with Store(store_path) as store:
            accounts = {row.account_id for row in store.all_transactions()}

        assert accounts == {"halifax-reward"}, (
            f"the same payment is filed under {accounts} - a refile that leaves the "
            "old rows behind counts the money twice"
        )

    def test_Refiling_DoesNotStrandAHandAppliedCategory(self, tmp_path):
        # The expensive half. A category is hand-applied work that nothing
        # re-derives, and it is keyed to an entity id that folds in the account -
        # so a refile re-mints the id and can leave the annotation pointing at a
        # row that no longer exists, which reads afterwards as simply
        # uncategorised.
        store_path = tmp_path / "store.sqlite3"
        with Store(store_path) as store:
            _land_under(store, "halifax-current")
            artefact_id = _artefact_id(store)
        _replay(store_path, artefact_id)

        with Store(store_path) as store:
            entity = store.all_transactions()[0].entity_id
            store.annotate(entity, "category", "Coffee", provenance="human")
            store.refile_artefact(artefact_id, "halifax-reward")
        _replay(store_path, artefact_id)

        with Store(store_path) as store:
            stranded = store.dangling_annotations()
            categories = store.annotations("category")
            filed_under = {
                row.entity_id: row.account_id for row in store.all_transactions()
            }

        assert stranded == 0, f"{stranded} annotation(s) left pointing at nothing"
        assert categories, "the category did not survive the refile at all"
        assert [filed_under.get(entity) for entity in categories] == [
            "halifax-reward"
        ], (
            "the category survived, but attached to the payment as it was filed "
            "before the correction - which reads as categorised while the account "
            "it belongs to reads as not"
        )


class TestWhenTheStatementWasAlreadyImportedCorrectly:
    """The recovery people actually reach for first: import it again under the
    right account, then tidy up the misfiled copy. Both copies have derived
    rows by then, so the refile is a merge rather than a move."""

    def _both_landed(self, store_path) -> tuple[int, int]:
        with Store(store_path) as store:
            _land_under(store, "halifax-current")
            misfiled = _artefact_id(store, "halifax-current")
            _land_under(store, "halifax-reward")
            correct = _artefact_id(store, "halifax-reward")
        _replay(store_path, misfiled)
        _replay(store_path, correct)
        return misfiled, correct

    def test_Refiling_OntoAnAlreadyCorrectImport_LeavesOnePayment(self, tmp_path):
        store_path = tmp_path / "store.sqlite3"
        misfiled, _correct = self._both_landed(store_path)

        with Store(store_path) as store:
            store.refile_artefact(misfiled, "halifax-reward")
        with Store(store_path) as store:
            rows = store.all_transactions()

        assert [row.account_id for row in rows] == ["halifax-reward"], (
            f"{len(rows)} rows survive the merge: {[r.account_id for r in rows]}"
        )

    def test_Refiling_OntoAnAlreadyCorrectImport_KeepsThePersonsCategory(
        self, tmp_path
    ):
        # The duplicate about to be discarded carries a human's word; the
        # survivor carries a rule's. Rank decides, as it does everywhere else -
        # the alternative is that which copy happened to be misfiled decides.
        store_path = tmp_path / "store.sqlite3"
        misfiled, correct = self._both_landed(store_path)

        with Store(store_path) as store:
            filed = {row.account_id: row.entity_id for row in store.all_transactions()}
            store.annotate(
                filed["halifax-current"], "category", "Coffee", provenance="human"
            )
            store.annotate(
                filed["halifax-reward"], "category", "Uncategorised", provenance="rule"
            )
            store.refile_artefact(misfiled, "halifax-reward")

        with Store(store_path) as store:
            categories = store.annotations("category")
            stranded = store.dangling_annotations()

        assert stranded == 0, f"{stranded} annotation(s) left pointing at nothing"
        assert [value for value, _provenance in categories.values()] == ["Coffee"], (
            "the discarded duplicate's human category was dropped in favour of the "
            f"survivor's rule-set one: {categories}"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
