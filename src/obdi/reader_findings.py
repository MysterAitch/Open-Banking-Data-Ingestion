"""What a statement reader concluded from a kept statement, said without any value.

The masked shape page shows a statement's layout and nothing it concluded, so a section whose
balance or period did not arrive could not be told from one that had never been read. This says,
per section (or for the whole document where there are none), what the reader made of it: the
account it is assigned to, the period it found, whether it found an opening and a closing balance
and under which label, how many transactions it listed, what it took the account for and what
decided that, and what the arithmetic gate said.

Dates, counts, and fixed labels only. A balance, an amount, or the difference the gate found is
never said: the gate's refusal is `PdfStatementParser.refusal_of(figures=False)`, the same verdict
as the one that stops a statement, worded for a page served on a plain GET. A section's label has
every digit shown as 9, because a loan's label carries its rate.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from .errors import DataError
from .page_times import range_text, range_with_span
from .parsers.base import ParseError
from .parsers.pdf_statements import PdfStatementParser
from .parsers.statement_reading import StatementReading
from .parsers.uk_banks import detect
from .statement_sections import masked, read_sections

#: The title of the one finding for a document that is not divided into accounts.
WHOLE_DOCUMENT = "The whole document"

#: What an account is called when nothing has been assigned it.
UNASSIGNED_WORD = "unassigned"


@dataclass(frozen=True)
class Finding:
    """What the reader made of one section, or of the whole document."""

    title: str
    #: The account it is assigned to, "" while it is not.
    account: str
    period: tuple[date, date] | None
    opening_found: bool
    closing_found: bool
    closing_label: str
    transactions: int
    #: "saver", "loan", or "" where this reader does not tell the two apart.
    kind: str
    kind_basis: str
    #: "" when the arithmetic gate passed; otherwise its reason, digit-masked and figure-free.
    refusal: str


@dataclass(frozen=True)
class Findings:
    """Every finding for a document, or the reason there are none."""

    found: tuple[Finding, ...] = ()
    #: Why there is nothing to report: no reader recognised the document, or it could not be
    #: divided into accounts. Empty when `found` holds findings.
    said: str = ""


def _finding(
    parser: PdfStatementParser, reading: StatementReading, *, title: str, account: str
) -> Finding:
    period = (
        (reading.period_start, reading.statement_date)
        if reading.period_start is not None and reading.statement_date is not None
        else None
    )
    return Finding(
        title=title,
        account=account,
        period=period,
        opening_found=reading.opening_balance_minor is not None,
        closing_found=reading.closing_balance_minor is not None,
        closing_label=reading.closing_label,
        transactions=len(reading.transactions),
        kind=reading.account_kind,
        kind_basis=reading.account_kind_basis,
        refusal=parser.refusal_of(reading, figures=False),
    )


def _lines(item: Finding, account_html: Callable[[str], str]) -> list[str]:
    """One finding's lines as HTML, in the order a person asks the questions."""
    account = account_html(item.account) if item.account else UNASSIGNED_WORD
    if item.period is None:
        period = "no"
    elif item.period[0] <= item.period[1]:
        period = range_with_span(*item.period)
    else:
        # A document that states a period ending before it starts is said as it states it.
        period = f"{range_text(*item.period)}, which ends before it starts"
    closing = (
        f"yes, read from the label {html.escape(item.closing_label)}"
        if item.closing_found and item.closing_label
        else "yes"
        if item.closing_found
        else "no"
    )
    taken = (
        f"a {html.escape(item.kind)} ({html.escape(item.kind_basis)})"
        if item.kind
        else "unknown (this reader does not tell a saver from a loan)"
    )
    gate = (
        "passed" if not item.refusal else f"refused - {html.escape(item.refusal)}"
    )
    return [
        f"Account: {account}",
        f"Period found: {period}",
        f"Opening balance found: {'yes' if item.opening_found else 'no'}",
        f"Closing balance found: {closing}",
        f"Transactions listed: {item.transactions}",
        f"Taken for: {taken}",
        f"Arithmetic gate: {gate}",
    ]


def findings_html(findings: Findings, account_html: Callable[[str], str]) -> str:
    """The "What the reader found" block of the shape page, every part of it escaped."""
    head = "<h3>What the reader found</h3>"
    if findings.said:
        return f"<div>{head}<p>{html.escape(findings.said)}</p></div>"
    parts = "".join(
        f"<section><h4>{html.escape(item.title)}</h4><ul>"
        + "".join(f"<li>{line}</li>" for line in _lines(item, account_html))
        + "</ul></section>"
        for item in findings.found
    )
    return f"<div>{head}{parts}</div>"


def findings_of(
    payload: bytes,
    *,
    account_of: Callable[[str], str] = lambda token: "",
    whole_account: str = "",
) -> Findings:
    """The reader's conclusions about `payload`.

    `account_of` maps a section's key to the account it is assigned to ("" when none), and
    `whole_account` is the account a document read whole is filed under. Nothing is written.
    """
    try:
        parser = detect(payload)
    except (DataError, ParseError, ValueError):
        return Findings(said="No reader recognised this document.")
    if not isinstance(parser, PdfStatementParser):
        return Findings(said="This reader does not report what it found.")
    try:
        divided = read_sections(payload)
    except (DataError, ParseError, ValueError) as exc:
        return Findings(said=f"It could not be divided into accounts: {masked(str(exc))[:300]}")
    if divided is None:
        return Findings(
            (
                _finding(
                    parser,
                    parser.read(payload),
                    title=WHOLE_DOCUMENT,
                    account=whole_account,
                ),
            )
        )
    _, readings = divided
    return Findings(
        tuple(
            _finding(
                parser, item.reading, title=masked(item.label), account=account_of(item.key)
            )
            for item in readings
        )
    )
