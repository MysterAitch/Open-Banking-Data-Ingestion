"""A rebuild re-derives what it can compute, and must not discard what it cannot.

The review queue is the backstop for the one case matching cannot settle: a
repeated payment and a duplicated report are indistinguishable, so the doubt is
recorded for a person rather than decided silently. When that person decides, the
decision is the only thing in the store that no amount of replaying raw evidence
can reproduce - the evidence is exactly what was ambiguous.

The rebuild wiped the whole table. It is encouraged after every refile and runs
by itself after every deploy, and the danger-zone copy reassures the reader that
"none touches the raw artefacts in layer 0" - which invites precisely the wrong
inference about everything else. Re-adjudication is not even idempotent: the
second pass can reach a different answer from the first, so a dismissal does not
survive as a re-derived dismissal either.

Entity ids are deterministic and a rebuild re-mints them identically, so a
resolved row kept across the wipe still names the transaction it was about. That
is the same property annotations rely on, and it is why keeping them is safe
rather than merely desirable.
"""

from __future__ import annotations

import json

import pytest

from obdi.store import Store


def _land_and_derive(store_path, description: str = "COFFEE SHOP") -> str:
    """One transaction in the store, through the ordinary doors, and its id."""
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
                    "description": description,
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


class TestWhatARebuildIsAllowedToDiscard:
    def test_ARowSomebodyHasAlreadyJudged_IsStillJudgedAfterARebuild(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw

        store_path = tmp_path / "store.sqlite3"
        entity = _land_and_derive(store_path)

        with Store(store_path) as store:
            store.queue_for_review(entity, "possible duplicate of an earlier payment")
            store.resolve_review(entity)

        with Store(store_path) as store:
            rebuild_from_raw(store)

        with Store(store_path) as store:
            judged = store.review_queue(include_resolved=True)
            awaiting = store.review_queue()

        assert judged, (
            "the rebuild discarded a decision only a person could make - the "
            "evidence behind it was ambiguous, which is why it was queued at all"
        )
        assert judged[0]["entity_id"] == entity
        assert judged[0]["resolved_at"], "the row survived but its decision did not"
        assert not awaiting, (
            "a row already judged is waiting to be judged again, which is how a "
            "queue somebody has emptied fills back up on its own"
        )

    def test_ARowNobodyHasJudgedYet_IsRederivedRatherThanKept(self, tmp_path):
        """The other half, and the reason this is not simply "keep everything".

        An unresolved entry is a claim the CURRENT rules make about the current
        evidence. Keeping it across a rebuild would preserve doubts that the
        rules have since learned to settle, and the queue would only ever grow.
        """
        from obdi.rebuild import rebuild_from_raw

        store_path = tmp_path / "store.sqlite3"
        entity = _land_and_derive(store_path)

        with Store(store_path) as store:
            store.queue_for_review(entity, "flagged by rules that have since changed")

        with Store(store_path) as store:
            rebuild_from_raw(store)

        with Store(store_path) as store:
            awaiting = store.review_queue()

        assert not awaiting, (
            "an unjudged flag survived the rebuild that would have re-raised it "
            "if it still applied - so the queue can only grow"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
