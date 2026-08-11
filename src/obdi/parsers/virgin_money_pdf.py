"""Virgin Money credit card statements, read from their layout.

Written from one real statement inspected through the masking surface, so
no figure had to be disclosed. It differs from the Santander format in
every particular that matters, which is why it is a separate parser rather
than a widened one:

  four columns          transaction date, POSTING date, description and a
                        separate merchant column, against an inline form
  both dates present    so no year has to be inferred from the statement
  minus-signed credits  a refund is '-£9.99', with no CR marker anywhere
  a Total line          closing the table

Its terms are richer, and one table is why this format matters: promotional
rates are listed WITH AN EXPLICIT END DATE column, which is a dated rate
window written out by the bank rather than inferred - the observation layer
was built for exactly this and had no source until this document.

One ambiguity the masking could not resolve is handled by tolerance rather
than by guessing: the date column's internal spacing is inconsistent in the
extracted text (a month abbreviation runs into its year on some rows and
not others), so the date pattern accepts any spacing between day, month and
year. Being wrong about that would not fail quietly - the balance walk
would refuse the statement.
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

#: `<dd> <Mon><yy>  <dd><Mon> <yy>  <description>  [<merchant>]  [-]£<amount>`
#: Spacing inside each date is deliberately loose: the extracted text runs
#: month into year on some rows and not others, and a parser that insisted
#: on one form would read half a statement.
_TRANSACTION = re.compile(
    r"^(\d{1,2})\s*([A-Za-z]{3})\s*(\d{2})\s+"
    r"\d{1,2}\s*[A-Za-z]{3}\s*\d{2}\s+"
    r"(.+?)"
    r"\s+(-)?£\s*([\d,]+\.\d{2})\s*$"
)

_PERIOD = re.compile(
    r"Statement\s+period:\s*(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2})/(\d{2})/(\d{4})"
)
_OPENING = re.compile(
    r"Balance\s+from\s+your\s+\S+\s+statement\s+.*?£\s*(-?[\d,]+\.\d{2})"
)
_CLOSING = re.compile(r"Your\s+\S+\s+balance\s+.*?£\s*(-?[\d,]+\.\d{2})")
_CREDIT_LIMIT = re.compile(r"credit\s+limit:\s*£\s*([\d,]+)")
#: The promotional table: a rate, the balance it applies to, and the date
#: it ends - a dated window stated by the bank rather than inferred.
_PROMOTIONAL = re.compile(
    r"^(.+?)\s+([\d.]+)%\s+£\s*([\d,]+\.\d{2})\s+(\d{2})/(\d{2})/(\d{4})\s*$"
)
#: The standard table: a rate by transaction type, with a monthly rate
#: beside it that is the same fact said twice.
_STANDARD_RATE = re.compile(
    r"^(Purchases|Balance\s+Transfers?|Money\s+Transfers?|Cash|Charges)\s+"
    r"([\d.]+)%\s+[\d.]+%"
)


def _minor(text: str) -> int:
    return round(float(text.replace(",", "")) * 100)


def read_statement(lines: list[str]) -> StatementReading:
    """Read a Virgin Money statement's lines into transactions and terms."""
    reading = StatementReading()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        period = _PERIOD.search(line)
        if period:
            reading.statement_date = date(
                int(period.group(6)), int(period.group(5)), int(period.group(4))
            )
            continue
        opening = _OPENING.search(line)
        if opening:
            # Stated as owed; held as the negative position it is.
            reading.opening_balance_minor = -_minor(opening.group(1))
            continue
        closing = _CLOSING.search(line)
        if closing:
            reading.closing_balance_minor = -_minor(closing.group(1))
            continue
        limit = _CREDIT_LIMIT.search(line)
        if limit:
            reading.credit_limit_minor = _minor(limit.group(1) + ".00")
            continue
        standard = _STANDARD_RATE.match(line)
        if standard:
            name = re.sub(r"\s+", " ", standard.group(1)).strip().casefold()
            reading.rates[name] = float(standard.group(2))
            continue
        promotional = _PROMOTIONAL.match(line)
        if promotional:
            reading.rate_windows.append(
                RateWindow(
                    percent=float(promotional.group(2)),
                    until=date(
                        int(promotional.group(6)),
                        int(promotional.group(5)),
                        int(promotional.group(4)),
                    ),
                )
            )
            continue

        row = _TRANSACTION.match(line)
        if row:
            month = _MONTHS.get(row.group(2).lower())
            if month is None:
                continue
            amount = _minor(row.group(6))
            reading.transactions.append(
                StatementRow(
                    # Two-digit years, from a card that cannot predate the
                    # century it was issued in.
                    value_date=date(2000 + int(row.group(3)), month, int(row.group(1))),
                    description=re.sub(r"\s{2,}", "  ", row.group(4)).strip(),
                    # A minus is the only thing that makes money arrive
                    # here; there is no credit marker in this format.
                    amount_minor=amount if row.group(5) else -amount,
                )
            )
    return reading
