"""What reading a statement produces, whichever bank wrote it.

Shared because the SHAPE of the answer does not vary - transactions,
the balances that gate them, and the terms - while the document in front
of it varies completely. A parser per format, one reading for all of them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class StatementRow:
    value_date: date
    description: str
    amount_minor: int
    #: The second date a row states where its format states two (the date the card issuer
    #: posted or entered it, beside the date of the transaction), or None where it states one.
    #: Never the row's own date: that is `value_date`.
    posted: date | None = None


@dataclass(frozen=True)
class RateWindow:
    percent: float
    until: date


@dataclass
class StatementReading:
    """One statement, read - with the evidence to judge whether to trust it."""

    statement_date: date | None = None
    opening_balance_minor: int | None = None
    closing_balance_minor: int | None = None
    credit_limit_minor: int | None = None
    #: What the document calls the account this statement covers, verbatim.
    #: Worth keeping because some issuers put the account's TERMS inside its
    #: name - a loan named for its rate - so the name is evidence as well as
    #: a label, and the part of it that is stable between statements is what
    #: identifies the account. Empty where the format states no name.
    account_name: str = ""
    transactions: list[StatementRow] = field(default_factory=list)
    #: (day, balance) for each end-of-day balance the document prints, in page
    #: order. Empty for a format that prints none. Kept as printed and NOT
    #: judged here: `statement_terms` accepts one only when it agrees with the
    #: statement's own rows.
    end_of_day_minor: list[tuple[date, int]] = field(default_factory=list)
    rates: dict[str, float] = field(default_factory=dict)
    rate_windows: list[RateWindow] = field(default_factory=list)
    #: Why this reading is incomplete, if it is. Named rather than papered
    #: over: a statement whose date could not be found cannot date its own
    #: rows, and guessing the current year would mis-file a whole history
    #: of statements silently.
    notes: list[str] = field(default_factory=list)
    #: The first day of the period the statement itself says it covers, where its format states
    #: one (a heading such as "Statement period: 05/07/2026 - 04/08/2026"). None for a format
    #: that states only the closing date, whose period is then known only from the statement
    #: before it. Never the first row's date: that is a fact about the rows, not the document.
    period_start: date | None = None
    #: The day the document says it was produced (a credit union's "Date of Issue"), where its
    #: format prints one. Never the closing day: `statement_span` uses it, and only it, to tell
    #: a statement that was produced before its period ended from one that was not.
    produced: date | None = None

    @property
    def discrepancy_minor(self) -> int:
        """What the rows fail to explain, in the house convention.

        Zero means the statement's own opening and closing balances agree
        with every row between them. Anything else means a row was missed,
        misread, or signed the wrong way - and the reading must not be
        stored on the strength of looking reasonable.
        """
        if self.opening_balance_minor is None or self.closing_balance_minor is None:
            return 0
        walked = self.opening_balance_minor + sum(
            row.amount_minor for row in self.transactions
        )
        return self.closing_balance_minor - walked

    @property
    def reconciles(self) -> bool:
        return (
            self.opening_balance_minor is not None
            and self.closing_balance_minor is not None
            and self.discrepancy_minor == 0
        )


#: The version of what a reading holds. Raised whenever a parser starts reading a field it
#: previously ignored, so that a reading kept by an older version is recognised as possibly
#: lacking it (`kept_format`) and read again from the document, rather than being taken to say
#: the document states nothing. 2: every layout that prints a start day or a day beside its
#: opening balance keeps it as `period_start`, and `produced` is kept.
READING_FORMAT = 2


def kept_format(text: str) -> int:
    """The format a kept reading was written in; 1 for one written before formats were named."""
    found = json.loads(text)
    return int(found.get("format", 1))


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _maybe_day(value: object) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


def reading_to_json(reading: StatementReading) -> str:
    """The reading as text, so a store can keep what a document said.

    Every field is written, so a reading read back is equal to the one kept and
    a field added to the reading without being added here fails the round-trip
    test rather than being quietly lost.
    """
    return json.dumps(
        {
            "statement_date": _day(reading.statement_date),
            "opening_balance_minor": reading.opening_balance_minor,
            "closing_balance_minor": reading.closing_balance_minor,
            "credit_limit_minor": reading.credit_limit_minor,
            "account_name": reading.account_name,
            "transactions": [
                [row.value_date.isoformat(), row.description, row.amount_minor, _day(row.posted)]
                for row in reading.transactions
            ],
            "end_of_day_minor": [
                [day.isoformat(), minor] for day, minor in reading.end_of_day_minor
            ],
            "rates": reading.rates,
            "rate_windows": [
                [window.percent, window.until.isoformat()] for window in reading.rate_windows
            ],
            "notes": reading.notes,
            "period_start": _day(reading.period_start),
            "produced": _day(reading.produced),
            "format": READING_FORMAT,
        }
    )


def _row_from_json(item: list[object]) -> StatementRow:
    """One row as `reading_to_json` wrote it: three fields, or four with the posting date.

    A reading kept before rows carried a posting date has three, and reads as having none.
    """
    day, description, minor, *rest = item
    return StatementRow(
        date.fromisoformat(str(day)),
        str(description),
        int(str(minor)),
        _maybe_day(rest[0]) if rest else None,
    )


def reading_from_json(text: str) -> StatementReading:
    """The reading `reading_to_json` wrote. Refuses text that is not one, with
    ValueError, KeyError, or TypeError, rather than returning a partial reading."""
    found = json.loads(text)
    return StatementReading(
        statement_date=_maybe_day(found["statement_date"]),
        opening_balance_minor=found["opening_balance_minor"],
        closing_balance_minor=found["closing_balance_minor"],
        credit_limit_minor=found["credit_limit_minor"],
        account_name=str(found["account_name"]),
        transactions=[
            _row_from_json(item)
            for item in found["transactions"]
        ],
        end_of_day_minor=[
            (date.fromisoformat(day), int(minor)) for day, minor in found["end_of_day_minor"]
        ],
        rates={str(kind): float(percent) for kind, percent in found["rates"].items()},
        rate_windows=[
            RateWindow(float(percent), date.fromisoformat(until))
            for percent, until in found["rate_windows"]
        ],
        notes=[str(note) for note in found["notes"]],
        # A reading kept before periods were kept has no such key and reads as stating none;
        # `statement_terms.keep_statement_readings` reads those documents again.
        period_start=_maybe_day(found.get("period_start")),
        produced=_maybe_day(found.get("produced")),
    )
