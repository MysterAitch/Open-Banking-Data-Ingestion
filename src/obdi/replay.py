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
are never added twice. What ours is made of, and why it is not the entity id,
is stated where it is built, in `to_actual_transaction`.

**On a match, existing values win.** Actual preserves a payee, category or note
you set by hand rather than overwriting it from the incoming record, and never
touches a reconciled transaction. Re-importing therefore does not undo manual
categorisation - which is what makes replaying safe to do casually.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from .core.models import Transaction, TransactionStatus
from .core.namespaces import CASH_LEG_SOURCE, UNITEMISED_SOURCE


class ReplayError(RuntimeError):
    """A transaction cannot be replayed safely."""


@dataclass(frozen=True)
class ActualAccountBinding:
    """Maps a canonical account to an Actual account id."""

    canonical_id: str
    actual_account_id: str
    label: str = ""


def is_cleared(transaction: Transaction, cleared: Collection[str] | None) -> bool:
    """Whether a row goes to Actual as cleared.

    With `cleared`, the entity ids `clearing.cleared_entity_ids` found, a row is cleared when an
    authoritative listing lists it (`clearing` says which and why), or when it is the account's own
    arithmetic from balances its owner stated (`UNITEMISED_SOURCE`), which no source lists and which
    is as authoritative as a figure gets, or when it is the leg of a booked cash withdrawal
    (`CASH_LEG_SOURCE`), which no source lists and which is made only once its withdrawal is
    booked. Without it, any row that is not pending, the rule before clearing was a fact about a
    row.

    A pending row is never cleared: it will be superseded by its settled form.
    """
    if transaction.status is TransactionStatus.PENDING:
        return False
    if cleared is None:
        return True
    return transaction.source in (UNITEMISED_SOURCE, CASH_LEG_SOURCE) or (
        transaction.entity_id in cleared
    )


def to_actual_transaction(
    transaction: Transaction, *, cleared: Collection[str] | None = None
) -> dict[str, object]:
    """Map one canonical transaction into Actual's import shape.

    Amounts pass through unchanged: Actual also stores integer minor units with
    a negative outflow, so there is no conversion to get wrong.

    `cleared` is passed through to `is_cleared`. THE ENVELOPE NEVER CARRIES `reconciled`, and that
    is decided here: the applier will not change a reconciled row (`applier/lib.mjs`, `audit.mjs`),
    so a row obdi marked reconciled could never again be corrected by obdi's own push, and obdi's
    corrections are exactly what a rebuild produces. Actual's reconcile is the owner's own act, made
    in Actual, and obdi's equivalent is protection (`protection`), which alarms and never freezes.
    """
    if not transaction.content_key:
        raise ReplayError(
            "transaction has no content key, so it has no stable imported_id. "
            "Replaying it would create a duplicate on every run."
        )

    if transaction.currency != "GBP":
        # Actual is currency-agnostic but single-currency per budget file, so a
        # foreign amount has nowhere correct to land. Emitting it unlabelled
        # would silently book a euro figure as sterling.
        raise ReplayError(
            f"transaction {transaction.entity_id} is in {transaction.currency}; "
            "the budget file is single-currency and there is no correct "
            "destination for a foreign amount"
        )

    payee = transaction.counterparty or transaction.description

    return {
        # Actual's idempotency key. Ours is the CONTENT identity plus its
        # occurrence index, deliberately not the entity id: entity ids are
        # minted at first sighting and a rebuild re-mints them, which would
        # duplicate every transaction in Actual on the next replay. The
        # content key is deterministic over the payment itself, so it
        # survives rebuilds and is identical however many sources observed
        # the payment.
        "imported_id": f"{transaction.content_key}:{transaction.occurrence}",
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
        "cleared": is_cleared(transaction, cleared),
    }


def history_imported_ids(transactions: list[Transaction]) -> list[str]:
    """The imported ids of stored rows that are history, for the audit to explain orphans by.

    A row that is void, folded, or reversed is no longer sent, so a copy of it already in
    Actual is an orphan; this is how the audit tells such an orphan from one obdi has never
    held. The id is the one a send would have carried (`to_actual_transaction`), so a stored
    row maps back to a row in Actual by content key and occurrence, which survive a rebuild
    where the entity id does not.

    An id that a row still being sent carries is left out, so a history row sharing an
    identity with a live one can never explain away the live one. A row with no content key
    has no id to give and is left out rather than invented.
    """
    sent = {
        f"{t.content_key}:{t.occurrence}"
        for t in transactions
        if t.content_key and not t.status.is_history
    }
    return sorted(
        {
            f"{t.content_key}:{t.occurrence}"
            for t in transactions
            if t.content_key and t.status.is_history
        }
        - sent
    )


def _notes_for(transaction: Transaction) -> str:
    """The transaction's own words, not ours.

    "via truelayer" labelled every row identically - the definition of no
    information - while the bank's actual narrative sat invisible in
    imported_payee. Notes now carry the description when it says more
    than the payee line already does, then the flags that matter in a
    register. Provenance lives in obdi's ledger, where it belongs."""
    payee = transaction.counterparty or transaction.description
    parts = []
    if transaction.description and transaction.description != payee:
        parts.append(transaction.description)
    if transaction.transfer_confirmed:
        parts.append("internal transfer")
    elif transaction.is_internal_transfer:
        # Claimed by the provider but the opposite side was never found in
        # the store - worth a distinct label, because the reader is exactly
        # the person who can tell whether an account is missing. It stays an
        # ordinary row in Actual, and this note is the only place it says so.
        parts.append("internal transfer (unpaired claim)")
    if transaction.status is TransactionStatus.PENDING:
        parts.append("pending")
    if transaction.source == UNITEMISED_SOURCE:
        parts.append("derived from the stated balances, not itemised")
    return " | ".join(parts)


def _sendable(
    transactions: list[Transaction], bindings: list[ActualAccountBinding]
) -> list[tuple[Transaction, str]]:
    """Each transaction that reaches the budget, with its Actual account.

    Accounts with no binding are skipped rather than guessed at, because
    inventing a destination would scatter transactions into the wrong budget.
    A voided pending row is history, not money: it either settled as a
    different row (already here) or never happened.
    """
    by_canonical = {binding.canonical_id: binding.actual_account_id for binding in bindings}
    sendable: list[tuple[Transaction, str]] = []
    for transaction in transactions:
        actual_account = by_canonical.get(transaction.account_id)
        # An unbound account is also a withheld reason; it is tested here so
        # the type checker knows the destination exists below.
        if actual_account is None or withheld_reason(transaction, bound=True):
            continue
        sendable.append((transaction, actual_account))
    return sendable


#: The imported id of an account's opening-balance row, which is the prefix
#: plus the canonical account reference. It is not the shape of a payment's id
#: (a content key and an occurrence), so the applier recognises it separately
#: (`isObdiImportedId` in applier/audit.mjs, which must agree with this).
OPENING_IMPORTED_ID_PREFIX = "obdi-opening:"

#: Actual's own name for the entry that holds an account's opening balance.
OPENING_PAYEE = "Starting Balance"


@dataclass(frozen=True)
class OpeningBalance:
    """An account's derived opening balance, as the end of `as_at`."""

    canonical_id: str
    as_at: date
    amount_minor: int


def opening_imported_id(canonical_id: str) -> str:
    return f"{OPENING_IMPORTED_ID_PREFIX}{canonical_id}"


def to_actual_opening(opening: OpeningBalance) -> dict[str, object]:
    """The one extra row an account's list carries for its opening balance.

    Cleared, because it is a statement of fact rather than a payment awaiting
    settlement, and flagged as Actual's starting balance so the budget shows
    it as one. Actual keeps an existing row's values on a re-import, so the
    applier corrects a changed amount or date afterwards.
    """
    return {
        "imported_id": opening_imported_id(opening.canonical_id),
        "date": opening.as_at.isoformat(),
        "amount": opening.amount_minor,
        "payee_name": OPENING_PAYEE,
        "starting_balance_flag": True,
        "cleared": True,
    }


def build_opening_entries(
    bindings: list[ActualAccountBinding], openings: list[OpeningBalance]
) -> list[dict[str, object]]:
    """What the applier must keep exact, one entry per bound account's opening.

    An opening for an account with no binding has no Actual account to land
    in and is left out, as an unbound account's transactions are.
    """
    by_canonical = {binding.canonical_id: binding.actual_account_id for binding in bindings}
    return [
        {
            "account": by_canonical[opening.canonical_id],
            "imported_id": opening_imported_id(opening.canonical_id),
            "date": opening.as_at.isoformat(),
            "amount": opening.amount_minor,
        }
        for opening in openings
        if opening.canonical_id in by_canonical
    ]


def build_payload(
    transactions: list[Transaction],
    bindings: list[ActualAccountBinding],
    openings: list[OpeningBalance] | None = None,
    cleared: Collection[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Group transactions by Actual account, ready to import.

    Movements between your own accounts go as ordinary rows, each with its
    own imported id: omitting them leaves every account's balance out by the
    sum of its transfers. A flat import cannot express Actual's transfer
    type, so the pairing is sent separately (`build_transfer_pairs`) and
    linked by the applier once the rows are in. Whether the provider claimed
    the transfer or the pairing pass proved it, the row is sent and its note
    says which.

    An account with a derived opening balance carries it as one further row
    (`to_actual_opening`), so that Actual's balance is the account's and not
    merely the sum of the history a provider happened to hold.
    """
    payload: dict[str, list[dict[str, object]]] = defaultdict(list)
    by_canonical = {binding.canonical_id: binding.actual_account_id for binding in bindings}
    for opening in openings or []:
        actual_account = by_canonical.get(opening.canonical_id)
        if actual_account is not None:
            payload[actual_account].append(to_actual_opening(opening))
    for transaction, actual_account in _sendable(transactions, bindings):
        payload[actual_account].append(to_actual_transaction(transaction, cleared=cleared))
    return dict(payload)


#: The reasons a row is kept out of the payload, as the words a reader sees.
WITHHELD_VOID = "void"
WITHHELD_FOLDED = (
    "a copy of a payment held under a Space, or the same money a statement itemises"
)
WITHHELD_UNBOUND = "no Actual binding"
WITHHELD_REVERSED = "reversed by the bank"


def withheld_reason(transaction: Transaction, *, bound: bool) -> str | None:
    """Why this row is NOT sent to Actual, or None when it is.

    The single statement of what the payload leaves out, shared by the
    payload builder and by anything that must say what the builder would do
    without building it. A copy of this rule elsewhere would agree until the
    day somebody changed one.

    A movement between your own accounts is NOT a reason.
    Withholding those left every account's balance in Actual out by the sum
    of its transfers; they are sent as rows and linked afterwards.
    """
    # A voided pending row is history, not money: it either settled as
    # a different row (already in the payload) or never happened.
    if transaction.status is TransactionStatus.VOID:
        return WITHHELD_VOID
    # A main-account copy of a payment held under a Space (the Space's row is
    # the one sent), or a feed row that is the same money as a statement's rows
    # (the statement's are the ones sent): either way the payment reaches the
    # budget once.
    if transaction.status is TransactionStatus.FOLDED:
        return WITHHELD_FOLDED
    if transaction.status is TransactionStatus.REVERSED:
        return WITHHELD_REVERSED
    if not bound:
        return WITHHELD_UNBOUND
    return None


def build_transfer_pairs(
    transactions: list[Transaction],
    bindings: list[ActualAccountBinding],
    pairs: list[tuple[str, str]],
) -> list[dict[str, object]]:
    """The confirmed pairs whose two rows are both in the payload.

    A pair is listed only when the applier will be able to find both rows:
    each leg bound, to two DIFFERENT Actual accounts (one account cannot hold
    a transfer to itself), and neither void. Anything short of that is not
    listed - its row still travels as an ordinary row, so the balance is
    right and the note says what it is. Only pairs the pairing pass PROVED
    are linked; a provider's unpaired claim has no partner to link to.
    """
    held = {
        transaction.entity_id: (transaction, actual_account)
        for transaction, actual_account in _sendable(transactions, bindings)
    }
    listed: list[dict[str, object]] = []
    for debit_id, credit_id in pairs:
        debit = held.get(debit_id)
        credit = held.get(credit_id)
        if debit is None or credit is None or debit[1] == credit[1]:
            continue
        listed.append({"debit": _leg(*debit), "credit": _leg(*credit)})
    return listed


def _leg(transaction: Transaction, actual_account: str) -> dict[str, object]:
    row = to_actual_transaction(transaction)
    return {
        "account": actual_account,
        # obdi's own name for the account, so a skipped pair can be named on a page.
        "account_name": transaction.account_id,
        "imported_id": row["imported_id"],
        "date": row["date"],
        "amount": row["amount"],
    }


def unbound_accounts(
    transactions: list[Transaction], bindings: list[ActualAccountBinding]
) -> list[str]:
    """Canonical accounts with no Actual destination.

    Reported rather than silently dropped: an account quietly missing from a
    budget looks like missing spending, and is very hard to notice.
    """
    bound = {binding.canonical_id for binding in bindings}
    return sorted({t.account_id for t in transactions} - bound)
