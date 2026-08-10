"""One sign convention, everywhere, whatever the document says.

Money leaving an account is NEGATIVE and money arriving is POSITIVE, in
every account of every kind. A balance is then simply the running sum, so
an overdraft goes negative, a credit card goes further negative as it is
spent on, and a payment to that card moves it back towards zero. Nothing
downstream needs to know whether an account is an asset or a liability,
because the sign already says which way the money went.

Documents disagree about this and always will - Starling publishes spend
as negative, Amex publishes the same spend as positive - so the
translation belongs at the document boundary, in the parser, and nowhere
else. That is what this pins: every parser, no exceptions, and a
completeness check so a parser added later cannot quietly skip the rule.
"""

from __future__ import annotations

from obdi.parsers.uk_banks import (
    PARSERS,
    AmexUkCsvParser,
    MonzoCsvParser,
    QifParser,
    StarlingCsvParser,
)
from test_parsers import AMEX, MONZO, STARLING
from test_qif import BANK_QIF

#: One case per parser: a payload holding a spend and (where the format
#: shows one) a credit, and what the parser must produce for each.
CASES = {
    StarlingCsvParser: (STARLING, "publishes spend negative"),
    MonzoCsvParser: (MONZO, "publishes spend negative"),
    AmexUkCsvParser: (AMEX, "publishes spend POSITIVE - must be inverted"),
    QifParser: (BANK_QIF, "publishes spend negative"),
}


class TestEveryParserSpeaksTheSameSign:
    def test_ASpend_IsNegative_WhicheverBankPublishedIt(self):
        for parser_class, (payload, note) in CASES.items():
            rows = list(parser_class().parse(payload, account_id="a"))
            spends = [row for row in rows if "TESCO" in row.description.upper()]
            assert spends, f"{parser_class.__name__}: no spend row found"
            for row in spends:
                assert row.amount_minor < 0, (
                    f"{parser_class.__name__} ({note}) produced "
                    f"{row.amount_minor} for a spend"
                )

    def test_ACredit_IsPositive_WhicheverBankPublishedIt(self):
        wanted = {
            StarlingCsvParser: "SALARY",
            AmexUkCsvParser: "PAYMENT RECEIVED",
            QifParser: "EMPLOYER",
        }
        for parser_class, needle in wanted.items():
            payload, note = CASES[parser_class]
            rows = list(parser_class().parse(payload, account_id="a"))
            credits = [
                row
                for row in rows
                if needle in (row.description + row.counterparty).upper()
            ]
            assert credits, f"{parser_class.__name__}: no credit row found"
            for row in credits:
                assert row.amount_minor > 0, (
                    f"{parser_class.__name__} ({note}) produced "
                    f"{row.amount_minor} for a credit"
                )

    def test_TheAmexInversion_IsRealAndNotAnAccident(self):
        # Amex is the reason this file exists: the same spend that Starling
        # publishes as -14.99 appears as 14.99 on an Amex export, so a
        # parser that passed the number through would invert every row on
        # the statement while looking perfectly reasonable.
        amex = list(AmexUkCsvParser().parse(AMEX, account_id="a"))

        spend = next(row for row in amex if "TESCO" in row.description.upper())
        payment = next(
            row for row in amex if "PAYMENT RECEIVED" in row.description.upper()
        )

        assert (spend.amount_minor, payment.amount_minor) == (-1499, 50000)


class TestNoParserCanSkipTheRule:
    def test_EveryRegisteredParser_IsCoveredHere(self):
        # The backstop: adding a parser without stating which way its
        # document signs money fails this test rather than silently
        # inverting an account's history.
        assert {parser.__name__ for parser in PARSERS} == {
            parser.__name__ for parser in CASES
        }, "a parser exists with no sign-convention case - add one"
