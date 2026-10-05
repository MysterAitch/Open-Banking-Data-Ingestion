"""What testing a statement by what it LISTS concludes, as plain data the agreement rule reads.

`statement_listing_measure` reaches these conclusions, from the transactions each statement lists
and the store holds, and `agreement` reads them without importing the measurement (which reads
`agreement` itself). The rules they carry are written once, in `agreement`'s module docstring
(R1 to R4); this module is only the shape of the answers.

Nothing here is a figure a page may show, except where a field is declared `Structural`: the
amounts are what the arithmetic needs and are never rendered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .masking import Structural

#: Which check a statement that does not add up failed (`StatementCheck.fault`). The words each is
#: said in are `agreement.statement_fault_sentence`'s.
NOT_READ_WHOLE = "not-read-whole"
NOT_HELD = "not-held"
OTHER_AMOUNT = "other-amount"
HELD_TWICE = "held-twice"
LISTED_TWICE = "listed-twice"
DOES_NOT_REACH = "does-not-reach"


@dataclass(frozen=True)
class ClosedBefore:
    """A statement taken to have closed before some transactions, which explains why another
    source's balance for the same day differs from it (`agreement`, R2)."""

    day: Structural[date]
    #: How many counting transactions the difference is exactly, none of them listed by the
    #: statement or by any other.
    transactions: Structural[int]
    #: They are dated the day AFTER the closing day, so the other source dates them to the
    #: closing day and the statement is not shifted.
    next_day: Structural[bool]
    #: What the other source's balance exceeds the statement's by: the sum of those transactions.
    #: Compared by the rule, never rendered.
    difference_minor: int
    #: The part of that sum the chain counts at the statement's closing, because no statement
    #: placed those transactions after it. Never rendered.
    counted_minor: int


@dataclass(frozen=True)
class StatementCheck:
    """One statement's own test: its opening balance plus what it lists equals its closing."""

    day: Structural[date]
    #: Never rendered.
    figure: int
    #: The transactions its reading lists.
    listed: Structural[int]
    #: Whether it adds up by what it lists; None is "cannot say", which verifies nothing and
    #: faults nothing.
    adds_up: Structural[bool | None]
    #: The days it spans are tested (R1): no counting transaction dated in them is listed by
    #: no statement. Meaningful only where `adds_up` is True.
    days_tested: Structural[bool]
    #: Counting transactions in its span that no statement lists; None where nothing places the
    #: span.
    unlisted: Structural[int | None]
    #: Which check failed where it is a fault (R3), else "". Words, no figure.
    fault: Structural[str] = ""
    closed_before: Structural[ClosedBefore | None] = None


@dataclass(frozen=True)
class StatementChecks:
    """Every statement of one account, tested by what it lists."""

    statements: Structural[tuple[StatementCheck, ...]] = field(default_factory=tuple)
    #: How many of them add up by their own dates, where that was worked out; else None.
    by_date_adds_up: Structural[int | None] = None

    @property
    def adding_up(self) -> int:
        return sum(1 for s in self.statements if s.adds_up is True)
