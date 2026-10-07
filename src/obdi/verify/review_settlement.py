"""Close the review flags the stored evidence already answers.

A flag asks whether a row stored as new is a repeated payment or a duplicate
report. Several kinds of flag ask a question that has an answer on file (see
`review_report.FlagClass`): the flagged row is gone or is history, every
neighbour is history, one response named the row and every neighbour under
different provider ids, the source never reuses an id, or the known balances on
both sides are reproduced only with both rows counted. This pass deletes those
flags and leaves the rest for a person. What a proof needs is stated once, on
the class, in `review_report.FlagClass`.

It is a DERIVED pass, like the Space fold: it reads the evidence held now and
runs after every fold, so a flag the evidence later answers is closed on the
next pass, and a rebuild re-derives the same open set from raw. It deletes
only OPEN flags. It never writes `resolved_at`, which is a person's judgement
and is kept across rebuilds; it never changes a transaction; and it leaves
`matching.resolve` alone, so no entity id moves.

Rejected: suppressing the flag when it is raised. Proof can arrive after the
row does - a later response listing both ids, a pending row voided a cycle
later, a row later folded into a Space row - and a flag never raised is never
reconsidered. Raise-time knowledge also depends on the order the batches
arrived in, where this pass depends only on what is held.

Rejected: closing a flag on ANY proven neighbour. The flag stands for the
whole set of near-misses, and a neighbour not yet proven separate is still a
possible duplicate report, so every live neighbour must be proven.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.plural import plural
from ..ingest.store import Store
from .review_report import SETTLED_CLASSES, FlagClass, assess_flags


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


def settle_review_flags(store: Store) -> SettleReport:
    """Delete every open flag whose question is answered; count them by class."""
    report = SettleReport()
    for entity_id, assessment in assess_flags(store).items():
        if assessment.flag_class not in SETTLED_CLASSES:
            report.still_open += 1
            continue
        store.connection.execute(
            "DELETE FROM review_queue WHERE entity_id = ? AND resolved_at IS NULL",
            (entity_id,),
        )
        report.settled[assessment.flag_class] = (
            report.settled.get(assessment.flag_class, 0) + 1
        )
    store.connection.commit()
    return report
