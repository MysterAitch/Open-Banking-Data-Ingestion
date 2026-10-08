"""Payments to an account obdi holds nothing for, and the owner's declaration that it is theirs.

`docs/design/2026-10-commitments/entities.md` section 3a: where payments go to an account
identifier no held account owns, the page offers "This account is mine", and what makes the other
party "me" is that declaration - never the owner's name printed in a description. The declaration
is an external account (`AccountRecord.external`) carrying the identifier; after it, every row that
states the identifier is a transfer to it (`entities.held_counterparts`).

An account is offered by the key `name_of` makes of it (`acct-<digest>`), which is what a form
carries; the number itself is read back from the rows when a press arrives, so it is on no page, in
no attribute, and in no form. The page shows how many payments and the last four characters of the
number, and nothing else of it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import date

from ..core.errors import DataError
from ..core.models import Transaction
from ..core.plural import plural
from ..ingest.accounts import AccountRecord, AccountRef
from ..ingest.entity_records import EntityRefused
from ..ingest.store import Store
from ..read.account_names import AccountsShown
from ..read.ledger import row_anchor
from .entities import ACCOUNT_KEY_PREFIX, HELD_PREFIX, Named, UnheldAccount, UnheldPayment

#: The kind a declared external account carries, so a reader of the registry can tell what it is.
EXTERNAL_KIND = "external"

#: Begins the canonical name of an external account; the rest is the start of the key's digest, so
#: the same number declared twice (after a removal) is named alike and two numbers are not.
EXTERNAL_REF_PREFIX = "external-"
_REF_DIGEST_LENGTH = 12

#: Begins the name of the preference that records the owner declined to declare an account.
NOT_MINE_PREFIX = "not-an-external-account:"

#: The longest a label may be typed.
LABEL_LENGTH = 120

#: How many characters of an account's number the page may show.
ENDING_LENGTH = 4

#: How many payments the fold under an unheld account lists; the rest are counted.
PAID_SHOWN = 10


def _unheld(
    rows: Sequence[Transaction], named: Sequence[Named]
) -> dict[str, tuple[str, int]]:
    """Each account key that rows state and no held or external account owns, with the number it
    stands for and how many rows state it. A row is named by an account key only where nothing
    owns the number: a row that is a leg of a confirmed pair, or states a number the pairs or a
    declaration lead to an account, is named for that account (`held_counterparts`)."""
    counts: Counter[str] = Counter()
    numbers: dict[str, str] = {}
    for row, item in zip(rows, named, strict=True):
        if row.party_account and item.name.startswith(ACCOUNT_KEY_PREFIX):
            counts[item.name] += 1
            numbers.setdefault(item.name, row.party_account)
    return {key: (numbers[key], count) for key, count in counts.items()}


def _paid(
    rows: Sequence[Transaction],
    named: Sequence[Named],
    keys: Collection[str],
    names: AccountsShown | None,
) -> dict[str, tuple[UnheldPayment, ...]]:
    """The newest `PAID_SHOWN` payments to each of `keys`, newest first, from the rows already
    read: no statement of its own, however many accounts are listed."""
    wanted: dict[str, list[Transaction]] = {}
    for row, item in zip(rows, named, strict=True):
        if row.party_account and item.name in keys:
            wanted.setdefault(item.name, []).append(row)
    return {
        key: tuple(
            UnheldPayment(
                row.value_date,
                row.source,
                row.description,
                row.amount_minor,
                row.currency,
                row.account_id,
                names.of(row.account_id).label if names is not None else "",
                row_anchor(row.entity_id),
            )
            for row in sorted(found, key=lambda r: (r.value_date, r.entity_id), reverse=True)[
                :PAID_SHOWN
            ]
        )
        for key, found in wanted.items()
    }


def unheld_accounts(
    rows: Sequence[Transaction],
    named: Sequence[Named],
    dismissed: Collection[str] = (),
    names: AccountsShown | None = None,
) -> tuple[tuple[UnheldAccount, ...], int]:
    """The accounts payments state that nothing owns, most payments first, and how many more the
    owner has declined to declare. `named` is `name_rows`' answer for `rows`; `dismissed` is the
    keys the owner declined (`dismissed_keys`); `names` labels the account each payment left."""
    found = _unheld(rows, named)
    paid = _paid(rows, named, {key for key in found if key not in dismissed}, names)
    offered = [
        UnheldAccount(key, number[-ENDING_LENGTH:], count, paid.get(key, ()))
        for key, (number, count) in found.items()
        if key not in dismissed
    ]
    offered.sort(key=lambda account: (-account.payments, account.ending, account.key))
    return tuple(offered), sum(1 for key in found if key in dismissed)


def dismissed_keys(store: Store) -> set[str]:
    """The keys of the accounts the owner declined to declare."""
    return {
        name[len(NOT_MINE_PREFIX) :] for name in store.preference_names(NOT_MINE_PREFIX)
    }


def _account_of(
    rows: Sequence[Transaction], named: Sequence[Named], form: Mapping[str, Sequence[str]]
) -> tuple[str, str, int]:
    """The key a press names, with the number it stands for and its payments; refused where the
    rows no longer state it unowned, since the page was made before they changed."""
    values = form.get("account", [])
    key = values[0].strip() if values else ""
    found = _unheld(rows, named).get(key)
    if found is None:
        raise EntityRefused(
            "That account is not one payments go to that is not held here now; the page may "
            "have changed."
        )
    return key, found[0], found[1]


def declare_external(
    store: Store,
    rows: Sequence[Transaction],
    named: Sequence[Named],
    form: Mapping[str, Sequence[str]],
) -> str:
    """Declare the account a press names as the owner's, called what the form's label says, and
    say what was done. Refused, writing nothing, where the label is empty or too long, the
    account is no longer one payments state that nothing owns, or the name it would take is
    already declared."""
    labels = form.get("label", [])
    label = " ".join((labels[0] if labels else "").split())
    if not label:
        raise EntityRefused("Say what to call the account; nothing was declared.")
    if len(label) > LABEL_LENGTH:
        raise EntityRefused(
            f"That name is longer than {LABEL_LENGTH} characters; nothing was declared."
        )
    key, number, payments = _account_of(rows, named, form)
    ref = EXTERNAL_REF_PREFIX + key[len(ACCOUNT_KEY_PREFIX) :][:_REF_DIGEST_LENGTH]
    if store.declared_account(AccountRef(ref)) is not None:
        raise EntityRefused("That account is already declared; nothing was changed.")
    store.declare_account(
        AccountRecord(
            ref=AccountRef(ref),
            kind=EXTERNAL_KIND,
            label=label,
            identifier=number,
            external=True,
        )
    )
    store.forget_preference(NOT_MINE_PREFIX + key)
    return (
        f"Declared {label} as your account. {plural(payments, 'payment')} to the account "
        f"ending {number[-ENDING_LENGTH:]} are now transfers to it."
    )


def held_choices(store: Store, names: AccountsShown) -> tuple[tuple[str, str], ...]:
    """The accounts "It is this account" may name, each once: the declared accounts obdi holds a
    source for (never an external one), as (canonical name, label as the account pages name it),
    by label then name. An account known only from its rows has no declaration to carry a number
    and is not offered; it is declared on the accounts page first."""
    return tuple(
        sorted(
            ((ref, names.of(ref).name) for ref in store.held_account_refs()),
            key=lambda choice: (choice[1].casefold(), choice[0]),
        )
    )


def identify_held(
    store: Store,
    rows: Sequence[Transaction],
    named: Sequence[Named],
    form: Mapping[str, Sequence[str]],
) -> str:
    """Say that the held account a press names answers to the number the unheld account it names
    stands for, and say what was done. The account then answers to that number as well as any it
    had (a sort-code migration gives one account a second), and every payment stating it is a
    transfer to the account, paired or not. Refused, writing nothing, where the unheld account is
    no longer one payments state, the held account is not one declared here, or another account
    already answers to the number."""
    key, number, payments = _account_of(rows, named, form)
    values = form.get("held", [])
    ref = values[0].strip() if values else ""
    if not ref:
        raise EntityRefused("Say which account it is; nothing was changed.")
    try:
        store.add_account_identifier(AccountRef(ref), number)
    except DataError as refusal:
        raise EntityRefused(str(refusal)) from None
    store.forget_preference(NOT_MINE_PREFIX + key)
    return (
        f"The account ending {number[-ENDING_LENGTH:]} is now {ref}. "
        f"{plural(payments, 'payment')} to it are transfers to that account."
    )


def other_leg_notes(
    rows: Sequence[Transaction],
    named: Sequence[Named],
    pairs: Collection[tuple[str, str]],
    first_rows: Mapping[str, date],
    names: AccountsShown,
) -> dict[str, str]:
    """For each unpaired payment named as a transfer to a held account whose rows begin AFTER the
    payment's day, the sentence that says why the other leg is not held, by the payment's entity.

    A transfer between accounts of unequal history has one side, and the model says so rather
    than inventing a row. `first_rows` is each account's first row (`overview.first_row_dates`).
    A payment the pairs tie to a row is not noted, nor one the account's rows cover: an uncovered
    day that falls inside the account's history is a missing row, which is not claimed here.
    Only the start of an account's rows is read; an account whose rows END before a payment is
    not noted."""
    paired = {entity for pair in pairs for entity in pair}
    notes: dict[str, str] = {}
    for row, item in zip(rows, named, strict=True):
        if not item.name.startswith(HELD_PREFIX) or row.entity_id in paired:
            continue
        ref = item.name[len(HELD_PREFIX) :]
        began = first_rows.get(ref)
        if began is not None and row.value_date < began:
            notes[row.entity_id] = (
                f"{names.of(ref).name}'s rows begin {began.isoformat()}, so the other leg "
                "is not held"
            )
    return notes


def dismiss(
    store: Store,
    rows: Sequence[Transaction],
    named: Sequence[Named],
    form: Mapping[str, Sequence[str]],
) -> str:
    """Record that the account a press names is not one the owner means to declare, so the page
    stops offering it. Nothing is declared and no row is named differently."""
    key, number, _payments = _account_of(rows, named, form)
    store.set_preference(NOT_MINE_PREFIX + key, "1")
    return f"The account ending {number[-ENDING_LENGTH:]} will not be offered again."


def offer_again(store: Store) -> str:
    """Forget every decline, so the accounts the owner dismissed are offered again."""
    names = store.preference_names(NOT_MINE_PREFIX)
    for name in names:
        store.forget_preference(name)
    return f"{plural(len(names), 'account')} will be offered again."
