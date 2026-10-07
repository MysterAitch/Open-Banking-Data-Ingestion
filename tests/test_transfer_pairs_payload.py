"""Transfers reach Actual as ordinary rows, with the confirmed pairs listed apart.

Withholding every movement between your own accounts left each balance in
Actual wrong by the sum of that account's transfers: a current account that
should read 400.00 read 1,900.00, and its savings pot -1,200.00 instead of
300.00. A flat import cannot express Actual's transfer type, so the rows go
as ordinary rows and the pairing travels in the envelope for the applier to
link once they are in.

The household here is invented and built through real Starling feed
artefacts, `rebuild_from_raw` and its pairing pass, so the store under test
holds what a live store would: main takes 2000.00 of income, sends 1500.00
to the pot and spends 100.00 (ends on 400.00); the pot receives the 1500.00
and spends 1200.00 (ends on 300.00).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from obdi.actual_push import build_audit_envelope, build_envelope, build_prune_envelope
from obdi.core.models import TransactionStatus
from obdi.ingest.accounts import AccountBinding, AccountMap, AccountRecord, AccountRef
from obdi.ingest.providers import starling
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.replay import (
    ActualAccountBinding,
    build_payload,
    build_transfer_pairs,
    to_actual_transaction,
)

MAIN = "household-main"
POT = "household-pot"

ACCOUNT_MAP = AccountMap(
    [
        AccountBinding(MAIN, "starling", "main-cat"),
        AccountBinding(POT, "starling", "pot-cat"),
    ]
)

BOUND_BOTH = [
    ActualAccountBinding(MAIN, "act-main"),
    ActualAccountBinding(POT, "act-pot"),
]


def _item(uid: str, minor: int, direction: str, source: str, day: str, who: str) -> dict:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": direction,
        "transactionTime": f"2026-09-{day}T09:00:00.000Z",
        "source": source,
        "status": "SETTLED",
        "counterPartyName": who,
        "reference": uid,
    }


INCOME = _item("income", 200000, "IN", "FASTER_PAYMENTS_IN", "01", "Employer")
TO_POT = _item("to-pot", 150000, "OUT", "INTERNAL_TRANSFER", "02", "Savings pot")
SPEND = _item("spend", 10000, "OUT", "MASTER_CARD", "03", "Shop")
FROM_MAIN = _item("from-main", 150000, "IN", "INTERNAL_TRANSFER", "02", "Current account")
POT_SPEND = _item("pot-spend", 120000, "OUT", "MASTER_CARD", "04", "Holiday")


def _land(store: Store, provider_account: str, items: list[dict]) -> None:
    store.land_artefact(
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id=f"starling:{provider_account}",
            kind="feed",
            origin=f"https://api.example.com/feed/account/a/category/{provider_account}",
        )
    )


def _household(store: Store, *, main=None, pot=None) -> None:
    _land(store, "main-cat", main if main is not None else [INCOME, TO_POT, SPEND])
    pot = pot if pot is not None else [FROM_MAIN, POT_SPEND]
    if pot:
        _land(store, "pot-cat", pot)
    rebuild_from_raw(store, account_map=ACCOUNT_MAP)


def _sums(payload: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    return {
        account: sum(int(str(row["amount"])) for row in rows)
        for account, rows in payload.items()
    }


def _imported_id(store: Store, account: str, amount: int) -> str:
    (row,) = [
        to_actual_transaction(t)
        for t in store.all_transactions()
        if t.account_id == account and t.amount_minor == amount
    ]
    return str(row["imported_id"])


class TestHouseholdBalances:
    def test_Payload_WhenTransfersAreBetweenBoundAccounts_EveryAccountSumsToItsTrueBalance(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            payload = build_payload(store.all_transactions(), BOUND_BOTH)

        assert _sums(payload) == {"act-main": 40000, "act-pot": 30000}
        assert {len(rows) for rows in payload.values()} == {3, 2}

    def test_Envelope_WhenTransfersAreBetweenBoundAccounts_ListsExactlyThePairWithItsImportedIds(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            envelope = build_envelope(store, BOUND_BOTH, {})

            debit_id = _imported_id(store, MAIN, -150000)
            credit_id = _imported_id(store, POT, 150000)

        assert envelope["version"] == 3
        assert envelope["transfers"] == [
            {
                "debit": {
                    "account": "act-main",
                    "account_name": MAIN,
                    "imported_id": debit_id,
                    "date": "2026-09-02",
                    "amount": -150000,
                },
                "credit": {
                    "account": "act-pot",
                    "account_name": POT,
                    "imported_id": credit_id,
                    "date": "2026-09-02",
                    "amount": 150000,
                },
            }
        ]

    def test_Envelope_PairedLegs_AreRowsInTheAccountsTheyAreLinkedWithin(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            envelope = build_envelope(store, BOUND_BOTH, {})

        accounts = envelope["accounts"]
        (pair,) = envelope["transfers"]  # type: ignore[misc]
        assert isinstance(accounts, dict)
        for leg in (pair["debit"], pair["credit"]):
            held = {row["imported_id"] for row in accounts[leg["account"]]}
            assert leg["imported_id"] in held

    def test_AuditAndPruneEnvelopes_CarryTheSameVersionAndTheSamePairs(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            push = build_envelope(store, BOUND_BOTH, {})
            audit = build_audit_envelope(store, BOUND_BOTH)
            prune = build_prune_envelope(store, BOUND_BOTH)

        assert audit["version"] == prune["version"] == push["version"] == 3
        assert audit["transfers"] == prune["transfers"] == push["transfers"]
        assert len(push["transfers"]) == 1  # type: ignore[arg-type]
        assert audit["kind"] == "audit"
        assert prune["kind"] == "prune"

    def test_Notes_WhenPairIsConfirmed_BothRowsSayInternalTransferAndNeitherCallsItUnpaired(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            payload = build_payload(store.all_transactions(), BOUND_BOTH)

        notes = [
            str(row["notes"])
            for rows in payload.values()
            for row in rows
            if abs(int(str(row["amount"]))) == 150000
        ]
        assert len(notes) == 2
        assert all("internal transfer" in n and "unpaired" not in n for n in notes)


class TestWhatIsNotPaired:
    def test_Envelope_WhenOtherAccountIsUnbound_RowIsSentAndNoPairIsListed(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            envelope = build_envelope(store, [ActualAccountBinding(MAIN, "act-main")], {})

        assert envelope["transfers"] == []
        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert _sums(accounts) == {"act-main": 40000}

    def test_Envelope_WhenProviderClaimsATransferWithNoPartnerHeld_RowGoesWithItsNoteAndNoPair(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store, pot=[])

            envelope = build_envelope(store, BOUND_BOTH, {})

        assert envelope["transfers"] == []
        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert _sums(accounts) == {"act-main": 40000}
        (claim,) = [r for r in accounts["act-main"] if r["amount"] == -150000]
        assert "internal transfer (unpaired claim)" in str(claim["notes"])

    def test_Envelope_WhenBothCanonicalAccountsShareOneActualAccount_NoPairIsListed(
        self, tmp_path
    ):
        shared = [ActualAccountBinding(MAIN, "act-one"), ActualAccountBinding(POT, "act-one")]
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            envelope = build_envelope(store, shared, {})

        # A transfer needs two accounts to move between; both rows are still
        # in the one account, so its balance is the sum of the lot.
        assert envelope["transfers"] == []
        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert _sums(accounts) == {"act-one": 70000}

    def test_TransferPairs_WhenALegIsVoid_NoPairIsListedAndTheVoidRowIsWithheld(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)
            transactions = [
                replace(t, status=TransactionStatus.VOID)
                if t.account_id == POT and t.amount_minor == 150000
                else t
                for t in store.all_transactions()
            ]
            pairs = store.confirmed_transfer_pairs()

            listed = build_transfer_pairs(transactions, BOUND_BOTH, pairs)
            payload = build_payload(transactions, BOUND_BOTH)

        assert pairs, "the pair must exist in the store for this to mean anything"
        assert listed == []
        assert len(payload["act-pot"]) == 1

    def test_TransferPairs_WhenTheStoreProvedNoPair_NothingIsListed(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)

            listed = build_transfer_pairs(store.all_transactions(), BOUND_BOTH, [])

        assert listed == []


def _archive_the_pot(store: Store) -> None:
    store.declare_account(
        AccountRecord(
            ref=AccountRef(POT), kind="starling-space", label="Savings pot",
            closed=date(2026, 9, 30),
        )
    )


class TestAnArchivedAccountThatHoldsATransfersOtherLeg:
    """An archived account is still sent, so a historical transfer has an account to land in.

    Archiving says the account is no longer in use, not that its history is gone: the main
    account's 1500.00 to the pot is only a transfer in Actual while the pot is there to
    receive it, and without the pot main's own balance would still be right and the money
    would simply have left the budget.
    """

    def test_Envelope_WhenTheArchivedAccountIsNotInActualYet_AsksForItToBeCreated(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)
            _archive_the_pot(store)

            envelope = build_envelope(store, [ActualAccountBinding(MAIN, "act-main")], {})

        assert envelope["provision"] == [{"canonical_id": POT, "label": "Savings pot"}]

    def test_Envelope_WhenTheArchivedAccountIsInActual_SendsItsRowsAndListsThePair(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store)
            _archive_the_pot(store)

            envelope = build_envelope(store, BOUND_BOTH, {})

        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert _sums(accounts) == {"act-main": 40000, "act-pot": 30000}
        pairs = envelope["transfers"]
        assert isinstance(pairs, list) and len(pairs) == 1
        assert pairs[0]["debit"]["account"] == "act-main"
        assert pairs[0]["credit"]["account"] == "act-pot"

    def test_Envelope_WhenTheAccountIsNotArchived_IsTheSame(self, tmp_path):
        with Store(tmp_path / "open.sqlite3") as store:
            _household(store)
            open_envelope = build_envelope(store, BOUND_BOTH, {})
        with Store(tmp_path / "archived.sqlite3") as store:
            _household(store)
            _archive_the_pot(store)
            archived_envelope = build_envelope(store, BOUND_BOTH, {})

        for part in ("accounts", "transfers", "provision"):
            assert archived_envelope[part] == open_envelope[part], part


class TestTwoIdenticalTransfersOnOneDay:
    def test_Envelope_WhenTwoTransfersMatchInAmountAndDay_BothPairsListFourDistinctIds(
        self, tmp_path
    ):
        out_1 = _item("out-1", 50000, "OUT", "INTERNAL_TRANSFER", "05", "Savings pot")
        out_2 = _item("out-2", 50000, "OUT", "INTERNAL_TRANSFER", "05", "Savings pot")
        in_1 = _item("in-1", 50000, "IN", "INTERNAL_TRANSFER", "05", "Current account")
        in_2 = _item("in-2", 50000, "IN", "INTERNAL_TRANSFER", "05", "Current account")
        with Store(tmp_path / "s.sqlite3") as store:
            _household(store, main=[out_1, out_2], pot=[in_1, in_2])

            envelope = build_envelope(store, BOUND_BOTH, {})

        pairs = envelope["transfers"]
        assert isinstance(pairs, list)
        assert len(pairs) == 2
        ids = [leg["imported_id"] for pair in pairs for leg in (pair["debit"], pair["credit"])]
        assert len(set(ids)) == 4
        for pair in pairs:
            assert pair["debit"]["amount"] == -50000
            assert pair["credit"]["amount"] == 50000
