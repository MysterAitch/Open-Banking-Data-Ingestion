"""What a landing function is given to finish its work, and the records those finishers return.

Rows land in `ingest`, but what happens after they land is judgement: review flags the evidence
now answers are settled, protections are rechecked, a person's "one payment" answers are
repeated, and the sections of an "all accounts" statement are read back. That judgement is
`verify`, which sits above `ingest` and so cannot be imported from here. The caller passes it in:
every function that lands rows takes a `Finishers` as a REQUIRED keyword, and `verify.landing`
builds the real one.

It is required, with no default, because a default that does nothing fails open: a caller that
forgot it would land rows and leave the flags unsettled and the protections unchecked, and
nothing would say so. A missing argument is a `TypeError` at the first call instead.

The records are defined here, below the code that holds them, because the rebuild report carries
them: `FlagClass` and `SettleReport` (the review flags settled), and `SectionBatches` (what one
statement reads back as).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ..core.models import Transaction
from ..core.plural import plural
from .store import Store


class FlagClass(StrEnum):
    """Why an open flag is, or is not, still a question. Strongest proof first."""

    #: The flagged row no longer exists.
    ROW_GONE = "row-gone"
    #: The flagged row is void or folded: history, not a payment to confirm.
    ROW_IS_HISTORY = "row-is-history"
    #: No neighbour the matcher would have weighed is still live.
    NO_LIVE_NEIGHBOUR = "no-live-neighbour"
    #: Every live neighbour was reported under a different provider id in one
    #: response, which is the provider saying two payments.
    LISTED_TOGETHER = "listed-together"
    #: Every live neighbour carries a different id from a source that names a
    #: payment by one id for life. Also holds where the neighbours are a mix of
    #: this proof and `LISTED_TOGETHER`: the weaker of the two names the class.
    IDS_KEPT_FOR_LIFE = "ids-kept-for-life"
    #: The rows reproduce a known balance before every row of the pair and one on or after
    #: every row, with both counted, so dropping either would put the later balance out.
    #: THE PROOF'S CONDITIONS ARE STATED ONCE, here, and `verify.review_report.balance_proof`
    #: enforces them:
    #:
    #:   - Some source LISTS both rows as separate lines in one response (`_listed_in_one`),
    #:     for every neighbour that no id proof has already separated. A statement reader can
    #:     read one line twice at a page boundary, and the balance is what rules that out; where
    #:     no source lists both, the rows may be one payment seen by two sources and the
    #:     balance is not independent of either, so the flag stays open however it looks.
    #:   - Every row of the set (the flagged row and all its live neighbours) is BOOKED, in one
    #:     account, with a non-nil amount. A pending row is not in a bank's or a statement's
    #:     booked balance, so its being counted proves nothing.
    #:   - K1 is the latest known balance dated strictly BEFORE the earliest row, and K2 the
    #:     earliest dated on or after the latest row. A known balance is a figure for the END
    #:     of its day and includes that day's rows (`balance_anchors.derive_opening`), so a
    #:     statement dated on the rows' own day closes them and cannot open them.
    #:   - K2 must be TESTED: the rows reproduce it. K1 may be tested or may be the one that
    #:     DEFINES the opening, because the proof is the difference between the two, which the
    #:     opening cancels out of. A nil opening is a premise rather than a known balance and
    #:     never serves, so a pair before the first known balance is open: the opening is
    #:     worked out backwards from that balance and absorbs any error.
    #:   - Every known balance from K1 to K2, whichever source states it, is reproduced, and no
    #:     two sources state different figures for one day in that span. A balance stated for a
    #:     moment is never K1 or K2 but is still held to this.
    #:   - The account is not tracked by its stated balances alone (those are followed: a row is
    #:     derived to make each agree, so nothing is tested) and has no known Space (its
    #:     balances may be the whole family's, which needs the account map that this report is
    #:     not given). An account fed by the bank's own feed counts as possibly having Spaces.
    #:
    #: THE HONEST LIMIT. A duplicate offset by a MISSING row of the same size in the same span
    #: would also reproduce K2. The money is then still right and no row changes, so the flag
    #: is closed in name only; that is accepted because the balance is what the flag protects.
    #: Rows are placed by their stored dates: a source whose own dating moves a row across a
    #: known balance is not separately checked, and nor are the movement checks that the
    #: account's agreement also reads.
    #:
    #: WHEN IT STOPS HOLDING it behaves as the other settled classes do: the flag is already
    #: deleted and stays so until a rebuild, which raises it again and closes it only if the
    #: proof still holds.
    BALANCES_NEED_BOTH = "balances-need-both"
    #: The flagged row is of nil amount, and so is every neighbour, since a neighbour is a row
    #: of the same amount. A flag asks whether a sum is counted once or twice, and nil counted
    #: twice is nil: no balance, total, or budget figure depends on the answer, so there is
    #: nothing for a person to decide and no balance that could decide it (the balance proof
    #: above refuses a nil amount for that reason). Both rows are kept, as every line a source
    #: lists is. Found on a card whose statements each list two lines of 0.00 under different
    #: descriptions on the statement's date: nine of the eleven flags open on the real store
    #: were these, one raised by every statement.
    NIL_AMOUNT = "nil-amount"
    #: Everything else: a real question for a person.
    OPEN = "open"


#: The classes whose flag asks a question the evidence has answered.
SETTLED_CLASSES: tuple[FlagClass, ...] = (
    FlagClass.ROW_GONE,
    FlagClass.ROW_IS_HISTORY,
    FlagClass.NO_LIVE_NEIGHBOUR,
    FlagClass.LISTED_TOGETHER,
    FlagClass.IDS_KEPT_FOR_LIFE,
    FlagClass.BALANCES_NEED_BOTH,
    FlagClass.NIL_AMOUNT,
)


@dataclass
class SettleReport:
    #: Flags deleted, by the class of proof that closed them.
    settled: dict[FlagClass, int] = field(default_factory=dict)
    #: Open flags left after the pass: the real questions.
    still_open: int = 0

    @property
    def total(self) -> int:
        return sum(self.settled.values())

    def describe(self) -> str:
        if not self.settled:
            return (
                f"no review flag was settled; {self.still_open} "
                f"{'remains' if self.still_open == 1 else 'remain'} open"
            )
        parts = ", ".join(
            f"{count} {flag_class.value}"
            for flag_class in SETTLED_CLASSES
            if (count := self.settled.get(flag_class, 0))
        )
        return (
            f"{plural(self.total, 'review flag')} settled by evidence already held "
            f"({parts}); {self.still_open} "
            f"{'remains' if self.still_open == 1 else 'remain'} open"
        )


@dataclass
class SectionBatches:
    """What a rebuild reads back out of one multi-account statement."""

    #: (account, rows) per assigned section, in document order.
    batches: list[tuple[str, list[Transaction]]] = field(default_factory=list)
    #: Sections the document holds that nobody has given an account.
    unassigned: int = 0
    #: Sections the document holds, assigned or not; zero for a document that
    #: reads as a single account or cannot be read.
    sections: int = 0
    #: Why an assigned section could not be read back, one line each.
    problems: list[str] = field(default_factory=list)

    @property
    def every_section_assigned(self) -> bool:
        """A several-account document that is waiting for nothing."""
        return self.sections > 0 and self.unassigned == 0


class Recheck(Protocol):
    """`verify.protection.recheck`: the keywords are the ones a landing function uses."""

    def __call__(self, store: Store, *, finished_rebuild: bool = False) -> object: ...


@dataclass(frozen=True)
class Finishers:
    """The judgement that runs once rows have landed, handed in by the caller.

    `settle` closes the review flags the evidence now answers; `recheck` records a protection
    that broke or healed; `replay_joins` repeats a person's "one payment" answers over rows just
    derived; `replay_batches` reads the assigned sections of one kept statement back into their
    accounts (a rebuild calls it in the middle of its replay, not after).
    """

    settle: Callable[[Store], SettleReport]
    recheck: Recheck
    replay_joins: Callable[[Store], object]
    replay_batches: Callable[[Store, str, bytes, Callable[[str], str]], SectionBatches]
