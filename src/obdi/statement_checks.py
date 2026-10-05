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

#: Which check a statement that IS in use (it read whole, so its closing is a known balance) and
#: does not add up by what it lists failed (`StatementCheck.fault`). The words each is said in
#: are `agreement.statement_fault_sentence`'s. A statement that did not read whole is no fault:
#: it is a fact about the reading, and it is never a known balance (`StatementCheck.note`).
NOT_HELD = "not-held"
OTHER_AMOUNT = "other-amount"
HELD_TWICE = "held-twice"
LISTED_TWICE = "listed-twice"
NO_LONGER_COUNTS = "no-longer-counts"
HELD_ELSEWHERE = "held-elsewhere"
DOES_NOT_REACH = "does-not-reach"


@dataclass(frozen=True)
class ClosedBefore:
    """A statement taken to have closed before the transactions nobody lists, which explains why
    another source's balance for the same day differs from it (`agreement`, R2).

    A statement that closed at some moment precedes EVERYTHING unlisted after it, so there are two
    hypotheses and no more, each all-or-nothing: the other balance is for the end of the closing
    day (the difference is ALL the unlisted transactions dated that day), or it was really taken
    the day after (ALL the unlisted dated that day AND the next). The amounts are carried, not a
    sum, so `agreement` adds them up for itself.
    """

    day: Structural[date]
    #: How many counting transactions the difference is exactly.
    transactions: Structural[int]
    #: The second hypothesis: they include the ones dated the day AFTER the closing day.
    next_day: Structural[bool]
    #: The amounts of the unlisted counting transactions dated the closing day, and the day after.
    #: None of them is listed by this statement or by an earlier one. Compared by the rule, never
    #: rendered.
    that_amounts: tuple[int, ...] = ()
    next_amounts: tuple[int, ...] = ()
    #: Of `that_amounts`, the ones no statement at all lists, which the chain counts at the
    #: statement's closing and which the statement is taken not to hold.
    counted_amounts: tuple[int, ...] = ()


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
    #: Why the answer is cannot say, or what makes two documents clash, in words.
    note: Structural[str] = ""
    #: Another document closes the same day and lists different transactions, so neither tests
    #: its days and neither explains a day away.
    clash: Structural[bool] = False
    #: The first day its listing tests, where the days are tested.
    span_start: Structural[date | None] = None


@dataclass(frozen=True)
class StatementChecks:
    """Every statement of one account, tested by what it lists."""

    statements: Structural[tuple[StatementCheck, ...]] = field(default_factory=tuple)

    @property
    def adding_up(self) -> int:
        return sum(1 for s in self.statements if s.adds_up is True)
