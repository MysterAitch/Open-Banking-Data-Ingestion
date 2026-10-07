"""Who a payment was to: the shapes counterparties print, and the entities they are gathered into.

An ENTITY is one name over every shape the sources print for it. The owner decides which shapes
belong together (`Store.create_entity` and its neighbours keep that decision across every rebuild
from raw); this module holds what is computed from the transactions alone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from .errors import DataError
from .identity import normalise_description

#: The rules that can join two shapes into one proposed group, said once here and by name
#: everywhere else. OPENING_WORDS: they begin with the same two or more words. SAME_WORDS: once
#: one-letter codes are set aside, they are made of the same words in whatever order.
OPENING_WORDS = "opening words"
SAME_WORDS = "same words"

#: Fewest words at the start that two shapes must share to be one proposed group. A single shared
#: word is two retailers that begin alike far more often than one retailer, and a wrong merge is
#: the owner's to find, so the rule leaves it unproposed.
MIN_OPENING_WORDS = 2


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


def shape_of(text: str) -> str:
    """The shape of a counterparty as printed: the normalised description without any word that
    holds a digit, since a reference, a store number, a mandate, or a card tail is what changes
    between two sightings of one payee. Empty where nothing but codes is left.

    The one definition: the recurring detector and the Entities page both read it from here.
    """
    return " ".join(
        w for w in normalise_description(text).split() if not any(c.isdigit() for c in w)
    )


def count_shapes(descriptions: Iterable[str]) -> dict[str, int]:
    """How many of the descriptions have each shape; descriptions with no shape are not counted."""
    counted = Counter(shape_of(text) for text in descriptions)
    counted.pop("", None)
    return dict(counted)


@dataclass(frozen=True)
class Proposal:
    """Shapes the rules think are one counterparty, for the owner to accept, trim, or ignore."""

    #: The opening words the shapes share, said as a name; the owner may change it.
    name: str
    #: Most-used first, then alphabetical.
    shapes: tuple[str, ...]
    #: The rules (`OPENING_WORDS`, `SAME_WORDS`) under which some pair in the group joined.
    rules: frozenset[str]
    #: Transactions across the shapes.
    transactions: int


def _words(shape: str) -> frozenset[str]:
    """The words that tell a shape apart: its own, without the one-letter codes."""
    return frozenset(word for word in shape.split() if len(word) > 1)


def _common_opening(shapes: Collection[str]) -> list[str]:
    run: list[str] | None = None
    for shape in shapes:
        words = shape.split()
        if run is None:
            run = words
            continue
        keep = 0
        while keep < min(len(run), len(words)) and run[keep] == words[keep]:
            keep += 1
        run = run[:keep]
    return run or []


#: Most shapes a proposed group may hold. Measured on the invented household's large store: the
#: opening words "faster payment" joined 25 different merchants, because they name how a payment
#: was made and not who it was to, while the real variants of one retailer were four towns. A
#: group above this is counted and left unproposed (`Proposals.too_broad`), never merged on the
#: owner's one press; the figure is a threshold to relax on evidence, not a property of retailers.
MAX_SHAPES_PROPOSED = 8


@dataclass(frozen=True)
class Proposals:
    """What `propose_groups` found: the groups to offer, and the ones too broad to offer."""

    groups: tuple[Proposal, ...]
    too_broad: tuple[Proposal, ...]


def propose_groups(
    counts: Mapping[str, int], *, taken: Collection[str] = frozenset()
) -> Proposals:
    """The groups of free shapes that plain text analysis says are one counterparty.

    Two shapes join where they share their first `MIN_OPENING_WORDS` words, or where they hold the
    same words once one-letter codes are set aside; joining is transitive, so a group is every
    shape reachable through either rule. Shapes in `taken` (already under an entity) are shown
    where they are and never proposed again. A group is at least two shapes and at most
    `MAX_SHAPES_PROPOSED`, largest first.
    """
    free = sorted(shape for shape in counts if shape not in taken and _words(shape))
    parent = {shape: shape for shape in free}

    def root(shape: str) -> str:
        while parent[shape] != shape:
            parent[shape] = parent[parent[shape]]
            shape = parent[shape]
        return shape

    buckets: dict[tuple[str, object], list[str]] = {}
    for shape in free:
        words = shape.split()
        if len(words) >= MIN_OPENING_WORDS:
            opening = tuple(words[:MIN_OPENING_WORDS])
            buckets.setdefault((OPENING_WORDS, opening), []).append(shape)
        buckets.setdefault((SAME_WORDS, _words(shape)), []).append(shape)
    for members in buckets.values():
        for other in members[1:]:
            parent[root(other)] = root(members[0])

    groups: dict[str, list[str]] = {}
    for shape in free:
        groups.setdefault(root(shape), []).append(shape)
    found: list[Proposal] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        rules = frozenset(
            rule
            for (rule, _), joined in buckets.items()
            if len(joined) >= 2 and root(joined[0]) == root(members[0])
        )
        ordered = tuple(sorted(members, key=lambda s: (-counts[s], s)))
        shared = _common_opening(members)
        # No common opening (the words were reordered): the commonest shape, of those the
        # shortest, since a longer one carries a town or a branch.
        words = shared or min(members, key=lambda s: (-counts[s], len(s.split()), s)).split()
        found.append(
            Proposal(
                name=" ".join(word.capitalize() for word in words),
                shapes=ordered,
                rules=rules,
                transactions=sum(counts[s] for s in members),
            )
        )
    found.sort(key=lambda p: (-p.transactions, p.name, p.shapes))
    return Proposals(
        groups=tuple(p for p in found if len(p.shapes) <= MAX_SHAPES_PROPOSED),
        too_broad=tuple(p for p in found if len(p.shapes) > MAX_SHAPES_PROPOSED),
    )
