"""Whether a statement follows the one before it with nothing between, by what is known of the edge.

`statement_span.describe_account` already says, for each statement, how its first day is known and
whether a hole lies before it, so this asks that and does not decide the question a second time. A
statement DIRECTLY FOLLOWS the one before it when no hole lies between them and its start is
either printed as the day after the earlier closing (`Known.STATED`) or inferred from equal
balances one period apart (`Known.BALANCES_MEET`). The second is evidence and not proof: equal
balances are also what a missing statement whose movements net to nil leaves
(`statement_span`'s module docstring), so it is said as an inference wherever it is relied on.

A statement that starts earlier than the one before it closed overlaps it and does not directly
follow it. The first statement of an account follows nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from .ingest.statement_terms import StatementPeriod
from .statement_span import Known, RowEvidence, describe_account


def directly_follows(
    held: Sequence[StatementPeriod], today: date, evidence: RowEvidence | None = None
) -> dict[date, Known]:
    """Closing day -> how the edge before that statement is known, for each statement that
    directly follows the one before it. `held` has one statement per closing day, earliest first.
    Statements not in the answer follow a hole, an overlap, or nothing.
    """
    if len(held) < 2:
        return {}
    spans = describe_account(held, today, evidence)
    after_a_hole = {hole.later_closing for hole in spans.holes}
    found: dict[date, Known] = {}
    for previous, item, span in zip(held, held[1:], spans.statements[1:], strict=False):
        if item.closing in after_a_hole or span.first is None:
            continue
        if span.first_known not in (Known.STATED, Known.BALANCES_MEET):
            continue
        if span.first == previous.closing + timedelta(days=1):
            found[item.closing] = span.first_known
    return found
