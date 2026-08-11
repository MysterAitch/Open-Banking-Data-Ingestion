"""Read a statement as the table it is, by position rather than by spacing.

Layout-mode text extraction reconstructs columns from whitespace, which
works while the columns are near each other and fails when they are not.
On a statement whose columns sit at the far edges of a wide page, the
reconstruction ran fields together - two amounts arriving as one token, an
amount fused to the description after it - and a parser reading those
lines would be guessing rather than reading.

The positions are in the document all along. Every word carries its own
coordinates and its own width, so a row is a set of words sharing a
baseline, a column is a set sharing a left edge, and a cell ends where a
measured band of empty space begins. None of that needs a guess about how
many spaces mean a new field, and coordinates cannot be fused the way
reconstructed text can.

Nothing here interprets a statement. It turns a page into rows of cells,
which is the thing a parser can then read without inventing anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Fragments whose baselines differ by less than this belong to one row.
#: Generous enough to survive the sub-point drift of a justified line, tight
#: enough not to swallow the row beneath.
ROW_TOLERANCE = 3.0

#: Left edges closer than this are the same column. Wider than a space,
#: narrower than the gap a table puts between columns.
COLUMN_TOLERANCE = 12.0

#: Empty space wider than this ends a cell. A space between words of one
#: description is a few points at statement type sizes; the band a table
#: leaves between columns is tens. Measured in points between one word's
#: right edge and the next word's left, never counted in space characters -
#: counting characters is the failure this module exists to replace.
CELL_GAP = 12.0


@dataclass(frozen=True)
class Fragment:
    """One run of text and where the page puts it."""

    text: str
    x: float
    y: float
    page: int
    #: Right edge. Defaults to the left edge, which reads as "width
    #: unknown" and makes every neighbour look far away - so a caller that
    #: cannot supply widths gets one cell per word rather than a wrong
    #: guess about which words belong together.
    x_end: float = 0.0

    def right(self) -> float:
        return max(self.x, self.x_end)


@dataclass
class Cell:
    """Text sharing a row and a column, in the order it was placed."""

    x: float
    text: str
    x_end: float = 0.0


@dataclass
class Row:
    """One line of a table, as cells rather than as a sentence."""

    y: float
    page: int
    cells: list[Cell] = field(default_factory=list)

    def texts(self) -> list[str]:
        return [cell.text for cell in self.cells]


def words_from(path: Path) -> list[Fragment]:
    """Every word on every page, with the position the page gives it.

    The geometry is read rather than inferred. Text extraction that
    rebuilds columns from whitespace works while columns sit near each
    other and fails when they do not - on a wide statement it fused two
    amounts into one token and glued an amount to the description beside
    it. A word's own coordinates cannot be fused with anything.

    Kept deliberately thin: everything that decides what the words MEAN
    lives below in pure functions, tested by supplying coordinates
    directly. This function is tested too, against a real file - untested
    it would be a claim rather than a fact, and it is the one place the
    module depends on a reader's account of a document.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - the dependency is declared
        return []
    found: list[Fragment] = []
    try:
        with pdfplumber.open(str(path)) as document:
            for number, page in enumerate(document.pages, start=1):
                for word in page.extract_words():
                    text = str(word.get("text", "")).strip()
                    if not text:
                        continue
                    found.append(
                        Fragment(
                            text=text,
                            x=float(word.get("x0", 0.0)),
                            # Measured from the top, so a smaller number is
                            # higher on the page - negated here so the rest
                            # of this module can treat larger as higher and
                            # match how a page is described.
                            y=-float(word.get("top", 0.0)),
                            page=number,
                            x_end=float(word.get("x1", 0.0)),
                        )
                    )
    except Exception:
        return []
    return found


def rows(
    path: Path,
    *,
    row_tolerance: float = ROW_TOLERANCE,
    cell_gap: float = CELL_GAP,
) -> list[Row]:
    """The page's text as rows of cells, top to bottom, left to right.

    Rows are grouped by baseline rather than by line break, so a cell
    placed far to the right of its row joins that row rather than becoming
    a line of its own - which is the whole problem with reading a wide
    table from reconstructed text.
    """
    return rows_from_words(
        words_from(path), row_tolerance=row_tolerance, cell_gap=cell_gap
    )


def rows_from_words(
    found: list[Fragment],
    *,
    row_tolerance: float = ROW_TOLERANCE,
    cell_gap: float = CELL_GAP,
) -> list[Row]:
    """Group placed words into rows by their baseline.

    The part worth testing: a cell a thousand points to the right of its
    row's first cell belongs to that row, and a row is a set of words
    sharing a line rather than a string with gaps in it.
    """
    if not found:
        return []

    ordered = sorted(found, key=lambda item: (item.page, -item.y, item.x))
    built: list[Row] = []
    for fragment in ordered:
        current = built[-1] if built else None
        if (
            current is not None
            and current.page == fragment.page
            and abs(current.y - fragment.y) <= row_tolerance
        ):
            current.cells.append(
                Cell(x=fragment.x, text=fragment.text, x_end=fragment.right())
            )
            continue
        built.append(
            Row(
                y=fragment.y,
                page=fragment.page,
                cells=[
                    Cell(x=fragment.x, text=fragment.text, x_end=fragment.right())
                ],
            )
        )
    for row in built:
        row.cells.sort(key=lambda cell: cell.x)
        row.cells = _merge_adjacent(row.cells, cell_gap=cell_gap)
    return built


def _merge_adjacent(cells: list[Cell], *, cell_gap: float) -> list[Cell]:
    """Join words separated by less than a column's worth of empty space.

    A description is several words and one cell. What separates it from
    the next column is a measured band of nothing, not a word boundary.
    """
    merged: list[Cell] = []
    for cell in cells:
        previous = merged[-1] if merged else None
        if previous is not None and cell.x - previous.x_end <= cell_gap:
            previous.text = f"{previous.text} {cell.text}".strip()
            previous.x_end = max(previous.x_end, cell.x_end)
            continue
        merged.append(Cell(x=cell.x, text=cell.text, x_end=cell.x_end))
    return merged


def column_edges(
    table: list[Row], *, column_tolerance: float = COLUMN_TOLERANCE
) -> list[float]:
    """The left edges the page actually uses, in order.

    Derived from where text IS rather than from where a reader guesses a
    column ought to begin, so a table whose columns sit at the far edges of
    the page yields the same answer as one whose columns are close
    together.

    A column is an edge that recurs on MOST rows. The second and third
    words of a description each start somewhere, and treating every
    starting point as a column would shred one description into several.
    Recurrence alone is not enough either: two descriptions can wrap at
    much the same place by chance, and on a short table that coincidence
    looks exactly like a column. A real column is present on nearly every
    row, so a majority is the discriminator, and it grows stricter as the
    table grows - which is the right direction, since a longer table gives
    coincidence more chances.

    Below two rows there is no recurrence to measure and every distinct
    edge is kept, which is the honest answer for a table too short to have
    structure.
    """
    seen: list[tuple[float, set[int]]] = []
    for index, row in enumerate(table):
        for cell in row.cells:
            for edge, rows_at in seen:
                if abs(cell.x - edge) <= column_tolerance:
                    rows_at.add(index)
                    break
            else:
                seen.append((cell.x, {index}))
    if len(table) < 2:
        return sorted(edge for edge, _ in seen)
    return sorted(edge for edge, rows_at in seen if len(rows_at) * 2 > len(table))


def covering_edges(
    table: list[Row], *, column_tolerance: float
) -> list[float]:
    """Column edges, widened until no row has two cells in one column.

    The majority rule that finds columns is a judgement about the table as
    a whole, and a judgement can be too strict: a column present on a
    minority of rows is discarded, and its cells then have nowhere to go.
    Left to itself the placement would fuse them into a neighbour, which
    is the very failure this module replaces - two fields arriving as one.

    A row holding two cells that want the same column is PROOF that the
    judgement missed a column, because a row's cells are separated by
    measured empty space. So the row's own evidence is admitted and the
    edge restored. This can only ever add columns, never merge them.
    """
    edges = column_edges(table, column_tolerance=column_tolerance)
    missing: list[float] = []
    for row in table:
        taken: set[int] = set()
        for cell in row.cells:
            index = _column_of(cell.x, edges, column_tolerance)
            if index is None or index in taken:
                missing.append(cell.x)
                continue
            taken.add(index)
    if not missing:
        return edges
    widened = list(edges)
    for x in sorted(missing):
        if not any(abs(x - edge) <= column_tolerance for edge in widened):
            widened.append(x)
    return sorted(widened)


def _column_of(
    x: float, edges: list[float], column_tolerance: float
) -> int | None:
    """The column a word at x belongs to: the nearest edge at or before it.

    None when the word starts before every known edge, which is not a
    placement but a question - answered by `covering_edges` admitting the
    edge rather than by rounding the word into the first column.
    """
    at_or_left = [
        position
        for position, edge in enumerate(edges)
        if edge <= x + column_tolerance
    ]
    return at_or_left[-1] if at_or_left else None


def aligned(
    table: list[Row], *, column_tolerance: float = COLUMN_TOLERANCE
) -> list[list[str]]:
    """Rows as equal-length lists of cells, one per column the page uses.

    A missing cell is an empty string rather than an absent entry, because
    a debit column that is blank on a credit row is a FACT about that row -
    the blank is which side of the ledger it fell on, and collapsing it
    loses exactly the thing a reader needs.

    A word belongs to the nearest column starting at or before it, because
    that is what a column IS: text set from its left edge and running
    rightwards. Nearest-in-either-direction would pull the tail of a long
    description into the column beyond it.
    """
    edges = covering_edges(table, column_tolerance=column_tolerance)
    if not edges:
        return []
    out: list[list[str]] = []
    for row in table:
        cells = [""] * len(edges)
        for cell in row.cells:
            index = _column_of(cell.x, edges, column_tolerance)
            # `covering_edges` guarantees an edge at or before every cell,
            # so the fallback is unreachable rather than a rounding rule.
            cells[index if index is not None else 0] = (
                f"{cells[index if index is not None else 0]} {cell.text}".strip()
            )
        out.append(cells)
    return out
