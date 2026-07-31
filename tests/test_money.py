import pytest

from obdi.money import AmountParseError, format_amount, parse_amount


class TestAmountParsing:
    def test_Amount_WhenPlainDecimal_StoredAsIntegerPence(self):
        assert parse_amount("14.99") == 1499

    def test_Amount_WhenNegative_SignPreserved(self):
        assert parse_amount("-14.99") == -1499

    def test_Amount_WhenThousandsSeparatorPresent_ParsedCorrectly(self):
        assert parse_amount("1,234.56") == 123456

    def test_Amount_WhenCurrencySymbolPresent_ParsedCorrectly(self):
        assert parse_amount("£20.00") == 2000

    def test_Amount_WhenParenthesisedNegative_TreatedAsNegative(self):
        # Some exports render debits accountancy-style rather than with a sign.
        assert parse_amount("(25.00)") == -2500

    def test_Amount_WhenWholeNumberWithoutDecimals_ParsedCorrectly(self):
        assert parse_amount("20") == 2000

    def test_Amount_WhenSubPennyPrecision_RejectedRatherThanRounded(self):
        # Silently rounding here is how float noise enters a store and breaks
        # matching later. Refuse instead.
        with pytest.raises(AmountParseError):
            parse_amount("14.9912")

    def test_Amount_WhenNotANumber_Rejected(self):
        with pytest.raises(AmountParseError):
            parse_amount("not money")

    def test_Amount_WhenEmpty_Rejected(self):
        with pytest.raises(AmountParseError):
            parse_amount("   ")

    def test_Amount_WhenNonGbpCurrency_RejectedUntilSupported(self):
        # Guarding beats guessing: GBX-quoted values are a known 100x hazard.
        with pytest.raises(AmountParseError):
            parse_amount("10.00", currency="USD")


class TestAmountFormatting:
    def test_Amount_WhenFormattedForDisplay_ShowsPoundsAndPence(self):
        assert format_amount(-1499) == "-£14.99"

    def test_Amount_WhenUnderOnePound_PadsPence(self):
        assert format_amount(5) == "£0.05"
