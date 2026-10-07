"""Credit card statements of one issuer, read from their masked shapes.

Written from five masked shapes (four to six pages each) of monthly statements,
so no figure or payee had to be disclosed. The issuer's name was masked, so the
source is a placeholder (`UK_CARD_STATEMENT_SOURCE`) until it is known.

What the shapes show, and so what this reads:

  the transaction table  one table on one page in every shape, headed Date of
                         transaction, Date entered, Description and an amount.
                         It opens with a BALANCE FROM PREVIOUS STATEMENT row
                         and closes with a New balance row, and the rows
                         between are the month's
  no year                dates print as "28 JUNE", so the statement's own date
                         (the line under the heading "Your credit ... statement")
                         supplies it, and a December row on a January statement
                         is last year's. The cover also prints the payment due
                         date, which is why the heading is the anchor and a bare
                         date line is not
  the sign               a payment, a refund, an interest refund, and a balance
                         in credit all carry CR after the figure; a spend has no
                         marker. Balances print as money OWED, so the previous
                         and new balances are negated into the house convention
  a second line          only for a foreign-currency purchase: "15.00 EUR @
                         1.1546" on the line beneath its row. It is joined onto
                         the row's description, never made a row
  two figures for each   the cover's Summary box states the previous and new
  balance                balance too, so the table is checked against the cover

Read from the page's text lines rather than its coordinates, like the other
card parsers. The coordinate grid was tried and rejected: across the five
shapes it came out as 17 to 20 columns, a different grid every month, while
the text lines kept every row whole.

Not checked, and why. The Summary box's "Payments received" and "New
transactions, fees and charges" are not compared with the rows: the shapes do
not say whether a refund or an interest credit counts among the payments or is
netted off the charges, and a check built on a guess would refuse honest
statements. The "Total ... between <date> and <date>" line is on the ANNUAL
statement page and totals a year, not the month, so it is not a check on these
rows either.

Everything that does not fit is refused instead of read: a missing or repeated
previous-balance row, a table with no end, a cover that disagrees with the
table, a row that cannot be dated or is dated after its statement, a line in
the table that ends in a figure but is not a row, and a text line at the
description's edge that is not a foreign-currency line. A refusal can be fixed
tomorrow; a credit read as a spend corrupts a ledger quietly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .statement_reading import StatementReading, StatementRow

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}

#: The text layer yields "Â£" for a pound sign on this issuer's documents (a
#: pound's two UTF-8 bytes read as two characters), so a stray capital
#: A-circumflex may precede the symbol. The symbol itself is optional because
#: the table's figures carry none.
_POUND = r"(?:Â?£)?\s*"
_FIGURE = r"([\d,]+\.\d{2})"
_CR = r"(?:\s*(CR))?"

_HEADING = re.compile(r"^\s{0,6}Your credit \S+ statement\b")
#: At the start of its line with at most a few spaces before it, and followed
#: by a gap or the end: the cover's other dates sit mid-line, beside a label.
_STATEMENT_DATE = re.compile(r"^\s{0,6}(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})(?:\s{2,}|$)")

_PREVIOUS_ROW = re.compile(rf"^\s*BALANCE FROM \S+ STATEMENT\s+{_POUND}{_FIGURE}{_CR}\s*$", re.I)
#: Anchored at the line's start: the cover's "Your new balance" is a different
#: line stating the same fact, and sits before the table.
_NEW_ROW = re.compile(rf"^\s*New balance\s+{_POUND}{_FIGURE}{_CR}\s*$", re.I)

_COVER_PREVIOUS = re.compile(rf"Previous balance\s+{_POUND}{_FIGURE}{_CR}\s*$")
_COVER_NEW = re.compile(rf"Your new balance\s+{_POUND}{_FIGURE}{_CR}\s*$")
_CREDIT_LIMIT = re.compile(rf"Your credit limit\s+{_POUND}([\d,]+(?:\.\d{{2}})?)\s*$")
_RATE = re.compile(r"(\d+\.\d{2})%\s+\S+\s+\([^)]*\)\s+for\s+(Cash Transactions|Purchases)\b")

#: `[card] <day> <MONTH> <day> <MONTH> <description> <figure> [CR]`. The four
#: digits ahead of the first date are the card's, and are absent on payments
#: and interest. The first date is the date of transaction and the second the
#: date entered.
_ROW = re.compile(
    rf"^(?:\d{{4}}\s+)?(\d{{1,2}})\s+([A-Za-z]+)\s+(\d{{1,2}})\s+([A-Za-z]+)\s+(.+?)\s+{_FIGURE}{_CR}$"
)
_FOREIGN = re.compile(r"^\s*([\d,]+\.\d{2})\s+([A-Z]{3})\s+@\s+(\d+(?:\.\d+)?)\s*$")
_ENDS_IN_FIGURE = re.compile(r"\d\.\d{2}(?:\s*CR)?\s*$")

#: A text line this near the previous-balance row's own left edge is a line at
#: the description's edge. Measured in characters of the laid-out text, where
#: the real shapes put the two within a column or so of each other and the page
#: header block more than twenty columns away.
_DESCRIPTION_EDGE = 3


def _minor(text: str) -> int:
    whole, _, pence = text.replace(",", "").partition(".")
    return int(whole) * 100 + int(pence or "0")


def _owed_as_position(figure: str, marker: str | None) -> int:
    """A printed balance in the house convention: owed is negative, CR positive."""
    amount = _minor(figure)
    return amount if marker == "CR" else -amount


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _statement_date(lines: list[str], notes: list[str]) -> date | None:
    for position, line in enumerate(lines[:-1]):
        if not _HEADING.match(line):
            continue
        found = _STATEMENT_DATE.match(lines[position + 1])
        month = _MONTHS.get(found.group(2).casefold()) if found else None
        if found is None or month is None:
            break
        try:
            return date(int(found.group(3)), month, int(found.group(1)))
        except ValueError:
            notes.append(
                f"the statement date {lines[position + 1].strip()!r} names a day "
                "that does not exist - a misread digit, not a day to round"
            )
            return None
    notes.append(
        "no statement date found under the heading 'Your credit ... statement' - "
        "rows carry no year of their own, so none can be dated"
    )
    return None


def _cover_figure(
    pattern: re.Pattern[str], lines: list[str], label: str, notes: list[str]
) -> int | None:
    """The one balance the cover states under `label`, or None after saying why not."""
    found = {
        _owed_as_position(match.group(1), match.group(2))
        for line in lines
        if (match := pattern.search(line))
    }
    if len(found) != 1:
        notes.append(
            f"the cover states {len(found)} different figures for {label} - "
            "exactly one is needed to check the table against"
        )
        return None
    return found.pop()


def _terms(lines: list[str], reading: StatementReading) -> None:
    """The credit limit and the standard rates, where the cover states each once."""
    limits = {
        _minor(match.group(1)) for line in lines if (match := _CREDIT_LIMIT.search(line))
    }
    if len(limits) == 1:
        reading.credit_limit_minor = limits.pop()
    stated: dict[str, set[float]] = {}
    for line in lines:
        rate = _RATE.search(line)
        if rate:
            name = "cash" if rate.group(2).startswith("Cash") else "purchases"
            stated.setdefault(name, set()).add(float(rate.group(1)))
    # Two different figures for one kind leave no way to say which is current.
    reading.rates = {name: next(iter(found)) for name, found in stated.items() if len(found) == 1}


def read_statement(lines: list[str]) -> StatementReading:
    """Read a card statement's text lines into transactions, balances and terms."""
    reading = StatementReading()
    notes = reading.notes
    reading.statement_date = _statement_date(lines, notes)

    opening_at = [i for i, line in enumerate(lines) if _PREVIOUS_ROW.match(line)]
    if len(opening_at) != 1:
        notes.append(
            "the table's 'BALANCE FROM ... STATEMENT' row was "
            + ("not found" if not opening_at else "found more than once")
            + " - a statement whose table has moved reads as a quiet month, "
            "which is the one failure that looks exactly like a real one"
        )
        return reading
    start = opening_at[0]
    opening = _PREVIOUS_ROW.match(lines[start])
    ends = [
        (position, match)
        for position in range(start + 1, len(lines))
        if (match := _NEW_ROW.match(lines[position]))
    ]
    if opening is None or not ends:
        notes.append(
            "the table has no 'New balance' row after its previous-balance row, so "
            "it has no end and nothing to check its rows against"
        )
        return reading
    if len(ends) > 1:
        notes.append(
            "the table has more than one 'New balance' row, so which one ends it "
            "cannot be told - rows after the first would be left out of the walk"
        )
        return reading
    closing_at, closing = ends[0]

    reading.opening_balance_minor = _owed_as_position(opening.group(1), opening.group(2))
    reading.closing_balance_minor = _owed_as_position(closing.group(1), closing.group(2))

    cover = lines[:start]
    _terms(cover, reading)
    for label, pattern, table in (
        ("Previous balance", _COVER_PREVIOUS, reading.opening_balance_minor),
        ("Your new balance", _COVER_NEW, reading.closing_balance_minor),
    ):
        stated = _cover_figure(pattern, cover, label, notes)
        if stated is not None and stated != table:
            notes.append(
                f"the cover's {label} is {stated} minor units but the table says "
                f"{table} - two accounts of one fact, and no way to say which is right"
            )

    edge = _indent(lines[start])
    rows: list[_Open] = []
    for raw in lines[start + 1 : closing_at]:
        line = raw.strip()
        if not line:
            continue
        found = _ROW.match(line)
        if found:
            row = _row(found, reading.statement_date, notes)
            if row is not None:
                rows.append(row)
            continue
        if _FOREIGN.match(line):
            if not rows:
                notes.append(
                    f"the foreign-currency line {line!r} has no row above it to belong to"
                )
            elif rows[-1].foreign:
                notes.append(
                    f"a second foreign-currency line {line!r} follows one row - "
                    "refusing rather than choosing between them"
                )
            else:
                rows[-1].foreign = _tidy(line)
            continue
        if _ENDS_IN_FIGURE.search(line):
            notes.append(
                f"the table line {line!r} ends in a figure but is not a row this "
                "reader understands - a row left out is a movement the walk would lose"
            )
        elif abs(_indent(raw) - edge) <= _DESCRIPTION_EDGE:
            notes.append(
                f"the table line {line!r} continues a description in a form that is "
                "not foreign currency - refusing rather than losing or inventing it"
            )
    reading.transactions = [
        StatementRow(
            value_date=row.day,
            description=f"{row.description} {row.foreign}" if row.foreign else row.description,
            amount_minor=row.amount_minor,
            posted=row.entered,
        )
        for row in rows
    ]
    return reading


@dataclass
class _Open:
    """A dated row, still open to the foreign-currency line beneath it."""

    day: date
    description: str
    amount_minor: int
    foreign: str = ""
    entered: date | None = None


def _row(found: re.Match[str], statement: date | None, notes: list[str]) -> _Open | None:
    """One dated row, or None after saying why it could not be read."""
    text = found.group(0)
    month = _MONTHS.get(found.group(2).casefold())
    entered = _MONTHS.get(found.group(4).casefold())
    if month is None or entered is None:
        notes.append(f"the row {text!r} names a month this reader does not know")
        return None
    if statement is None:
        return None
    year = statement.year - 1 if month > statement.month else statement.year
    try:
        stated = date(year, month, int(found.group(1)))
    except ValueError:
        notes.append(f"the row {text!r} states a date that does not exist")
        return None
    if stated > statement:
        notes.append(
            f"the row {text!r} is dated after the statement itself - the year was "
            "worked out from the statement's date, so one of the two is misread"
        )
        return None
    amount = _minor(found.group(6))
    return _Open(
        day=stated,
        description=_tidy(found.group(5)),
        amount_minor=amount if found.group(7) == "CR" else -amount,
        entered=_entered_date(found, entered, statement),
    )


def _entered_date(found: re.Match[str], month: int, statement: date) -> date | None:
    """The date the issuer entered the row, or None where it is not a real one.

    Kept beside the date of the transaction and never in its place: it is a second date
    the statement states, and the year is worked out from the statement as for the first.
    A misread one costs only itself, so it is left out rather than refusing the statement.
    """
    year = statement.year - 1 if month > statement.month else statement.year
    try:
        return date(year, month, int(found.group(3)))
    except ValueError:
        return None
