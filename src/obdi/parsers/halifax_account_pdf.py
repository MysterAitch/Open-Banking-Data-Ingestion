"""Halifax bank account statements, read from the document's laid-out lines.

Written from one real one-page statement inspected through the masking
surface, so no figure had to be disclosed. A current account for a month: a
summary of Money In and Money Out beside two "Balance on <date>" lines, a
period, and a table of transactions.

THE TABLE IS NOT ONE LINE PER ROW. The text layer carries, for every row,
three lines:

  1  the date, description and type, each fused to a hidden label
     ("Date03 Aug 26"), and the Money Out and Balance labels with no figure
  2  the Money In label, with a lower-case word after it where the column is
     empty
  3  a "." under each of the first three columns, and the figures that are
     really on the page, each followed by "."

So a figure is attributed to a column from where it ENDS, relative to the
visible headings ("Money In (£).", "Money Out (£).", "Balance (£).") above
the first row, never from a label's position: the hidden labels sit tens of
columns right of the figures they name. A figure fused to its own label
("Money Out (£)12.50.") is attributed by the label, which is the one place
the layout says outright which column it means. Where the two disagree, or a
figure ends nearer to no column than half the gap between neighbours, it is
refused.

Things the shape settles, and the ones it does not:

  the sign              comes from which of Money In and Money Out a figure is
                        in, never from a symbol: both print positive figures,
                        and only the balance carries a minus, before or after
                        the pound sign
  the opening           is the EARLIER of the two "Balance on" dates. The page
                        puts the start of the period first, but a reordering
                        must not swap the two
  the totals            the summary's Money In and Money Out are checked
                        against the rows, as a second account of the same
                        movement
  the balance column    is printed on every row of the sample, so each printed
                        balance is walked from the opening, and a row that
                        prints none is simply not checked
  the sample's order    is oldest first, so the walk assumes it; a statement
                        listed newest first fails the walk on its first row,
                        loudly, rather than being read backwards
  the end of the table  is the legend "Transaction types", after which nothing
                        is a row

NOT settled, and so decided here the strict way: what a Money In row looks like
(none sits in the sample - its Money In total is a masked zero), whether a
description ever wraps onto a further line (any line inside a row's block that
is not one of the three is refused), what a second page carries above its first
row (a figure on any line between rows that is not a recognised summary or
heading line is refused), and whether the two balance lines can repeat. A
refusal can be relaxed once a real statement shows which of these holds; a
figure read into the wrong row corrupts a ledger quietly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

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

#: A cell is a run of text separated from the next by two or more spaces,
#: which is how the layout renders a gap between columns.
_CELL = re.compile(r"\S+(?: \S+)*")
_PERIOD = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+to\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
)
_BALANCE_ON = re.compile(r"Balance on\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
#: The first cell of a transaction's first line. The word "Date" is a hidden
#: label fused to the value, and is optional so that a document without it
#: reads the same.
_ROW_START = re.compile(r"(?:Date)?\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{2}|\d{4})")
_LABEL = re.compile(r"(Money In|Money Out|Balance) \(£\)(.*)")
_HEADING_CELL = re.compile(r"(Money In|Money Out|Balance) \(£\)\.?")
#: A figure inside running text: a line of unknown kind that carries one may be
#: a movement that lost its row, which is never read past.
_FIGURE_IN_TEXT = re.compile(r"\d[\d,]*\.\d{2}")

_COLUMNS = {"Money In": "in", "Money Out": "out", "Balance": "balance"}


@dataclass(frozen=True)
class _Cell:
    column: int
    text: str

    @property
    def end(self) -> int:
        """Where the cell's text ends, not counting a trailing full stop."""
        return self.column + len(self.text.rstrip("."))


@dataclass
class _Record:
    """A transaction's block, open until its third line arrives."""

    stated: str
    description: str
    figures: dict[str, int] = field(default_factory=dict)
    refused: bool = False


def _cells(line: str) -> list[_Cell]:
    # The pound sign arrives as two characters in some text layers; removing the
    # stray first one leaves every label and figure as it is on the page.
    return [
        _Cell(found.start(), found.group())
        for found in _CELL.finditer(line.replace("Â", ""))
    ]


def _month(word: str) -> int | None:
    return _MONTHS.get(word.casefold())


def _full_date(day: str, month_word: str, year: str) -> date | None:
    month = _month(month_word)
    if month is None:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _figure(text: str) -> int | None:
    """A cell's figure, whose trailing full stop is the layout's, not the number's."""
    stripped = text.rstrip(".")
    return minor(stripped) if is_figure(stripped) else None


class _Reader:
    def __init__(self, notes: list[str]) -> None:
        self.notes = notes
        self.period: tuple[date, date] | None = None
        self.received: int | None = None
        self.paid: int | None = None
        self.balances: list[tuple[date, int]] = []
        self.ends: dict[str, int] | None = None
        self.in_table = False
        self.heading_seen = False
        self.record: _Record | None = None
        self.entries: list[tuple[str, date, int, int | None, str]] = []

    # -- the page ----------------------------------------------------------

    def feed(self, line: str) -> None:
        cells = _cells(line)
        if not cells:
            return
        if self._heading(cells):
            return
        first = cells[0].text.casefold()
        if first.startswith("transaction types"):
            self.close()
            self.in_table = False
            return
        if not self.in_table:
            self._summary(cells)
            return
        self._table_line(line, cells)

    def _heading(self, cells: list[_Cell]) -> bool:
        named = {
            _COLUMNS[found.group(1)]: cell
            for cell in cells
            if (found := _HEADING_CELL.fullmatch(cell.text))
        }
        if set(named) != {"in", "out", "balance"} or len(cells) > 6:
            return False
        if any(not _HEADING_CELL.fullmatch(cell.text) for cell in cells if cell.text != "."):
            return False
        self.close()
        self.ends = {name: cell.end for name, cell in named.items()}
        self.in_table = True
        self.heading_seen = True
        return True

    # -- the summary ---------------------------------------------------------

    def _summary(self, cells: list[_Cell]) -> None:
        for index, cell in enumerate(cells):
            period = _PERIOD.fullmatch(cell.text)
            if period and self.period is None:
                begins = _full_date(*period.group(1, 2, 3))
                ends = _full_date(*period.group(4, 5, 6))
                if begins is None or ends is None:
                    self.notes.append(
                        f"the period {cell.text!r} names a date that does not "
                        "exist - a misread digit, not a day to round"
                    )
                else:
                    self.period = (begins, ends)
            if cell.text.startswith("Balance on"):
                balance = _BALANCE_ON.fullmatch(cell.text)
                stated = _full_date(*balance.group(1, 2, 3)) if balance else None
                figure = next(
                    (
                        _figure(other.text)
                        for other in cells[index + 1 :]
                        if _figure(other.text) is not None
                    ),
                    None,
                )
                if stated is None or figure is None:
                    self.notes.append(
                        f"the line {cell.text!r} does not state a date and a "
                        "balance that can be read"
                    )
                else:
                    self.balances.append((stated, figure))
        label = cells[0].text.casefold()
        if label in ("money in", "money out"):
            figure = next(
                (
                    _figure(other.text)
                    for other in cells[1:]
                    if _figure(other.text) is not None
                ),
                None,
            )
            if figure is None:
                self.notes.append(f"the {cells[0].text} total could not be read")
            elif label == "money in":
                self.received = abs(figure)
            else:
                self.paid = abs(figure)

    # -- the table -----------------------------------------------------------

    def _table_line(self, line: str, cells: list[_Cell]) -> None:
        start = _ROW_START.fullmatch(cells[0].text)
        if start is not None:
            self.close()
            self._open(cells)
            return
        record = self.record
        if record is None:
            if _FIGURE_IN_TEXT.search(line):
                self.notes.append(
                    f"the line {tidy(line)!r} carries a figure between rows and "
                    "is not one this layout is known to have - a movement the "
                    "walk would otherwise lose"
                )
            return
        if all(cell.text == "." for cell in cells):
            return
        if all(_LABEL.fullmatch(cell.text) for cell in cells):
            self._labels(cells, record)
            return
        if all(cell.text == "." or _figure(cell.text) is not None for cell in cells):
            self._values(cells, record)
            self.close()
            return
        self.notes.append(
            f"the line {tidy(line)!r} sits inside a transaction's block and is "
            f"not one of its three lines (row dated {record.stated}) - a "
            "wrapped description, or a layout not seen before, and the figures "
            "that follow cannot be trusted to belong to this row"
        )
        record.refused = True

    def _open(self, cells: list[_Cell]) -> None:
        stated = _ROW_START.fullmatch(cells[0].text)
        assert stated is not None  # the caller matched it
        labels = [cell for cell in cells[1:] if _LABEL.fullmatch(cell.text)]
        others = [cell for cell in cells[1:] if not _LABEL.fullmatch(cell.text)]
        label = f"dated {tidy(cells[0].text.removeprefix('Date'))}"
        record = _Record(
            stated=tidy(cells[0].text.removeprefix("Date")), description=""
        )
        if len(others) == 2:
            words, kind = others[0].text, others[1].text
        elif len(others) > 2 and others[-1].text.startswith("Type"):
            words, kind = " ".join(c.text for c in others[:-1]), others[-1].text
        else:
            self.notes.append(
                f"the row {label} has {len(others)} cells after its date where "
                "a description and a type were expected - the columns are not "
                "where the layout puts them"
            )
            record.refused = True
            self.record = record
            return
        record.description = tidy(
            f"{kind.removeprefix('Type')} {words.removeprefix('Description')}"
        )
        self.record = record
        self._labels(labels, record)

    def _labels(self, cells: list[_Cell], record: _Record) -> None:
        """Figures a label carries itself: the one place the column is named."""
        for cell in cells:
            found = _LABEL.fullmatch(cell.text)
            if found is None:
                continue
            rest = found.group(2).strip().rstrip(".").strip()
            if not rest or re.fullmatch(r"[a-z]+", rest):
                continue
            figure = _figure(rest)
            if figure is None:
                self.notes.append(
                    f"the label {cell.text!r} on the row dated {record.stated} "
                    "carries something that is neither blank nor a figure"
                )
                record.refused = True
                continue
            self._place(record, _COLUMNS[found.group(1)], figure)

    def _values(self, cells: list[_Cell], record: _Record) -> None:
        if self.ends is None:
            return
        ordered = sorted(self.ends.values())
        spacing = min(b - a for a, b in pairwise(ordered))
        for cell in cells:
            figure = _figure(cell.text)
            if figure is None:
                continue
            name, distance = min(
                ((name, abs(cell.end - end)) for name, end in self.ends.items()),
                key=lambda pair: pair[1],
            )
            if distance * 2 >= spacing:
                self.notes.append(
                    f"the figure {cell.text!r} on the row dated {record.stated} "
                    "ends too far from every heading to be attributed to Money "
                    "In, Money Out, or Balance - cannot be attributed, and is "
                    "not guessed at"
                )
                record.refused = True
                continue
            self._place(record, name, figure)

    def _place(self, record: _Record, column: str, figure: int) -> None:
        if column in record.figures:
            self.notes.append(
                f"the row dated {record.stated} has two figures under the "
                f"{column} column - refusing rather than picking one"
            )
            record.refused = True
            return
        record.figures[column] = figure

    # -- a finished row ------------------------------------------------------

    def close(self) -> None:
        record, self.record = self.record, None
        if record is None or record.refused:
            return
        label = f"dated {record.stated}"
        found = _ROW_START.fullmatch(record.stated)
        assert found is not None
        month = _month(found.group(2))
        if month is None:
            self.notes.append(
                f"the date {record.stated!r} names no month - a misread letter, "
                "not a day to guess at"
            )
            return
        year = int(found.group(3))
        try:
            day = date(year + 2000 if year < 100 else year, month, int(found.group(1)))
        except ValueError:
            self.notes.append(
                f"the row {label} states a date that does not exist - a misread "
                "digit, not a day to round to the nearest real one"
            )
            return
        if self.period is not None and not self.period[0] <= day <= self.period[1]:
            self.notes.append(
                f"the row {label} ({day.isoformat()}) is outside the "
                "statement's period - a misread date, and the year was read "
                "from the row itself"
            )
            return
        placed = record.figures
        if "in" in placed and "out" in placed:
            self.notes.append(
                f"the row {label} carries a figure in BOTH money columns, so it "
                "reads as a payment in and a payment out at once - refusing "
                "rather than picking one"
            )
            return
        for column in ("in", "out"):
            if column in placed and placed[column] < 0:
                self.notes.append(
                    f"the row {label} has a signed figure under Money "
                    f"{column.title()} - the column is the whole of the sign, "
                    "so a minus there is a misreading to refuse"
                )
                return
        if "in" in placed:
            amount = placed["in"]
        elif "out" in placed:
            amount = -placed["out"]
        else:
            self.notes.append(
                f"the row {label} carries no figure under Money In or Money "
                "Out - a row that moved nothing is a row the walk would "
                "silently lose"
            )
            return
        self.entries.append(
            (record.stated, day, amount, placed.get("balance"), record.description)
        )


def read_statement(lines: list[str]) -> StatementReading:
    """Read the document's text lines into a reading.

    Line order is page order. The summary and period come before the table and
    are read until its heading; the table is then read until the legend that
    ends it, and a heading that recurs on a later page starts it again.
    """
    reading = StatementReading()
    notes = reading.notes
    reader = _Reader(notes)
    for line in lines:
        reader.feed(line)
    reader.close()

    reading.transactions = [
        StatementRow(value_date=day, description=description, amount_minor=amount)
        for _, day, amount, _, description in reader.entries
    ]
    if not reader.heading_seen:
        notes.append(
            "the transaction table's heading could not be found - a statement "
            "whose columns have moved or been renamed reads as a statement "
            "with no transactions on it, which is the one failure that looks "
            "exactly like a quiet month"
        )
        return reading
    if reader.period is None:
        notes.append(
            "the statement's period could not be found, so it cannot be dated "
            "- guessing a date would file a whole history on the wrong days"
        )
    else:
        reading.statement_date = reader.period[1]
    _balances(reader, reading)
    _totals(reader, reading)
    return reading


def _balances(reader: _Reader, reading: StatementReading) -> None:
    notes = reading.notes
    if len(reader.balances) != 2:
        notes.append(
            f"the summary has {len(reader.balances)} Balance on lines where "
            "exactly two (the start and the end of the period) were expected - "
            "no way to say which balance the rows start from"
        )
        return
    (first_day, first), (second_day, second) = sorted(reader.balances)
    if first_day == second_day:
        notes.append(
            "both Balance on lines state the same date - no way to say which "
            "is the start of the period and which the end"
        )
        return
    reading.opening_balance_minor = first
    reading.closing_balance_minor = second
    running = first
    for stated, day, amount, printed, _ in reader.entries:
        running += amount
        if printed is not None and printed != running:
            notes.append(
                f"the running balance printed on the row dated {stated} "
                f"({day.isoformat()}) is {printed} minor units but the rows so "
                f"far reach {running} - {printed - running} minor units "
                "unexplained, which is a row missed, a row misread, or a "
                "figure read into the wrong column"
            )
            break


def _totals(reader: _Reader, reading: StatementReading) -> None:
    received = sum(row.amount_minor for row in reading.transactions if row.amount_minor > 0)
    paid = -sum(row.amount_minor for row in reading.transactions if row.amount_minor < 0)
    for name, stated, read in (
        ("Money In", reader.received, received),
        ("Money Out", reader.paid, paid),
    ):
        if stated is None:
            reading.notes.append(
                f"the summary does not state a {name} total - the statement's "
                "own account of the movement is what the rows are checked "
                "against, so without it nothing can be"
            )
        elif stated != read:
            reading.notes.append(
                f"the summary's {name} total is {stated} minor units but the "
                f"rows read as {read} - {stated - read} minor units "
                "unexplained, which is a row missed or read into the wrong "
                "column"
            )
