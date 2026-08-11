"""When several parsers recognise one statement.

A parser answers "can I read this layout?", which is a fact about a FORMAT
and not about a bank. The two are not the same and do not stay aligned:
banks merge and rebrand while their layout is unchanged, one bank runs a
different layout per product, and a layout drifts under a name that does
not. So more than one parser recognising a document is a thing that will
happen, and picking the first would be choosing by the order classes were
added to a list.

Running them all and comparing is the better question. Two parsers that
produce the same rows and the same balances have produced the same ledger,
and which one ran is then provenance rather than correctness. Only a
DISAGREEMENT is a fault - and it is a much stronger signal than "two of
them recognised it", because it says where they differ.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.parsers.base import ParseError
from obdi.parsers.pdf_statements import PdfStatementParser, pdf_parser_for
from obdi.parsers.statement_reading import StatementReading, StatementRow

PDF = b"%PDF-1.4 pretend"


def reading(amount: int, *, closing: int = 100) -> StatementReading:
    return StatementReading(
        statement_date=date(2026, 5, 31),
        opening_balance_minor=0,
        closing_balance_minor=closing,
        transactions=[
            StatementRow(
                value_date=date(2026, 5, 4), description="A PAYEE", amount_minor=amount
            )
        ],
    )


def parser_that(name: str, claims: bool, produces):
    class Fake(PdfStatementParser):
        source = name

        def sniff(self, payload: bytes) -> bool:
            return claims

        def read(self, payload: bytes) -> StatementReading:
            if isinstance(produces, Exception):
                raise produces
            return produces

    return Fake


@pytest.fixture
def registry(monkeypatch):
    def install(*parsers) -> None:
        monkeypatch.setattr(
            "obdi.parsers.pdf_statements.PDF_PARSERS", tuple(parsers)
        )

    return install


class TestSeveralParsersRecognisingOneStatement:
    def test_WhenTheyAgree_TheReadingProceeds(self, registry):
        # Same rows, same balances: the ledger is the same either way, so
        # stalling on the ambiguity would refuse a document nothing is
        # actually wrong with.
        registry(
            parser_that("alpha-pdf", True, reading(-500)),
            parser_that("beta-pdf", True, reading(-500)),
        )

        chosen = pdf_parser_for(PDF)

        assert chosen is not None
        assert chosen.source in {"alpha-pdf", "beta-pdf"}

    def test_WhenTheyDisagreeOnAnAmount_ItIsRefused(self, registry):
        registry(
            parser_that("alpha-pdf", True, reading(-500)),
            parser_that("beta-pdf", True, reading(500)),
        )

        with pytest.raises(ParseError) as refusal:
            pdf_parser_for(PDF)

        # Both are named, because "one of these is wrong" is only
        # actionable if you know which two to compare.
        assert "alpha-pdf" in str(refusal.value)
        assert "beta-pdf" in str(refusal.value)

    def test_WhenTheyDisagreeOnAClosingBalance_ItIsRefused(self, registry):
        # A balance is not a row, and a reading can match row for row while
        # disagreeing about where the statement ends.
        registry(
            parser_that("alpha-pdf", True, reading(-500, closing=100)),
            parser_that("beta-pdf", True, reading(-500, closing=900)),
        )

        with pytest.raises(ParseError):
            pdf_parser_for(PDF)

    def test_AClaimantThatCannotRead_DoesNotVeto_TheOneThatCan(self, registry):
        # Recognising a document and being able to read it are different
        # claims. A parser that cannot read it has disqualified itself
        # rather than disagreed.
        registry(
            parser_that("alpha-pdf", True, ValueError("header not found")),
            parser_that("beta-pdf", True, reading(-500)),
        )

        chosen = pdf_parser_for(PDF)

        assert chosen is not None
        assert chosen.source == "beta-pdf"

    def test_WhenNoneOfThemCanRead_ItSaysWhatEachSaid(self, registry):
        registry(
            parser_that("alpha-pdf", True, ValueError("header not found")),
            parser_that("beta-pdf", True, ValueError("no closing balance")),
        )

        with pytest.raises(ParseError) as refusal:
            pdf_parser_for(PDF)

        assert "header not found" in str(refusal.value)
        assert "no closing balance" in str(refusal.value)

    def test_OneClaimant_IsNotReadTwice(self, registry):
        # The comparison costs a second full parse, and a document with one
        # claimant has nothing to compare against. Seconds per page on a
        # real statement makes this worth not doing.
        reads = []

        class Counting(PdfStatementParser):
            source = "alpha-pdf"

            def sniff(self, payload: bytes) -> bool:
                return True

            def read(self, payload: bytes) -> StatementReading:
                reads.append(1)
                return reading(-500)

        registry(Counting)

        assert pdf_parser_for(PDF) is not None
        assert reads == []

    def test_NoClaimant_IsNotAFault_JustNoParser(self, registry):
        registry(parser_that("alpha-pdf", False, reading(-500)))

        assert pdf_parser_for(PDF) is None
