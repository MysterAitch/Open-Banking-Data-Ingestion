"""Credit union statements, read from the table's own geometry.

Written from a real statement inspected through the masking surface, so no
figure had to be disclosed. It is the first format here that could not be
read as lines at all, and the first that is not a credit card.

The page is WIDE and its columns sit hundreds of points apart. Whitespace
reconstruction fuses fields at that distance - two amounts arriving as one
token, an amount glued to the text beside it - so this parser is handed
ROWS OF CELLS by `statement_columns` and never sees a line of text. That is
why it takes a grid rather than a list of strings: the thing it reads is a
table, and the table is in the page's coordinates rather than in its
spacing.

The table's header is TWO LINES. The first names Date, Source, Payee,
Debit, Credit, Interest, Transaction and Balance; the second puts "Amount"
under each of Debit, Credit and Interest, and "Total" under Transaction.
Read one line at a time, "Credit" heads a column that does not exist and
"Transaction" heads another, which places a row's transaction total exactly
where its credit is expected - and every debit row would then acquire a
credit of the same size.

TWO KINDS OF ACCOUNT share the layout, and they do not share their
arithmetic. A savings balance rises with a credit and falls with a debit. A
loan's Balance column is what is OUTSTANDING, so a credit - a repayment -
makes it smaller. The loan is a liability and is negated at this boundary,
the way the Santander card already is, so that nothing downstream has to
know which kind it is holding: a repayment then reads as money moving the
balance toward zero, and the same row rule serves both kinds.

Which kind a section is comes from the account's own NAME, because a loan
carries its rate inside it ("Personal -9.50%") and a savings account does
not ("Regular Saver"). The rate is extracted as a term of the account, and
the STEM - the name without the rate - is what identifies the account
between statements, since the rate changes and the stem does not. Getting
the kind wrong is not a silent error: the balance walk cannot reach the
stated closing balance under the wrong sign convention, so the statement is
refused rather than stored inside out.

Three things are deliberately NOT trusted:

  the sign               comes from WHICH COLUMN a figure sits in, never
                         from a symbol: this format has no credit marker
                         and no minus sign anywhere
  a row's date           is its own, fully qualified, and a row that has
                         lost it is refused rather than dated from the row
                         above it
  Transaction Total      is informational and nothing here derives from
                         it. It usually equals the credit plus the
                         interest and there are real rows where it does
                         not, so a parser that checked the relation would
                         refuse honest statements

A DOCUMENT MAY COVER MANY ACCOUNTS. The issuer's "all accounts" export
prints each account's pages one after another, the page numbering restarting
at "Page 1" for each. `read_document` reads such a document one SECTION per
account, each with its own label, rows and balances, and each is judged by
the same arithmetic a single statement is (the gate lives with the parser
class, which holds the one copy of it). Where the sections cannot be told
apart with confidence the WHOLE document is refused: rows filed under the
wrong account still balance on their own, so nothing downstream could notice.

LABELS ARE MATCHED WITHOUT REGARD TO SPACING. The reader consumes the
positional word grid, and there a label can arrive as one word with no space
inside ("AccountName", "OpeningBalance", "Page1of1") while the text rendering
of the same page shows it spaced. A rule that insisted on the space would
read the page in one rendering and refuse it in the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..plural import plural
from .base import ParseError
from .statement_reading import StatementReading, StatementRow

#: Column names to the field each one carries, written without spaces because
#: names are compared without them (see `_normalised`). Both the first header
#: line alone and the two lines merged appear here, because a continuation
#: word that failed to extract is not a reason to refuse a statement - the
#: first line already names every column unambiguously.
_FIELDS = {
    "date": "date",
    "source": "source",
    "payee": "payee",
    "debit": "debit",
    "debitamount": "debit",
    "credit": "credit",
    "creditamount": "credit",
    "interest": "interest",
    "interestamount": "interest",
    "transaction": "total",
    "transactiontotal": "total",
    "balance": "balance",
}

#: Enough of a header to read a row at all: when a movement happened, and
#: which of the two money columns it fell in. A row's direction is carried
#: by nothing else, so a table without both money columns is not a table
#: this parser can read.
_REQUIRED = frozenset({"date", "debit", "credit"})

#: The words the second header line adds beneath the first. A row made of
#: nothing else is a continuation of the header above it rather than a
#: transaction with no date.
_CONTINUATIONS = frozenset({"amount", "total"})

_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

#: An amount, with or without the currency symbol the money columns carry.
#: The symbol is matched as "one character that is not a digit or a space"
#: rather than as a pound sign: what a PDF's text layer yields for it
#: depends on the document's encoding, and a parser that insisted on the
#: character would read half a statement on a file that encodes it
#: differently. The symbol is never load-bearing here - the column decides
#: the direction - so accepting whatever arrives costs nothing.
#:
#: Up to TWO characters, because a pound sign decoded one byte at a time
#: arrives as two, and a balance written that way was not a figure at all.
_AMOUNT = re.compile(r"-?\s*[^\s\d]{0,2}\s*-?[\d,]+\.\d{2}")

#: A currency symbol, which is what separates a stray zero printed in the
#: payee zone from a payee that merely contains digits ("Ref 12.50").
_SYMBOL = re.compile(r"[^\w\s.,-]")

#: The fields whose cells are text. A figure that falls in one of them is
#: furniture and is no part of what the row says.
_TEXT_FIELDS = frozenset({"source", "payee"})

#: What a statement's balance labels are, by their normalised text. Their
#: figures are printed BENEATH them, so a label is recognised by itself and
#: its figure is looked for on the rows that follow.
_BALANCE_LABELS = {"openingbalance": "opening", "closingbalance": "closing"}

#: How many rows beneath a balance label its figure may be. The opening
#: balance's figure sits beneath the account's name, which sits beneath the
#: label; further than that is a figure belonging to something else.
_FIGURE_REACH = 3

#: Every phrase below tolerates NO space between its words, for the reason
#: given in the module's account of labels.
_PERIOD = re.compile(r"Period\s*(\d{2})/(\d{2})/(\d{4})\s*to\s*(\d{2})/(\d{2})/(\d{4})")
_ISSUED = re.compile(r"Date\s*of\s*Issue\s*(\d{2})/(\d{2})/(\d{4})")
_OPENING = re.compile(rf"Opening\s*Balance.*?({_AMOUNT.pattern})")
_CLOSING = re.compile(rf"Closing\s*Balance.*?({_AMOUNT.pattern})")
_PAGE = re.compile(r"Page\s*(\d+)\s*of\s*(\d+)")

#: The foot label only a loan section prints, without spaces (see `_normalised`).
_LOAN_FOOT = "closingloanposition"

#: The label a closing balance is read from, as a person reads it: the one label this reader
#: takes a closing balance under, which the shape page names so a section that found none can
#: be told apart from one that printed it under another label.
_CLOSING_LABEL = "Closing Balance"

#: The label whose value - on the row BENEATH it, in the same column - is
#: the account this section covers.
_ACCOUNT_LABEL = "accountname"

#: A rate written into an account's name, which is how this issuer states a
#: loan's terms. Anchored at the end because the name is what precedes it.
_RATE_IN_NAME = re.compile(r"-?\s*([\d.]+)\s*%\s*$")


def _minor(text: str) -> int:
    """An amount in minor units, whatever decoration it arrived with."""
    return round(float(re.sub(r"[^\d.-]", "", text)) * 100)


def _normalised(text: str) -> str:
    """A label with its case and every space removed, for comparing labels.

    Spaces go entirely rather than being collapsed: the word grid can deliver
    "Opening Balance" as one word, and the two spellings must be one label.
    """
    return re.sub(r"\s+", "", text).casefold()


def _amount(text: str) -> int | None:
    """The figure in a money cell, or None when the cell holds no figure.

    A blank is the commonest cell in this table - the interest column is
    empty on every savings row, and the payee column on most rows - and a
    dash is used for "nothing here". Neither is a zero, and reading either
    as one would put a row into the walk that the statement never put
    there.
    """
    return _minor(text) if _AMOUNT.fullmatch(text.strip()) else None


def account_stem(name: str) -> str:
    """The part of an account's name that survives to the next statement.

    A loan is named for its rate, and the rate changes; matching a stored
    account on the whole name would therefore lose it the month the rate
    moved, which is precisely the month somebody wants to look.
    """
    return _RATE_IN_NAME.sub("", name).strip().rstrip("-").strip()


def _rate_in(name: str) -> float | None:
    """The rate an account's name states, if it states one.

    Its presence is also what tells a loan from a savings account: only the
    lending side of this issuer writes terms into a name.
    """
    found = _RATE_IN_NAME.search(name)
    return float(found.group(1)) if found else None


def _page_marks(grid: list[list[str]]) -> list[tuple[int, int, int]]:
    """(row, page, of how many) for every page marker in a grid."""
    return [
        (position, int(mark.group(1)), int(mark.group(2)))
        for position, row in enumerate(grid)
        if (mark := _PAGE.search(" ".join(cell.strip() for cell in row if cell.strip())))
    ]


def sections(grid: list[list[str]]) -> list[list[list[str]]]:
    """One grid per account section of an export covering several accounts.

    The page numbering restarts at each account: a section spanning two
    pages runs "Page 1 of 2", "Page 2 of 2", and the next account begins
    again at "Page 1 of 1". That restart is the only delimiter the document
    offers that cannot be confused with the contents of a table.

    A boundary is only taken where a completed section (its last page) is
    followed by a restart, so a statement covering one account is never cut
    up by its own footer, and trailing furniture stays where it is.

    Nothing here reads a statement. It exists so that a multi-account
    export becomes a loop over sections rather than a rewrite of the reader
    - and so the single-account case stays exactly what it is today: one
    section, read whole.
    """
    marks = _page_marks(grid)
    # BEFORE a restart, not after a completion. The page number is printed
    # in the page HEADER, above the account name box, so a marker opens the
    # page it numbers - and a section therefore begins AT its "Page 1 of N"
    # rather than after the previous section's last marker. Cut the other
    # way and each account's first page is handed to the account before it,
    # which then reconciles for neither.
    #
    # Keyed on the restart alone rather than on a completion followed by a
    # restart, so a section whose final page is missing, or whose total is
    # misprinted, still begins where it says it begins.
    restarts = [position for position, page, _total in marks if page == 1]
    cuts = restarts[1:]
    if not cuts:
        return [grid]
    split = [grid[start:end] for start, end in zip([0, *cuts], [*cuts, len(grid)], strict=True)]
    return [part for part in split if part]


def _owner(index: int, columns: list[int]) -> int | None:
    """The header a cell belongs to: the nearest one at or before it.

    The same rule the geometry uses to place a word, and it is needed again
    here because the two are not the same reading. Amounts are set flush
    right in this table while their headers are set flush left, so a money
    column can be discovered a little to the right of the word that names
    it. Nearest-in-either-direction would pull such a cell into the column
    beyond, which for the debit column means reading a payment out as a
    payment in.
    """
    at_or_left = [column for column in columns if column <= index]
    return at_or_left[-1] if at_or_left else None


def _named_columns(row: list[str]) -> dict[int, str]:
    """The cells of a row that are column names, by position."""
    return {index: cell.strip() for index, cell in enumerate(row) if _normalised(cell) in _FIELDS}


def _fields_of(names: dict[int, str]) -> dict[int, str]:
    """Which field each named column carries, dropping any name unknown."""
    return {
        index: _FIELDS[key]
        for index, text in names.items()
        if (key := _normalised(text)) in _FIELDS
    }


def _is_continuation(row: list[str]) -> bool:
    """Whether a row is the second line of a header rather than a row."""
    words = [cell.strip() for cell in row if cell.strip()]
    return bool(words) and all(_normalised(word) in _CONTINUATIONS for word in words)


def _merged(names: dict[int, str], continuation: list[str]) -> dict[int, str]:
    """The header's two lines as one name per column."""
    columns = sorted(names)
    merged = dict(names)
    for index, cell in enumerate(continuation):
        text = cell.strip()
        if not text:
            continue
        owner = _owner(index, columns)
        if owner is not None:
            merged[owner] = f"{merged[owner]} {text}"
    return merged


def _cells(row: list[str], fields: dict[int, str]) -> dict[str, str]:
    """A row's cells by the field each one falls under.

    A DATE set left of every heading belongs to the date column: the real
    statements start each date a little before the word "Date", in a column
    that no heading names, and dropping it left every row undated. Only a
    date is rescued there - anything else in the margin is not a row's own.

    A figure with a currency symbol in a text field is a stray zero the
    statement prints in the payee zone, and is left out of the description.
    """
    columns = sorted(fields)
    found: dict[str, str] = {}
    for index, cell in enumerate(row):
        text = cell.strip()
        if not text:
            continue
        owner = _owner(index, columns)
        if owner is None:
            if _DATE.fullmatch(text) and "date" in fields.values():
                found["date"] = f"{found.get('date', '')} {text}".strip()
            continue
        field = fields[owner]
        if field in _TEXT_FIELDS and _AMOUNT.fullmatch(text) and _SYMBOL.search(text):
            continue
        found[field] = f"{found.get(field, '')} {text}".strip()
    return found


def _figures(row: list[str]) -> dict[int, int]:
    """A row's amounts by position, when the row holds nothing else.

    A row of figures under a row of labels is nothing but figures; a row
    that also carries text is a transaction or a heading and is not this.
    """
    cells = {index: cell.strip() for index, cell in enumerate(row) if cell.strip()}
    found = {index: minor for index, text in cells.items() if (minor := _amount(text)) is not None}
    return found if cells and len(found) == len(cells) else {}


def _balance_labels(row: list[str]) -> dict[int, str] | None:
    """The labels of a row that names a balance, or None if it names none.

    Every non-empty cell is returned, not only the balance labels, because a
    figure beneath the row belongs to the nearest label at or left of it and
    that label may be "Interest Due" rather than a balance.
    """
    cells = {index: _normalised(cell) for index, cell in enumerate(row) if cell.strip()}
    if not any(text in _BALANCE_LABELS for text in cells.values()):
        return None
    return cells


def _nearest_text(row: list[str], column: int) -> str:
    """The text cell of a row nearest a column, within a few cells of it.

    The account's name is set beneath its label but not always in the same
    column: on the real statements it starts one cell LEFT of the label.
    """
    near = [
        (abs(index - column), index, cell.strip())
        for index, cell in enumerate(row)
        if cell.strip() and abs(index - column) <= 3
    ]
    return min(near)[2] if near else ""


def _assign_balances(
    reading: StatementReading, labels: dict[int, str], figures: dict[int, int]
) -> None:
    """File each figure beneath a row of labels under the label it belongs to.

    The nearest label at or left of a figure owns it, the way a heading owns
    a column. A figure with no label to its left, or two figures for one
    label, cannot be placed with confidence - and an opening or closing
    balance taken from the wrong one would be what the rows are checked
    against - so the statement is refused instead.
    """
    claimed: dict[int, int] = {}
    for index, minor in sorted(figures.items()):
        owner = max((label for label in labels if label <= index), default=None)
        if owner is None or owner in claimed:
            reading.notes.append(
                "a figure beneath a row of balance labels cannot be matched to "
                "exactly one label - refusing rather than taking a balance from "
                "the wrong one"
            )
            return
        claimed[owner] = minor
    for owner, minor in claimed.items():
        kind = _BALANCE_LABELS.get(labels[owner])
        if kind == "opening" and reading.opening_balance_minor is None:
            # First page only, for the reason given where the same-row
            # opening balance is read.
            reading.opening_balance_minor = minor
        elif kind == "closing":
            reading.closing_balance_minor = minor
            reading.closing_label = _CLOSING_LABEL


def _read_row(reading: StatementReading, cells: dict[str, str]) -> None:
    """Add one table row to the reading, or say why it cannot be added."""
    stated = cells.get("date", "")
    debit = _amount(cells.get("debit", ""))
    credit = _amount(cells.get("credit", ""))

    if debit is not None and credit is not None:
        reading.notes.append(
            f"the row dated {stated!r} carries a figure in BOTH money columns, "
            "so it reads as a payment in and a payment out at once - refusing "
            "rather than picking one"
        )
        return
    if debit is not None:
        # The column is the whole of the sign. A figure in the debit column
        # is money leaving whatever it looks like, which is why the
        # magnitude is taken rather than the value: a stray character read
        # as a minus cannot flip a payment into a receipt.
        amount = -abs(debit)
    elif credit is not None:
        amount = abs(credit)
    else:
        # No movement: a balance line, an account name, or text that
        # wrapped onto a row of its own. None of them belongs in the walk.
        return

    stamp = _DATE.fullmatch(stated)
    if stamp is None:
        reading.notes.append(
            "a row carrying an amount has no date of its own - a statement row "
            "is never dated from the row above it, and a date guessed here "
            "would file a transaction on a day it did not happen"
        )
        return
    try:
        value_date = date(int(stamp.group(3)), int(stamp.group(2)), int(stamp.group(1)))
    except ValueError:
        reading.notes.append(
            f"the row dated {stated} states a date that does not exist - a "
            "misread digit, not a day to round to the nearest real one"
        )
        return

    reading.transactions.append(
        StatementRow(
            value_date=value_date,
            # Both text columns, because either can be the whole of what a
            # row says: the source is how the money moved ("DD Lodgement",
            # "Internet Transfer") and is present on every row, while the
            # payee names a person and is empty on most.
            description=" ".join(
                part for part in (cells.get("source", ""), cells.get("payee", "")) if part
            ),
            amount_minor=amount,
        )
    )


def read_statement(grid: list[list[str]]) -> StatementReading:
    """Read ONE account section's rows of cells into a reading.

    The grid comes from `statement_columns`, one list of cells per row of
    the page with the blanks kept - and the blanks are the point, since
    which of the two money columns a figure fell in is the only thing that
    says which way money moved.

    A section may run to several pages. Each page reprints the account
    name, the opening balance and the column header, and a continuation
    page may carry no transactions at all - so the opening balance is taken
    from the first page that states it and never re-read, and a page with
    nothing on it is an ordinary page rather than a fault.
    """
    parts = sections(grid)
    if len(parts) > 1:
        # One statement is read into one account. A document of several
        # accounts read whole would take the first account's name, its
        # opening balance and the LAST account's closing balance, and file
        # every row under one account. `read_document` is the door that
        # reads such a document one account at a time.
        refused = StatementReading()
        refused.notes.append(
            f"this statement covers {len(parts)} accounts (its page numbering "
            f"restarts {plural(len(parts) - 1, 'time')}) and so cannot be assigned to "
            "one account - refusing rather than filing every account's rows "
            "under one"
        )
        return refused
    return _read_one(grid)[0]


def _read_one(grid: list[list[str]]) -> tuple[StatementReading, list[str]]:
    """One account's grid, read, with every account name it printed.

    The names are returned beside the reading because the section reader
    needs them to decide whether a boundary was found, and they are only
    known while the rows are being walked.
    """
    reading = StatementReading()
    fields: dict[int, str] = {}
    issued: date | None = None
    account_name = ""
    names_seen: list[str] = []
    name_column: int | None = None
    # A section's last row is its closing balance; anything below it is a
    # summary of what has already been counted.
    below_the_table = False
    skip_next = False
    prints_loan_position = False
    # The labels of a balance row whose figures are still awaited, and how
    # many rows are left to find them in.
    awaiting: dict[int, str] | None = None
    reach = 0

    for position, row in enumerate(grid):
        if skip_next:
            skip_next = False
            continue

        if name_column is not None:
            name = _nearest_text(row, name_column)
            if name:
                if name not in names_seen:
                    names_seen.append(name)
                account_name = account_name or name
            name_column = None
        label = next(
            (index for index, cell in enumerate(row) if _normalised(cell) == _ACCOUNT_LABEL),
            None,
        )
        if label is not None:
            # The value is beneath the label rather than beside it, so the
            # column is remembered and the next row answers for it.
            name_column = label

        joined = " ".join(cell.strip() for cell in row if cell.strip())
        if _LOAN_FOOT in _normalised(joined):
            prints_loan_position = True

        period = _PERIOD.search(joined)
        if period:
            reading.period_start = date(
                int(period.group(3)), int(period.group(2)), int(period.group(1))
            )
            reading.statement_date = date(
                int(period.group(6)), int(period.group(5)), int(period.group(4))
            )
            continue
        issue = _ISSUED.search(joined)
        if issue:
            issued = date(int(issue.group(3)), int(issue.group(2)), int(issue.group(1)))
        opening = _OPENING.search(joined)
        if opening:
            # First page only. Every continuation page repeats it, and a
            # repeat re-read as a fresh figure would let page two silently
            # decide what page one opened with.
            if reading.opening_balance_minor is None:
                reading.opening_balance_minor = _minor(opening.group(1))
            continue
        closing = _CLOSING.search(joined)
        if closing:
            reading.closing_balance_minor = _minor(closing.group(1))
            reading.closing_label = _CLOSING_LABEL
            below_the_table = True
            continue

        if awaiting is not None:
            figures = _figures(row)
            if figures:
                _assign_balances(reading, awaiting, figures)
                awaiting = None
                continue
            reach -= 1
            if reach == 0:
                awaiting = None
        labels = _balance_labels(row)
        if labels is not None:
            # The figures are printed beneath the labels, so the table is
            # over at a closing label whether or not its figure is found.
            awaiting, reach = labels, _FIGURE_REACH
            if "closingbalance" in labels.values():
                below_the_table = True
            continue

        names = _named_columns(row)
        if set(_fields_of(names).values()) >= _REQUIRED:
            following = grid[position + 1] if position + 1 < len(grid) else []
            if _is_continuation(following):
                names = _merged(names, following)
                skip_next = True
            fields = _fields_of(names)
            # The header reprints at the top of each page, and the table it
            # heads carries on from the one before.
            below_the_table = False
            continue

        if fields and not below_the_table:
            _read_row(reading, _cells(row, fields))

    if len(names_seen) > 1:
        reading.notes.append(
            "this statement names more than one account "
            f"({', '.join(sorted(names_seen))}) and so cannot be assigned to "
            "one account"
        )
    reading.account_name = account_name
    rate = _rate_in(account_name)
    if rate is not None and prints_loan_position:
        reading.account_kind, reading.account_kind_basis = (
            "loan", "the rate in its name and the Closing Loan Position line"
        )
    elif rate is not None:
        reading.account_kind, reading.account_kind_basis = "loan", "the rate in its name"
    elif prints_loan_position:
        reading.account_kind, reading.account_kind_basis = (
            "loan", "the Closing Loan Position line"
        )
    else:
        reading.account_kind, reading.account_kind_basis = (
            "saver", "no rate in the name and no Closing Loan Position line"
        )
    if rate is not None or prints_loan_position:
        # A loan, which states its balance as what is OWED. Held as the negative position it
        # is, so a repayment reads as money moving the balance toward zero and nothing
        # downstream needs to know this account is a liability. A loan is known by the rate in
        # its name OR by the "Closing Loan Position" its foot prints and a saver's never does:
        # a loan whose name carries no rate was read as a saver, its balances the wrong way
        # round for its own rows, and the section refused for rows that do not carry the
        # balances.
        if rate is not None:
            reading.rates[account_stem(account_name).casefold()] = rate
        if reading.opening_balance_minor is not None:
            reading.opening_balance_minor = -reading.opening_balance_minor
        if reading.closing_balance_minor is not None:
            reading.closing_balance_minor = -reading.closing_balance_minor

    reading.produced = issued
    if reading.statement_date is None:
        # The period is the statement's own account of what it covers; the
        # issue date is when it was printed, which is close enough to date
        # the document by and never used to date a row.
        reading.statement_date = issued
    if not fields:
        reading.notes.append(
            "the transaction table's header could not be found - a statement "
            "whose columns have moved or been renamed reads as a statement "
            "with no transactions on it, which is the one failure that looks "
            "exactly like a quiet month"
        )
    return reading, names_seen


@dataclass(frozen=True)
class StatementSection:
    """One account of a document that covers several.

    `label` is what the document itself calls the account, as printed, and is
    for showing a person. `key` is what identifies the account BETWEEN
    documents, derived from the label by `section_key`.
    """

    key: str
    label: str
    reading: StatementReading


def section_key(label: str) -> str:
    """What names an account across documents, from the label it is printed under.

    The label with a loan's rate removed (a rate changes and the account does
    not), then reduced to its letters and digits in lower case, so a change of
    case, spacing, or punctuation between two exports does not make one
    account two.
    """
    return re.sub(r"[\W_]+", "", account_stem(label).casefold())


def _numbering_is_whole(marks: list[tuple[int, int, int]]) -> bool:
    """Whether a section's page markers are one complete, in-order run.

    "Page 1 of 2" then "Page 2 of 2". A section that begins at a restart but
    does not finish its own count has either lost a page or been cut in the
    wrong place, and in both cases what lies beyond it is not known.
    """
    if not marks:
        return False
    total = marks[0][2]
    return all(of == total for _, _, of in marks) and [page for _, page, _ in marks] == list(
        range(1, total + 1)
    )


def read_heading(grid: list[list[str]]) -> str:
    """The account label a document of ONE account prints, or "" where it prints none, prints
    several, or covers several accounts (`read_document` names those).

    A year in which only one of an owner's accounts was open yields a document that is read
    whole, so nothing else names the account it is for: its label is the one fact that does,
    and the same label printed in earlier documents is what an earlier assignment was made on.
    """
    parts = sections(grid)
    if len(parts) != 1:
        return ""
    names = _read_one(parts[0])[1]
    return names[0] if names and len({section_key(name) for name in names}) == 1 else ""


def read_document(grid: list[list[str]]) -> list[StatementSection] | None:
    """The document's accounts, one section each, or None for a single account.

    HOW A SECTION IS FOUND. The page numbering restarts at each account (see
    `sections`), and the account's own label sits beneath "Account Name" on
    every one of its pages. A boundary is accepted only when the evidence
    agrees with itself:

      each section's page markers form one complete run, "Page 1 of N" to
      "Page N of N"
      each section prints exactly one account, by `section_key`

    Anything else is a boundary the document does not settle, and raises
    `ParseError` for the WHOLE document. The alternative - reading the
    sections that look fine - is the one that files an account's rows under
    its neighbour, which the arithmetic gate cannot see because each walk
    still balances on its own.

    Two sections printing the same label are kept apart by an ordinal on BOTH
    keys, so a document holding the label once and another holding it twice
    do not silently agree about which one is which.

    A single-account document returns None, and is read by `read_statement`
    exactly as it always was.
    """
    parts = sections(grid)
    if len(parts) < 2:
        return None
    found: list[tuple[str, str, StatementReading]] = []
    for number, part in enumerate(parts, start=1):
        if not _numbering_is_whole(_page_marks(part)):
            raise ParseError(
                f"this statement covers {len(parts)} accounts but the page "
                f"numbering of section {number} is not one complete run of "
                "pages - where the accounts begin and end cannot be told, so "
                "the whole document is refused rather than guessed at"
            )
        reading, names = _read_one(part)
        keys = {section_key(name) for name in names}
        if not names or "" in keys:
            raise ParseError(
                f"this statement covers {len(parts)} accounts but section "
                f"{number} prints no account name, so it cannot be told from "
                "the others - the whole document is refused rather than "
                "guessed at"
            )
        if len(keys) > 1:
            raise ParseError(
                f"this statement covers {len(parts)} accounts but section "
                f"{number} names more than one account - a page marker was "
                "lost, so the whole document is refused rather than guessed at"
            )
        found.append((names[0], section_key(names[0]), reading))
    held = [key for _, key, _ in found]
    seen: dict[str, int] = {}
    result: list[StatementSection] = []
    for label, key, reading in found:
        if held.count(key) > 1:
            seen[key] = seen.get(key, 0) + 1
            unique = f"{key}#{seen[key]}"
        else:
            unique = key
        result.append(StatementSection(key=unique, label=label, reading=reading))
    return result
