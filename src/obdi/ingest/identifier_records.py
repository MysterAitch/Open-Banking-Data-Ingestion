"""The numbers an account answers to, as the owner declares them (`account_identifiers`).

Two kinds. An `account` identifier is a sort code and account number (or a non-GB IBAN) in the one
canonical form a transaction's `party_account` takes (`party_fields`), so a payment stating it is
a payment to the account. A `card` identifier is the last four digits of a card and NOTHING more
of it: it is kept as a record of which card numbers an account has had (several are replaced, one
switched network) and joins no row, since a card's last four is shared by chance across cards and
a payment that states one states it as a card, not as an account.

The owner's own `valid_from` and `valid_to` say when the number was in use; they are separate from
the days the transactions state it, which are read from the rows when a page needs them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..core.errors import DataError
from .party_fields import iban_account, uk_account

ACCOUNT = "account"
CARD = "card"
KINDS = (ACCOUNT, CARD)

#: How many digits of a card are kept.
CARD_DIGITS = 4

_TYPED_UK = re.compile(r"^\s*(\d{2}[\s-]?\d{2}[\s-]?\d{2})[\s-]+(\d[\d\s]*)$")


@dataclass(frozen=True)
class IdentifierEntry:
    """One number an account answers to, as stored."""

    id: int
    kind: str
    value: str
    valid_from: date | None
    valid_to: date | None


def checked_identifier(kind: str, value: str) -> str:
    """The value as it is kept, or a `DataError` saying what it must be.

    A typed sort code and number may carry spaces or hyphens and are written in the canonical
    form; a card is refused unless it is exactly the last four digits, so a full card number
    pasted by mistake is never stored, even in part."""
    if kind == CARD:
        if not re.fullmatch(rf"\d{{{CARD_DIGITS}}}", value.strip()):
            raise DataError(
                f"A card is kept by its last {CARD_DIGITS} digits and nothing more; what was "
                "typed is not exactly that. Nothing was changed."
            )
        return value.strip()
    if kind == ACCOUNT:
        typed = _TYPED_UK.match(value)
        found = uk_account(typed.group(1), typed.group(2)) if typed else iban_account(value)
        if not found:
            raise DataError(
                "An account is a six-digit sort code and an eight-digit account number, or an "
                "IBAN; what was typed is neither. Nothing was changed."
            )
        return found
    raise DataError("An identifier is an account or a card. Nothing was changed.")


def checked_day(text: str) -> date | None:
    """A date typed as YYYY-MM-DD, or None where nothing was typed."""
    if not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise DataError(
            f"'{text.strip()}' is not a date written YYYY-MM-DD. Nothing was changed."
        ) from None
