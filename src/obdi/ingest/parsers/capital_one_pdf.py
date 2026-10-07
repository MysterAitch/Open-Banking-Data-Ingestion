"""Capital One credit card statements, read from their masked shapes.

Written from two masked shapes (three and five pages) of monthly statements,
so no figure or payee had to be disclosed. What the shapes show, and so what
this reads:

  the cover              page one's "Your account summary" box states the
                         Credit limit, the Previous balance, Payments received,
                         New transactions, and Your new balance
  the transaction table  headed "Your transaction details", "Paid in" and
                         "Paid out", and REPEATED at the top of every page that
                         holds rows, with "continued on next page..." and
                         "continued from previous page..." between the pages
  no sign                a row carries one figure, with no minus and no CR: the
                         column it stands in is its whole sign. Paid in (a
                         payment or a refund) lifts the balance owed down, paid
                         out (a purchase, a fee, interest) raises it
  no year                rows print "DD Mon", so the statement date ("2 July
                         26", printed on every page) supplies it, and a month
                         later in the year than the statement's belongs to the
                         year before
  the end                a STATEMENT TOTALS line carrying each column's total,
                         then a line of the previous balance, then the
                         NEW CLOSING BALANCE
  the annual page        the last page of a year-end statement carries yearly
                         totals in £ figures. It sits after the closing
                         balance, which is where this stops reading

The column of a figure is worked out from where it ENDS in the laid-out text,
against where the nearest heading's "Paid in" and "Paid out" end. The shapes
put a figure within a character or two of its heading's end and the two
headings about nineteen characters apart, and the page-to-page drift in the
whole layout is a few characters, so the nearest heading is the right one to
measure against and a figure beyond `_COLUMN_SLACK` of both is refused.

Two checks stand beside the arithmetic gate in `PdfStatementParser.parse`,
which is the safety argument. The rows must add up to each column's total on
the STATEMENT TOTALS line, which catches a figure filed in the wrong column
as well as a missed row; and the table's balances must agree with the cover's.

Everything that does not fit is refused instead of read: a figure with no
date, a dated line with no figure, a figure in neither column, a row above the
heading or after the totals, a balance printed twice, a date that does not
exist or follows the statement. A refusal can be fixed tomorrow; a payment read
as a purchase corrupts a ledger quietly.
"""

from __future__ import annotations

import re
from datetime import date

from .statement_reading import StatementReading, StatementRow

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}

#: The text layer yields "Â£" for a pound sign on this issuer's documents (a
#: pound's two UTF-8 bytes read as two characters), so a stray capital
#: A-circumflex may precede the symbol. The symbol itself is optional because
#: the table's figures carry none.
_POUND = r"(?:Â?£)?\s*"
_AMOUNT = r"[\d,]*\d\.\d{2}"
_FIGURE = f"({_AMOUNT})"

#: A balance's figure with its sign: a minus before the pound ("-£99.99"), after it ("£-99.99"),
#: or with no pound at all. A balance owed prints bare, so a minus is a balance the card owes its
#: owner - a card in credit - and is the only marker this reader understands. The lookahead
#: refuses two minuses ("-£-99.99"), which no layout is known to print and which a reader
#: choosing one of them would be guessing at.
_SIGNED = (
    r"(?!-\s*(?:Â?£)?\s*-)(?:(?P<before>-)\s*)?(?:Â?£\s*)?(?:(?P<after>-)\s*)?"
    rf"(?P<figure>{_AMOUNT})"
)

#: Characters between a figure's end and its column heading's end beyond which
#: the figure is not attributed to that column.
_COLUMN_SLACK = 4

#: How far before its statement a row may be dated. The rows carry no year, so
#: the year is worked out from the month alone, and a row whose month is later
#: in the year than the statement's is taken as last year's. Without a bound
#: that wrap turns a misread month into a plausible row eleven months old,
#: which the arithmetic would accept. A statement covers about a month, and a
#: purchase takes days to post, so a hundred days is generous.
_MAX_AGE_DAYS = 100

_STATEMENT_DATE = re.compile(r"\bStatement date\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}|\d{2})\b")
#: The table's heading. The cover's right-hand panel ("Your interest rates") prints beside the
#: table on page one, so a figure of the panel's may follow "Paid out" on the same row, apart
#: from it or fused to it; nothing after "Paid out" is the table's, and the column ends are
#: measured at the two labels alone.
_HEADING = re.compile(r"^\s*Your transaction details\s+(Paid in)\s+(Paid out)(?:\s*£.*|\s+\S.*)?$")

#: Anchored at the line's end: the figure on a label's own line, with nothing
#: after it - so a marker the shapes never showed (CR) fails to match and is
#: refused rather than dropped. A balance may carry a minus (`_SIGNED`); the credit limit
#: is never negative.
_COVER_PREVIOUS = re.compile(rf"Previous balance\s+{_SIGNED}\s*$")
_COVER_NEW = re.compile(rf"Your new balance\s+{_SIGNED}\s*$")
_COVER_LIMIT = re.compile(rf"Credit limit\s+{_POUND}{_FIGURE}\s*$")

_CLOSING = re.compile(r"\bCLOSING BALANCE\b(.*)$", re.I)
_CLOSING_FIGURE = re.compile(rf"^\s*{_SIGNED}\s*$")
_TOTALS = re.compile(r"^\s*(?:\d{1,2}\s+[A-Za-z]{3,4}\s+)?STATEMENT TOTALS\b")
_TABLE_PREVIOUS = re.compile(rf"^\s*Previous balance\s+{_SIGNED}\s*$", re.I)

#: A line the table prints a page's "Page 1 of 2" on. Every page reprints the table's heading,
#: so a page's rows are attributed by the heading above them on THAT page and never by the
#: previous page's.
_PAGE = re.compile(r"\bPage\s+\d+\s+of\s+\d+\b")

#: `<day> <Mon> <description> <figure>`. A gap of two spaces or more ahead of
#: the figure, because the figure is right-aligned beneath its heading and a
#: description's own trailing digits are not.
_ROW = re.compile(rf"^\s*(\d{{1,2}})\s+([A-Za-z]{{3,4}})\s+(.+?)\s{{2,}}{_FIGURE}\s*$")
_DATED = re.compile(r"^\s*\d{1,2}\s+[A-Za-z]{3,4}\b")
_ENDS_IN_FIGURE = re.compile(r"\d\.\d{2}\s*$")


def _minor(text: str) -> int:
    whole, _, pence = text.replace(",", "").partition(".")
    return int(whole) * 100 + int(pence or "0")


def _owed(found: re.Match[str]) -> int:
    """The amount a balance figure says is owed, in minor units: negative for a card in credit."""
    owed = _minor(found.group("figure"))
    return -owed if found.group("before") or found.group("after") else owed


def _money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100:,}.{abs(minor) % 100:02d}"


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _statement_date(lines: list[str], notes: list[str]) -> date | None:
    found = {
        match.groups()
        for line in lines
        if (match := _STATEMENT_DATE.search(line))
    }
    if len(found) != 1:
        notes.append(
            f"the pages state {len(found)} different 'Statement date' values - "
            "rows carry no year of their own, so exactly one is needed to date them"
        )
        return None
    day, month_name, year = found.pop()
    month = _MONTHS.get(month_name[:3].casefold())
    if month is None:
        notes.append(f"the statement date names a month this reader does not know: {month_name!r}")
        return None
    try:
        return date(int(year) + (2000 if len(year) == 2 else 0), month, int(day))
    except ValueError:
        notes.append(
            f"the statement date {day} {month_name} {year} names a day that does "
            "not exist - a misread digit, not a day to round"
        )
        return None


def _cover_figure(
    pattern: re.Pattern[str], cover: list[str], label: str, notes: list[str]
) -> int | None:
    """The one figure the cover states under `label`, or None after saying why not."""
    found = {_owed(match) for line in cover if (match := pattern.search(line))}
    if len(found) != 1:
        notes.append(
            f"the cover states {len(found)} different figures for '{label}' - "
            "exactly one is needed to check the table against"
        )
        return None
    return found.pop()


def _column(end: int, paid_in_end: int, paid_out_end: int) -> str | None:
    """Which column a figure ending at `end` stands in, or None if neither."""
    to_in = abs(end - paid_in_end)
    to_out = abs(end - paid_out_end)
    if min(to_in, to_out) > _COLUMN_SLACK:
        return None
    return "in" if to_in < to_out else "out"


#: A figure the table could have printed: bare (the table's figures carry no pound), and ending
#: the word it is in, so "9.99%" and "£9.99." are a panel's.
_TABLE_FIGURE = re.compile(rf"(?<![\d£.,\-])({_AMOUNT})(?=\s|$)")


def _without_side_panel(raw: str, paid_in_end: int, paid_out_end: int) -> str:
    """The line as far as the table prints it.

    A figure that ends at one of the two columns' edges is the table's, and whatever follows the
    last such figure is the cover's right-hand panel: its words, its percentages, a figure of
    its own that ends nowhere near a column. A line with no figure at a column's edge is
    returned whole, so a row whose only figure is the panel's is refused as a row with a figure
    in neither column rather than being read from the panel.
    """
    aligned = [
        found
        for found in _TABLE_FIGURE.finditer(raw)
        if _column(found.end(), paid_in_end, paid_out_end) is not None
    ]
    return raw[: aligned[-1].end()] if aligned else raw


def _row_date(
    found: re.Match[str], statement: date | None, notes: list[str]
) -> date | None:
    text = found.group(0).strip()
    month = _MONTHS.get(found.group(2)[:3].casefold())
    if month is None or (len(found.group(2)) == 4 and found.group(2).casefold() != "sept"):
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
    if (statement - stated).days > _MAX_AGE_DAYS:
        notes.append(
            f"the row {text!r} is dated {(statement - stated).days} days before the "
            "statement - a statement covers about a month, so a month is misread "
            "or the year wrapped the wrong way"
        )
        return None
    return stated


def read_statement(lines: list[str]) -> StatementReading:
    """Read a card statement's text lines into transactions and balances."""
    reading = StatementReading()
    notes = reading.notes
    reading.statement_date = _statement_date(lines, notes)

    headings = [i for i, line in enumerate(lines) if _HEADING.match(line)]
    if not headings:
        notes.append(
            "no 'Your transaction details' heading over 'Paid in' and 'Paid out' "
            "was found - a statement whose table has moved reads as a quiet month, "
            "which is the one failure that looks exactly like a real one"
        )
        return reading
    start = headings[0]

    cover = lines[:start]
    for line in cover:
        if _ROW.match(line):
            notes.append(
                f"the line {line.strip()!r} is a row above the table's heading, so "
                "no column can be attributed to its figure"
            )
    previous = _cover_figure(_COVER_PREVIOUS, cover, "Previous balance", notes)
    new = _cover_figure(_COVER_NEW, cover, "Your new balance", notes)
    limits = {_minor(m.group(1)) for line in cover if (m := _COVER_LIMIT.search(line))}
    if len(limits) == 1:
        reading.credit_limit_minor = limits.pop()

    closings = [
        (position, match)
        for position in range(start + 1, len(lines))
        if (match := _CLOSING.search(lines[position]))
    ]
    if len(closings) != 1:
        notes.append(
            f"the table has {len(closings)} 'CLOSING BALANCE' lines - exactly one "
            "ends it, and rows after a second would be left out of the walk"
        )
        closing_at = None
    else:
        closing_at, closing = closings[0]
        figure = _CLOSING_FIGURE.match(closing.group(1))
        if figure is None:
            notes.append(
                f"the closing balance line {lines[closing_at].strip()!r} is not a "
                "plain figure owed - a marker the layout has not shown is not guessed at"
            )
        else:
            reading.closing_balance_minor = -_owed(figure)
            reading.closing_in_credit = reading.closing_balance_minor > 0
    if previous is not None:
        reading.opening_balance_minor = -previous
    closing_minor = reading.closing_balance_minor
    if new is not None and closing_minor is not None and -new != closing_minor:
        notes.append(
            f"the cover's 'Your new balance' is {_money(new)} but the closing "
            f"balance line says {_money(-closing_minor)} - two "
            "accounts of one fact, and no way to say which is right"
        )
    if closing_at is None:
        return reading

    totals: dict[str, int] | None = None
    summed = {"in": 0, "out": 0}
    # The columns of the page being read: None from a page's top until its own heading, so a
    # row on a page that reprints none is refused rather than measured against another page's.
    ends: tuple[int, int] | None = _heading_ends(lines[start])
    rows: list[StatementRow] = []
    for raw in lines[start + 1 : closing_at]:
        line = raw.strip()
        if not line:
            continue
        if _HEADING.match(raw):
            ends = _heading_ends(raw)
            continue
        if _PAGE.search(raw):
            ends = None
            continue
        if _TOTALS.match(raw):
            if totals is not None:
                notes.append("the table has more than one STATEMENT TOTALS line")
                continue
            totals = {"in": 0, "out": 0}
            if ends is None:
                notes.append(
                    "the STATEMENT TOTALS line has no 'Paid in' and 'Paid out' heading "
                    "above it on its own page, so its figures cannot be attributed"
                )
                continue
            for figure in re.finditer(_AMOUNT, raw[raw.index("TOTALS") :]):
                end = raw.index("TOTALS") + figure.end()
                column = _column(end, *ends)
                if column is None:
                    notes.append(
                        f"a figure on the STATEMENT TOTALS line ends at {end}, in "
                        "neither the 'Paid in' nor the 'Paid out' column"
                    )
                else:
                    totals[column] = _minor(figure.group(0))
            continue
        carried = _TABLE_PREVIOUS.match(raw)
        if carried:
            if previous is not None and _owed(carried) != previous:
                notes.append(
                    f"the table's previous balance is {_money(_owed(carried))} but the "
                    f"cover's is {_money(previous)} - two accounts of one fact"
                )
            continue
        if ends is not None:
            raw = _without_side_panel(raw, *ends)
            line = raw.strip()
        found = _ROW.match(raw)
        if found:
            if totals is not None:
                notes.append(f"the row {line!r} comes after the STATEMENT TOTALS line")
                continue
            stated = _row_date(found, reading.statement_date, notes)
            if ends is None:
                notes.append(
                    f"the row {line!r} has no 'Paid in' and 'Paid out' heading above it "
                    "on its own page, so no column can be attributed to its figure"
                )
                continue
            column = _column(len(raw.rstrip()), *ends)
            if column is None:
                notes.append(
                    f"the row {line!r} has a figure in neither the 'Paid in' nor "
                    "the 'Paid out' column, so its sign cannot be told"
                )
                continue
            if stated is None:
                continue
            amount = _minor(found.group(4))
            summed[column] += amount
            rows.append(
                StatementRow(
                    value_date=stated,
                    description=_tidy(found.group(3)),
                    amount_minor=amount if column == "in" else -amount,
                )
            )
            continue
        if _DATED.match(raw):
            notes.append(
                f"the table line {line!r} starts with a date but ends in no figure - "
                "a row left out is a movement the walk would lose"
            )
        elif _ENDS_IN_FIGURE.search(line):
            notes.append(
                f"the table line {line!r} ends in a figure but is not a dated row "
                "this reader understands - a row left out is a movement the walk would lose"
            )

    reading.transactions = rows
    if totals is None:
        notes.append(
            "the table has no STATEMENT TOTALS line, so the columns' totals "
            "cannot be checked against the rows"
        )
        return reading
    for column, name in (("in", "paid-in"), ("out", "paid-out")):
        if summed[column] != totals[column]:
            notes.append(
                f"the {name} rows add to {_money(summed[column])} but the STATEMENT "
                f"TOTALS line says {_money(totals[column])} - "
                f"{totals[column] - summed[column]} minor units unexplained, "
                "from a missed row or a figure in the wrong column"
            )
    return reading


def _heading_ends(heading: str) -> tuple[int, int]:
    """Where 'Paid in' and 'Paid out' end in a heading line already matched."""
    found = _HEADING.match(heading)
    if found is None:
        raise ValueError("not a heading line")
    return found.end(1), found.end(2)
