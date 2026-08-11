"""Choosing a parser must not re-read the document once per parser.

Every parser in the registry is asked whether it recognises a document,
and each one asked for the document's text. So three parsers meant three
full extractions of the same bytes before any reading began, and the
reading itself was a fourth; twenty parsers would have meant twenty. That
is per FILE - a directory of two hundred statements pays it two hundred
times.

The same shape as the batch upload that read every page's geometry and
then threw it away, and found the same way: by asking what the work costs
per item rather than what the total looks like.

Counted rather than timed, deliberately. "How many times was the document
read" is the question, and the answer does not change with how busy the
machine is.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.parsers import pdf_statements
from obdi.parsers.pdf_statements import PdfStatementParser, pdf_parser_for
from obdi.parsers.statement_reading import StatementReading, StatementRow

PDF = b"%PDF-1.4 one document, read once"
OTHER = b"%PDF-1.4 a different document"


def a_reading() -> StatementReading:
    return StatementReading(
        statement_date=date(2026, 5, 31),
        opening_balance_minor=0,
        closing_balance_minor=-500,
        transactions=[
            StatementRow(
                value_date=date(2026, 5, 4), description="A PAYEE", amount_minor=-500
            )
        ],
    )


@pytest.fixture
def extractions(monkeypatch):
    """Count how often the document's text is actually pulled out of it."""
    counted: list[bytes] = []
    pdf_statements._lines.cache_clear()

    def counting(temporary):
        counted.append(b"read")
        return ["Example Bank", "Date Description Amount"]

    monkeypatch.setattr("obdi.statement_shape.pdf_lines", counting)
    yield counted
    pdf_statements._lines.cache_clear()


def parser_named(name: str, marker: str):
    class Fake(PdfStatementParser):
        source = name
        marker_word = marker

        def read(self, payload: bytes) -> StatementReading:
            return a_reading()

    Fake.marker = marker
    return Fake


class TestChoosingAParser:
    def test_TheDocumentIsReadOnce_HoweverManyParsersAreAsked(
        self, extractions, monkeypatch
    ):
        # The registry is asked in full; the document is opened once.
        monkeypatch.setattr(
            pdf_statements,
            "PDF_PARSERS",
            (
                parser_named("alpha-pdf", "Nobody"),
                parser_named("beta-pdf", "Nobody Else"),
                parser_named("gamma-pdf", "Example Bank"),
            ),
        )

        chosen = pdf_parser_for(PDF)

        assert chosen is not None
        assert chosen.source == "gamma-pdf"
        assert len(extractions) == 1, (
            f"read {len(extractions)} times for one document"
        )

    def test_AddingParsers_DoesNotAddReadings(self, extractions, monkeypatch):
        # The property that matters as the registry grows: cost per file is
        # flat in the number of parsers, not linear.
        monkeypatch.setattr(
            pdf_statements,
            "PDF_PARSERS",
            (
                *(parser_named(f"p{n}-pdf", "Nobody") for n in range(12)),
                parser_named("gamma-pdf", "Example Bank"),
            ),
        )

        pdf_parser_for(PDF)

        assert len(extractions) == 1

    def test_ASecondDocument_IsStillReadOnItsOwnTerms(
        self, extractions, monkeypatch
    ):
        # Caching must not answer for a document it was never given: two
        # files, two readings, and the wrong one would be catastrophic
        # rather than slow.
        monkeypatch.setattr(
            pdf_statements,
            "PDF_PARSERS",
            (parser_named("gamma-pdf", "Example Bank"),),
        )

        pdf_parser_for(PDF)
        pdf_parser_for(OTHER)

        assert len(extractions) == 2
