"""Whether a stored row would reach Actual, and if not, why.

The ledger shows, row by row, whether Actual would take a row (`withheld_reason`) or refuse it
(`unsendable_reason`). The replay builds the payload from the same two answers, so what the page
says and what a push does cannot drift: a copy of either rule elsewhere would agree until the day
somebody changed one. The replay (`export.replay`) imports these; the read models cannot import the
replay, which sits above them.
"""

from __future__ import annotations

from ..core.models import Transaction, TransactionStatus


class ReplayError(RuntimeError):
    """A transaction cannot be replayed safely."""


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


def unsendable_reason(transaction: Transaction) -> str | None:
    """Why a row that is not withheld still cannot be converted to Actual's shape, or None.

    The replay raises this as a `ReplayError`; the ledger reads it without converting.
    """
    if not transaction.content_key:
        return (
            "transaction has no content key, so it has no stable imported_id. "
            "Replaying it would create a duplicate on every run."
        )

    if transaction.currency != "GBP":
        # Actual is currency-agnostic but single-currency per budget file, so a
        # foreign amount has nowhere correct to land. Emitting it unlabelled
        # would silently book a euro figure as sterling.
        return (
            f"transaction {transaction.entity_id} is in {transaction.currency}; "
            "the budget file is single-currency and there is no correct "
            "destination for a foreign amount"
        )
    return None
