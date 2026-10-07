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

from ..core.masking import Structural

#: Which check a statement that IS in use (it read whole, so its closing is a known balance) and
#: does not add up by what it lists failed (`StatementCheck.fault`). The words each is said in
#: are `agreement.statement_fault_sentence`'s. A statement that did not read whole is no fault:
#: it is a fact about the reading, and it is never a known balance (`StatementCheck.note`).
NOT_HELD = "not-held"
OTHER_AMOUNT = "other-amount"
HELD_TWICE = "held-twice"
LISTED_TWICE = "listed-twice"
NO_LONGER_COUNTS = "no-longer-counts"
DOES_NOT_REACH = "does-not-reach"


@dataclass(frozen=True)
class ClosedBefore:
    """A statement taken to have closed before the transactions nobody lists, which explains why
    another source's balance for the same day differs from it (`agreement`, R2).

    ONE hypothesis, all or nothing: the other balance is for the end of the closing day and
    differs from the statement's closing by exactly ALL the counting transactions dated that day
    that the statement does not list. Anything else stays the conflict it is. (A second, that the
    other balance was really taken the day after, was tried and withdrawn: it widened what can
    add up, and the balances the page then showed were not the ones stated.) The amounts are
    carried, not a sum, so `agreement` adds them up for itself.
    """

    day: Structural[date]
    #: How many counting transactions the difference is exactly.
    transactions: Structural[int]
    #: The amounts of the unlisted counting transactions dated the closing day. None of them is
    #: listed by this statement or by an earlier one. Compared by the rule, never rendered.
    that_amounts: tuple[int, ...] = ()
    #: Of `that_amounts`, the ones no statement at all lists, which the chain counts at the
    #: statement's closing and which the statement is taken not to hold.
    counted_amounts: tuple[int, ...] = ()


@dataclass(frozen=True)
class DateDifference:
    """How a statement's closing balance and the balance drawn by each transaction's date for
    that day part: counts only. A statement is tested by what it lists, and the page's running
    position is drawn by stored date, so on a closing day the two can differ by transactions
    dated on or before it that the statement's balance does not hold, and by ones it lists that
    are dated after it."""

    #: Pending transactions dated on or before the day: in the position, not in a statement's.
    pending: Structural[int]
    #: Counting transactions dated on or before the day that a LATER statement lists.
    later: Structural[int]
    #: Transactions the statement lists that are dated after the day.
    listed_after: Structural[int]


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
    #: Where the balance drawn by date for its closing day is not the stated one, because of
    #: transactions the page names (None where it is the stated one, or the difference is only
    #: what R2 explains).
    by_date: Structural[DateDifference | None] = None
    #: The balance drawn by date for the day, less the stated closing balance, which
    #: `by_date` and the R2 claim account for exactly. Compared by a test, never rendered.
    date_gap_minor: int = 0


@dataclass(frozen=True)
class StatementChecks:
    """Every statement of one account, tested by what it lists."""

    statements: Structural[tuple[StatementCheck, ...]] = field(default_factory=tuple)

    @property
    def adding_up(self) -> int:
        return sum(1 for s in self.statements if s.adds_up is True)
