"""What the store returns for the links the learned rule inferred, and how each turned out.

A row with no identifier of its own is named by an inference when its description opens as
every row of one party does (`analysis.learned_rules`). The inference is recomputed from the rows
each time and so leaves no memory: a row that later gains an identifier (a richer source for the
same payment, folded into the held row) would show only the identifier. The one place the reverse
direction of the rule is measured for real is that moment, so the inference is written down when
it is first made and compared when the identifier arrives (`analysis.rule_confirmation`).

This is a measurement, not a decision: nothing here is declared by the owner, and no standing,
balance, or protection reads it. It is kept across the rebuild from raw because the row ids the
records are about are stable across it, and a rebuild that forgot would reset the measured
precision to nothing each time.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The outcome of a link before any identifier has arrived for its row.
PENDING = ""
#: A later identifier named the party the rule had inferred.
AGREED = "agreed"
#: A later identifier named another party: a counterexample to the rule.
DISAGREED = "disagreed"


@dataclass(frozen=True)
class InferredLink:
    """One row the rule named: the held row (`entity_id`), the party and opening the rule gave,
    the day it was decided, and its outcome (`PENDING`, `AGREED`, or `DISAGREED`)."""

    entity_id: str
    party: str
    opening: str
    decided_at: str
    outcome: str = PENDING
    settled_at: str = ""
