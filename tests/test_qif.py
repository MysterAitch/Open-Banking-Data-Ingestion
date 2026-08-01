import pytest

from obdi.parsers.base import ParseError
from obdi.parsers.qif import QifParser, parse_qif_date
from obdi.parsers.uk_banks import detect

BANK_QIF = (
    b"!Type:Bank\n"
    b"D14/03/2026\n"
    b"T-14.99\n"
    b"PTESCO STORES\n"
    b"MCard payment\n"
    b"^\n"
    b"D15/03/2026\n"
    b"T2500.00\n"
    b"PEmployer Ltd\n"
    b"^\n"
)


class TestDateHandling:
    def test_Date_WhenDayFirst_ReadAsDayFirst(self):
        assert parse_qif_date("14/03/2026").month == 3

    def test_Date_WhenAmbiguousDayAndMonth_StillReadAsDayFirst(self):
        # 03/04 is 3 April in a UK export. A library left to guess would make
        # it 4 March for the first twelve days of every month, and the result
        # looks entirely plausible.
        parsed = parse_qif_date("03/04/2026")
        assert (parsed.day, parsed.month) == (3, 4)

    def test_Date_WhenTwoDigitYear_TreatedAsThisCentury(self):
        assert parse_qif_date("14/03/26").year == 2026

    def test_Date_WhenQuickenApostropheYear_Parsed(self):
        # Quicken's own convention for post-2000 dates.
        assert parse_qif_date("14/03'26").year == 2026

    def test_Date_WhenMonthAboveTwelve_RefusedRatherThanSwapped(self):
        # A month-first export. Silently swapping would corrupt every date in
        # the file, so it is refused.
        with pytest.raises(ParseError, match="month-first"):
            parse_qif_date("03/14/2026")

    def test_Date_WhenNotADate_Rejected(self):
        with pytest.raises(ParseError):
            parse_qif_date("not-a-date")

    def test_Date_WhenImpossible_Rejected(self):
        with pytest.raises(ParseError):
            parse_qif_date("31/02/2026")


class TestRecordParsing:
    def test_Statement_WhenQifImported_AmountsSignedCorrectly(self):
        rows = list(QifParser().parse(BANK_QIF, account_id="halifax"))
        assert [t.amount_minor for t in rows] == [-1499, 250000]

    def test_Statement_WhenQifImported_PayeeUsedAsDescription(self):
        rows = list(QifParser().parse(BANK_QIF, account_id="halifax"))
        assert rows[0].description == "TESCO STORES"

    def test_Statement_WhenQifImported_NoSourceIdAvailable(self):
        # QIF carries no transaction id at all, so overlapping exports depend
        # entirely on content matching.
        rows = list(QifParser().parse(BANK_QIF, account_id="halifax"))
        assert all(t.source_id is None for t in rows)
        assert all(t.content_key for t in rows)

    def test_Statement_WhenFinalRecordUnterminated_StillParsed(self):
        # A trailing record with no closing caret is malformed but common.
        unterminated = b"!Type:Bank\nD14/03/2026\nT-1.00\nPSHOP\n"
        assert len(list(QifParser().parse(unterminated, account_id="a"))) == 1

    def test_Statement_WhenAmountHasThousandsSeparator_Parsed(self):
        payload = b"!Type:Bank\nD14/03/2026\nT-1,234.56\nPRENT\n^\n"
        rows = list(QifParser().parse(payload, account_id="a"))
        assert rows[0].amount_minor == -123456

    def test_Statement_WhenRecordMissingAmount_Refused(self):
        payload = b"!Type:Bank\nD14/03/2026\nPSHOP\n^\n"
        with pytest.raises(ParseError, match="date or amount"):
            list(QifParser().parse(payload, account_id="a"))


class TestUnsupportedVariants:
    def test_Statement_WhenInvestmentQif_RefusedRatherThanMisread(self):
        # Investment QIF is a different grammar; reading it as a bank export
        # would produce plausible nonsense.
        payload = b"!Type:Invst\nD14/03/2026\nNBuy\nT100.00\n^\n"
        with pytest.raises(ParseError, match="investment"):
            list(QifParser().parse(payload, account_id="a"))


class TestDetection:
    def test_Statement_WhenQifOffered_DetectedByTypeHeader(self):
        assert isinstance(detect(BANK_QIF), QifParser)

    def test_Statement_WhenCreditCardQif_AlsoDetected(self):
        assert isinstance(detect(b"!Type:CCard\nD14/03/2026\nT-5.00\nPSHOP\n^\n"), QifParser)
