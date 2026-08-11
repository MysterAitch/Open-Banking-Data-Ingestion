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


class PdfStatementParser(StatementParser):
    """One bank's statement PDF, gated on the statement's own arithmetic."""

    #: Text that identifies the issuer. Matched against the document's own
    #: words rather than the filename, which a person can rename.
    marker: str
    reader: Callable[[list[str]], StatementReading]
    date_format = "%d/%m/%Y"
    expected_headers = ()

    def sniff(self, payload: bytes) -> bool:
        if not payload.startswith(PDF_MAGIC):
            return False
        return any(self.marker.casefold() in line.casefold() for line in _lines(payload))

    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        reading = self.reader(_lines(payload))
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


class SantanderCreditCardPdfParser(PdfStatementParser):
    source = "santander-cc-pdf"
    marker = "Santander"
    reader = staticmethod(read_santander)


class VirginMoneyCreditCardPdfParser(PdfStatementParser):
    source = "virgin-money-cc-pdf"
    marker = "Virgin Money"
    reader = staticmethod(read_virgin)


PDF_PARSERS: tuple[type[PdfStatementParser], ...] = (
    SantanderCreditCardPdfParser,
    VirginMoneyCreditCardPdfParser,
)
