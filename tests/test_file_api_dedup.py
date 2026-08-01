"""The same payment arriving by file export AND by live API.

This is not hypothetical. File exports are the only route for some accounts and
the deep-history route for all of them, so any account with an API connection
will also have overlapping CSVs imported. If those do not reconcile, every
backfilled month is double-counted.

Exercised through the real store rather than the matching function alone,
because the failure would happen in the wiring - a canonical account not shared
between the two paths - not in the matcher.
"""

from datetime import date

import pytest

from obdi.ingest import import_file, reconcile_batch
from obdi.providers import starling as starling_provider
from obdi.providers import truelayer
from obdi.store import Store

STARLING_CSV = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category,Notes\n"
    b"14/03/2026,Tesco,TESCO STORES,CARD,-14.99,1200.00,GROCERIES,\n"
    b"15/03/2026,Employer Ltd,SALARY MARCH,FASTER PAYMENT,2500.00,3700.00,INCOME,\n"
)

# The same two payments as the aggregator reports them: its own ids, and its
# own way of describing the merchant.
TRUELAYER_RECORDS = [
    {
        "transaction_id": "volatile-1",
        "normalised_provider_transaction_id": "tl-tx-1",
        "timestamp": "2026-03-14T00:00:00Z",
        "description": "TESCO STORES 4912",
        "amount": -14.99,
        "currency": "GBP",
        "transaction_type": "DEBIT",
        "merchant_name": "Tesco",
    },
    {
        "transaction_id": "volatile-2",
        "normalised_provider_transaction_id": "tl-tx-2",
        "timestamp": "2026-03-15T00:00:00Z",
        "description": "SALARY MARCH",
        "amount": 2500.00,
        "currency": "GBP",
        "transaction_type": "CREDIT",
    },
]

CANONICAL = "halifax-current"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def import_csv(store: Store, tmp_path, account_id: str = CANONICAL) -> None:
    path = tmp_path / "export.csv"
    path.write_bytes(STARLING_CSV)
    import_file(store, path, account_id=account_id)


def pull_api(store: Store, account_id: str = CANONICAL) -> None:
    transactions = [
        truelayer.to_transaction(record, account_id=account_id) for record in TRUELAYER_RECORDS
    ]
    reconcile_batch(store, transactions, digest="api-digest")


class TestFileThenApi:
    def test_Payment_WhenImportedFromCsvThenPulledFromApi_StoredOnce(self, store, tmp_path):
        import_csv(store, tmp_path)
        pull_api(store)
        assert len(store.transactions_for_account(CANONICAL)) == 2

    def test_Payment_WhenPulledFromApiThenImportedFromCsv_StoredOnce(self, store, tmp_path):
        # Order must not matter: backfills routinely arrive after a live pull.
        pull_api(store)
        import_csv(store, tmp_path)
        assert len(store.transactions_for_account(CANONICAL)) == 2

    def test_Payment_WhenBothRoutesRepeated_StillStoredOnce(self, store, tmp_path):
        # Export caps force overlapping downloads, so repetition is the norm.
        import_csv(store, tmp_path)
        pull_api(store)
        import_csv(store, tmp_path)
        pull_api(store)
        assert len(store.transactions_for_account(CANONICAL)) == 2

    def test_Payment_WhenMatchedAcrossRoutes_ProviderIdRetained(self, store, tmp_path):
        # The CSV has no id; the API does. After matching, the better
        # identifier should survive rather than the poorer one.
        import_csv(store, tmp_path)
        pull_api(store)
        ids = {t.source_id for t in store.transactions_for_account(CANONICAL)}
        assert ids == {"tl-tx-1", "tl-tx-2"}


class TestAccountBindingIsLoadBearing:
    def test_Payment_WhenFileAndApiUseDifferentAccountIds_NotMatched(self, store, tmp_path):
        # The failure mode worth knowing about: matching is scoped per account,
        # so importing under a name that does not match the bound canonical
        # account silently double-counts everything.
        import_csv(store, tmp_path, account_id="halifax")
        pull_api(store, account_id="halifax-current")

        assert len(store.transactions_for_account("halifax")) == 2
        assert len(store.transactions_for_account("halifax-current")) == 2


class TestThreeWayOverlap:
    def test_Payment_WhenSeenByCsvAggregatorAndFirstPartyApi_StoredOnce(self, store, tmp_path):
        # The full cross-check case: a file backfill, an aggregator, and the
        # bank's own API all reporting the same payment.
        import_csv(store, tmp_path)
        pull_api(store)

        first_party = starling_provider.to_transaction(
            {
                "feedItemUid": "feed-1",
                "amount": {"currency": "GBP", "minorUnits": 1499},
                "direction": "OUT",
                "transactionTime": "2026-03-14T09:15:00.000Z",
                "source": "MASTER_CARD",
                "status": "SETTLED",
                "counterPartyName": "Tesco",
                "reference": "TESCO STORES",
            },
            account_id=CANONICAL,
        )
        reconcile_batch(store, [first_party], digest="starling-digest")

        matching_date = [
            t
            for t in store.transactions_for_account(CANONICAL)
            if t.value_date == date(2026, 3, 14)
        ]
        assert len(matching_date) == 1
