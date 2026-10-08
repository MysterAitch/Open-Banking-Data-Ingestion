"""The presses that declare a receivable on a transaction and close one by hand.

ONE PRESS DECLARES (worked example E in `plan.md`): from the transaction being looked at, "owed by
this entity", with an optional label. The transaction is named by the opaque anchor the ledger
draws it by (`read.ledger.row_anchor`) and found again here from the transactions held, so a press
made on a page that has since changed is refused rather than applied to another row. The entity is
named in words: an entity of that name is used, else an empty one is made, which is the organisation
the owner deals with but never pays; nothing can meet the receivable until payments from it are
gathered under that entity on the Entities page, and the receivable says so.

The amount is the whole of the transaction unless the owner types a smaller one (part of a shop may
be reclaimable). Only money that left an account and is neither pending nor history can be owed
back.

CLOSING BY HAND takes a way (written off, received elsewhere) and a reason, both kept. A receivable
met by a transfer needs no press: that is worked out from the transactions on every read
(`flows.settle_receivables`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from ..core.models import Transaction
from ..core.money import parse_amount
from ..ingest.receivable_records import ReceivableRefused
from ..ingest.store import Store
from ..read.ledger import row_anchor
from .recurring import counts_as_occurrence

ACT_DECLARE = "declare"
ACT_CLOSE = "close"

#: The longest an entity's name may be typed here, as elsewhere.
NAME_LENGTH = 120


def _field(form: Mapping[str, Sequence[str]], name: str) -> str:
    values = form.get(name, [])
    return " ".join(values[0].split()) if values else ""


def declare(
    store: Store, rows: Sequence[Transaction], form: Mapping[str, Sequence[str]]
) -> tuple[str, str, str]:
    """Declare what the form's press asks and return (what was done, the account, the month) so the
    page can come back to the row. The sentence holds no name and no amount.

    Refused, writing nothing, for a transaction not held, one that is not money out or is pending
    or history, no entity named, an amount that is not a positive sum no larger than the
    transaction, and an expected day that is not a date or is before the expense."""
    anchor = _field(form, "anchor")
    row = next((t for t in rows if row_anchor(t.entity_id) == anchor), None) if anchor else None
    if row is None:
        raise ReceivableRefused("That transaction is not held now; the page may have changed.")
    if row.amount_minor >= 0 or not counts_as_occurrence(row):
        raise ReceivableRefused(
            "Only a payment that has left an account, and is settled, can be owed back."
        )
    name = _field(form, "debtor")
    if not name:
        raise ReceivableRefused("Say who owes it.")
    if len(name) > NAME_LENGTH:
        raise ReceivableRefused(f"That name is longer than {NAME_LENGTH} characters.")
    typed = _field(form, "amount")
    amount = abs(row.amount_minor) if not typed else parse_amount(typed)
    if amount <= 0 or amount > abs(row.amount_minor):
        raise ReceivableRefused(
            "What is owed is more than nothing and no more than the payment itself."
        )
    expected_text = _field(form, "expected")
    expected: date | None = None
    if expected_text:
        try:
            expected = date.fromisoformat(expected_text)
        except ValueError:
            raise ReceivableRefused("The expected day is a date such as 2026-11-30.") from None
    debtor = store.entity_named(name)
    made = debtor is None
    if debtor is None:
        debtor = store.create_empty_entity(name)
    store.declare_receivable(
        account=row.account_id,
        row_ref=row.entity_id,
        day=row.value_date,
        debtor=debtor,
        amount_minor=amount,
        label=_field(form, "label"),
        expected_day=expected,
    )
    said = "Marked as owed to you."
    if made:
        said += (
            " The entity is new: gather its payments under it on the Entities page so that a "
            "transfer from it can close this."
        )
    return said, row.account_id, f"{row.value_date:%Y-%m}"


def close(store: Store, form: Mapping[str, Sequence[str]]) -> str:
    """Close the receivable the form names by hand, with the way and the reason it gives, and say
    what was done. Refused as the store refuses: no such receivable, already closed, no reason."""
    ident = _field(form, "receivable")
    if not ident.isdigit():
        raise ReceivableRefused("There is no such receivable; it may have been removed.")
    how = _field(form, "how")
    store.close_receivable(int(ident), how, _field(form, "reason"))
    return "Closed; the reason is kept."


def apply_press(
    store: Store,
    action: str,
    form: Mapping[str, Sequence[str]],
    rows: Sequence[Transaction] = (),
) -> tuple[str, str, str]:
    """Do what one press asks: (what was done, the account, the month) - the account and month
    are empty for a closing, which is pressed on a page that needs neither."""
    if action == ACT_DECLARE:
        return declare(store, rows, form)
    if action == ACT_CLOSE:
        return close(store, form), "", ""
    raise ReceivableRefused("That is not something this page does.")
