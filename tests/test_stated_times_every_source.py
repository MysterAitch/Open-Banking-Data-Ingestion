"""Every date a source states for a payment is kept against it, whichever source it is.

The first build kept a feed item's moments and nothing else, so the aggregator's, the
exports', and the statements' dates were thrown away as soon as one of them became the
row's date. Each scenario goes in through the door the source really uses (a landed
aggregator response, an imported file, an imported statement) and is read back from the
stored row, and again after a rebuild from raw.

KNOWN ANSWERS, decided before the first run:

    an aggregator item stating `timestamp`, `meta.provider_posted` and `meta.detail.cleared`
    beside text, an amount, and a malformed date
        exactly those three, the nested ones by path, as stated
    a Starling export row (column Date), an Amex export row (Date), a QIF record (D)
        the one date column, in ISO, whatever format the file pinned
    a Virgin Money statement row (transaction date, posting date)
        both dates, and the statement's own date beside them
    a card statement row (date of transaction, date entered)
        both, the entered date in the year the statement's date implies
    a Santander statement row, which states one date
        the transaction date and the statement's, and no posting date
    a statement kept before rows carried a posting date
        reads as having none, not as unreadable
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from late_settlement_corpus import export_text
from obdi.ingest import import_file
from obdi.parsers.statement_reading import reading_from_json, reading_to_json
from obdi.parsers.virgin_money_pdf import read_statement
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from test_id_tier import aggregator_artefact, arrive
from test_pdf_import import SANTANDER
from test_space_attribution import MAIN, MAP
from test_statement_shape import build_pdf
from test_uk_card_statement import CONTRACT, build_card_statement_pdf
from test_virgin_money_statement import STATEMENT as VIRGIN_LINES


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "stated.sqlite3")
    yield opened
    opened.close()


def stated(store: Store, *, account: str | None = None) -> dict[tuple[str, str], str]:
    """Every (source, field) -> stated text, over the account's rows or all of them."""
    found: dict[tuple[str, str], str] = {}
    rows = store.all_transactions() if account is None else store.transactions_for_account(account)
    for row in rows:
        for moment in store.stated_times_for(row.entity_id):
            found[(moment["source"], moment["field"])] = moment["stated"]
    return found


def rebuilt(store: Store) -> None:
    assert rebuild_from_raw(store, account_map=MAP).problems == []


ITEM = {
    "transaction_id": "volatile-1",
    "normalised_provider_transaction_id": "tl-1",
    "timestamp": "2026-09-14T10:00:00Z",
    "description": "ABROAD SHOP",
    "amount": "-42.10",
    "currency": "GBP",
    "transaction_type": "DEBIT",
    "meta": {
        "provider_category": "PURCHASE",
        "provider_posted": "2026-09-15T04:00:00+01:00",
        "detail": {"cleared": "2026-09-16", "badly_dated": "2026-13-45"},
    },
}


class TestAnAggregatorItem:
    def test_Item_WhenItStatesDatesAtSeveralDepths_EveryOneIsKeptByItsPath(self, store):
        arrive(store, aggregator_artefact([ITEM]), MAIN)

        assert stated(store) == {
            ("truelayer", "timestamp"): "2026-09-14T10:00:00Z",
            ("truelayer", "meta.provider_posted"): "2026-09-15T04:00:00+01:00",
            ("truelayer", "meta.detail.cleared"): "2026-09-16",
        }

    def test_Item_WhenTheStoreIsRebuiltFromRaw_HoldsTheSameDates(self, store):
        arrive(store, aggregator_artefact([ITEM]), MAIN)
        before = stated(store)

        rebuilt(store)

        assert stated(store) == before

    def test_Item_WhenItStatesNoId_IsStillRecorded(self, store):
        record = {k: v for k, v in ITEM.items() if k != "normalised_provider_transaction_id"}
        arrive(store, aggregator_artefact([record]), MAIN)

        assert ("truelayer", "timestamp") in stated(store)


class TestFileExports:
    def test_StarlingExport_WhenImported_KeepsItsDateColumnInIso(self, store, tmp_path):
        path = tmp_path / "export.csv"
        path.write_text(export_text([("Coffee", -350, date(2026, 9, 10))]))
        import_file(store, path, account_id=MAIN)

        assert stated(store) == {("starling-csv", "Date"): "2026-09-10"}

    def test_AmexExport_WhenImported_KeepsItsDateColumnInIso(self, store, tmp_path):
        path = tmp_path / "amex.csv"
        path.write_text(
            "Date,Description,Amount,Reference\n10/09/2026,EXAMPLE SHOP,12.50,'AT26001\n"
        )
        import_file(store, path, account_id="amex")

        assert stated(store) == {("amex-uk-csv", "Date"): "2026-09-10"}

    def test_Qif_WhenImported_KeepsItsDateRecordInIso(self, store, tmp_path):
        path = tmp_path / "bank.qif"
        path.write_text("!Type:Bank\nD10/09/2026\nT-5.00\nPExample Shop\n^\n")
        import_file(store, path, account_id="qif-account")

        assert stated(store) == {("qif", "D"): "2026-09-10"}

    def test_Export_WhenTheStoreIsRebuiltFromRaw_HoldsTheSameDates(self, store, tmp_path):
        path = tmp_path / "bank.qif"
        path.write_text("!Type:Bank\nD10/09/2026\nT-5.00\nPExample Shop\n^\n")
        import_file(store, path, account_id="qif-account")

        rebuilt(store)

        assert stated(store) == {("qif", "D"): "2026-09-10"}


def statement_dates(store: Store) -> dict[str, dict[str, str]]:
    """Each row's description -> its stated fields."""
    return {
        row.description: {
            m["field"]: m["stated"] for m in store.stated_times_for(row.entity_id)
        }
        for row in store.all_transactions()
    }


class TestStatements:
    def test_VirginMoneyRow_WhenImported_KeepsBothItsDatesAndTheStatements(self, store, tmp_path):
        path = tmp_path / "virgin.pdf"
        path.write_bytes(build_pdf(VIRGIN_LINES))
        import_file(store, path, account_id="virgin-cc")

        row = statement_dates(store)["ANOTHER MERCHANT  BRISTOL"]
        assert row["transaction_date"] == "2026-07-20"
        assert row["posting_date"] == "2026-07-21"
        assert row["statement_date"] == read_statement(VIRGIN_LINES).statement_date.isoformat()

    def test_CardStatementRow_WhenImported_KeepsTheDateOfTransactionAndTheDateEntered(
        self, store, tmp_path
    ):
        lines = [
            line.replace("ROW|1234|06 JULY|06 JULY", "ROW|1234|06 JULY|08 JULY")
            for line in CONTRACT
        ]
        path = tmp_path / "card.pdf"
        path.write_bytes(build_card_statement_pdf(lines))
        import_file(store, path, account_id="card")

        row = next(r for d, r in statement_dates(store).items() if "EXAMPLE SHOP" in d)
        assert row["transaction_date"] == "2026-07-06"
        assert row["posting_date"] == "2026-07-08"

    def test_SantanderRow_WhenTheFormatStatesOneDate_HasNoPostingDate(self, store, tmp_path):
        path = tmp_path / "santander.pdf"
        path.write_bytes(SANTANDER)
        import_file(store, path, account_id="santander-cc")

        every = statement_dates(store)
        assert every
        for fields in every.values():
            assert "transaction_date" in fields
            assert "posting_date" not in fields

    def test_Statement_WhenRebuiltFromRaw_HoldsBothDatesAgain(self, store, tmp_path):
        path = tmp_path / "virgin.pdf"
        path.write_bytes(build_pdf(VIRGIN_LINES))
        import_file(store, path, account_id="virgin-cc")
        before = statement_dates(store)

        rebuilt(store)

        assert statement_dates(store) == before


class TestReadingsKeptBeforeRowsCarriedAPostingDate:
    def test_Reading_WhenKeptWithThreeFieldRows_ReadsAsHavingNoPostingDate(self):
        reading = read_statement(VIRGIN_LINES)
        kept = json.loads(reading_to_json(reading))
        kept["transactions"] = [row[:3] for row in kept["transactions"]]

        older = reading_from_json(json.dumps(kept))

        assert [row.posted for row in older.transactions] == [None] * len(older.transactions)
        assert [row.value_date for row in older.transactions] == [
            row.value_date for row in reading.transactions
        ]

    def test_Reading_WhenRoundTripped_KeepsEachRowsPostingDate(self):
        reading = read_statement(VIRGIN_LINES)

        again = reading_from_json(reading_to_json(reading))

        assert again == reading
        assert all(row.posted is not None for row in again.transactions)
