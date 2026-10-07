"""A row carries the day a payment was made on `value_date` and the day it posted on `booking_date`.

The answers are decided from what each source states, before the mapping was changed: the card
statement lists a transaction date with an entered date beside it, the bank feed a
`transactionTime` and a `settlementTime`, and the aggregator one `timestamp` which its
documentation calls the posting date. A rhythm (`analysis.recurring`) is measured on whichever
of the two its kind needs, so each must mean one thing on every row.
"""

from __future__ import annotations

from datetime import date

from obdi.core.models import Transaction
from obdi.ingest.parsers.pdf_statements import PdfStatementParser
from obdi.ingest.parsers.statement_reading import StatementReading, StatementRow
from obdi.ingest.providers import starling, truelayer


def feed_item(**overrides) -> dict:
    item = {
        "feedItemUid": "feed-1",
        "amount": {"currency": "GBP", "minorUnits": 1499},
        "direction": "OUT",
        "transactionTime": "2026-03-15T09:15:00.000Z",
        "settlementTime": "2026-03-17T02:00:00.000Z",
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": "Bakery",
        "reference": "BAKERY",
    }
    item.update(overrides)
    return item


def statement_rows(posted: date | None) -> list[Transaction]:
    class Reads(PdfStatementParser):
        source = "invented-statement-pdf"

        def sniff(self, payload: bytes) -> bool:
            return True

        def read(self, payload: bytes) -> StatementReading:
            raise AssertionError("not read")

    reading = StatementReading(
        statement_date=date(2026, 4, 30),
        transactions=[
            StatementRow(
                value_date=date(2026, 4, 19),
                description="A PAYEE",
                amount_minor=-500,
                posted=posted,
            )
        ],
    )
    return list(Reads().rows_of(reading, "acct"))


class TestStarlingFeedRow:
    def test_FeedRow_WhenBothTimesStated_MadeOnValueDateAndSettledOnBookingDate(self):
        row = starling.to_transaction(feed_item(), account_id="a")

        assert row is not None
        assert row.value_date == date(2026, 3, 15)
        assert row.booking_date == date(2026, 3, 17)
        assert row.states_transaction_date

    def test_FeedRow_WhenNoSettlementTimeYet_BothDatesCarryTheTransactionDay(self):
        row = starling.to_transaction(feed_item(settlementTime=""), account_id="a")

        assert row is not None
        assert row.value_date == row.booking_date == date(2026, 3, 15)

    def test_FeedRow_WhenOnlySettlementTimeStated_BothDatesCarryIt(self):
        row = starling.to_transaction(feed_item(transactionTime=""), account_id="a")

        assert row is not None
        assert row.value_date == row.booking_date == date(2026, 3, 17)

    def test_FeedRow_WhenSettlementMovesTheDay_IdentityKeyStillRestsOnTheTransactionDay(self):
        settled = starling.to_transaction(feed_item(), account_id="a")
        unsettled = starling.to_transaction(feed_item(settlementTime=""), account_id="a")

        assert settled is not None and unsettled is not None
        assert settled.content_key == unsettled.content_key


class TestCardStatementRow:
    def test_StatementRow_WhenEnteredDateStated_MadeOnValueDateAndEnteredOnBookingDate(self):
        (row,) = statement_rows(date(2026, 4, 21))

        assert row.value_date == date(2026, 4, 19)
        assert row.booking_date == date(2026, 4, 21)

    def test_StatementRow_WhenOneDateStated_BothDatesCarryIt(self):
        (row,) = statement_rows(None)

        assert row.value_date == row.booking_date == date(2026, 4, 19)

    def test_StatementRow_WhenEnteredDateStated_IdentityKeyRestsOnTheTransactionDay(self):
        (entered,) = statement_rows(date(2026, 4, 21))
        (alone,) = statement_rows(None)

        assert entered.content_key == alone.content_key


class TestAggregatorRow:
    def test_AggregatorRow_StatesOnlyAPostingDate_SoBothDatesCarryItAndTheRowSaysSo(self):
        record = {
            "timestamp": "2026-04-20T00:00:00Z",
            "amount": "-5.00",
            "currency": "GBP",
            "transaction_type": "DEBIT",
            "description": "A PAYEE",
            "normalised_provider_transaction_id": "n-1",
            "meta": {},
        }

        row = truelayer.to_transaction(record, account_id="a")

        assert row.value_date == row.booking_date == date(2026, 4, 20)
        assert not row.states_transaction_date
