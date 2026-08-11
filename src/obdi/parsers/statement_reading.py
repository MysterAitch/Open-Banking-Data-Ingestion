"""What reading a statement produces, whichever bank wrote it.

Shared because the SHAPE of the answer does not vary - transactions,
the balances that gate them, and the terms - while the document in front
of it varies completely. A parser per format, one reading for all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class StatementRow:
    value_date: date
    description: str
    amount_minor: int


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
    rates: dict[str, float] = field(default_factory=dict)
    rate_windows: list[RateWindow] = field(default_factory=list)
    #: Why this reading is incomplete, if it is. Named rather than papered
    #: over: a statement whose date could not be found cannot date its own
    #: rows, and guessing the current year would mis-file a whole history
    #: of statements silently.
    notes: list[str] = field(default_factory=list)

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
