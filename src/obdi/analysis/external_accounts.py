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

from ..core.models import Transaction
from ..core.plural import plural
from ..ingest.accounts import AccountRecord, AccountRef
from ..ingest.entity_records import EntityRefused
from ..ingest.store import Store
from .entities import ACCOUNT_KEY_PREFIX, Named, UnheldAccount

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


def unheld_accounts(
    rows: Sequence[Transaction],
    named: Sequence[Named],
    dismissed: Collection[str] = (),
) -> tuple[tuple[UnheldAccount, ...], int]:
    """The accounts payments state that nothing owns, most payments first, and how many more the
    owner has declined to declare. `named` is `name_rows`' answer for `rows`; `dismissed` is the
    keys the owner declined (`dismissed_keys`)."""
    found = _unheld(rows, named)
    offered = [
        UnheldAccount(key, number[-ENDING_LENGTH:], count)
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
