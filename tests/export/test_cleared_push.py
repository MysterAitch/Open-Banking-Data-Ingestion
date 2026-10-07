"""What Actual is told about a row's cleared state.

The applier hands each payload row to Actual's own importer unchanged (`applyAccounts` in
applier/lib.mjs), so the flag in the envelope is the whole of what Actual receives, and the importer
keeps an existing row's values on a re-import: `cleared` therefore reaches a row when it is first
added and is the owner's to change afterwards. `reconciled` is never sent.

March 2026 in one account, bound to an Actual account:

    c1  03-02  starling-csv, booked                    cleared
    c2  03-03  truelayer-booked only                   not cleared: the aggregator alone
    c3  03-04  starling-csv, PENDING                   not cleared: pending is never cleared
    c4  03-05  the bank's feed, booked, as the provider  cleared
               makes it (source `starling`)
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.core.models import TransactionStatus
from obdi.export.actual_push import build_envelope
from obdi.export.replay import ActualAccountBinding, is_cleared, to_actual_transaction
from obdi.ingest.store import Store
from round_up_corpus import rows_the_provider_makes
from test_ledger import land, txn

D = date
ACCOUNT = "everyday"
BINDINGS = [ActualAccountBinding(ACCOUNT, "act-everyday", "Everyday")]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "cleared.sqlite3") as opened:
        land(opened, "d1", txn(ACCOUNT, "starling-csv", "c1", D(2026, 3, 2), -100, "ONE"),
             txn(ACCOUNT, "starling-csv", "c3", D(2026, 3, 4), -300, "THREE",
                 status=TransactionStatus.PENDING))
        land(opened, "d2", txn(ACCOUNT, "truelayer-booked", "c2", D(2026, 3, 3), -200, "TWO"))
        land(opened, "d3", *rows_the_provider_makes(ACCOUNT, "c4", "FOUR", 400, "2026-03-05"))
        yield opened


def rows_sent(store: Store) -> dict[str, dict[str, object]]:
    envelope = build_envelope(store, BINDINGS, {})
    accounts = envelope["accounts"]
    assert isinstance(accounts, dict)
    return {str(row["imported_payee"]): row for row in accounts["act-everyday"]}


class TestWhatTheEnvelopeCarries:
    def test_Envelope_MarksARowClearedWhenAnAuthoritativeListingListsIt(self, store):
        sent = rows_sent(store)

        assert sent["ONE"]["cleared"] is True
        assert sent["FOUR"]["cleared"] is True

    def test_Envelope_DoesNotMarkARowClearedWhenOnlyTheAggregatorListsIt(self, store):
        assert rows_sent(store)["TWO"]["cleared"] is False

    def test_Envelope_NeverMarksAPendingRowCleared_WhoeverListsIt(self, store):
        assert rows_sent(store)["THREE"]["cleared"] is False

    def test_Envelope_NeverSendsReconciled(self, store):
        for row in rows_sent(store).values():
            assert "reconciled" not in row

    def test_Envelope_KeepsTheOpeningRowCleared(self, tmp_path):
        from obdi.verify.balance_anchors import record_stated_anchor

        with Store(tmp_path / "opening.sqlite3") as opened:
            land(opened, "d", txn(ACCOUNT, "truelayer-booked", "x", D(2026, 3, 2), -100, "ONE"))
            record_stated_anchor(opened, ACCOUNT, "2026-03-02", "10.00")
            envelope = build_envelope(opened, BINDINGS, {})

        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        opening = [r for r in accounts["act-everyday"] if r.get("starting_balance_flag")]
        assert [r["cleared"] for r in opening] == [True]


class TestTheRuleItself:
    def test_IsCleared_WithoutTheListIsTheRuleBeforeClearing_AnyRowThatIsNotPending(self, store):
        rows = {t.description: t for t in store.transactions_for_account(ACCOUNT)}

        assert is_cleared(rows["TWO"], None) is True
        assert is_cleared(rows["THREE"], None) is False

    def test_ToActualTransaction_WithTheListFollowsTheList(self, store):
        rows = {t.description: t for t in store.transactions_for_account(ACCOUNT)}

        assert to_actual_transaction(rows["TWO"], cleared=set())["cleared"] is False
        listed = {rows["TWO"].entity_id}
        assert to_actual_transaction(rows["TWO"], cleared=listed)["cleared"] is True
        assert (
            to_actual_transaction(rows["THREE"], cleared={rows["THREE"].entity_id})["cleared"]
            is False
        )
