"""Spaces (savings pots) modelled as accounts.

The failure this guards against is a real one, seen in other budgeting tools:
a movement into a savings pot is treated as leaving your money entirely, so it
shows up as spending, and the matching inflow shows up as income. The month
looks wildly wrong and the pot balance is invisible.

Discarding the movement instead is not a fix - it swaps inflated spending for
money that silently vanishes.

The correct model is that a Space IS an account. Moving money into one is a
transfer between two accounts you own: both sides are recorded, in different
accounts, and paired so neither counts as spending or income.
"""

from datetime import date

from obdi.accounts import AccountBinding, AccountMap
from obdi.ingest import pair_transfers_across_store, reconcile_batch
from obdi.providers.starling import to_transaction
from obdi.store import Store

MAIN = "starling-personal"
SPACE = "starling-holiday-fund"

MOVE_OUT = {
    "feedItemUid": "feed-out",
    "amount": {"currency": "GBP", "minorUnits": 20000},
    "direction": "OUT",
    "transactionTime": "2026-03-14T09:00:00.000Z",
    "source": "INTERNAL_TRANSFER",
    "status": "SETTLED",
    "counterPartyName": "Holiday fund",
    "reference": "To Holiday fund",
}

MOVE_IN = {
    "feedItemUid": "feed-in",
    "amount": {"currency": "GBP", "minorUnits": 20000},
    "direction": "IN",
    "transactionTime": "2026-03-14T09:00:00.000Z",
    "source": "INTERNAL_TRANSFER",
    "status": "SETTLED",
    "counterPartyName": "Personal",
    "reference": "From Personal",
}

SPENDING = {
    "feedItemUid": "feed-spend",
    "amount": {"currency": "GBP", "minorUnits": 1499},
    "direction": "OUT",
    "transactionTime": "2026-03-15T09:00:00.000Z",
    "source": "MASTER_CARD",
    "status": "SETTLED",
    "counterPartyName": "Tesco",
    "reference": "TESCO STORES",
}


class TestSpaceIsAnAccount:
    def test_Space_WhenBound_ResolvesToItsOwnCanonicalAccount(self):
        account_map = AccountMap(
            [
                AccountBinding(MAIN, "starling", "account-uid"),
                AccountBinding(SPACE, "starling", "savings-goal-uid"),
            ]
        )
        assert account_map.resolve("starling", "savings-goal-uid") == SPACE
        assert account_map.resolve("starling", "account-uid") == MAIN


class TestTransferBetweenAccountAndSpace:
    def test_Transfer_WhenMovedIntoSpace_BothSidesRecorded(self, tmp_path):
        # Neither side may be discarded: one is the money leaving the current
        # account, the other is the pot balance going up.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [to_transaction(MOVE_OUT, account_id=MAIN)], digest="d1")
            reconcile_batch(store, [to_transaction(MOVE_IN, account_id=SPACE)], digest="d2")

            assert len(store.transactions_for_account(MAIN)) == 1
            assert len(store.transactions_for_account(SPACE)) == 1

    def test_Transfer_WhenBothSidesStored_PairedAsInternal(self, tmp_path):
        # Pairing is what stops it counting as spending on one side and income
        # on the other.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [to_transaction(MOVE_OUT, account_id=MAIN)], digest="d1")
            reconcile_batch(store, [to_transaction(MOVE_IN, account_id=SPACE)], digest="d2")

            flagged = pair_transfers_across_store(store)

            assert flagged >= 1
            assert all(t.is_internal_transfer for t in store.all_transactions())

    def test_Spending_WhenAlongsideATransfer_NotFlaggedAsInternal(self, tmp_path):
        # The point of the exercise: real spending must survive the pairing.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    to_transaction(MOVE_OUT, account_id=MAIN),
                    to_transaction(SPENDING, account_id=MAIN),
                ],
                digest="d1",
            )
            reconcile_batch(store, [to_transaction(MOVE_IN, account_id=SPACE)], digest="d2")
            pair_transfers_across_store(store)

            spending = [
                t
                for t in store.transactions_for_account(MAIN)
                if t.value_date == date(2026, 3, 15)
            ]
            assert len(spending) == 1
            assert not spending[0].is_internal_transfer


class TestSpaceFoldedIntoParentWouldBreakPairing:
    def test_Transfer_WhenSpaceFoldedIntoParentAccount_CannotBePaired(self, tmp_path):
        # Documents WHY a Space needs its own account: pairing requires the two
        # sides to sit in different accounts, so folding them together leaves
        # both looking like ordinary spending and income.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    to_transaction(MOVE_OUT, account_id=MAIN),
                    to_transaction(MOVE_IN, account_id=MAIN),
                ],
                digest="d1",
            )
            pair_transfers_across_store(store)

            paired = [t for t in store.all_transactions() if t.is_internal_transfer]
            # Both still carry the provider's own internal-transfer marker, but
            # neither was PAIRED, so nothing confirms them against each other.
            assert len(paired) == 2
            assert len({t.account_id for t in paired}) == 1
