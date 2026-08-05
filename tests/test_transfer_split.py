"""Provider-claimed vs pairing-confirmed internal transfers.

Two different pieces of evidence say "this movement is between your own
accounts": the provider's own marker on the feed item, and the pairing pass
actually finding the opposite side in another account. Conflating them into
one flag destroys the distinction the moment it is written - a flag reading
1 cannot say whether anyone ever found the other side.

The split: the transactions column carries ONLY the provider's claim, and
confirmations live in their own table, written by the pairing pass alone.
"""

from obdi.ingest import pair_transfers_across_store, reconcile_batch, unconfirmed_transfers
from obdi.providers.starling import to_transaction
from obdi.replay import ActualAccountBinding, build_payload, to_actual_transaction
from obdi.store import Store

CURRENT = "starling-personal"
SAVINGS = "starling-holiday-fund"

BINDINGS = [
    ActualAccountBinding(CURRENT, "actual-current"),
    ActualAccountBinding(SAVINGS, "actual-savings"),
]


def _feed_item(uid: str, *, direction: str, minor: int, source: str, reference: str) -> dict:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": direction,
        "transactionTime": "2026-03-14T09:00:00.000Z",
        "source": source,
        "status": "SETTLED",
        "counterPartyName": "Self",
        "reference": reference,
    }


# Faster Payments between two of your own accounts: a genuine internal
# transfer that NO provider marks as one - the exact case only pairing
# can catch.
UNCLAIMED_OUT = _feed_item(
    "fp-out", direction="OUT", minor=5000, source="FASTER_PAYMENTS_OUT", reference="to savings"
)
UNCLAIMED_IN = _feed_item(
    "fp-in", direction="IN", minor=5000, source="FASTER_PAYMENTS_IN", reference="from current"
)

# A provider-marked transfer whose other side has not been ingested.
CLAIMED_LONELY = _feed_item(
    "internal-1", direction="OUT", minor=7500, source="INTERNAL_TRANSFER", reference="to pot"
)


def _store_with(tmp_path, batches):
    store = Store(tmp_path / "s.sqlite3")
    for account_id, item, digest in batches:
        reconcile_batch(store, [to_transaction(item, account_id=account_id)], digest=digest)
    return store


class TestClaimAndConfirmationAreSeparateFacts:
    def test_PairingPass_WhenNeitherSideClaimed_ConfirmsWithoutForgingAClaim(self, tmp_path):
        with _store_with(
            tmp_path, [(CURRENT, UNCLAIMED_OUT, "d1"), (SAVINGS, UNCLAIMED_IN, "d2")]
        ) as store:
            pairs = pair_transfers_across_store(store)
            held = store.all_transactions()

            assert pairs == 1
            # The provider never said "internal transfer", and pairing must
            # not put those words in its mouth.
            assert not any(t.is_internal_transfer for t in held)
            assert all(t.transfer_confirmed for t in held)

    def test_ProviderClaim_WhenOtherSideMissing_StaysUnconfirmed(self, tmp_path):
        with _store_with(tmp_path, [(CURRENT, CLAIMED_LONELY, "d1")]) as store:
            pairs = pair_transfers_across_store(store)
            (held,) = store.all_transactions()

            assert pairs == 0
            assert held.is_internal_transfer
            assert not held.transfer_confirmed
            assert [t.entity_id for t in unconfirmed_transfers(store)] == [held.entity_id]

    def test_UnconfirmedTransfers_WhenPairExists_ListsNothing(self, tmp_path):
        # Both sides provider-marked AND paired: a claim with its
        # confirmation present is not worth a warning.
        claimed_out = _feed_item(
            "int-out", direction="OUT", minor=2000, source="INTERNAL_TRANSFER", reference="out"
        )
        claimed_in = _feed_item(
            "int-in", direction="IN", minor=2000, source="INTERNAL_TRANSFER", reference="in"
        )
        with _store_with(
            tmp_path, [(CURRENT, claimed_out, "d1"), (SAVINGS, claimed_in, "d2")]
        ) as store:
            pair_transfers_across_store(store)
            assert unconfirmed_transfers(store) == []

    def test_PairingPass_WhenRunTwice_ReportsTheSamePairsNotDouble(self, tmp_path):
        with _store_with(
            tmp_path, [(CURRENT, UNCLAIMED_OUT, "d1"), (SAVINGS, UNCLAIMED_IN, "d2")]
        ) as store:
            first = pair_transfers_across_store(store)
            second = pair_transfers_across_store(store)

            assert (first, second) == (1, 1)
            assert len(store.confirmed_transfer_entities()) == 2

    def test_PairCount_CountsPairs_NotSides(self, tmp_path):
        # One movement between two accounts is ONE transfer, however many
        # rows record it. "1 pair" is the honest summary; "2" was the
        # count of flags written.
        with _store_with(
            tmp_path, [(CURRENT, UNCLAIMED_OUT, "d1"), (SAVINGS, UNCLAIMED_IN, "d2")]
        ) as store:
            assert pair_transfers_across_store(store) == 1


class TestReplayHonoursBothKindsOfEvidence:
    def test_Replay_WhenPairConfirmedButUnclaimed_BothSidesExcluded(self, tmp_path):
        # The regression the split must not introduce: a transfer nobody
        # claimed but pairing proved must still stay out of spending.
        with _store_with(
            tmp_path, [(CURRENT, UNCLAIMED_OUT, "d1"), (SAVINGS, UNCLAIMED_IN, "d2")]
        ) as store:
            pair_transfers_across_store(store)
            payload = build_payload(store.all_transactions(), BINDINGS)

            assert payload == {}

    def test_Replay_WhenClaimUnpaired_StillExcludedOnTheProvidersWord(self, tmp_path):
        with _store_with(tmp_path, [(CURRENT, CLAIMED_LONELY, "d1")]) as store:
            pair_transfers_across_store(store)
            payload = build_payload(store.all_transactions(), BINDINGS)

            assert payload == {}

    def test_Notes_WhenClaimUnpaired_SayTheExclusionRestsOnTheProvidersWordAlone(
        self, tmp_path
    ):
        with _store_with(tmp_path, [(CURRENT, CLAIMED_LONELY, "d1")]) as store:
            pair_transfers_across_store(store)
            (held,) = store.all_transactions()

            notes = str(to_actual_transaction(held)["notes"])
            assert "internal transfer" in notes
            assert "unpaired" in notes

    def test_Notes_WhenPairConfirmed_PlainInternalTransfer(self, tmp_path):
        with _store_with(
            tmp_path, [(CURRENT, UNCLAIMED_OUT, "d1"), (SAVINGS, UNCLAIMED_IN, "d2")]
        ) as store:
            pair_transfers_across_store(store)
            confirmed = [t for t in store.all_transactions() if t.transfer_confirmed]

            for transaction in confirmed:
                notes = str(to_actual_transaction(transaction)["notes"])
                assert "internal transfer" in notes
                assert "unpaired" not in notes


class TestPairingSurvivesReRuns:
    def test_PairingPass_WhenASideVanishes_ConfirmationVanishesWithIt(self, tmp_path):
        # Confirmations are DERIVED facts: each pass states what the store
        # can prove NOW, never what an earlier pass once found. A stale
        # confirmation for a deleted row would exclude real spending on
        # evidence that no longer exists.
        with _store_with(
            tmp_path, [(CURRENT, UNCLAIMED_OUT, "d1"), (SAVINGS, UNCLAIMED_IN, "d2")]
        ) as store:
            pair_transfers_across_store(store)
            store.connection.execute(
                "DELETE FROM transactions WHERE account_id = ?", (SAVINGS,)
            )
            store.connection.commit()

            assert pair_transfers_across_store(store) == 0
            assert store.confirmed_transfer_entities() == set()
