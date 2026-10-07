"""What the store returns and raises where the owner's entities are concerned.

An entity is the owner's name for who a payment was to (`analysis.entities` holds how the names
under it are decided). The store keeps the rows - an entity, the names attached to it by hand, the
rules it keeps, the names split apart from a rule - and hands them back as these records, so the
records sit here, below the store, and the analysis imports them rather than the store importing
the analysis. Nothing here reads a name or a rule: a rule is words and a kind as given, and what
it matches is for the analysis to say.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import DataError

#: The role of the entity that stands for the owner: the payee of every payment between his own
#: accounts. No instance declares the owner's name anywhere, so the entity is made as `OWNER_NAME`
#: unless the owner has typed another, and renamed like any entity.
OWNER_ROLE = "owner"
OWNER_NAME = "Me"

#: The two kinds of rule an entity keeps, said once here and everywhere else by name. BEGINS: the
#: name begins with these words. CONTAINS: the name holds these words, in any order. A third kind
#: or a side effect of a rule (a label, a category) is added by the work that needs it. The store
#: refuses a kind outside these, since a row of any other kind could never be read back.
BEGINS = "begins"
CONTAINS = "contains"
RULE_KINDS = (BEGINS, CONTAINS)


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
    #: What the entity is to the household (`OWNER_ROLE`), or None for a payee like any other.
    role: str | None = None
    #: The names in `shapes` that are there only because a live rule of the entity matches them
    #: (`analysis.entities.with_rules`); the rest were attached by hand. Empty where rules were
    #: not applied, as they are not in what the store returns.
    by_rule: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityRule:
    """One rule an entity keeps: a name that matches it is under the entity without being
    attached by hand, so a new variant of a payee attaches on sight."""

    id: int
    entity_id: int
    kind: str
    #: The words as kept: the rule's text in the form a name is held in
    #: (`analysis.entities.clean_rule` makes it so before the store is asked to keep it).
    words: str
