"""Who a payment was to: the shapes counterparties print, and the entities they are gathered into.

An ENTITY is one name over every shape the sources print for it. The owner decides which shapes
belong together (`Store.create_entity` and its neighbours keep that decision across every rebuild
from raw); this module holds what is computed from the transactions alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import DataError


class EntityRefused(DataError):
    """A change to the entities that was not made, said in the words the page shows."""


@dataclass(frozen=True)
class Entity:
    """One entity the owner named, and the shapes attached to it now."""

    id: int
    name: str
    #: The entity this one sits under, or None; kept, and read by nothing yet.
    parent_id: int | None
    shapes: tuple[str, ...]
