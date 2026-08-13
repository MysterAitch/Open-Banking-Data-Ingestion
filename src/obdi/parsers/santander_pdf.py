"""Santander credit card statements, read from their layout.

Written from two real statements inspected through the masking surface -
the first ever issued for the account and the most recent - so no figure
had to be disclosed to write it. That is the working pattern for every
statement format: read the shape, write the parser, let the arithmetic
prove it.

Three things make this format its own problem.

Transaction lines carry NO YEAR. The statement date supplies it, and a
December row on a January statement belongs to the year before - wrong by
a year, every year, on one statement in twelve, unless it is handled.

The token before the amount is either a CREDIT MARKER or a COUNTRY CODE,
and that single distinction decides the sign of the row. A refund and a
purchase against the same merchant differ by nothing else.

And the statement states balances as amounts OWED, which is the negation
of the house convention (money out negative, in every account of every
kind). The flip happens here, at the document boundary, so that nothing
downstream needs to know this is a liability.

None of that is trusted: the statement declares its opening and closing
balances, so the rows must walk from one to the other or the reading is
refused rather than stored.
"""

from __future__ import annotations

import re
from datetime import date

from .statement_reading import RateWindow, StatementReading, StatementRow

_MONTHS = {
    month: number
    for number, month in enumerate(
        (
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ),
        start=1,
    )
}

#: `<day><ordinal> <Mon> <description> [<marker>] <amount>`. The marker is
#: optional because fees and interest carry none, and it is matched at the
#: END so a description ending in a capitalised word cannot swallow it.
_TRANSACTION = re.compile(
    r"^(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+"
    r"(.+?)"
    r"(?:\s+(CR|[A-Z]{2,3}))?"
    r"\s+([\d,]+\.\d{2})$"
)

_STATEMENT_DATE = re.compile(
    r"Statement Date:\s*(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]+)\s+(\d{4})"
)
_OPENING = re.compile(
    r"Balance brought forward from previous statement\s+([\d,]+\.\d{2})"
)
_CLOSING = re.compile(r"Your new balance:\s*£?\s*([\d,]+\.\d{2})")
_CREDIT_LIMIT = re.compile(r"Account credit limit:\s*£?\s*([\d,]+\.\d{2})")
_RATE = re.compile(r"^x?\s*(Purchases|Cash transactions)\s+([\d.]+)%")
#: `Balance <amount> Interest <rate>% to <DD-MM-YYYY>` - a dated rate
#: window with its reversion date, which no feed exposes anywhere.
_RATE_WINDOW = re.compile(
    r"Balance\s+[\d,]+\.\d{2}\s+Interest\s+([\d.]+)%\s+to\s+"
    r"(\d{2})-(\d{2})-(\d{4})"
)

#: Lines that look like transactions but are totals or summaries.
_NOT_A_TRANSACTION = ("Total of", "Balance brought forward", "Total Payments")


def _minor(text: str) -> int:
    return round(float(text.replace(",", "")) * 100)


def _year_for(month: int, statement: date) -> int:
    """A transaction month later than the statement's belongs to last year."""
    return statement.year - 1 if month > statement.month else statement.year


def read_statement(lines: list[str]) -> StatementReading:
    """Read a statement's lines into transactions, balances and terms."""
    reading = StatementReading()

    # The statement date first, wherever it sits: every transaction line
    # depends on it for a year, so a single pass would have to guess.
    for line in lines:
        stamp = _STATEMENT_DATE.search(line)
        if stamp:
            month = _MONTHS.get(stamp.group(2)[:3].lower())
            if month:
                reading.statement_date = date(
                    int(stamp.group(3)), month, int(stamp.group(1))
                )
                break

    if reading.statement_date is None:
        reading.notes.append(
            "no statement date found - transaction lines carry no year of "
            "their own, so none can be dated"
        )

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        opening = _OPENING.search(line)
        if opening:
            # THE FIRST ONE WINS, because a multi-page statement repeats this
            # line at the top of every page carrying the RUNNING figure rather
            # than the statement's opening. Overwriting took the last page's
            # carried total as the month's starting position: measured on a
            # two-page statement, the opening read 130.96 where the statement
            # opened at 100.96.
            #
            # No row is lost and no total is wrong, which is what makes it
            # worth a comment - it makes the statement's own arithmetic
            # disagree with itself, and so discredits the one check that needs
            # nothing but the file to run.
            #
            # Stated as owed; held as the negative position it is.
            if reading.opening_balance_minor is None:
                reading.opening_balance_minor = -_minor(opening.group(1))
            continue
        closing = _CLOSING.search(line)
        if closing:
            reading.closing_balance_minor = -_minor(closing.group(1))
            continue
        limit = _CREDIT_LIMIT.search(line)
        if limit:
            reading.credit_limit_minor = _minor(limit.group(1))
            continue
        rate = _RATE.search(line)
        if rate:
            name = "cash" if rate.group(1).startswith("Cash") else "purchases"
            reading.rates[name] = float(rate.group(2))
            continue
        window = _RATE_WINDOW.search(line)
        if window:
            reading.rate_windows.append(
                RateWindow(
                    percent=float(window.group(1)),
                    until=date(
                        int(window.group(4)), int(window.group(3)), int(window.group(2))
                    ),
                )
            )
            continue
        if any(line.startswith(prefix) for prefix in _NOT_A_TRANSACTION):
            continue

        row = _TRANSACTION.match(line)
        if row:
            month = _MONTHS.get(row.group(2)[:3].lower())
            if month is None or reading.statement_date is None:
                continue
            marker = row.group(4) or ""
            amount = _minor(row.group(5))
            reading.transactions.append(
                StatementRow(
                    value_date=date(
                        _year_for(month, reading.statement_date),
                        month,
                        int(row.group(1)),
                    ),
                    description=row.group(3).strip(),
                    # CR is the only thing that makes money arrive; a
                    # country code, or no marker at all, is money leaving.
                    amount_minor=amount if marker == "CR" else -amount,
                )
            )
            continue

        # Interest is charged, not transacted, but it is money owed and
        # the balance will not walk without it.
        if line.startswith(("Purchase Interest", "Balance Transfer Interest")):
            charged = re.search(r"([\d,]+\.\d{2})\s*$", line)
            if charged and reading.statement_date is not None:
                reading.transactions.append(
                    StatementRow(
                        value_date=reading.statement_date,
                        description=line.rsplit(" ", 1)[0].strip(),
                        amount_minor=-_minor(charged.group(1)),
                    )
                )
    return reading
