"""Invented credit union "all accounts" documents, with their answers.

A document here is a list of pipe-separated lines, one cell per column of the
real layout (`REAL_X`), assembled from sections the way the issuer prints
them: each account's pages open with a page marker, then an account name box,
then the table, and the numbering restarts at "Page 1" for the next account.

Every section is built from a list of moves and an opening balance, and its
printed balances are COMPUTED from them - so a document's known answer is the
list of moves that went in, and a test that wants a section a penny out
corrects one figure on purpose rather than hand-typing a whole table.

`fused=True` draws the labels the way the positional word grid can deliver
them, with no space inside ("AccountName", "OpeningBalance", "Page1of1"):
the masked shape dumps showed those labels in two renderings, and the readers
consume the grid.
"""

from __future__ import annotations

from dataclasses import dataclass

from test_statement_columns import build_positioned_pages

#: Where each of the ten columns sits on the page: a margin the dates sit in,
#: then the nine headings. Far apart on purpose, as on the real page.
REAL_X = (20.0, 60.0, 200.0, 330.0, 560.0, 760.0, 940.0, 1120.0, 1280.0, 1480.0)
COLUMNS = len(REAL_X)

HEADER = "|Date|Source|Payee||Debit|Credit|Interest|Transaction|Balance"
CONTINUATION = "|||||Amount|Amount|Amount|Total"

FIRM = "Example Credit Union Limited"


@dataclass(frozen=True)
class Move:
    """One table row: a day, how the money moved, and a signed amount.

    Positive is money INTO the account's balance in the store's convention,
    so for a loan a repayment is positive and interest charged is negative.
    """

    day: str
    source: str
    minor: int
    payee: str = ""


def _money(minor: int) -> str:
    return f"£{abs(minor) / 100:,.2f}"


def _plain(minor: int) -> str:
    return f"{abs(minor) / 100:,.2f}"


def _label(text: str, fused: bool) -> str:
    return text.replace(" ", "") if fused else text


def _row(move: Move, shown_balance: int) -> str:
    cells = [""] * COLUMNS
    cells[0] = move.day
    cells[2] = move.source
    cells[3] = move.payee
    cells[4] = "£0.00"
    cells[5 if move.minor < 0 else 6] = _money(move.minor)
    cells[8] = _plain(move.minor)
    cells[9] = _plain(shown_balance)
    return "|".join(cells)


def closing_of(opening_minor: int, moves: list[Move]) -> int:
    """The balance after the moves, in the store's convention."""
    return opening_minor + sum(move.minor for move in moves)


def section(
    label: str,
    opening_minor: int,
    moves: list[Move],
    *,
    period: str = "01/05/2025 to 31/05/2025",
    loan: bool = False,
    pages: int = 1,
    fused: bool = False,
    printed_closing_minor: int | None = None,
    header_above_marker: bool = False,
) -> list[str]:
    """One account's pages.

    `opening_minor` is in the store's convention (a loan's is negative, being
    owed); the figures PRINTED are what the issuer prints, which for a loan is
    the amount outstanding as a positive number. `printed_closing_minor`
    overrides the closing figure - the way a section is made a penny out.

    `header_above_marker` lays the page out as the issuer's statement really
    does: the period and the date of issue are printed in the page header
    ABOVE the page-number box, so they precede the page marker in the grid.
    The default keeps the earlier layout, in which the marker opens the page.
    """
    sign = -1 if loan else 1
    closing = closing_of(opening_minor, moves)
    printed_closing = closing if printed_closing_minor is None else printed_closing_minor
    per_page = -(-len(moves) // pages) if moves else 0
    lines: list[str] = []
    balance = opening_minor
    gap = "" if fused else " "
    stated_period = period.replace(" to ", f"{gap}to{gap}")
    for page in range(1, pages + 1):
        chunk = moves[(page - 1) * per_page : page * per_page] if per_page else []
        marker = f"|||||||Page{gap}{page}{gap}of{gap}{pages}"
        period_line = f"|Period{gap}{stated_period}"
        if header_above_marker:
            lines += [
                FIRM,
                "|Private & Confidential||||||Member Statement",
                f"|||||||{period_line.lstrip('|')}",
                "|||||||Date of Issue|06/10/2026",
                marker,
                f"||{_label('Account Name', fused)}|||||{_label('Opening Balance', fused)}",
                f"|{label}",
                f"||||||||{_money(sign * opening_minor)}",
                HEADER,
                CONTINUATION,
            ]
        else:
            lines += [
                FIRM,
                marker,
                f"||{_label('Account Name', fused)}|||||{_label('Opening Balance', fused)}",
                f"|{label}",
                f"||||||||{_money(sign * opening_minor)}",
                period_line,
                HEADER,
                CONTINUATION,
            ]
        for move in chunk:
            balance += move.minor
            lines.append(_row(move, sign * balance))
    if loan:
        owed = sign * printed_closing
        lines += [
            f"||{_label('Interest Due', fused)}||||{_label('Closing Loan Position', fused)} *"
            f"||{_label('Closing Balance', fused)}",
            f"||£12.00||||{_money(owed + 1200)}||{_money(owed)}",
            "||* Closing Loan Position = Loan Balance + Closing Interest on 31/05/2025",
        ]
    else:
        lines += [
            f"||||||||{_label('Closing Balance', fused)}",
            f"||||||||{_money(printed_closing)}",
        ]
    return lines


def document(*sections: list[str]) -> list[str]:
    """Sections one after another, as the issuer's export prints them."""
    return [line for lines in sections for line in lines]


def grid(lines: list[str]) -> list[list[str]]:
    """The lines as the column reader hands them over: equal rows, blanks kept."""
    return [
        [cell.strip() for cell in line.split("|")]
        + [""] * (COLUMNS - len(line.split("|")))
        for line in lines
    ]


def pages(lines: list[str]) -> list[int]:
    """Which page each line is printed on, as the word grid knows it: every
    page of an invented document opens with the firm's name."""
    found: list[int] = []
    page = 0
    for line in lines:
        if line == FIRM:
            page += 1
        found.append(page)
    return found


def pdf(lines: list[str], *, step: float = 20.0, paged: bool = False) -> bytes:
    """The lines as a real wide page, each cell at its column's point.

    `step` is the gap between lines: a nine-account document has well over a
    hundred, and at the default spacing the tail would be drawn below the page.

    `paged` draws each page of the document (see `pages`) on a page of its own,
    as the issuer's file is - so where a page begins is known to the reader.
    """
    sheets: list[list[tuple[float, float, str]]] = []
    rows_on_sheet = 0
    for line, page in zip(lines, pages(lines), strict=True):
        if not sheets or (paged and page != len(sheets)):
            sheets.append([])
            rows_on_sheet = 0
        for column, cell in enumerate(line.split("|")):
            if cell.strip():
                sheets[-1].append((REAL_X[column], 780.0 - rows_on_sheet * step, cell.strip()))
        rows_on_sheet += 1
    return build_positioned_pages(sheets)


# ---------------------------------------------------------------------------
# Nine accounts, with the answers decided before the document existed.
# ---------------------------------------------------------------------------

#: (label, opening in store convention, moves, is a loan). Seven savings
#: accounts and two loans, each with its own opening balance, so that a reader
#: which carried the first account's opening balance into the second would
#: walk to the wrong closing figure.
NINE: list[tuple[str, int, list[Move], bool]] = [
    ("Regular Saver", 80000, [
        Move("04/05/2025", "DD Lodgement", 2500),
        Move("09/05/2025", "Div - Regular Saver", 249),
        Move("17/05/2025", "Internet Transfer", -30000, "J SMITH"),
    ], False),
    ("Christmas Club", 15000, [Move("05/05/2025", "DD Lodgement", 1000)], False),
    ("Holiday Fund", 0, [Move("06/05/2025", "Internet Transfer", 5000)], False),
    ("Rainy Day", 22050, [], False),
    ("Junior Saver", 1000, [
        Move("07/05/2025", "DD Lodgement", 500),
        Move("21/05/2025", "DD Lodgement", 500),
    ], False),
    ("Share Account", 500, [], False),
    ("Home Improvement", 30000, [Move("12/05/2025", "Internet Transfer", -1500)], False),
    ("Personal -9.50%", -50000, [
        Move("12/05/2025", "tx", 15500),
        Move("26/05/2025", "tx", 249),
    ], True),
    ("Car -7.25%", -120000, [
        Move("03/05/2025", "tx", 10000),
        Move("03/05/2025", "tx", 10000),
        Move("31/05/2025", "tx", 10000),
    ], True),
]

#: Row counts and closing balances (store convention) the nine above must read as.
NINE_ROWS = [3, 1, 1, 0, 2, 0, 1, 2, 3]
NINE_CLOSINGS = [52749,16000, 5000, 22050, 2000, 500, 28500, -34251, -90000]


def nine_accounts(*, fused: bool = False, penny_out: int | None = None) -> list[str]:
    """The nine-account document; `penny_out` prints that section's closing 1p high."""
    parts = []
    for index, (label, opening, moves, loan) in enumerate(NINE):
        wrong = (
            closing_of(opening, moves) + 1
            if penny_out is not None and index == penny_out
            else None
        )
        parts.append(
            section(
                label, opening, moves, loan=loan, fused=fused, printed_closing_minor=wrong
            )
        )
    return document(*parts)
