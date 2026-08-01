"""Replay the canonical store into Actual Budget.

Replay, not sync: the store is the record and Actual is a view over it. That is
what makes Actual disposable - wipe the budget, replay, and nothing is lost.

The write path is Node-only. `@actual-app/api` embeds Actual's own budget
engine and runs its JavaScript migrations, so it is versioned in lockstep with
the server. A Python reimplementation exists and is good for reading, but its
own documentation warns against using it to create budgets, which is precisely
what a rebuild does. So this module produces the payload and a small pinned
Node process applies it - the polyglot split is confined to one container whose
only job is to track Actual's version.

Three behaviours of Actual's importer shape everything here.

**Use its import path, never the raw insert.** The raw one skips reconciliation
entirely and silently duplicates on any re-run.

**`imported_id` is the idempotency key.** Transactions carrying the same one
are never added twice. Ours is the canonical entity id, so a transaction keeps
its identity across every replay.

**On a match, existing values win.** Actual preserves a payee, category or note
you set by hand rather than overwriting it from the incoming record, and never
touches a reconciled transaction. Re-importing therefore does not undo manual
categorisation - which is what makes replaying safe to do casually.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import Transaction, TransactionStatus


class ReplayError(RuntimeError):
    """A transaction cannot be replayed safely."""


@dataclass(frozen=True)
class ActualAccountBinding:
    """Maps a canonical account to an Actual account id."""

    canonical_id: str
    actual_account_id: str
    label: str = ""


def to_actual_transaction(transaction: Transaction) -> dict:
    """Map one canonical transaction into Actual's import shape.

    Amounts pass through unchanged: Actual also stores integer minor units with
    a negative outflow, so there is no conversion to get wrong.
    """
    if not transaction.entity_id:
        raise ReplayError(
            "transaction has no entity id, so it has no stable imported_id. "
            "Replaying it would create a duplicate on every run."
        )

    payee = transaction.counterparty or transaction.description

    return {
        # Actual's idempotency key. Ours is the canonical identity, so the same
        # payment maps to the same row on every replay, however many sources
        # observed it.
        "imported_id": transaction.entity_id,
        "date": transaction.value_date.isoformat(),
        "amount": transaction.amount_minor,
        "payee_name": payee,
        # Kept distinct from payee_name so Actual's own renaming rules have the
        # original text to work from after a payee has been tidied up.
        "imported_payee": transaction.description or payee,
        "notes": _notes_for(transaction),
        # Pending transactions are explicitly uncleared: a pending record will
        # later be superseded by its settled form, and marking it cleared would
        # freeze it against that.
        "cleared": transaction.status is not TransactionStatus.PENDING,
    }


def _notes_for(transaction: Transaction) -> str:
    """Carry provenance into Actual, where it is otherwise invisible."""
    parts = [f"via {transaction.source}"]
    if transaction.is_internal_transfer:
        parts.append("internal transfer")
    if transaction.status is TransactionStatus.PENDING:
        parts.append("pending")
    return " | ".join(parts)


def build_payload(
    transactions: list[Transaction],
    bindings: list[ActualAccountBinding],
    *,
    include_internal_transfers: bool = False,
) -> dict[str, list[dict]]:
    """Group transactions by Actual account, ready to import.

    Internal transfers are excluded by default. They are real movements between
    your own accounts, but counting both sides inflates spending and income
    alike; Actual models them as its own transfer type, which a flat import
    cannot express.

    Accounts with no binding are skipped rather than guessed at, because
    inventing a destination would scatter transactions into the wrong budget.
    """
    by_canonical = {binding.canonical_id: binding.actual_account_id for binding in bindings}
    payload: dict[str, list[dict]] = defaultdict(list)

    for transaction in transactions:
        if transaction.is_internal_transfer and not include_internal_transfers:
            continue
        actual_account = by_canonical.get(transaction.account_id)
        if actual_account is None:
            continue
        payload[actual_account].append(to_actual_transaction(transaction))

    return dict(payload)


def unbound_accounts(
    transactions: list[Transaction], bindings: list[ActualAccountBinding]
) -> list[str]:
    """Canonical accounts with no Actual destination.

    Reported rather than silently dropped: an account quietly missing from a
    budget looks like missing spending, and is very hard to notice.
    """
    bound = {binding.canonical_id for binding in bindings}
    return sorted({t.account_id for t in transactions} - bound)
