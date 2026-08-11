"""What every statement parser must do, checked against the registry.

The CSV parsers have had a completeness guard for a while: each is
exercised against a spend and a credit, and a check ties the case list to
the parser registry so one added later cannot quietly skip the rule. The
statement parsers had no such guard - they were written after it, and
inherited none of it - which left the same hole the guard was written to
close. A third bank's parser could invert every payment on a statement, or
skip the arithmetic gate, and nothing would refuse it.

So this is the contract, registry-tied like the other:

  a spend is negative and a credit is positive, whatever the document says
  a statement whose rows do not walk its own balances is REFUSED
  the parser recognises its own issuer from the document's words

Adding a statement parser without a case here fails, which is the point.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from obdi.parsers.base import ParseError
from obdi.parsers.pdf_statements import (
    PDF_PARSERS,
    CreditUnionStatementPdfParser,
    SantanderCreditCardPdfParser,
    VirginMoneyCreditCardPdfParser,
)
from test_credit_union_statement import build_columned_pdf
from test_statement_shape import build_pdf

SANTANDER = [
    "Santander UK plc. Registered Office: 2 Triton Square",
    "Statement Date: 11th July 2026      Page No: 4 / 4",
    "Balance brought forward from previous statement          1,000.00",
    "29th Jun    EXAMPLE SHOP LTD LONDON GB                      40.00",
    "3rd Jul     Direct Payment                     CR           15.00",
    "Your new balance:                                        1,025.00",
]

VIRGIN = [
    "Your credit card account is a Virgin Money account (Your credit limit: £4,000)",
    "Statement  period: 05/07/2026        - 04/08/2026",
    "Balance  from your  previous statement                      £1,000.00",
    "06 Jul26             07Jul  26            EXAMPLE STORE LTD   LONDON   £40.00",
    "07 Jul26             08Jul  26            EXAMPLE STORE LTD   LONDON  -£15.00",
    "Your new  balance                                           £1,025.00",
]

CREDIT_UNION = [
    "Example Credit Union Limited",
    "Account Name||||||Opening Balance|1,000.00",
    "Regular Saver",
    "Period 01/07/2026 to 31/07/2026",
    "Date|Source|Payee|Debit|Credit|Interest|Transaction|Balance",
    "|||Amount|Amount|Amount|Total",
    "06/07/2026|Internet Transfer|EXAMPLE SHOP LTD|£40.00|||40.00|960.00",
    "07/07/2026|DD Lodgement|Direct Payment||£15.00||15.00|975.00",
    "Closing Balance|975.00",
    "Page 1 of 1",
]


@dataclass(frozen=True)
class Case:
    """One statement parser's proof that it keeps the contract.

    `spend` and `credit` name descriptions the reading must sign one way
    and the other; `drop` names the row to delete when proving the gate
    notices a missing one. Both are named rather than guessed - the
    formats write a refund differently, which is the whole reason each
    needs its own parser.

    `build` is part of the format too. Most of these statements are legible
    as lines, and one is a table so wide that reading it from spacing
    fuses its columns - so its fixture is laid out at real coordinates
    rather than padded with spaces. A shared builder here would have
    quietly excluded the formats that need geometry from the contract.
    """

    lines: list[str]
    spend: str
    credit: str
    drop: str
    build: Callable[[list[str]], bytes] = field(default=build_pdf)


#: One case per statement parser, tied to the registry below.
CASES = {
    SantanderCreditCardPdfParser: Case(
        SANTANDER, "EXAMPLE SHOP", "Direct Payment", "Direct Payment",
    ),
    VirginMoneyCreditCardPdfParser: Case(
        VIRGIN, "EXAMPLE STORE", "EXAMPLE STORE", "-£15.00",
    ),
    CreditUnionStatementPdfParser: Case(
        CREDIT_UNION,
        "EXAMPLE SHOP",
        "Direct Payment",
        "Direct Payment",
        build=build_columned_pdf,
    ),
}


def _rows(parser_class, case, lines=None):
    return list(
        parser_class().parse(
            case.build(case.lines if lines is None else lines),
            account_id="an-account",
        )
    )


class TestEveryStatementParserKeepsTheContract:
    def test_ASpend_IsNegative_AndACreditIsPositive(self):
        for parser_class, case in CASES.items():
            spend, credit = case.spend, case.credit
            rows = _rows(parser_class, case)
            spends = [
                row.amount_minor
                for row in rows
                if spend in row.description and row.amount_minor < 0
            ]
            credits = [
                row.amount_minor
                for row in rows
                if credit in row.description and row.amount_minor > 0
            ]
            assert spends == [-4000], f"{parser_class.__name__}: spend"
            assert credits == [1500], f"{parser_class.__name__}: credit"

    def test_AStatementThatDoesNotBalance_IsRefused(self):
        for parser_class, case in CASES.items():
            # Delete a row and the declared balances no longer describe
            # what is left, which is the whole point of declaring them.
            broken = [line for line in case.lines if case.drop not in line]

            with pytest.raises(ParseError) as refused:
                _rows(parser_class, case, broken)

            assert "unexplained" in str(refused.value), parser_class.__name__

    def test_EachParser_RecognisesItsOwnIssuer(self):
        for parser_class, case in CASES.items():
            assert parser_class().sniff(case.build(case.lines)), parser_class.__name__

    def test_NoParser_ClaimsAnotherBanksStatement(self):
        for parser_class, case in CASES.items():
            others = [other for other in CASES if other is not parser_class]
            for other in others:
                assert not other().sniff(case.build(case.lines)), (
                    f"{other.__name__} claimed {parser_class.__name__}'s statement"
                )


class TestNoStatementParserCanSkipTheContract:
    def test_EveryRegisteredStatementParser_IsCoveredHere(self):
        # The backstop. A parser added without stating which way its
        # document signs money, or without proving its gate refuses a
        # statement that does not balance, fails here rather than
        # inverting an account's history in silence.
        assert {parser.__name__ for parser in PDF_PARSERS} == {
            parser.__name__ for parser in CASES
        }, "a statement parser exists with no contract case - add one"
