"""Whose an account is, and how much of it counts as the owner's.

An account's ownership is declared (`Store.declare_ownership`): which entities own it, in whole
percentages that add up to 100. Position counts the OWNER'S share of a joint account's balance and
of what leaves it, so a half-share account holding a thousand pounds is five hundred held, and the
basis beside the figure says "your half of the balance" so a smaller number than the bank's is
never a surprise.

An account with nothing declared is the owner's alone: no row is written for that default, and
every reader goes through `share_of`, which says 100 for it. That is the one place the default is
decided.

The press on an account's page declares a joint account of two owners, the owner and one other
entity, because that is the case that exists (`plan.md` question 7: a joint account with a
partner). The store holds any number of owners and refuses shares that do not add up; the form
asks for two so that its one number is the owner's and the other's is the rest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..core.masking import mask_text
from ..ingest.ownership_records import WHOLE, OwnerShare, OwnershipRefused
from ..ingest.store import Store

#: The presses on an account's ownership.
ACT_JOINT = "joint"
ACT_SOLE = "sole"

#: The longest a co-owner's name may be typed, matching an entity's name elsewhere.
NAME_LENGTH = 120

_WORDS = {50: "half", 25: "quarter", 75: "three quarters"}


def share_of(owners: Mapping[str, Sequence[OwnerShare]], account: str) -> int:
    """The whole percent of `account` that counts as the household owner's: 100 where nothing is
    declared. Declared owners without the owner among them cannot be stored, so none is read."""
    declared = owners.get(account)
    if not declared:
        return WHOLE
    return sum(o.percent for o in declared if o.owner)


def scaled(minor: int, percent: int) -> int:
    """`minor` scaled to `percent` of itself, rounded to the nearest minor unit with halves away
    from zero, so that the two halves of an odd amount are each as near as they can be and a
    negative balance is rounded the way a positive one is."""
    if percent >= WHOLE:
        return minor
    magnitude = (abs(minor) * percent + WHOLE // 2) // WHOLE
    return -magnitude if minor < 0 else magnitude


def share_words(percent: int) -> str:
    """`half`, `quarter`, or `40%`: how a share is said in "your half of the balance"."""
    return _WORDS.get(percent, f"{percent}%")


def your_share_of(percent: int, what: str) -> str:
    """"your half of the balance": the phrase a basis opens with when only a share counts."""
    return f"your {share_words(percent)} of {what}"


def owners_sentence(owners: Sequence[OwnerShare], *, hide_names: bool = False) -> str:
    """Who owns an account, as the account's page says it: "You, solely" or "You (50%) and
    Casey (50%)". The names are the owners' own, so a masked page asks for them hidden
    (`hide_names`); the shares are percentages and are said either way."""
    if not owners:
        return "You, solely"
    parts = [
        ("You" if o.owner else (mask_text(o.name) if hide_names else o.name), o.percent)
        for o in owners
    ]
    parts.sort(key=lambda part: part[0] != "You")
    shown = [f"{who} ({percent}%)" for who, percent in parts]
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def _field(form: Mapping[str, Sequence[str]], name: str) -> str:
    values = form.get(name, [])
    return " ".join(values[0].split()) if values else ""


def apply_press(
    store: Store, action: str, form: Mapping[str, Sequence[str]], *, accounts: Sequence[str]
) -> str:
    """Do what one press on an account's ownership asks and say what was done, in a sentence that
    holds no name and no amount. `accounts` are the references the store holds, so a press for an
    account that is not one is refused and not written against a typed-in name.

    `ACT_JOINT` reads the co-owner's name and the owner's share, makes the co-owner an entity
    where none has that name (an organisation or person the owner deals with, which the Entities
    page can then gather payments under), and declares the two shares. Refused for an empty name,
    a share that is not a whole number, and whatever the store refuses (a share outside 1 to 99,
    which would leave the other owner with none or the owner with none)."""
    ref = _field(form, "ref")
    if ref not in accounts:
        raise OwnershipRefused("That is not an account held here; the page may have changed.")
    if action == ACT_SOLE:
        store.clear_ownership(ref)
        return "This account is yours alone."
    if action != ACT_JOINT:
        raise OwnershipRefused("That is not something this page does.")
    name = _field(form, "partner")
    if not name:
        raise OwnershipRefused("Say who owns the account with you.")
    if len(name) > NAME_LENGTH:
        raise OwnershipRefused(f"That name is longer than {NAME_LENGTH} characters.")
    raw = _field(form, "share")
    if not raw.isdigit():
        raise OwnershipRefused("Your share is a whole number of percent, such as 50.")
    share = int(raw)
    if not 1 <= share <= WHOLE - 1:
        raise OwnershipRefused(
            "A joint account has two owners who each hold at least 1 percent; "
            "your share is from 1 to 99."
        )
    owner = store.owner_entity()
    partner = store.entity_named(name)
    if partner is None:
        partner = store.create_empty_entity(name)
    if partner == owner:
        raise OwnershipRefused("The other owner is someone other than you.")
    store.declare_ownership(ref, [(owner, share), (partner, WHOLE - share)])
    return f"This account is now shared: your share is {share}%."
