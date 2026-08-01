from datetime import date

import pytest

from obdi.models import Transaction, TransactionStatus
from obdi.replay import (
    ActualAccountBinding,
    ReplayError,
    build_payload,
    to_actual_transaction,
    unbound_accounts,
)

BINDINGS = [
    ActualAccountBinding("starling-personal", "actual-acc-1"),
    ActualAccountBinding("halifax-current", "actual-acc-2"),
]


def txn(**overrides) -> Transaction:
    base = {
        "account_id": "starling-personal",
        "amount_minor": -1499,
        "value_date": date(2026, 3, 14),
        "booking_date": date(2026, 3, 14),
        "description": "TESCO STORES",
        "counterparty": "Tesco",
        "source": "starling",
        "entity_id": "ent-1",
    }
    base.update(overrides)
    return Transaction(**base)


class TestIdempotency:
    def test_Transaction_WhenReplayed_CanonicalIdBecomesTheIdempotencyKey(self):
        # Actual never adds two transactions sharing an imported_id, so this is
        # what makes replaying safe to repeat.
        assert to_actual_transaction(txn())["imported_id"] == "ent-1"

    def test_Transaction_WhenReplayedTwice_ProducesIdenticalPayload(self):
        assert to_actual_transaction(txn()) == to_actual_transaction(txn())

    def test_Transaction_WhenLackingEntityId_RefusedRatherThanDuplicated(self):
        # Without a stable key every replay would add another copy.
        with pytest.raises(ReplayError, match="duplicate"):
            to_actual_transaction(txn(entity_id=""))


class TestFieldMapping:
    def test_Amount_WhenReplayed_PassedThroughUnchanged(self):
        # Actual also stores integer minor units with a negative outflow, so
        # there is no conversion to get wrong.
        assert to_actual_transaction(txn())["amount"] == -1499

    def test_Payee_WhenCounterpartyKnown_UsedInPreferenceToRawDescription(self):
        assert to_actual_transaction(txn())["payee_name"] == "Tesco"

    def test_Payee_WhenCounterpartyMissing_FallsBackToDescription(self):
        assert to_actual_transaction(txn(counterparty=""))["payee_name"] == "TESCO STORES"

    def test_Payee_WhenReplayed_OriginalTextPreservedSeparately(self):
        # Actual's renaming rules need the untidied text to work from.
        assert to_actual_transaction(txn())["imported_payee"] == "TESCO STORES"

    def test_Transaction_WhenReplayed_ProvenanceCarriedIntoNotes(self):
        assert "via starling" in to_actual_transaction(txn())["notes"]


class TestPendingHandling:
    def test_Transaction_WhenSettled_MarkedCleared(self):
        assert to_actual_transaction(txn())["cleared"] is True

    def test_Transaction_WhenPending_LeftUnclearedSoItCanBeSuperseded(self):
        # A pending record is later replaced by its settled form; marking it
        # cleared would freeze it against that.
        pending = txn(status=TransactionStatus.PENDING)
        assert to_actual_transaction(pending)["cleared"] is False

    def test_Transaction_WhenPending_FlaggedInNotes(self):
        assert "pending" in to_actual_transaction(txn(status=TransactionStatus.PENDING))["notes"]


class TestGroupingByAccount:
    def test_Payload_WhenBuilt_GroupedByActualAccount(self):
        payload = build_payload(
            [txn(), txn(account_id="halifax-current", entity_id="ent-2")], BINDINGS
        )
        assert set(payload) == {"actual-acc-1", "actual-acc-2"}

    def test_Payload_WhenAccountUnbound_SkippedRatherThanGuessed(self):
        # Inventing a destination would scatter transactions into the wrong
        # budget, which is worse than omitting them.
        payload = build_payload([txn(account_id="unknown-account")], BINDINGS)
        assert payload == {}

    def test_Payload_WhenAccountUnbound_ReportedSoTheGapIsVisible(self):
        # An account quietly missing from a budget looks like missing spending.
        missing = unbound_accounts([txn(account_id="unknown-account")], BINDINGS)
        assert missing == ["unknown-account"]


class TestInternalTransfers:
    def test_Transfer_WhenReplayed_ExcludedByDefault(self):
        # Counting both sides inflates spending and income alike.
        transfer = txn(is_internal_transfer=True)
        assert build_payload([transfer], BINDINGS) == {}

    def test_Transfer_WhenExplicitlyRequested_Included(self):
        transfer = txn(is_internal_transfer=True)
        payload = build_payload([transfer], BINDINGS, include_internal_transfers=True)
        assert len(payload["actual-acc-1"]) == 1

    def test_Transfer_WhenIncluded_LabelledInNotes(self):
        transfer = txn(is_internal_transfer=True)
        assert "internal transfer" in to_actual_transaction(transfer)["notes"]
