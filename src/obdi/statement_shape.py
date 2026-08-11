"""Read a statement's SHAPE without disclosing its contents.

Bank statements are the only source for several facts nothing else exposes
- the terms (interest rates by kind, fee schedules, promotional periods and
the dates they revert), the opening and closing balances, and for accounts
with no feed at all, the transactions themselves. Writing a parser for one
needs the LAYOUT: column order, header wording, date format, how the
balance lines are phrased.

It does not need anybody's money. So this module masks by default: every
digit becomes a 9, every word that is not recognisable statement furniture
becomes Xs of the same length and casing, and the structure - spacing,
punctuation, currency symbols, word counts - survives intact. Disclosing
real values takes an explicit ask. A leak here would not be a bug, it would
be a disclosure, which is why the masking is tested harder than the PDF
plumbing that feeds it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NewType

from .timings import Timings

#: Text taken verbatim from a statement: payees, addresses, account
#: numbers, amounts. Never leaves the machine that read the file.
RawText = NewType("RawText", str)

#: Text whose values have been masked - layout intact, contents gone.
#: The ONLY kind a sharing surface accepts, so disclosing by accident
#: becomes a type error rather than a test that someone forgot to write.
MaskedText = NewType("MaskedText", str)

#: Words a parser keys on, which must survive masking or the report cannot
#: describe the layout it exists to describe. Everything else is masked -
#: the list is deliberately small and structural, never a merchant, person,
#: place or product.
FURNITURE = frozenset(
    word.casefold()
    for word in (
        "statement", "account", "sort", "code", "number", "page", "of", "to",
        "from", "date", "dates", "description", "details", "reference", "type",
        "paid", "in", "out", "money", "debit", "debits", "credit", "credits",
        "balance", "balances", "opening", "closing", "brought", "carried",
        "forward", "start", "end", "total", "totals", "subtotal", "summary",
        "period", "transactions", "transaction", "interest", "rate", "rates",
        "apr", "annual", "percentage", "fee", "fees", "charge", "charges",
        "charged", "purchases", "purchase", "cash", "advance", "advances",
        "transfer", "transfers", "promotional", "promotion", "introductory",
        "expires", "ends", "until", "limit", "available", "overdraft", "arranged",
        "minimum", "payment", "payments", "due", "repayment", "term",
        "and", "on", "the", "for", "your", "this", "was", "will", "be",
        # Ledger markers and country codes. Both render as two or three
        # capitals, so masking them made a CREDIT indistinguishable from a
        # country - the single fact a parser most needs from a line, since
        # getting it wrong inverts every payment. Neither identifies a
        # person, a merchant or an amount.
        "cr", "dr", "gb", "gbr", "us", "usa", "ie", "irl", "fr", "fra",
        "de", "deu", "es", "esp", "nl", "nld", "it", "ita", "eur", "usd",
        "gbp",
        "jan", "feb", "mar", "apr_month", "may", "jun", "jul", "aug", "sep",
        "oct", "nov", "dec",
        "january", "february", "march", "april", "june", "july", "august",
        "september", "october", "november", "december",
    )
)

#: Month abbreviations collide with statement words ('apr' vs APR), so they
#: are listed separately and checked case-sensitively where it matters.
_MONTHS = frozenset(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
)

_WORD = re.compile(r"[0-9A-Za-z]+")


def shareable(lines: list[MaskedText]) -> str:
    """The door values pass through on their way to anyone else.

    Typed rather than merely documented: pass raw text here and mypy - a
    CI gate - refuses the build. A guarantee that holds for code nobody
    has written yet is worth more than one asserted per call site.
    """
    return "\n".join(lines)


def _mask_word(word: str) -> str:
    """Digits to 9, letters to X preserving case - length and shape kept."""
    return "".join(
        "9" if char.isdigit() else ("X" if char.isupper() else "x") for char in word
    )


def mask_line(line: str) -> MaskedText:
    """Mask a line's values while keeping every structural cue.

    Furniture words pass through so the layout stays legible. A word is
    furniture only if it is purely alphabetic and listed - a token mixing
    letters and digits ('DAP90481679') is a reference and never survives.
    """

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.isalpha() and (
            word.casefold() in FURNITURE or word.casefold() in _MONTHS
        ):
            return word
        return _mask_word(word)

    return MaskedText(_WORD.sub(replace, line))


def _page_text(page: object) -> str:
    """One page's text, laid out rather than merely extracted.

    Layout mode keeps horizontal position, which is what makes a statement
    readable as the table it is. Plain extraction can emit one TOKEN per
    line - observed on a real statement, where 2,714 lines held what layout
    mode renders as a few hundred, and the transaction table became
    unreadable and got truncated away. Plain remains the fallback, because
    some documents defeat layout mode entirely and a worse shape beats no
    shape.
    """
    extract = getattr(page, "extract_text", None)
    if extract is None:
        return ""
    try:
        laid_out = extract(extraction_mode="layout") or ""
    except Exception:
        laid_out = ""
    if laid_out.strip():
        return str(laid_out)
    try:
        return str(extract() or "")
    except Exception:
        return ""


def pdf_lines(path: Path) -> list[RawText]:
    """Every text line in a PDF, in page order.

    Text-embedded PDFs only - a scanned page has no text layer and yields
    nothing, which the report names rather than reporting an empty file.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [_page_text(page) for page in reader.pages]
    except Exception:
        return []
    lines: list[RawText] = []
    for text in pages:
        lines.extend(
            RawText(line.rstrip()) for line in text.splitlines() if line.strip()
        )
    return lines


@dataclass
class ShapeReport:
    """What a statement looks like, safe to paste to someone who should not
    see what it says."""

    path: str
    line_count: int = 0
    page_count: int = 0
    masked: bool = True
    readable: bool = True
    #: Masked when `masked` is set, raw otherwise - which is why the union
    #: is written out: a caller cannot forget which it is holding.
    lines: list[MaskedText] | list[RawText] = field(default_factory=list)
    #: The same page read by coordinate rather than by spacing, as equal-
    #: length rows. Carried ALONGSIDE the lines rather than instead of
    #: them, because the disagreement between the two views is the
    #: evidence: where reconstruction fused two fields, the line shows one
    #: token and the row shows two, and a reader can see which to believe.
    #: Masked on exactly the same terms as `lines`.
    rows: list[list[MaskedText]] | list[list[RawText]] = field(default_factory=list)
    #: The left edges the page uses, so a reader can see the table's shape
    #: without reading its contents.
    edges: list[float] = field(default_factory=list)
    #: Whether the coordinate reading was ASKED FOR. Distinct from whether
    #: it succeeded, and the distinction is load-bearing: reading geometry
    #: costs seconds per page on a real statement, so a caller that only
    #: needs counts declines it - and "not asked" reported as "could not be
    #: read" would accuse a perfectly readable document.
    columns_attempted: bool = True

    def describe(self) -> str:
        if not self.readable:
            return (
                f"{self.path}: could not be read as a PDF - is it a PDF at all?"
            )
        if not self.line_count:
            return (
                f"{self.path}: {self.page_count} page(s) but NO TEXT layer - a "
                "scanned or photographed statement. Text-embedded PDFs only "
                "for now; OCR is a separate problem."
            )
        header = (
            f"{self.path}: {self.line_count} line(s) across {self.page_count} "
            f"page(s)"
            + (
                f", read as {len(self.rows)} row(s) of "
                f"{len(self.edges)} column(s)"
                if self.rows
                else ""
                if not self.columns_attempted
                else " - NO column reading (the page's geometry could not "
                "be read, so only the spacing view is available)"
            )
            + (
                " - values MASKED (digits as 9, other words as X, layout kept)"
                if self.masked
                else " - REAL VALUES, not masked"
            )
        )
        parts = [header, *self.lines]
        if self.rows:
            edges = ", ".join(f"{edge:.0f}" for edge in self.edges)
            parts.append("")
            parts.append(
                f"--- the same pages read by position, columns at x={edges} ---"
            )
            # Pipes rather than spacing, so a cell the page left EMPTY is
            # visible as an empty cell. Reading this view back as spacing
            # would reintroduce the exact ambiguity it exists to remove.
            parts.extend(" | ".join(row) for row in self.rows)
        return "\n".join(parts)


def shape_report(
    path: Path,
    *,
    mask: bool = True,
    limit: int = 1200,
    columns: bool = True,
    timings: Timings | None = None,
) -> ShapeReport:
    """Read a statement's layout, masked unless explicitly asked otherwise.

    Set `columns` false when only the counts are wanted. Reading a page's
    geometry costs seconds per page on a real statement - font programs
    have to be parsed before a word has a position - against milliseconds
    for the text, so a caller listing a batch of files pays that cost once
    per file for an answer it then discards. The report says which happened
    rather than letting a declined reading look like a failed one.
    """
    # Measured where the work happens rather than around the whole call.
    # A single "read" figure said one provider's statements cost thirty
    # times another's per page and could not say which step did it.
    clock = timings if timings is not None else Timings()
    report = ShapeReport(path=path.name, masked=mask, columns_attempted=columns)
    try:
        from pypdf import PdfReader

        with clock.phase("open"):
            report.page_count = len(PdfReader(str(path)).pages)
    except Exception:
        report.readable = False
        return report

    with clock.phase("text"):
        raw = pdf_lines(path)
    report.line_count = len(raw)
    with clock.phase("mask"):
        if mask:
            masked_lines = [mask_line(line) for line in raw[:limit]]
            if len(raw) > limit:
                masked_lines.append(
                    MaskedText(f"... {len(raw) - limit} further line(s) not shown")
                )
            report.lines = masked_lines
        else:
            raw_lines = list(raw[:limit])
            if len(raw) > limit:
                raw_lines.append(
                    RawText(f"... {len(raw) - limit} further line(s) not shown")
                )
            report.lines = raw_lines

    if columns:
        with clock.phase("geometry"):
            _add_column_view(report, path, mask=mask, limit=limit)
    return report


def _add_column_view(
    report: ShapeReport, path: Path, *, mask: bool, limit: int
) -> None:
    """Attach the coordinate reading of the same pages.

    Failure here is not failure of the report: the line view is the older
    and better-proven of the two, so a page whose geometry cannot be read
    still yields everything it yielded before, with the rows simply
    absent. Absent rows are visible in the summary rather than silent.
    """
    from .statement_columns import COLUMN_TOLERANCE, aligned, covering_edges, rows

    try:
        table = rows(path)
    except Exception:
        return
    if not table:
        return
    # The edges the rows were ACTUALLY laid out against, not the stricter
    # set the majority rule starts from. A real statement reported "1
    # column" in its summary above a grid of eighteen, because the header
    # was quoting a different answer from the one the table used.
    report.edges = covering_edges(table, column_tolerance=COLUMN_TOLERANCE)
    grid = aligned(table)[:limit]
    if mask:
        report.rows = [[mask_line(cell) for cell in row] for row in grid]
    else:
        report.rows = [[RawText(cell) for cell in row] for row in grid]
