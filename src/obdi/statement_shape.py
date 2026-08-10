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


def pdf_lines(path: Path) -> list[RawText]:
    """Every text line in a PDF, in page order.

    Text-embedded PDFs only - a scanned page has no text layer and yields
    nothing, which the report names rather than reporting an empty file.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
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
                " - values MASKED (digits as 9, other words as X, layout kept)"
                if self.masked
                else " - REAL VALUES, not masked"
            )
        )
        return "\n".join([header, *self.lines])


def shape_report(path: Path, *, mask: bool = True, limit: int = 200) -> ShapeReport:
    """Read a statement's layout, masked unless explicitly asked otherwise."""
    report = ShapeReport(path=path.name, masked=mask)
    try:
        from pypdf import PdfReader

        report.page_count = len(PdfReader(str(path)).pages)
    except Exception:
        report.readable = False
        return report

    raw = pdf_lines(path)
    report.line_count = len(raw)
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
    return report
