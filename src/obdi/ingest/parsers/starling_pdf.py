"""Starling certified statements, read from the table's own geometry.

Written from a real statement inspected through the masking surface, so no
figure had to be disclosed. A current account over several months: a
Summary block (period, opening balance, payments in, payments out, closing
balance) and then one table headed Date, Type, Transaction, In, Out and
"End of day account balance".

FIGURES ARE RIGHT-ALIGNED beneath headings that are left-aligned. The table
reader places a word in the column whose LEFT edge is nearest at or before
it, so a wide figure starts left of its heading's column and a narrow one
starts right of it: on the real statement the In figures landed in five
different columns and the Out figures in two. A direction read from a
column index would be wrong for exactly the figures that happen to be wide.
So this parser is handed the cells with their RIGHT edges and reads a
figure's column from where it ENDS, which for all three figure columns is
where the heading ends too.

Things the shape settles, and the one it does not:

  the sign              comes from which of In and Out a figure ends under,
                        never from a symbol: both columns print positive
                        figures, and only the balance column carries a minus
  the balance column    holds a figure on SOME rows only, and the heading
                        says END OF DAY, so it is a day's closing balance
                        rather than a per-row running balance. Its figures
                        are read so they cannot be mistaken for a movement,
                        and kept with the date of the row that prints them
                        (`StatementReading.end_of_day_minor`). The parser
                        does not judge them: which row of a day carries one
                        cannot be told from a masked page, and a check built
                        on a guess would refuse honest statements. The
                        statement's own rows are the judge, in
                        `statement_terms`
  the header            appears once, on page one; later pages carry no
                        heading, so the columns found there serve the rest
  page furniture        is a block of text and single-glyph rows above the
                        first row of every later page and a legal footer
                        below the last, none of which carries a date
  a wrapped description continues on the row beneath at the same left edge,
                        with nothing else on it; it may be separated from
                        its first line by a page break

Everything that does not fit is refused instead of read: a dated row with no
figure, a figure on a row with no date, text among the figures, a figure in
both money columns, a figure that ends under no column, and a Summary that
disagrees with the table it heads. A refusal can be fixed tomorrow; a figure
read into the wrong column corrupts a ledger quietly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..statement_columns import Cell, Row
from .statement_reading import StatementReading, StatementRow

#: A figure, with the currency symbol the text layer yields. That is "£" on
#: the page and "Â£" in this issuer's text layer (a pound sign's two UTF-8
#: bytes read as two characters), so an optional stray capital A-circumflex
#: precedes one symbol. Never a letter in general: a payee's name in the
#: amounts area must not parse as money.
_AMOUNT = re.compile(r"-?\s*Â?[^\w\s.,-]?\s*-?\d[\d,]*\.\d{2}")
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_PERIOD = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2})/(\d{2})/(\d{4})")

#: The words that make a row the table's heading, each as a whole cell.
_HEADING = frozenset({"date", "type", "transaction", "in", "out", "account"})

#: How far to the left of its column's right edge a figure may begin: the
#: widest figure on a statement, plus a margin. Anything further left is
#: description, whatever it looks like.
_WIDEST_FIGURE = 60.0

#: A cell this close to a column's left edge belongs to that column.
_ZONE_SLACK = 6.0

#: How far a figure's right edge may sit from its heading's. The three
#: columns end tens of points apart, so this separates them with room to
#: spare; a figure further out than this ends under no column at all.
_ENDS_WITHIN = 20.0

#: A wrapped line starts where the line above it did, to the point.
_SAME_LEFT_EDGE = 2.0

_SUMMARY_LABELS = {
    "opening balance": "opening",
    "payments in": "in",
    "payments out": "out",
    "closing balance": "closing",
}


@dataclass(frozen=True)
class _Columns:
    """Where the table's columns are, measured from its heading."""

    date_x: float
    type_x: float
    description_x: float
    in_end: float
    out_end: float
    balance_end: float

    @property
    def money_from(self) -> float:
        return self.in_end - _WIDEST_FIGURE

    def unmoved(self, other: _Columns) -> bool:
        return all(
            abs(a - b) <= _SAME_LEFT_EDGE
            for a, b in zip(
                (
                    self.date_x,
                    self.type_x,
                    self.description_x,
                    self.in_end,
                    self.out_end,
                    self.balance_end,
                ),
                (
                    other.date_x,
                    other.type_x,
                    other.description_x,
                    other.in_end,
                    other.out_end,
                    other.balance_end,
                ),
                strict=True,
            )
        )


@dataclass
class _Pending:
    """A transaction row still open to a wrapped line beneath it."""

    day: date
    amount_minor: int
    description_x: float
    words: list[str] = field(default_factory=list)
    #: The end-of-day balance printed on this row, when one is.
    printed_minor: int | None = None


def _tidy(text: str) -> str:
    """Whitespace made single, case kept: a description is identity, and a
    re-read must give the same string whatever the spacing the page used."""
    return re.sub(r"\s+", " ", text).strip()


def _norm(text: str) -> str:
    return _tidy(text).casefold()


def _minor(text: str) -> int:
    """A figure in minor units; a minus anywhere before the digits is negative."""
    stripped = text.strip()
    whole, _, pence = re.sub(r"[^\d.]", "", stripped).partition(".")
    value = int(whole) * 100 + int(pence)
    return -value if "-" in stripped else value


def _is_figure(cell: Cell) -> bool:
    return _AMOUNT.fullmatch(cell.text.strip()) is not None


def _day(match: re.Match[str], offset: int = 0) -> date:
    return date(
        int(match.group(3 + offset)),
        int(match.group(2 + offset)),
        int(match.group(1 + offset)),
    )


def _heading_at(rows: list[Row], index: int) -> _Columns | None:
    """The table's columns if the row at `index` is its heading, else None.

    The heading is three lines in the page's own order: END OF DAY above,
    the row naming Date to Account, and BALANCE beneath. The balance column
    is anchored on that last word, which is the one that sits directly over
    its figures.
    """
    named = {_norm(cell.text): cell for cell in rows[index].cells}
    if not named.keys() >= _HEADING:
        return None
    beneath = rows[index + 1].cells if index + 1 < len(rows) else []
    balance = next((cell for cell in beneath if _norm(cell.text) == "balance"), None)
    if balance is None:
        return None
    return _Columns(
        date_x=named["date"].x,
        type_x=named["type"].x,
        description_x=named["transaction"].x,
        in_end=named["in"].x_end,
        out_end=named["out"].x_end,
        balance_end=balance.x_end,
    )


def _summary_row(
    row: Row, found: dict[str, int], reading: StatementReading, notes: list[str]
) -> None:
    """Take a Summary line's figure, or the statement's period, from a row."""
    texts = [_norm(cell.text) for cell in row.cells]
    if "summary" in texts:
        for cell in row.cells:
            period = _PERIOD.fullmatch(cell.text.strip())
            if period:
                try:
                    reading.statement_date = _day(period, 3)
                    reading.period_start = _day(period)
                except ValueError:
                    notes.append(
                        f"the Summary's period {cell.text!r} names a date that "
                        "does not exist - a misread digit, not a day to round"
                    )
        return
    for cell in row.cells:
        label = _SUMMARY_LABELS.get(_norm(cell.text))
        if label is None or label in found:
            continue
        figure = next(
            (other for other in row.cells if other.x > cell.x and _is_figure(other)),
            None,
        )
        if figure is not None:
            found[label] = _minor(figure.text)


def read_statement(table: list[Row]) -> StatementReading:
    """Read the whole document's rows of positioned cells into a reading.

    Row order is page order, top to bottom. The Summary and the heading are
    only looked for until the heading is found; after it, every row is the
    table's, furniture included, and is either a transaction, the table's
    own opening balance, the continuation of a description, or text that is
    none of those and is ignored. A row carrying a figure is never ignored.
    """
    reading = StatementReading()
    notes = reading.notes
    summary: dict[str, int] = {}
    columns: _Columns | None = None
    table_opening: int | None = None
    pending: _Pending | None = None
    rows_in: list[_Pending] = []

    for position, row in enumerate(table):
        heading = _heading_at(table, position)
        if heading is not None:
            if columns is not None and not columns.unmoved(heading):
                notes.append(
                    "the table's columns sit somewhere else on this page than "
                    "on the first - a layout that moves cannot be read by one "
                    "set of positions"
                )
            columns = columns or heading
            pending = None
            continue
        if columns is None:
            _summary_row(row, summary, reading, notes)
            continue

        before = [c for c in row.cells if c.x < columns.type_x - _ZONE_SLACK]
        kinds = [
            c
            for c in row.cells
            if columns.type_x - _ZONE_SLACK <= c.x < columns.description_x - _ZONE_SLACK
        ]
        words = [
            c
            for c in row.cells
            if columns.description_x - _ZONE_SLACK <= c.x < columns.money_from
        ]
        money = [c for c in row.cells if c.x >= columns.money_from]
        stated = before[0].text.strip() if before else ""
        dated = _DATE.fullmatch(stated) if len(before) == 1 else None

        if dated is None:
            figures = [cell for cell in money if _is_figure(cell)]
            if _norm(" ".join(c.text for c in kinds)) == "opening balance":
                table_opening = _opening_of(figures, columns, notes)
                pending = None
            elif figures:
                notes.append(
                    f"a row carrying the figure {figures[0].text!r} has no date "
                    "of its own - a transaction is never dated from the row "
                    "above it, and a figure with no date is a movement the "
                    "walk would otherwise lose"
                )
            elif (
                pending is not None
                and len(row.cells) == 1
                and words
                and abs(words[0].x - pending.description_x) <= _SAME_LEFT_EDGE
            ):
                pending.words.append(words[0].text.strip())
            continue

        try:
            day = _day(dated)
        except ValueError:
            notes.append(
                f"the row dated {stated} states a date that does not exist - a "
                "misread digit, not a day to round to the nearest real one"
            )
            pending = None
            continue

        moved = _movement(money, columns, notes, stated)
        if moved is None:
            pending = None
            continue
        amount, printed = moved
        label = " ".join(c.text.strip() for c in (*kinds, *words) if c.text.strip())
        if not label:
            notes.append(
                f"the row dated {stated} has neither a type nor a description "
                "- the columns are not where the heading says they are"
            )
            pending = None
            continue
        pending = _Pending(
            day=day,
            amount_minor=amount,
            description_x=words[0].x if words else columns.description_x,
            words=[label],
            printed_minor=printed,
        )
        rows_in.append(pending)

    reading.transactions = [
        StatementRow(
            value_date=row.day,
            description=_tidy(" ".join(row.words)),
            amount_minor=row.amount_minor,
        )
        for row in rows_in
    ]
    reading.end_of_day_minor = [
        (row.day, row.printed_minor) for row in rows_in if row.printed_minor is not None
    ]
    _conclude(reading, summary, table_opening, columns is not None)
    return reading


def _opening_of(
    figures: list[Cell], columns: _Columns, notes: list[str]
) -> int | None:
    """The figure on the table's own OPENING BALANCE row."""
    if len(figures) != 1 or _column_of(figures[0], columns) != "balance":
        notes.append(
            "the table's OPENING BALANCE row does not carry exactly one "
            "figure under the balance column"
        )
        return None
    return _minor(figures[0].text)


def _column_of(cell: Cell, columns: _Columns) -> str | None:
    """Which figure column a cell ends under, or None if it ends under none."""
    ends = {
        "in": columns.in_end,
        "out": columns.out_end,
        "balance": columns.balance_end,
    }
    name, distance = min(
        ((name, abs(cell.x_end - end)) for name, end in ends.items()),
        key=lambda pair: pair[1],
    )
    return name if distance <= _ENDS_WITHIN else None


def _movement(
    money: list[Cell], columns: _Columns, notes: list[str], stated: str
) -> tuple[int, int | None] | None:
    """A dated row's signed amount and the end-of-day balance printed on it
    (None when it prints none), or None after saying why not.

    Money in is positive and money out negative, the house convention. The
    column is the whole of the sign, so the magnitude is taken: a stray
    minus inside In or Out must not turn a payment into a receipt.
    """
    placed: dict[str, int] = {}
    for cell in money:
        if not _is_figure(cell):
            notes.append(
                f"the row dated {stated} has text {cell.text!r} among its "
                "figures - not a figure, and not something to guess at"
            )
            return None
        column = _column_of(cell, columns)
        if column is None:
            notes.append(
                f"the row dated {stated} has a figure {cell.text!r} that ends "
                "under none of the In, Out, or balance columns"
            )
            return None
        if column in placed:
            notes.append(
                f"the row dated {stated} has two figures under the {column} "
                "column - refusing rather than picking one"
            )
            return None
        placed[column] = _minor(cell.text)
    if "in" in placed and "out" in placed:
        notes.append(
            f"the row dated {stated} carries a figure in BOTH money columns, "
            "so it reads as a payment in and a payment out at once - refusing "
            "rather than picking one"
        )
        return None
    if "in" in placed:
        return abs(placed["in"]), placed.get("balance")
    if "out" in placed:
        return -abs(placed["out"]), placed.get("balance")
    notes.append(
        f"the row dated {stated} carries no figure under In or Out - a "
        "dated row that moved nothing is a row the walk would silently lose"
    )
    return None


def _conclude(
    reading: StatementReading,
    summary: dict[str, int],
    table_opening: int | None,
    found_table: bool,
) -> None:
    """Set the balances and check the Summary against the table beneath it."""
    notes = reading.notes
    if not found_table:
        notes.append(
            "the transaction table's heading could not be found - a statement "
            "whose columns have moved or been renamed reads as a statement "
            "with no transactions on it, which is the one failure that looks "
            "exactly like a quiet month"
        )
        return
    missing = [name for name in ("opening", "in", "out", "closing") if name not in summary]
    if missing:
        notes.append(
            "the Summary does not state " + ", ".join(missing) + " - the "
            "statement's own account of itself is what the rows are checked "
            "against, so without it nothing can be"
        )
        return
    if reading.statement_date is None:
        notes.append(
            "the Summary states no period, so the statement cannot be dated - "
            "guessing a date would file a whole history on the wrong days"
        )
    if table_opening is None:
        notes.append("the table's OPENING BALANCE row could not be read")
    elif table_opening != summary["opening"]:
        notes.append(
            f"the Summary opens at {summary['opening']} minor units but the "
            f"table's OPENING BALANCE row says {table_opening} - two accounts "
            "of one fact, and no way to say which is right"
        )
    reading.opening_balance_minor = summary["opening"]
    reading.closing_balance_minor = summary["closing"]
    received = sum(row.amount_minor for row in reading.transactions if row.amount_minor > 0)
    paid = -sum(row.amount_minor for row in reading.transactions if row.amount_minor < 0)
    for name, stated, read in (("in", summary["in"], received), ("out", summary["out"], paid)):
        if abs(stated) != read:
            notes.append(
                f"the Summary's payments {name} total {abs(stated)} minor units "
                f"but the rows read as {read} - {abs(stated) - read} minor "
                "units unexplained, which is a row missed or read into the "
                "wrong column"
            )
