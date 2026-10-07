"""Nationwide FlexAccount statements, read from the table's own geometry.

Written from one real statement inspected through the masking surface, so no
figure had to be disclosed. A current account for a month: a transactions
table headed Date, Description, £ Out, £ In and £ Balance, with a panel to
its right that carries the statement's Start balance and End balance.

Things the shape settles, and what is inferred where it does not:

  the sign              comes from which of Out and In a figure ends under,
                        never from a symbol: both columns print positive
                        figures, and only the balance column carries a minus
  the year              is printed once, in the Date column, as a line of its
                        own (on the opening row on the shape's first page);
                        every row beneath carries only a day and a month. A
                        new year line replaces it. Where none is printed, a
                        January row after a December one is the next year's;
                        any other step backwards is refused, because a row
                        dated earlier than the one above it means a date was
                        misread and the year cannot be trusted either
  a row with no date    shares the date of the row above. It is told from a
                        wrapped description by its figure: a figure under Out
                        or In is a movement, and text with no figure is the
                        previous row's description continuing
  the balance column    holds a figure on SOME rows - the last of a day. Each
                        printed balance must equal the opening balance plus
                        every row up to and including its own, and the first
                        that does not is refused
  the opening           is the table's "Balance from statement" row. It is not
                        a transaction, and it must agree with the panel's Start
                        balance: two accounts of one fact are checked, not
                        chosen between
  the panel             sits right of the balance column on page one, among
                        averages, rates and fees that are mostly figures. A
                        cell that starts beyond the balance column is the panel
                        and is never part of a row
  other tables          later pages carry a table of charges with a Date
                        heading of its own, a year line, and dated rows with a
                        figure. The transactions table is read from its own
                        heading until the page ends or another Date heading
                        begins, so a page without the heading is not read

NOT settled by the shape, and so decided here the strict way: whether a long
statement repeats the heading on every page (a page without one is not read,
and the gate then refuses the statement for the rows it lost), whether a
description's wrapped line can start anywhere but the description's own left
edge (it must, or it is not attached), and where the panel starts relative to
the balance column on a different page layout. A refusal can be relaxed once a
real statement shows which of these holds; a figure read into the wrong row
corrupts a ledger quietly.

Everything that does not fit is refused instead of read: a figure with no date
and no row above it, text among the figures, a figure in both money columns, a
figure that ends under no column, two different Start balances, and a printed
balance the rows do not reach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..statement_columns import Cell, Row
from .statement_figures import is_figure, minor, tidy
from .statement_reading import StatementReading, StatementRow

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}

_DAY_MONTH = re.compile(r"(\d{1,2})\s+([A-Za-z]+)")
_YEAR = re.compile(r"\d{4}")
#: Labels are matched with or without the space inside them. The document's
#: word positions give "Statementdate:", "Startbalance", and "Endbalance" as
#: single words, and the first real statement was refused as having no date and
#: no balances while printing all three.
_STATEMENT_DATE = re.compile(
    r"statement\s*date:?\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.IGNORECASE
)
#: The opening row, "Balance from statement NN dated DD/MM/YYYY", with its spaces optional for
#: the same reason. The date is the day the PREVIOUS statement closed, so this statement covers
#: from the day after it.
_OPENING_ROW = re.compile(r"balancefromstatement\d*(?:dated(\d{2})/(\d{2})/(\d{4}))?")
_PANEL_FIGURE = re.compile(r"^(start|end)\s*balance\s+(.+)$", re.IGNORECASE)
_PANEL_LABELS = {"startbalance": "start", "endbalance": "end"}
#: A figure inside running text: a wrapped line that carries one may be a
#: movement that lost its columns, which is never joined to the row above.
_FIGURE_IN_TEXT = re.compile(r"\d[\d,]*\.\d{2}\b")

#: The words that make a row the table's heading, each as a whole cell once
#: the pound sign (or its two-character mangling) is taken off.
_HEADING = frozenset({"date", "description", "out", "in", "balance"})

#: How far to the left of the Out heading's right edge a figure may begin: the
#: widest figure on a statement, plus a margin. Anything further left is
#: description, whatever it looks like.
_WIDEST_FIGURE = 60.0

#: A cell this close to a column's left edge belongs to that column.
_ZONE_SLACK = 6.0

#: How far past the balance heading's right edge a cell may START and still be
#: the table's. A figure begins well left of where it ends, so this is only
#: ever crossed by the panel.
_ZONE_MARGIN = 8.0

#: How far a figure's right edge may sit from its heading's. The three columns
#: end tens of points apart; a figure further out than this ends under none.
_ENDS_WITHIN = 20.0

#: A wrapped line starts where the description above it did, to the point.
_SAME_LEFT_EDGE = 2.0


@dataclass(frozen=True)
class _Columns:
    """Where the table's columns are, measured from its heading."""

    date_x: float
    description_x: float
    out_end: float
    in_end: float
    balance_end: float

    @property
    def money_from(self) -> float:
        return self.out_end - _WIDEST_FIGURE

    @property
    def zone_end(self) -> float:
        return self.balance_end + _ZONE_MARGIN

    def column_of(self, cell: Cell) -> str | None:
        """Which figure column a cell ends under, or None if it ends under none."""
        ends = {"out": self.out_end, "in": self.in_end, "balance": self.balance_end}
        name, distance = min(
            ((name, abs(cell.x_end - end)) for name, end in ends.items()),
            key=lambda pair: pair[1],
        )
        return name if distance <= _ENDS_WITHIN else None


@dataclass
class _Entry:
    """A transaction row still open to a wrapped line beneath it."""

    day: date
    amount_minor: int
    description_x: float
    words: list[str] = field(default_factory=list)


def _name(text: str) -> str:
    """A heading cell's name: no pound sign, however it was decoded."""
    return tidy(text.replace("Â", "").replace("£", "")).casefold()


def _month(word: str) -> int | None:
    return _MONTHS.get(word.casefold())


def _heading(row: Row) -> _Columns | None:
    named = {_name(cell.text): cell for cell in row.cells}
    if not named.keys() >= _HEADING:
        return None
    return _Columns(
        date_x=named["date"].x,
        description_x=named["description"].x,
        out_end=named["out"].x_end,
        in_end=named["in"].x_end,
        balance_end=named["balance"].x_end,
    )


def _joined(row: Row) -> str:
    return tidy(" ".join(cell.text for cell in row.cells))


def _statement_date(table: list[Row]) -> date | None:
    for row in table:
        found = _STATEMENT_DATE.search(_joined(row))
        month = _month(found.group(2)) if found else None
        if found and month:
            try:
                return date(int(found.group(3)), month, int(found.group(1)))
            except ValueError:
                return None
    return None


def _panel_balances(table: list[Row], notes: list[str]) -> dict[str, int]:
    """The Start balance and End balance the panel states, each exactly once.

    A statement saying two different things about its own start is a document
    this reader does not understand, and no choice between them is safe.
    """
    stated: dict[str, set[int]] = {"start": set(), "end": set()}
    for row in table:
        for index, cell in enumerate(row.cells):
            label = _PANEL_LABELS.get("".join(cell.text.split()).casefold())
            figure_text: str | None = None
            if label is not None:
                beside = next(
                    (other for other in row.cells[index + 1 :] if is_figure(other.text)),
                    None,
                )
                figure_text = beside.text if beside else None
            else:
                fused = _PANEL_FIGURE.match(tidy(cell.text))
                if fused and is_figure(fused.group(2)):
                    label, figure_text = fused.group(1).casefold(), fused.group(2)
            if label is not None and figure_text is not None:
                stated[label].add(minor(figure_text))
    found: dict[str, int] = {}
    for label, title in (("start", "Start balance"), ("end", "End balance")):
        values = stated[label]
        if not values:
            notes.append(
                f"the statement does not state a {title} - its own account of "
                "itself is what the rows are checked against, so without it "
                "nothing can be"
            )
        elif len(values) > 1:
            notes.append(
                f"the statement states two different {title} figures - no way "
                "to say which is right"
            )
        else:
            found[label] = next(iter(values))
    return found


class _Reader:
    """One pass over the document's rows, holding what the table has said so far."""

    def __init__(self, statement_date: date | None, notes: list[str]) -> None:
        self.statement_date = statement_date
        self.notes = notes
        self.columns: _Columns | None = None
        self.active = False
        self.active_page = 0
        self.year: int | None = None
        self.last_day: date | None = None
        self.pending: _Entry | None = None
        self.entries: list[_Entry] = []
        self.table_opening: int | None = None
        #: The day the opening row says the balance stood (the previous statement's date).
        self.table_opening_day: date | None = None
        self.running: int | None = None
        self.walk_failed = False
        self.heading_seen = False

    # -- the page ----------------------------------------------------------

    def feed(self, row: Row) -> None:
        heading = _heading(row)
        if heading is not None:
            self.columns = heading
            self.heading_seen = True
            self.active = True
            self.active_page = row.page
            self.pending = None
            return
        if row.page != self.active_page:
            self.active = False
            self.pending = None
        if any(_name(cell.text) == "date" for cell in row.cells):
            self.active = False
            self.pending = None
            return
        if not self.active or self.columns is None:
            return
        self._row(row, self.columns)

    def _row(self, row: Row, columns: _Columns) -> None:
        cells = [cell for cell in row.cells if cell.x < columns.zone_end]
        if not cells:
            # Panel only: it sits between a row and its wrapped line without
            # belonging to either.
            return
        stated = [c for c in cells if c.x < columns.description_x - _ZONE_SLACK]
        words = [
            c
            for c in cells
            if columns.description_x - _ZONE_SLACK <= c.x < columns.money_from
        ]
        money = [c for c in cells if c.x >= columns.money_from]

        if len(stated) == 1 and _YEAR.fullmatch(stated[0].text.strip()):
            if not self._year_line(int(stated[0].text.strip())):
                return
            stated = []
            if not words and not money:
                self.pending = None
                return

        if len(stated) == 1 and _DAY_MONTH.fullmatch(stated[0].text.strip()):
            self._dated(stated[0].text.strip(), words, money, columns)
        elif stated:
            if any(is_figure(c.text) for c in money):
                self.notes.append(
                    f"a row states {tidy(' '.join(c.text for c in stated))!r} "
                    "where a date belongs, beside figures - a movement that "
                    "cannot be dated is one the walk would otherwise lose"
                )
            self.pending = None
        else:
            self._undated(words, money, columns)

    # -- the year and the date --------------------------------------------

    def _year_line(self, year: int) -> bool:
        if self.year is not None and year < self.year:
            self.notes.append(
                f"a year line says {year} after the statement had reached "
                f"{self.year} - years only move forward"
            )
            return False
        self.year = year
        return True

    def _day(self, text: str) -> date | None:
        found = _DAY_MONTH.fullmatch(text)
        month = _month(found.group(2)) if found else None
        if found is None or month is None:
            self.notes.append(
                f"the date {text!r} names no month - a misread letter, not a "
                "day to guess at"
            )
            return None
        if self.year is None:
            self.notes.append(
                f"the row dated {text} has no year: none was printed before "
                "the first dated row, and guessing the current year would file "
                "a whole history on the wrong days"
            )
            return None
        try:
            day = date(self.year, month, int(found.group(1)))
        except ValueError:
            self.notes.append(
                f"the row dated {text} states a date that does not exist - a "
                "misread digit, not a day to round to the nearest real one"
            )
            return None
        last = self.last_day
        if last is not None and day < last:
            if last.month == 12 and month == 1 and last.year == self.year:
                self.year += 1
                day = date(self.year, month, day.day)
            else:
                self.notes.append(
                    f"the row dated {text} ({day.isoformat()}) is before the "
                    f"row above it ({last.isoformat()}) - a statement runs "
                    "forward, so a date or its year was misread"
                )
                return None
        if self.statement_date is not None and day > self.statement_date:
            self.notes.append(
                f"the row dated {text} ({day.isoformat()}) is after the "
                f"statement date ({self.statement_date.isoformat()}) - a "
                "misread date or a year the rows should not have rolled into"
            )
            return None
        return day

    # -- a row of the table -----------------------------------------------

    def _figures(
        self, money: list[Cell], columns: _Columns, label: str
    ) -> dict[str, int] | None:
        """The row's figures by column, or None after saying why not."""
        placed: dict[str, int] = {}
        for cell in money:
            if not is_figure(cell.text):
                self.notes.append(
                    f"the row {label} has text {cell.text!r} among its "
                    "figures - not a figure, and not something to guess at"
                )
                return None
            column = columns.column_of(cell)
            if column is None:
                self.notes.append(
                    f"the row {label} has a figure {cell.text!r} that ends "
                    "under none of the Out, In, or Balance columns"
                )
                return None
            if column in placed:
                self.notes.append(
                    f"the row {label} has two figures under the {column} "
                    "column - refusing rather than picking one"
                )
                return None
            placed[column] = minor(cell.text)
        return placed

    def _movement(self, placed: dict[str, int], label: str) -> int | None:
        if "in" in placed and "out" in placed:
            self.notes.append(
                f"the row {label} carries a figure in BOTH money columns, so "
                "it reads as a payment in and a payment out at once - "
                "refusing rather than picking one"
            )
            return None
        for column in ("in", "out"):
            if column in placed and placed[column] < 0:
                self.notes.append(
                    f"the row {label} has a signed figure under {column} - "
                    "the column is the whole of the sign, so a minus there "
                    "is a misreading to refuse"
                )
                return None
        if "in" in placed:
            return placed["in"]
        if "out" in placed:
            return -placed["out"]
        self.notes.append(
            f"the row {label} carries no figure under Out or In - a dated row "
            "that moved nothing is a row the walk would silently lose"
        )
        return None

    def _dated(
        self, text: str, words: list[Cell], money: list[Cell], columns: _Columns
    ) -> None:
        self.pending = None
        day = self._day(text)
        if day is None:
            return
        label = f"dated {text}"
        placed = self._figures(money, columns, label)
        if placed is None:
            return
        amount = self._movement(placed, label)
        if amount is None:
            return
        self._record(day, words, amount, placed, label)

    def _undated(
        self, words: list[Cell], money: list[Cell], columns: _Columns
    ) -> None:
        text = tidy(" ".join(cell.text for cell in words))
        opening = _OPENING_ROW.match("".join(text.split()).casefold())
        if opening:
            self._opening(money, columns, opening)
            return
        if not money:
            self._wrapped(words, text)
            return
        placed = self._figures(money, columns, "with no date")
        if placed is None:
            self.pending = None
            return
        if "in" not in placed and "out" not in placed:
            self.notes.append(
                "an undated row carries only a balance - it moved nothing it "
                "can be attributed to, and a balance alone cannot be placed"
            )
            self.pending = None
            return
        if self.last_day is None:
            self.notes.append(
                "a row carrying figures has no date of its own and no dated "
                "row above it - a movement is never dated by guessing"
            )
            self.pending = None
            return
        amount = self._movement(placed, "with no date")
        if amount is None:
            self.pending = None
            return
        self._record(self.last_day, words, amount, placed, "with no date")

    def _wrapped(self, words: list[Cell], text: str) -> None:
        if not text:
            return
        if _FIGURE_IN_TEXT.search(text):
            self.notes.append(
                f"the line {text!r} carries a figure but sits in no money "
                "column - a continuation is never allowed one, because it "
                "would be a movement the walk loses"
            )
            self.pending = None
            return
        pending = self.pending
        if (
            pending is not None
            and len(words) == 1
            and abs(words[0].x - pending.description_x) <= _SAME_LEFT_EDGE
        ):
            pending.words.append(text)
            return
        self.pending = None

    def _opening(self, money: list[Cell], columns: _Columns, found: re.Match[str]) -> None:
        self.pending = None
        placed = self._figures(money, columns, "Balance from statement")
        if placed is None:
            return
        if set(placed) != {"balance"}:
            self.notes.append(
                "the Balance from statement row does not carry exactly one "
                "figure under the Balance column"
            )
            return
        if self.table_opening is not None:
            self.notes.append(
                "the table has a second Balance from statement row - two "
                "openings, and no way to say which the rows start from"
            )
            return
        self.table_opening = placed["balance"]
        self.running = placed["balance"]
        if found.group(1):
            try:
                self.table_opening_day = date(
                    int(found.group(3)), int(found.group(2)), int(found.group(1))
                )
            except ValueError:
                self.notes.append(
                    "the Balance from statement row is dated a day that does not exist"
                )

    def _record(
        self,
        day: date,
        words: list[Cell],
        amount: int,
        placed: dict[str, int],
        label: str,
    ) -> None:
        text = tidy(" ".join(cell.text for cell in words))
        if not text:
            self.notes.append(
                f"the row {label} has no description - the columns are not "
                "where the heading says they are"
            )
            self.pending = None
            return
        if self.running is None:
            self.notes.append(
                f"the row {label} comes before any Balance from statement "
                "row, so nothing says what it moved from"
            )
            self.pending = None
            return
        entry = _Entry(
            day=day,
            amount_minor=amount,
            description_x=words[0].x,
            words=[text],
        )
        self.entries.append(entry)
        self.pending = entry
        self.last_day = day
        self.running += amount
        printed = placed.get("balance")
        if printed is not None and printed != self.running and not self.walk_failed:
            self.walk_failed = True
            self.notes.append(
                f"the running balance printed on the row {label} "
                f"({day.isoformat()}) is {printed} minor units but the rows so "
                f"far reach {self.running} - {printed - self.running} minor "
                "units unexplained, which is a row missed, a row misread, or "
                "a figure read into the wrong column"
            )


def read_statement(table: list[Row]) -> StatementReading:
    """Read the whole document's rows of positioned cells into a reading.

    Row order is page order, top to bottom. The panel's balances and the
    statement date are looked for across the whole document first; the table
    is then walked once, in order, because a row's date and the running
    balance both depend on the rows above it.
    """
    reading = StatementReading()
    notes = reading.notes
    reading.statement_date = _statement_date(table)
    reader = _Reader(reading.statement_date, notes)
    for row in table:
        reader.feed(row)

    reading.transactions = [
        StatementRow(
            value_date=entry.day,
            description=tidy(" ".join(entry.words)),
            amount_minor=entry.amount_minor,
        )
        for entry in reader.entries
    ]
    if not reader.heading_seen:
        notes.append(
            "the transaction table's heading could not be found - a statement "
            "whose columns have moved or been renamed reads as a statement "
            "with no transactions on it, which is the one failure that looks "
            "exactly like a quiet month"
        )
        return reading
    if reading.statement_date is None:
        notes.append(
            "the statement date could not be found, so the statement cannot "
            "be dated - guessing a date would file a whole history on the "
            "wrong days"
        )
    balances = _panel_balances(table, notes)
    if reader.table_opening is None:
        notes.append(
            "the table's Balance from statement row could not be read, so "
            "the rows have no stated starting point of their own"
        )
    elif "start" in balances and reader.table_opening != balances["start"]:
        notes.append(
            f"the panel's Start balance is {balances['start']} minor units but "
            f"the table's Balance from statement row says "
            f"{reader.table_opening} - two accounts of one fact, and no way to "
            "say which is right"
        )
    if reader.table_opening_day is not None:
        reading.period_start = reader.table_opening_day + timedelta(days=1)
    reading.opening_balance_minor = balances.get("start")
    reading.closing_balance_minor = balances.get("end")
    return reading
