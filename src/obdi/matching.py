"""Resolving whether an incoming transaction is one already seen.

Tiered, highest fidelity first, and it never guesses:

  1. exact source_id, scoped to the account
  2. exact content_key
  3. fuzzy - same account, exact amount, value date within a window
  4. unresolved - flagged for human review, NOT silently inserted as new

Tier 3's window is +/- 7 days. Actual Budget uses exactly that; beancount-import
independently settled on 5. Two projects converging is the best evidence
available that the right answer is "about a week".

Separately, `pair_internal_transfers` handles a different problem: a movement
between two of your own accounts arrives twice, once as a debit and once as a
credit. Unpaired, it inflates both spending and income.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from .models import MatchTier, Transaction, TransactionStatus

FUZZY_WINDOW_DAYS = 7
INTERNAL_TRANSFER_WINDOW_DAYS = 1


@dataclass(frozen=True)
class MatchResult:
    tier: MatchTier
    existing: Transaction | None

    @property
    def is_new(self) -> bool:
        return self.existing is None

    @property
    def needs_review(self) -> bool:
        return self.tier is MatchTier.UNRESOLVED


def resolve(incoming: Transaction, existing: Sequence[Transaction]) -> MatchResult:
    """Decide whether `incoming` is already represented in `existing`."""
    same_account = [t for t in existing if t.account_id == incoming.account_id]

    if incoming.source_id:
        for candidate in same_account:
            if candidate.source_id and candidate.source_id == incoming.source_id:
                return MatchResult(MatchTier.SOURCE_ID, candidate)

    if incoming.content_key:
        for candidate in same_account:
            if candidate.content_key == incoming.content_key:
                return MatchResult(MatchTier.CONTENT_KEY, candidate)

    window = timedelta(days=FUZZY_WINDOW_DAYS)
    near = [
        t
        for t in same_account
        if t.amount_minor == incoming.amount_minor
        and abs(t.value_date - incoming.value_date) <= window
    ]

    # If both sides already carry provider ids and those ids did not match at
    # tier 1, they are authoritatively different transactions. Collapsing them
    # here would be a false positive, so they are excluded from fuzzy matching.
    if incoming.source_id:
        near = [t for t in near if not t.source_id]

    if not near:
        return MatchResult(MatchTier.UNRESOLVED, None)

    near.sort(key=lambda t: abs(t.value_date - incoming.value_date))
    return MatchResult(MatchTier.FUZZY, near[0])


def supersede(previous: Transaction, observation: Transaction) -> Transaction:
    """Apply a later sighting of a transaction already held.

    A pending transaction that settles often arrives with a NEW provider id and
    a shifted date. That is a supersession, not an update: the entity keeps its
    identity, the newer observation supplies the current facts, and both raw
    payloads remain in the raw layer. Modelling it this way is what makes a
    rebuild from raw reproducible.
    """
    return replace(
        observation,
        entity_id=previous.entity_id,
        # Retain the earliest booking date so "when did this first appear" is
        # answerable after settlement moves the dates.
        booking_date=min(previous.booking_date, observation.booking_date),
        status=observation.status or previous.status,
    )


def pair_internal_transfers(
    transactions: Iterable[Transaction],
    *,
    window_days: int = INTERNAL_TRANSFER_WINDOW_DAYS,
) -> list[Transaction]:
    """Flag matched debit/credit pairs across your own accounts.

    Matches on equal absolute amount, opposite sign, different account, and
    value dates within `window_days`. Each side is consumed once, so a repeated
    standing order of the same value does not chain-match.
    """
    items = sorted(transactions, key=lambda t: (t.value_date, t.account_id))
    window = timedelta(days=window_days)
    paired: set[int] = set()
    result = list(items)

    for i, debit in enumerate(items):
        if i in paired or debit.amount_minor >= 0:
            continue
        for j, credit in enumerate(items):
            if j in paired or j == i or credit.amount_minor <= 0:
                continue
            if credit.account_id == debit.account_id:
                continue
            if credit.amount_minor != -debit.amount_minor:
                continue
            if abs(credit.value_date - debit.value_date) > window:
                continue
            paired.update({i, j})
            result[i] = replace(debit, is_internal_transfer=True)
            result[j] = replace(credit, is_internal_transfer=True)
            break

    return result


def settled(transaction: Transaction) -> bool:
    return transaction.status is TransactionStatus.BOOKED
