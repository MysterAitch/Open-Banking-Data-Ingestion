"""Concrete parsers for UK export formats.

Layouts are as documented by research rather than observed against a real
export. Verify each against a first real download before trusting it; the
header check in `StatementParser.rows` will refuse a mismatch rather than
misread it, which is the intended failure mode.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..identity import content_key
from ..models import SourceTier, Transaction, TransactionStatus
from ..money import parse_amount
from .base import ParseError, StatementParser, parse_date

#: Statement PDFs, tried only once every CSV parser has declined: a PDF
#: cannot masquerade as a CSV, and sniffing one costs a text extraction
#: that should not be paid on every import.
from .pdf_statements import pdf_parser_for
from .qif import QifParser


class StarlingCsvParser(StatementParser):
    """Starling personal account CSV export.

    Two traps this handles:
      - the currency is baked into the column NAME ("Amount (GBP)"), so the
        column is matched by prefix rather than equality;
      - the CSV carries no transaction id, unlike the API, so identity rests
        entirely on the content key.
    """

    source = "starling-csv"
    date_format = "%d/%m/%Y"
    expected_headers = ("Date", "Counter Party", "Reference", "Type")

    def _amount_column(self, row: dict[str, str]) -> tuple[str, str]:
        """The amount column and the currency baked into its name.

        Both are returned: reading the currency merely to locate the column and
        then discarding it lets a foreign export parse silently as sterling.
        """
        for key in row:
            if key.startswith("Amount (") and key.endswith(")"):
                return key, key[len("Amount (") : -1].strip().upper()
        raise ParseError("Starling export has no 'Amount (...)' column")

    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        for row in self.rows(payload):
            column, currency = self._amount_column(row)
            amount = parse_amount(row[column], currency=currency)
            when = parse_date(row["Date"], self.date_format)
            description = row.get("Reference") or row.get("Counter Party", "")
            yield Transaction(
                account_id=account_id,
                amount_minor=amount,
                value_date=when,
                booking_date=when,
                description=description,
                counterparty=row.get("Counter Party", ""),
                source=self.source,
                source_id=None,
                tier=SourceTier.SYNTHETIC,
                currency=currency,
                content_key=content_key(
                    amount_minor=amount,
                    value_date=when,
                    description=description,
                ),
                raw=dict(row),
            )


class MonzoCsvParser(StatementParser):
    """Monzo app CSV export.

    The file carries a UTF-8 BOM; decoding as plain utf-8 leaves a zero-width
    character glued to the first column name, so the header check would fail
    for a reason that looks like nothing at all. `utf-8-sig` strips it.

    Rows with an empty Transaction ID are Monzo's own non-transaction lines and
    are skipped rather than stored.
    """

    source = "monzo-csv"
    date_format = "%d/%m/%Y"
    expected_headers = ("Transaction ID", "Date", "Amount")
    encoding = "utf-8-sig"

    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        for row in self.rows(payload):
            source_id = row.get("Transaction ID", "")
            if not source_id:
                continue
            amount = parse_amount(row["Amount"])
            when = parse_date(row["Date"], self.date_format)
            description = row.get("Description") or row.get("Name", "")
            yield Transaction(
                account_id=account_id,
                amount_minor=amount,
                value_date=when,
                booking_date=when,
                description=description,
                counterparty=row.get("Name", ""),
                source=self.source,
                source_id=source_id,
                tier=SourceTier.AUTHORITATIVE,
                content_key=content_key(
                    amount_minor=amount,
                    value_date=when,
                    description=description,
                ),
                raw=dict(row),
            )


class AmexUkCsvParser(StatementParser):
    """American Express UK full-detail CSV export.

    Two traps, either of which silently inverts or scrambles the data:

      - the sign is INVERTED against every other source here: a positive
        amount is a spend, a negative one is a payment to the card. The
        negation below is the whole reason this parser exists separately.
      - the UK export dates as DD/MM while the US export uses MM/DD. A US
        Amex parser cannot be reused, which is why the format is pinned.

    The Reference field is quote-wrapped ("'AT26...") as an Excel
    anti-scientific-notation hack. The prefix is stripped, but the value is
    kept, because it is one of the few genuinely stable ids in UK exports.
    """

    source = "amex-uk-csv"
    date_format = "%d/%m/%Y"
    expected_headers = ("Date", "Description", "Amount")

    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        for row in self.rows(payload):
            # Negated: Amex reports spend as positive, everything else here
            # treats a spend as negative.
            amount = -parse_amount(row["Amount"])
            when = parse_date(row["Date"], self.date_format)
            description = row.get("Description", "")
            reference = row.get("Reference", "").lstrip("'").strip()
            yield Transaction(
                account_id=account_id,
                amount_minor=amount,
                value_date=when,
                booking_date=when,
                description=description,
                source=self.source,
                source_id=reference or None,
                tier=SourceTier.AUTHORITATIVE,
                status=TransactionStatus.BOOKED,
                content_key=content_key(
                    amount_minor=amount,
                    value_date=when,
                    description=description,
                ),
                raw=dict(row),
            )


PARSERS: tuple[type[StatementParser], ...] = (
    StarlingCsvParser,
    MonzoCsvParser,
    AmexUkCsvParser,
    QifParser,
)

#: A PDF announces itself in its first bytes. Worth recognising even with
#: no parser to hand: "no parser for this bank's statement yet" is a
#: different situation from "this export's layout changed", and only one of
#: them is a fault.
PDF_MAGIC = b"%PDF-"


def detect(payload: bytes) -> StatementParser:
    """Pick a parser by header row, refusing to guess."""
    for parser_class in PARSERS:
        parser = parser_class()
        if parser.sniff(payload):
            return parser
    if payload.startswith(PDF_MAGIC):
        # One door, so this and the terms reader cannot disagree about
        # which parser owns a document - and so an ambiguous claim is
        # refused in both rather than resolved twice, differently.
        claimed = pdf_parser_for(payload)
        if claimed is not None:
            return claimed
        raise ParseError(
            "This is a PDF statement from a bank with no parser yet. "
            "Read its layout first (the statement-shape page, or "
            "'obdi statement-shape') - a parser is written per format from "
            "that shape. The file itself is kept either way, so a parser "
            "written later replays it without a re-download."
        )
    raise ParseError(
        "No parser recognised this file's header row. Either the source is new "
        "or an existing export changed layout - inspect it before widening."
    )
