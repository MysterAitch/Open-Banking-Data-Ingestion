"""Currency is either honoured or refused, never quietly assumed.

money.py has a deliberate guard rejecting non-GBP, because a pence-quoted or
foreign amount treated as sterling is a silent hundred-fold or exchange-rate
error. Three call sites routed around it: the aggregator parsed every amount as
GBP while storing the record's own currency beside it, the Starling CSV parser
read the currency out of the column heading and threw it away, and replay
emitted an amount with no reference to currency at all.

Refusing is the right answer rather than converting. The budgeting tool this
feeds is single-currency, so there is nowhere correct for a foreign amount to
go, and a loud failure is recoverable where a silent one is not.
"""

from datetime import date

import pytest

from obdi.models import Transaction
from obdi.money import AmountParseError, parse_amount
from obdi.parsers.base import ParseError
from obdi.parsers.uk_banks import StarlingCsvParser
from obdi.providers.starling import StarlingError
from obdi.providers.starling import to_transaction as starling_txn
from obdi.providers.truelayer import TrueLayerError
from obdi.providers.truelayer import to_transaction as truelayer_txn
from obdi.replay import ReplayError, to_actual_transaction


class TestMoneyGuard:
    def test_Amount_WhenSterling_Accepted(self):
        assert parse_amount("10.00", currency="GBP") == 1000

    def test_Amount_WhenForeign_Refused(self):
        with pytest.raises(AmountParseError):
            parse_amount("10.00", currency="EUR")

    def test_Amount_WhenQuotedInPence_Refused(self):
        # GBX is the specific hazard: UK fund prices are quoted in pence and
        # treating one as pounds is a hundred-fold error.
        with pytest.raises(AmountParseError):
            parse_amount("174.80", currency="GBX")


class TestAggregatorHonoursCurrency:
    def base(self, **overrides) -> dict:
        record = {
            "transaction_id": "volatile-1",
            "normalised_provider_transaction_id": "tl-1",
            "timestamp": "2026-03-14T00:00:00Z",
            "description": "TESCO",
            "amount": -14.99,
            "currency": "GBP",
            "transaction_type": "DEBIT",
        }
        record.update(overrides)
        return record

    def test_Transaction_WhenSterling_Parsed(self):
        assert truelayer_txn(self.base(), account_id="a").amount_minor == -1499

    def test_Transaction_WhenForeignCurrency_RefusedNotParsedAsSterling(self):
        # Previously stored as if sterling, with the true currency recorded
        # alongside - so the amount and its label disagreed silently.
        with pytest.raises((TrueLayerError, AmountParseError)):
            truelayer_txn(self.base(currency="EUR"), account_id="a")


class TestFirstPartyHonoursCurrency:
    def item(self, currency: str = "GBP") -> dict:
        return {
            "feedItemUid": "feed-1",
            "amount": {"currency": currency, "minorUnits": 1499},
            "direction": "OUT",
            "transactionTime": "2026-03-14T09:15:00.000Z",
            "source": "MASTER_CARD",
            "status": "SETTLED",
            "counterPartyName": "Tesco",
            "reference": "TESCO",
        }

    def test_Transaction_WhenSterling_Parsed(self):
        assert starling_txn(self.item(), account_id="a").amount_minor == -1499

    def test_Transaction_WhenForeignCurrency_Refused(self):
        with pytest.raises(StarlingError):
            starling_txn(self.item("EUR"), account_id="a")


class TestCsvHonoursCurrency:
    def test_Statement_WhenSterlingColumn_Parsed(self):
        payload = (
            b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
            b"14/03/2026,Tesco,TESCO,CARD,-14.99,1200.00\n"
        )
        rows = list(StarlingCsvParser().parse(payload, account_id="a"))
        assert rows[0].amount_minor == -1499
        assert rows[0].currency == "GBP"

    def test_Statement_WhenForeignCurrencyColumn_Refused(self):
        # The currency was read to FIND the column and then discarded, so a
        # euro export parsed silently as sterling.
        payload = (
            b"Date,Counter Party,Reference,Type,Amount (EUR),Balance (EUR)\n"
            b"14/03/2026,Cafe,CAFE,CARD,-14.99,1200.00\n"
        )
        with pytest.raises((ParseError, AmountParseError)):
            list(StarlingCsvParser().parse(payload, account_id="a"))


class TestReplayRefusesForeignAmounts:
    def txn(self, currency: str) -> Transaction:
        return Transaction(
            account_id="a",
            amount_minor=-1499,
            currency=currency,
            value_date=date(2026, 3, 14),
            booking_date=date(2026, 3, 14),
            description="TESCO",
            source="test",
            entity_id="ent-1",
        )

    def test_Transaction_WhenSterling_Replayed(self):
        assert to_actual_transaction(self.txn("GBP"))["amount"] == -1499

    def test_Transaction_WhenForeign_RefusedRatherThanSentAsSterling(self):
        # The target budget file is single-currency, so there is nowhere
        # correct for a foreign amount to land.
        with pytest.raises(ReplayError):
            to_actual_transaction(self.txn("EUR"))
