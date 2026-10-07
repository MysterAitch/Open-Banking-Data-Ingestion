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

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache

from ..identity import artefact_digest, content_key
from ..models import SourceTier, Transaction
from ..namespaces import UK_CARD_STATEMENT_SOURCE
from ..plural import plural
from ..statement_columns import Row, aligned
from .base import ParseError, StatementParser
from .capital_one_pdf import read_statement as read_capital_one
from .card_statement_pdf import read_statement as read_card_statement
from .credit_union_pdf import StatementSection
from .credit_union_pdf import read_document as read_credit_union_document
from .credit_union_pdf import read_heading as read_credit_union_heading
from .credit_union_pdf import read_statement as read_credit_union
from .halifax_account_pdf import read_statement as read_halifax_account
from .nationwide_pdf import read_statement as read_nationwide
from .santander_pdf import read_statement as read_santander
from .starling_pdf import read_statement as read_starling
from .statement_reading import StatementReading
from .virgin_money_pdf import read_statement as read_virgin

PDF_MAGIC = b"%PDF-"

#: Which extractor made a stored extraction (`statement_extraction`). Raise it whenever what a
#: document yields can change: a different way of reading a page's text or cells, a parser that
#: now reads sections differently or finds a different set of them, the list of names looked
#: for, or the masking of the shape. The rebuild extracts again every document whose stored
#: extraction was made by another version, and nothing else does; a page never reads a PDF, so
#: a change left unbumped is not seen until somebody drops the table.
EXTRACTOR_VERSION = 1


@dataclass(frozen=True)
class RawExtraction:
    """What reading a PDF's pages gives, before any parser has looked at it.

    The text lines and the table's cells are the only two reads of a document's bytes; every
    parser works from one or the other, so a document that has them needs its bytes no more.
    """

    lines: list[str]
    #: The page's words as rows of cells with their positions, before any column is chosen.
    table: list[Row]
    page_count: int
    #: Why the document could not be read at all, "" where it was.
    failure: str = ""

    def grid_and_pages(self) -> tuple[list[list[str]], list[int]]:
        """The table by column, and the page each of its rows was printed on."""
        return aligned(self.table), [row.page for row in self.table]


class NotExtracted(Exception):
    """A page asked for a document that has no stored extraction at the current version.

    Not a `DataError`: the callers that treat a document no parser reads as an answer must not
    take this one for it, because a document waiting for the rebuild is not a document nothing
    reads.
    """


#: Where a parser finds a document's extraction instead of reading its bytes: given the
#: document's digest, the stored extraction, or None to read the bytes. Installed per request or
#: per rebuild by `statement_extraction.serving`, and only ever consulted by `_supplied`.
ExtractionSupplier = Callable[[str], RawExtraction | None]
_SUPPLIER: ContextVar[ExtractionSupplier | None] = ContextVar(
    "obdi_extraction_supplier", default=None
)


@contextmanager
def supplying(supplier: ExtractionSupplier) -> Iterator[None]:
    """Inside the block, a parser asked to read a document asks `supplier` first."""
    token = _SUPPLIER.set(supplier)
    try:
        yield
    finally:
        _SUPPLIER.reset(token)


def _supplied(payload: bytes) -> RawExtraction | None:
    supplier = _SUPPLIER.get()
    return None if supplier is None else supplier(artefact_digest(payload))


#: How many payloads keep their readings. Two, because the sequence that
#: matters is one document being read several times in a row: every parser
#: in the registry sniffs it, then the one that claims it reads it again.
#: A third payload means a different document, and the first is finished
#: with. Small on purpose - a reading holds its payload alive.
_READINGS_KEPT = 2


@lru_cache(maxsize=_READINGS_KEPT)
def _lines(payload: bytes) -> list[str]:
    """The document's text, laid out - the same reading the shape page shows.

    Written to a temporary file because the reader takes a path, and removed
    immediately: the durable copy is the artefact the store already holds.

    CACHED because choosing a parser asks every parser in the registry
    whether it recognises the document, and each one asked for this text.
    Three parsers meant three full extractions of the same bytes before any
    reading began, and the fourth was the reading itself; twenty parsers
    would have meant twenty. That is per FILE, so a directory of two
    hundred statements paid it two hundred times over - the same shape as
    the batch upload that read every page's geometry and discarded it.
    """
    import tempfile
    from pathlib import Path

    from ..statement_shape import pdf_lines

    with tempfile.TemporaryDirectory() as scratch:
        temporary = Path(scratch) / "statement.pdf"
        temporary.write_bytes(payload)
        return [str(line) for line in pdf_lines(temporary)]


def read_raw(payload: bytes) -> RawExtraction:
    """Read a PDF's pages: its text and its table's cells. The one place both are read.

    This is the extraction a stored row saves, and only the doors that keep or rebuild call it.
    A document pypdf cannot open comes back with a `failure` rather than as an empty reading, so
    a document with no text layer (which opens, and yields no lines) is told from one that is
    not a PDF at all.
    """
    import tempfile
    from pathlib import Path

    from .. import statement_shape
    from ..statement_columns import rows

    with tempfile.TemporaryDirectory() as scratch:
        temporary = Path(scratch) / "statement.pdf"
        temporary.write_bytes(payload)
        try:
            from pypdf import PdfReader

            pages = len(PdfReader(str(temporary)).pages)
        except Exception as exc:
            return RawExtraction([], [], 0, f"could not be opened as a PDF: {type(exc).__name__}")
        lines = [str(line) for line in statement_shape.pdf_lines(temporary)]
        return RawExtraction(lines, rows(temporary), pages)


def _text_of(payload: bytes) -> list[str]:
    """A document's text lines: the stored extraction where one is supplied, else read."""
    supplied = _supplied(payload)
    return _lines(payload) if supplied is None else supplied.lines


def _grid_and_pages_of(payload: bytes) -> tuple[list[list[str]], list[int]]:
    supplied = _supplied(payload)
    return _grid_and_pages(payload) if supplied is None else supplied.grid_and_pages()


def _table_of(payload: bytes) -> list[Row]:
    supplied = _supplied(payload)
    return _table(payload) if supplied is None else supplied.table


def statement_lines(payload: bytes) -> list[str]:
    """A statement's text lines, for a caller outside the parsers.

    The stored extraction where one is supplied (`supplying`), else the cached reading the
    parsers use, so asking costs nothing more.
    """
    return _text_of(payload)


@lru_cache(maxsize=_READINGS_KEPT)
def _grid_and_pages(payload: bytes) -> tuple[list[list[str]], list[int]]:
    """The document's TABLE, read by coordinate rather than by spacing, and
    the page each of its rows was printed on.

    The second of the two readings of a page. Costlier than the text - a
    word has no position until the page's fonts have been parsed - and the
    only one that survives a table whose columns sit at the far edges of a
    wide page, where reconstruction fuses one field into the next.

    Cached for the same reason as the text, and it matters more here: this
    is the expensive reading, and where several parsers claim a document
    they are all run and compared, so it happens once per claimant. The
    pages are kept beside the grid because a reader that divides a document
    into its accounts needs to know where a page begins, and the grid alone
    cannot say.
    """
    import tempfile
    from pathlib import Path

    from ..statement_columns import aligned, rows

    with tempfile.TemporaryDirectory() as scratch:
        temporary = Path(scratch) / "statement.pdf"
        temporary.write_bytes(payload)
        table = rows(temporary)
        return aligned(table), [row.page for row in table]


def _grid(payload: bytes) -> list[list[str]]:
    """The document's table alone; see `_grid_and_pages`."""
    return _grid_and_pages_of(payload)[0]


@lru_cache(maxsize=_READINGS_KEPT)
def _table(payload: bytes) -> list[Row]:
    """The document's cells WITH their right edges, before any column is chosen.

    The third reading of a page, for a table whose figures are right-aligned
    beneath left-aligned headings. `_grid` has already filed each word under
    the column whose LEFT edge is nearest, which puts a wide figure and a
    narrow one beneath the same heading into different columns - and the
    information that would put them back (where the figure ENDS) is gone by
    the time a grid exists. Cached for the same reason as the other two.
    """
    import tempfile
    from pathlib import Path

    from ..statement_columns import rows

    with tempfile.TemporaryDirectory() as scratch:
        temporary = Path(scratch) / "statement.pdf"
        temporary.write_bytes(payload)
        return rows(temporary)


@dataclass(frozen=True)
class SectionReading:
    """One account of a multi-account document, with the gate's verdict on it.

    `refusal` is empty when the section's own rows carry its own opening
    balance to its own closing one and every other rule a statement faces is
    met; otherwise it says why, and the section yields no rows. The verdict is
    per section, so one that fails leaves the others usable.
    """

    key: str
    label: str
    reading: StatementReading
    refusal: str

    @property
    def rows(self) -> int:
        return len(self.reading.transactions)


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
        # Whitespace is disregarded on both sides: the text layer doubles the gap inside a
        # phrase on some documents ("Statement  period:") and drops it on others - the Virgin
        # Money card's statement of October 2026 never printed "Virgin Money" with a space, and
        # was refused as having no reader though its layout was September's to the line. A
        # phrase a parser requires must not depend on how the gap came out.
        lines = ["".join(line.split()).casefold() for line in _text_of(payload)]
        wanted = (self.marker, *self.requires)
        return all(
            any("".join(word.split()).casefold() in line for line in lines) for word in wanted
        )

    def read(self, payload: bytes) -> StatementReading:
        """The document, read - the one door every caller uses.

        A format decides HOW its page is read: most are legible as lines,
        and a wide table is not legible that way at all. Naming the door
        rather than the reading keeps that a fact about the format instead
        of something each caller has to know.
        """
        return self.reader(_text_of(payload))

    def refusal_of(self, reading: StatementReading, *, figures: bool = True) -> str:
        """Why a reading may not be stored, or "" when it may.

        The one copy of the arithmetic gate: a whole statement and each
        section of a multi-account document are judged by it, so a section
        cannot pass a rule a statement would fail.

        `figures=False` is the same verdict for a page served on a GET: the
        difference the rows leave is not said, and every digit in a note (which
        can carry an account's name, and so its rate) is shown as 9.
        """
        if reading.notes:
            said = "; ".join(reading.notes)
            return said if figures else re.sub(r"\d", "9", said)
        if reading.opening_balance_minor is None or reading.closing_balance_minor is None:
            return (
                f"{self.source}: the statement's own opening and closing "
                "balances could not both be found, so nothing can check the "
                "rows against them - refusing rather than importing on trust"
            )
        if not reading.reconciles:
            unexplained = (
                f"{reading.discrepancy_minor} minor units unexplained"
                if figures
                else "the sum did not reach the closing balance"
            )
            return (
                f"{self.source}: the rows do not carry the statement's "
                f"opening balance to its closing one - "
                f"{unexplained} across "
                f"{plural(len(reading.transactions), 'row')}. A missed row or a "
                "credit read as a spend both look like this; the file is "
                "kept, but nothing derived from it is stored"
            )
        return ""

    def heading(self, payload: bytes) -> str:
        """The account label a one-account document prints, or "" where the format prints none
        that names the account (every format but the credit union's)."""
        return ""

    def sections(self, payload: bytes) -> list[SectionReading] | None:
        """The accounts of a document that covers several, or None.

        None is the answer for every format that prints one account per
        document, and for a document of a sectioned format that happens to
        hold one account: both are read whole by `parse`. A document whose
        sections cannot be told apart raises `ParseError`.
        """
        return None

    def parse_section(
        self, payload: bytes, key: str, *, account_id: str
    ) -> Iterator[Transaction]:
        """One section's rows under `account_id`, after the same gate a statement faces."""
        found = self.sections(payload)
        if found is None:
            raise ParseError(
                f"{self.source}: this statement is not divided into accounts, so "
                "there are no sections to read"
            )
        for item in found:
            if item.key != key:
                continue
            if item.refusal:
                raise ParseError(item.refusal)
            yield from self.rows_of(item.reading, account_id)
            return
        raise ParseError(
            f"{self.source}: the statement holds no section with key {key!r}"
        )

    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        reading = self.read(payload)
        reason = self.refusal_of(reading)
        if reason:
            raise ParseError(reason)
        yield from self.rows_of(reading, account_id)

    def rows_of(
        self, reading: StatementReading, account_id: str
    ) -> Iterator[Transaction]:
        """A reading's rows as transactions, id-less, identified by content."""
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
                # Every date the row states, ISO, so each is kept against the payment
                # (`stated_times.recorded_for`) and none is lost to the one that became its date.
                raw={
                    "statement_date": str(reading.statement_date or ""),
                    "transaction_date": row.value_date.isoformat(),
                    **({"posting_date": row.posted.isoformat()} if row.posted else {}),
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
    #: Reads a document that may cover several accounts, one section each,
    #: or returns None for one account; see `read_document`. Given the grid
    #: and the page each row was printed on.
    document_reader: Callable[
        [list[list[str]], list[int]], list[StatementSection] | None
    ] | None = None
    #: The account label a one-account document prints, "" where it prints none.
    heading_reader: Callable[[list[list[str]]], str] | None = None

    def heading(self, payload: bytes) -> str:
        return "" if self.heading_reader is None else self.heading_reader(_grid(payload))

    def read(self, payload: bytes) -> StatementReading:
        return self.grid_reader(_grid(payload))

    def sections(self, payload: bytes) -> list[SectionReading] | None:
        if self.document_reader is None:
            return None
        found = self.document_reader(*_grid_and_pages_of(payload))
        if found is None:
            return None
        return [
            SectionReading(
                key=item.key,
                label=item.label,
                reading=item.reading,
                refusal=self.refusal_of(item.reading),
            )
            for item in found
        ]


class SantanderCreditCardPdfParser(PdfStatementParser):
    source = "santander-cc-pdf"
    marker = "Santander"
    #: The line its reader takes the closing balance from, colon included: the
    #: colon is what separates it from another issuer's "Your new balance"
    #: figure, which a card statement of a different layout also prints. A payee
    #: is free text, so another bank's statement can name Santander (a direct
    #: debit to one of its cards) without being one of its statements.
    requires = ("Your new balance:",)
    reader = staticmethod(read_santander)


class VirginMoneyCreditCardPdfParser(PdfStatementParser):
    source = "virgin-money-cc-pdf"
    marker = "Virgin Money"
    #: The heading its reader takes the statement's dates from. A payee is free
    #: text, so another issuer's statement can name Virgin Money (a payment to
    #: one of its cards) without being one of its statements, and the name alone
    #: then claims a document this reader cannot read.
    requires = ("Statement period:",)
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
    #: Two of its table's column names. A payee is free text, so a current
    #: account's statement can carry the words "Credit Union" (a direct debit
    #: to one) without being a credit union's statement; the heading is what
    #: only the real thing has.
    requires = ("Payee", "Source")
    grid_reader = staticmethod(read_credit_union)
    document_reader = staticmethod(read_credit_union_document)
    heading_reader = staticmethod(read_credit_union_heading)


class StarlingStatementPdfParser(PdfStatementParser):
    """A Starling certified statement: a current account over several months.

    Money in is positive and money out negative, and an account in credit is
    a positive balance - the opposite of the card parsers' owed-is-negative,
    which is a fact about cards and not about this layout.

    Recognised by the Summary's labels AND the table's own heading, never by
    the bank's name: the name is free text in every payee's line, and the
    heading is what says the columns are where the reader looks for them.
    """

    source = "starling-statement-pdf"
    marker = "Payments Out"
    requires = (
        "Payments In",
        "Opening Balance",
        "Closing Balance",
        "Sort code",
        "Account Number",
        "END OF",
        "TRANSACTION",
    )
    table_reader: Callable[[list[Row]], StatementReading] = staticmethod(read_starling)

    def read(self, payload: bytes) -> StatementReading:
        return self.table_reader(_table_of(payload))


class NationwideStatementPdfParser(PdfStatementParser):
    """A Nationwide FlexAccount statement: a current account for a month.

    Money in is positive and money out negative, and an account in credit is a
    positive balance. The table's rows carry a day and a month only, and the
    Start and End balances sit in a panel beside it.

    Recognised by the opening row's own label together with the panel's
    labels, never by the building society's name: the name is free text in
    every payee's line.
    """

    source = "nationwide-statement-pdf"
    marker = "Balance from statement"
    requires = ("Start balance", "End balance", "Sort code", "Statement date")
    table_reader: Callable[[list[Row]], StatementReading] = staticmethod(read_nationwide)

    def read(self, payload: bytes) -> StatementReading:
        return self.table_reader(_table_of(payload))


class HalifaxAccountStatementPdfParser(PdfStatementParser):
    """A Halifax bank account statement: a current account for a month.

    Money in is positive and money out negative, and an account in credit is a
    positive balance. NOT the Halifax credit card (`UkCardStatementPdfParser`):
    both documents name the bank, so the name decides nothing, and the two are
    told apart by structure - the sort code and account number, the Money In
    and Money Out columns, and the "Balance on" lines, none of which a card
    statement has.

    Recognised by the summary's labels AND the transactions heading, never by
    the bank's name: the name is free text in every payee's line, and the
    heading is what says the table is where the reader looks for it.
    """

    source = "halifax-statement-pdf"
    marker = "Money In"
    requires = (
        "Money Out",
        "Balance on",
        "Your Account",
        "Sort Code",
        "Account Number",
        "Your Transactions",
    )
    reader = staticmethod(read_halifax_account)


class UkCardStatementPdfParser(PdfStatementParser):
    """A credit card statement whose issuer the masked shapes did not name.

    Recognised by the table's own heading together with the summary labels and
    the previous-statement row, never by the issuer's name: the name was masked
    when the layout was read, and it is free text in every other statement's
    payees. The source is a placeholder (`UK_CARD_STATEMENT_SOURCE`) to be
    renamed once the issuer is known.
    """

    source = UK_CARD_STATEMENT_SOURCE
    marker = "Date of transaction"
    requires = (
        "Summary of your account",
        "BALANCE FROM",
        "Balance Type",
        "Minimum payment due",
        "Your credit limit",
    )
    reader = staticmethod(read_card_statement)


class CapitalOneCreditCardPdfParser(PdfStatementParser):
    """A Capital One card statement, whose figures carry their sign by column.

    The issuer's name is on every page and so is a hint at most - a payee is
    free text, and a payment to the card names it on another bank's statement.
    The summary heading, the table's own headings, and the closing lines are
    what say the layout is the one the reader was written from.
    """

    source = "capital-one-cc-pdf"
    marker = "Capital One"
    requires = (
        "Your account summary",
        "Your transaction details",
        "Paid in",
        "Paid out",
        "STATEMENT TOTALS",
        "CLOSING BALANCE",
        "Statement date",
    )
    reader = staticmethod(read_capital_one)


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
    StarlingStatementPdfParser,
    NationwideStatementPdfParser,
    HalifaxAccountStatementPdfParser,
    UkCardStatementPdfParser,
    CapitalOneCreditCardPdfParser,
)
