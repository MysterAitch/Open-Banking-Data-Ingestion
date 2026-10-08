"""What the store returns and raises where an account's ownership is concerned.

An account's ownership is a declared fact, like its kind and parent: which entities own it and in
what shares (`docs/design/2026-10-commitments/plan.md` section 3, question 7). Shares are whole
percentages that add up to 100, so a half is 50 and no share is a fraction a float could get
wrong. An account with nothing declared is the owner's alone; the store keeps no row for that
default, so declaring "solely mine" removes the rows rather than writing a 100.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import DataError

#: A share is whole percent of the account, and the shares of one account add up to this.
WHOLE = 100


class OwnershipRefused(DataError):
    """A change to an account's ownership that was not made, said in the words the page shows."""


@dataclass(frozen=True)
class OwnerShare:
    """One entity's share of one account.

    `owner` is whether the entity is the one that stands for the household owner (the entity with
    `entity_records.OWNER_ROLE`), which is whose share Position counts."""

    entity_id: int
    name: str
    percent: int
    owner: bool = False
