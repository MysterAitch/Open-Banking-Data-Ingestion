"""What the store returns and raises where money owed on one transaction is concerned.

A RECEIVABLE is the owner's word that a payment he made is owed back to him by an entity: a coffee
bought while volunteering, owed by the organisation (`docs/design/2026-10-commitments/plan.md`,
worked example E). It is declared on the transaction by one press, with an optional LABEL
("volunteering") that is a second axis beside the category and partitions nothing.

It stays open until a transfer from that entity meets it (`analysis.flows`, the same matcher as a
partner's share of a bill) or the owner closes it by hand, as written off or received elsewhere,
with the reason kept. Whether a transfer has met it is derived from the transactions on every read
and never stored: a stored match would go stale the moment the rows beneath it were rebuilt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..core.errors import DataError

#: How the owner closes a receivable that no transfer will meet.
WRITTEN_OFF = "written-off"
RECEIVED_ELSEWHERE = "received-elsewhere"
CLOSE_HOWS = (WRITTEN_OFF, RECEIVED_ELSEWHERE)

#: How long after the expense a receivable is expected back where the owner gave no day.
DEFAULT_EXPECTED_DAYS = 28

#: The longest a label or a reason may be typed.
TEXT_LENGTH = 120


class ReceivableRefused(DataError):
    """A change to the receivables that was not made, said in the words the page shows."""


@dataclass(frozen=True)
class Receivable:
    """One amount owed to the owner on one transaction.

    `row_ref` is the transaction's id and `day` its day, so that a transfer before the expense
    can never be taken for its reimbursement; `amount_minor` is a magnitude, declared by the owner
    (a part of the transaction may be reclaimable). `closed_how` is "" while the receivable is
    open, else `CLOSE_HOWS`, with `closed_reason` the owner's words for it."""

    id: int
    account: str
    row_ref: str
    day: date
    debtor: int
    amount_minor: int
    label: str
    expected_day: date
    declared_at: str
    closed_how: str = ""
    closed_reason: str = ""
    closed_at: str = ""
