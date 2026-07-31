import pytest

from obdi.parsers.base import ParseError
from obdi.parsers.uk_banks import AmexUkCsvParser, MonzoCsvParser, StarlingCsvParser, detect

STARLING = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category,Notes\n"
    b"14/03/2026,Tesco,TESCO STORES 4912,CARD,-14.99,1200.00,GROCERIES,\n"
    b"15/03/2026,Employer Ltd,SALARY MARCH,FASTER PAYMENT,2500.00,3700.00,INCOME,\n"
)

MONZO = (
    "Transaction ID,Date,Time,Type,Name,Description,Amount,Currency\n"
    "tx_0001,14/03/2026,09:15:00,Card payment,Tesco,TESCO STORES,-14.99,GBP\n"
    ",14/03/2026,09:16:00,Note,,IGNORED ROW,0.00,GBP\n"
).encode("utf-8-sig")

AMEX = (
    b"Date,Description,Amount,Reference,Extended Details\n"
    b"14/03/2026,TESCO STORES,14.99,'AT260314001,Some detail\n"
    b"20/03/2026,PAYMENT RECEIVED - THANK YOU,-500.00,'AT260320002,\n"
)


class TestStarlingParser:
    def test_Statement_WhenStarlingCsvImported_TransactionsParsedWithSignedAmounts(self):
        rows = list(StarlingCsvParser().parse(STARLING, account_id="starling-personal"))
        assert [t.amount_minor for t in rows] == [-1499, 250000]

    def test_Statement_WhenStarlingCsvImported_DatesReadAsDayFirst(self):
        first = next(iter(StarlingCsvParser().parse(STARLING, account_id="a")))
        assert (first.value_date.day, first.value_date.month) == (14, 3)

    def test_Statement_WhenStarlingCsvImported_NoSourceIdAvailable(self):
        # The CSV carries no id, unlike the API, so identity rests on content.
        first = next(iter(StarlingCsvParser().parse(STARLING, account_id="a")))
        assert first.source_id is None
        assert first.content_key


class TestMonzoParser:
    def test_Statement_WhenMonzoCsvHasByteOrderMark_HeaderStillRecognised(self):
        assert MonzoCsvParser().sniff(MONZO)

    def test_Statement_WhenMonzoCsvImported_TransactionIdRetainedAsSourceId(self):
        rows = list(MonzoCsvParser().parse(MONZO, account_id="monzo-personal"))
        assert rows[0].source_id == "tx_0001"

    def test_Statement_WhenMonzoRowHasNoTransactionId_RowSkipped(self):
        rows = list(MonzoCsvParser().parse(MONZO, account_id="monzo-personal"))
        assert len(rows) == 1


class TestAmexParser:
    def test_Statement_WhenAmexUkCsvImported_SpendStoredAsNegative(self):
        # Amex reports a spend as POSITIVE; storing it unchanged inverts the
        # entire card balance.
        rows = list(AmexUkCsvParser().parse(AMEX, account_id="amex"))
        assert rows[0].amount_minor == -1499

    def test_Statement_WhenAmexUkCsvImported_PaymentStoredAsPositive(self):
        rows = list(AmexUkCsvParser().parse(AMEX, account_id="amex"))
        assert rows[1].amount_minor == 50000

    def test_Statement_WhenAmexReferenceQuoteWrapped_PrefixStrippedButValueKept(self):
        rows = list(AmexUkCsvParser().parse(AMEX, account_id="amex"))
        assert rows[0].source_id == "AT260314001"


class TestFormatDetection:
    def test_Statement_WhenSourceUnknown_ParserSelectedFromHeaderRow(self):
        assert isinstance(detect(STARLING), StarlingCsvParser)
        assert isinstance(detect(MONZO), MonzoCsvParser)
        assert isinstance(detect(AMEX), AmexUkCsvParser)

    def test_Statement_WhenHeaderUnrecognised_RefusedRatherThanGuessed(self):
        with pytest.raises(ParseError):
            detect(b"Some,Other,Bank,Layout\n1,2,3,4\n")

    def test_Statement_WhenExportLayoutChanges_ParserRefusesRatherThanMisreading(self):
        # Banks change export layouts without notice; a silent misread is worse
        # than a hard failure.
        changed = b"Date,Counter Party,Reference,Kind,Amount (GBP)\n14/03/2026,Tesco,X,CARD,-1.00\n"
        with pytest.raises(ParseError):
            list(StarlingCsvParser().parse(changed, account_id="a"))

    def test_Statement_WhenDateNotInPinnedFormat_Rejected(self):
        # 2026-03-14 would parse as a US date under auto-detection.
        iso_dates = (
            b"Date,Counter Party,Reference,Type,Amount (GBP)\n2026-03-14,Tesco,X,CARD,-1.00\n"
        )
        with pytest.raises(ParseError):
            list(StarlingCsvParser().parse(iso_dates, account_id="a"))

    def test_Statement_WhenFileEmpty_Rejected(self):
        with pytest.raises(ParseError):
            detect(b"")
