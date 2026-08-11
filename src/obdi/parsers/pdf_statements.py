"""Statement PDFs at the import door.

The readers know how to turn one bank's document into a reading; this puts
them behind the same door every other format uses, so a statement lands as
an artefact, resolves against the same identity rules as an API pull, and
appears in the same ledgers.

The gate is what makes that safe. A statement declares its own opening and
closing balances, so a reading whose rows do not carry one to the other is
REFUSED - the artefact is kept (it was landed before parsing, and a parser
written later can replay it) but nothing derived from a misread document
enters the store. A missed row and a credit read as a spend both fail here,
which is exactly the class of error a plausible-looking parse would
otherwise slip through.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from ..identity import content_key
from ..models import SourceTier, Transaction
from .base import ParseError, StatementParser
from .credit_union_pdf import read_statement as read_credit_union
from .santander_pdf import read_statement as read_santander
from .statement_reading import StatementReading
from .virgin_money_pdf import read_statement as read_virgin

PDF_MAGIC = b"%PDF-"


def _lines(payload: bytes) -> list[str]:
    """The document's text, laid out - the same reading the shape page shows.

    Written to a temporary file because the reader takes a path, and removed
    immediately: the durable copy is the artefact the store already holds.
    """
    import tempfile
    from pathlib import Path

    from ..statement_shape import pdf_lines

    with tempfile.TemporaryDirectory() as scratch:
        temporary = Path(scratch) / "statement.pdf"
        temporary.write_bytes(payload)
        return [str(line) for line in pdf_lines(temporary)]


def _grid(payload: bytes) -> list[list[str]]:
    """The document's TABLE, read by coordinate rather than by spacing.

    The second of the two readings of a page. Costlier than the text - a
    word has no position until the page's fonts have been parsed - and the
    only one that survives a table whose columns sit at the far edges of a
    wide page, where reconstruction fuses one field into the next.
    """
    import tempfile
    from pathlib import Path

    from ..statement_columns import aligned, rows

    with tempfile.TemporaryDirectory() as scratch:
        temporary = Path(scratch) / "statement.pdf"
        temporary.write_bytes(payload)
        return aligned(rows(temporary))


class PdfStatementParser(StatementParser):
    """One bank's statement PDF, gated on the statement's own arithmetic."""

    #: Text that identifies the issuer. Matched against the document's own
    #: words rather than the filename, which a person can rename.
    marker: str
    #: Further words the document must ALSO carry, every one of them.
    #:
    #: Strict on purpose, and deliberately not clever. A brand name alone
    #: is the wrong question twice over: a bank that renames itself stops
    #: matching a parser that could still read it, which fails loudly and
    #: is recoverable - but a bank that keeps its name and changes its
    #: LAYOUT still matches, and the parser then reads a document it no
    #: longer understands. That one is quiet, which is the one to design
    #: against. Naming the column headings as well means a rearranged
    #: table stops being claimed rather than being misread.
    #:
    #: The right response to a refusal is a decision - relax this parser,
    #: or write a second one, equally strict - and not an accommodation
    #: made in advance for a format nobody has seen. Tighter rules than
    #: these (the x positions a format's columns actually occupy, the order
    #: its pages come in) become available once enough statements of one
    #: format have been read to MEASURE them; encoding them from a single
    #: document would be the same guessing this exists to avoid.
    requires: tuple[str, ...] = ()
    reader: Callable[[list[str]], StatementReading]
    date_format = "%d/%m/%Y"
    expected_headers = ()

    def sniff(self, payload: bytes) -> bool:
        if not payload.startswith(PDF_MAGIC):
            return False
        lines = _lines(payload)
        wanted = (self.marker, *self.requires)
        return all(
            any(word.casefold() in line.casefold() for line in lines)
            for word in wanted
        )

    def read(self, payload: bytes) -> StatementReading:
        """The document, read - the one door every caller uses.

        A format decides HOW its page is read: most are legible as lines,
        and a wide table is not legible that way at all. Naming the door
        rather than the reading keeps that a fact about the format instead
        of something each caller has to know.
        """
        return self.reader(_lines(payload))

    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        reading = self.read(payload)
        if reading.notes:
            raise ParseError("; ".join(reading.notes))
        if reading.opening_balance_minor is None or reading.closing_balance_minor is None:
            raise ParseError(
                f"{self.source}: the statement's own opening and closing "
                "balances could not both be found, so nothing can check the "
                "rows against them - refusing rather than importing on trust"
            )
        if not reading.reconciles:
            raise ParseError(
                f"{self.source}: the rows do not carry the statement's "
                f"opening balance to its closing one - "
                f"{reading.discrepancy_minor} minor units unexplained across "
                f"{len(reading.transactions)} row(s). A missed row or a "
                "credit read as a spend both look like this; the file is "
                "kept, but nothing derived from it is stored"
            )
        for row in reading.transactions:
            yield Transaction(
                account_id=account_id,
                amount_minor=row.amount_minor,
                value_date=row.value_date,
                booking_date=row.value_date,
                description=row.description,
                source=self.source,
                source_id=None,
                # A statement carries no transaction id, so identity rests
                # entirely on content - the same footing as a CSV export.
                tier=SourceTier.SYNTHETIC,
                content_key=content_key(
                    amount_minor=row.amount_minor,
                    value_date=row.value_date,
                    description=row.description,
                ),
                raw={
                    "statement_date": str(reading.statement_date or ""),
                    "description": row.description,
                    "amount": row.amount_minor / 100,
                },
            )


class ColumnPdfStatementParser(PdfStatementParser):
    """A statement whose columns are too far apart to read from spacing.

    Separate from the base rather than a flag on it, because the two read
    different things: one is handed the page's text, the other the page's
    table. A format that needs the table and is given the text does not
    fail loudly - it reads a plausible fraction of the rows - so which
    reading a parser gets is settled by its type.
    """

    grid_reader: Callable[[list[list[str]]], StatementReading]

    def read(self, payload: bytes) -> StatementReading:
        return self.grid_reader(_grid(payload))


class SantanderCreditCardPdfParser(PdfStatementParser):
    source = "santander-cc-pdf"
    marker = "Santander"
    reader = staticmethod(read_santander)


class VirginMoneyCreditCardPdfParser(PdfStatementParser):
    source = "virgin-money-cc-pdf"
    marker = "Virgin Money"
    reader = staticmethod(read_virgin)


class CreditUnionStatementPdfParser(ColumnPdfStatementParser):
    """A credit union share and loan statement.

    Named for the KIND of institution rather than for one of them: the
    layout is a common credit union statement, and the document identifies
    itself by carrying the words in its own heading. A second credit union
    whose statement differs gets its own parser and its own source, the
    same way two banks do.
    """

    source = "credit-union-pdf"
    marker = "Credit Union"
    grid_reader = staticmethod(read_credit_union)


def _comparable(reading: StatementReading) -> tuple[object, ...]:
    """The part of a reading two parsers must agree on to be interchangeable.

    The LEDGER, not the trimmings. Dates, descriptions, amounts and the
    two balances are what everything downstream is built from; a rate one
    parser happens to pick up and another does not is a difference in
    thoroughness rather than a disagreement about what happened.
    """
    return (
        reading.opening_balance_minor,
        reading.closing_balance_minor,
        tuple(
            (row.value_date, row.description, row.amount_minor)
            for row in reading.transactions
        ),
    )


def pdf_parser_for(payload: bytes) -> PdfStatementParser | None:
    """The parser to read this document with, or none that recognise it.

    Where SEVERAL claim it, they are all run and their readings compared.
    Two parsers agreeing on every row and both balances have produced the
    same ledger, and which of them ran is then a fact about provenance
    rather than a question about correctness - so the reading proceeds
    instead of stalling on an ambiguity that made no difference.

    They are only refused when they DISAGREE, which is a far stronger
    signal than "two of them recognised it": it names the document where
    one parser is reading a format it does not own, and the difference
    says where. Choosing the first claimant instead would be choosing by
    registration order - an accident of where a class sits in a list - and
    the wrong reading would be plausible and complete.

    A claimant that recognises the document but cannot read it is not a
    disagreement; it has disqualified itself, and the ones that could read
    it decide. One door for every caller, because a second place that
    chooses a parser is a second place that can choose differently.
    """
    claimed = [parser() for parser in PDF_PARSERS if parser().sniff(payload)]
    if len(claimed) <= 1:
        return claimed[0] if claimed else None

    readings: list[tuple[PdfStatementParser, StatementReading]] = []
    refused: list[str] = []
    for parser in claimed:
        try:
            readings.append((parser, parser.read(payload)))
        except Exception as exc:
            refused.append(f"{parser.source} ({exc})")
    if not readings:
        raise ParseError(
            "Several parsers recognised this statement and none could read "
            "it: " + "; ".join(refused)
        )

    agreed = {_comparable(reading) for _parser, reading in readings}
    if len(agreed) > 1:
        names = ", ".join(sorted(parser.source for parser, _ in readings))
        raise ParseError(
            f"{len(readings)} parsers read this statement differently "
            f"({names}). They disagree about the rows or the balances, so "
            "at least one is reading a format it does not own. Nothing is "
            "imported until that is settled; the statement stays kept."
        )
    return readings[0][0]


PDF_PARSERS: tuple[type[PdfStatementParser], ...] = (
    SantanderCreditCardPdfParser,
    VirginMoneyCreditCardPdfParser,
    CreditUnionStatementPdfParser,
)
