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


#: The kinds of identifier an entity holds, strongest first (`docs/design/2026-10-commitments/
#: entities.md` section 2 is the reasoning). Said once here, below the store, and read by
#: `analysis.entities` (whose ladder adds the kinds a name is LINKED by) and by the store, which
#: refuses a kind outside these since a row of any other kind could never be matched. ACCOUNT: the
#: other party's account number. SOURCE_ID: a source's own id for the party. STATED_NAME: the name
#: a source states for the party. DESCRIPTION: a shape of the printed description.
ACCOUNT = "account"
SOURCE_ID = "source id"
STATED_NAME = "stated name"
DESCRIPTION = "description"
IDENTIFIER_KINDS = (ACCOUNT, SOURCE_ID, STATED_NAME, DESCRIPTION)

#: Whether an identifier was attached by a press of the owner's or learned from the transactions
#: (`entities.md` section 4). Only declared identifiers are written today.
DECLARED = "declared"
LEARNED = "learned"
IDENTIFIER_BASES = (DECLARED, LEARNED)


class EntityRefused(DataError):
    """A change to the entities that was not made, said in the words the page shows."""


@dataclass(frozen=True)
class Identifier:
    """One thing an entity is known by: a kind (`IDENTIFIER_KINDS`) and the value as the analysis
    holds it (a name or a shape, or an account number with its spacing and dashes taken out).

    `source` names the source that stated it where that matters: a stated name is per source, an
    account number is not (""). It is kept for the page and not matched on, so one party stated
    the same name by two sources is one identifier. `support` is how many transactions carried it
    when it was attached or learned; the page counts the live ones itself."""

    kind: str
    value: str
    source: str = ""
    basis: str = DECLARED
    support: int = 0


@dataclass(frozen=True)
class Entity:
    """One entity the owner named, and the identifiers attached to it now.

    `shapes` is the value of each identifier, once, and the names a live rule adds: the names under
    the entity, which is what the pages list and count by (a page's counts are by name). It is
    kept beside `identifiers`, which says what kind each was attached as, so that the callers
    that want only the names did not all change."""

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
    #: What was attached by hand, with its kind. Rule-matched names are not here.
    identifiers: tuple[Identifier, ...] = ()


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
