"""Who a payment was to: the shapes counterparties print, and the entities they are gathered into.

An ENTITY is one name over every shape the sources print for it. The owner decides which shapes
belong together (`Store.create_entity` and its neighbours keep that decision across every rebuild
from raw); this module holds what is computed from the transactions alone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from .entity_tokens import (
    Token,
    align_initials,
    core_words,
    distinctive_words,
    is_method_only,
    tokens_of,
)
from .errors import DataError
from .identity import normalise_description

#: The rules that can join two shapes into one proposed group, said once here and by name
#: everywhere else. OPENING_WORDS: they begin with the same two or more words. SAME_WORDS: once
#: one-letter codes are set aside, they are made of the same words in whatever order.
OPENING_WORDS = "opening words"
SAME_WORDS = "same words"
#: Also a rule: the bank states the same merchant for the shapes' rows (compared as names are, see
#: `entity_tokens`). A shape whose rows state two different merchants joins none by it.
BANK_NAMES = "bank names"

#: Fewest words at the start that two shapes must share to be one proposed group. A single shared
#: word is two retailers that begin alike far more often than one retailer, and a wrong merge is
#: the owner's to find, so the rule leaves it unproposed.
MIN_OPENING_WORDS = 2


#: The role of the entity that stands for the owner: the payee of every payment between his own
#: accounts. No instance declares the owner's name anywhere, so the entity is made as `OWNER_NAME`
#: unless the owner has typed another, and renamed like any entity.
OWNER_ROLE = "owner"
OWNER_NAME = "Me"


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


#: The words a bank prints for the date a card payment was made ("ON 12 APR"), which change with
#: every sighting of one payee. The number is dropped as a word that holds a digit; this is what
#: that leaves behind. "sept" and "tues" and the like are the abbreviations banks print.
_DATE_WORDS = frozenset(
    {
        "january", "february", "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    }
)


def _shape(text: str) -> str:
    words = [w for w in normalise_description(text).split() if not any(c.isdigit() for c in w)]
    kept: list[str] = []
    index = 0
    while index < len(words):
        if words[index] == "on" and index + 1 < len(words) and words[index + 1] in _DATE_WORDS:
            index += 2
            continue
        kept.append(words[index])
        index += 1
    # A month or a weekday left at the very end is the date's remnant; one in the middle may be
    # part of a name, so only the end is cut.
    while kept and kept[-1] in _DATE_WORDS:
        kept.pop()
    return " ".join(kept)


def shape_of(text: str) -> str:
    """The shape of a counterparty as printed: the normalised description without any word that
    holds a digit, since a reference, a store number, a mandate, or a card tail is what changes
    between two sightings of one payee, and without the date a card payment prints ("on 12 apr").
    Empty where nothing but codes is left.

    The one definition: the recurring detector and the Entities page both read it from here. It
    reads the DESCRIPTION alone, never the counterparty a source may state: a bank names a
    merchant only where it identified one and a statement row names none, so keying on it gave one
    payee two shapes by source, and a series spanning both split into a stopped half and a new
    half (measured on the first real store after the change). The stated counterparty is used
    only as a reason to propose two shapes together (`BANK_NAMES`).
    """
    return _shape(text)


def shape_counterparties(rows: Iterable[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """For each shape, the counterparties its (description, counterparty) rows state and how many
    rows state each; rows stating none are not counted, and shapes with none are left out."""
    found: dict[str, Counter[str]] = {}
    for text, counterparty in rows:
        stated = " ".join(counterparty.split())
        shape = shape_of(text)
        if stated and shape:
            found.setdefault(shape, Counter())[stated] += 1
    return {shape: dict(counted) for shape, counted in found.items()}


def count_shapes(descriptions: Iterable[str]) -> dict[str, int]:
    """How many of the descriptions have each shape; descriptions with no shape are not counted."""
    counted = Counter(shape_of(text) for text in descriptions)
    counted.pop("", None)
    return dict(counted)


def count_row_legs(named: Iterable[tuple[str, bool]]) -> dict[str, int]:
    """For each shape, how many of the (description, is a transfer leg) rows that have it are
    legs; shapes with no leg are left out."""
    counted: Counter[str] = Counter()
    for text, leg in named:
        if leg:
            counted[shape_of(text)] += 1
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
    #: The words every shape in the group begins with; empty where the group was joined through
    #: words reordered, which share no opening.
    opening: str = ""
    #: The merchant the bank names for the group where `BANK_NAMES` joined it, title-cased from
    #: its commonest spelling; the group's name is this. Empty otherwise.
    bank_name: str = ""


def _bank_key(counterparty: str) -> str:
    """A stated counterparty reduced the way names are compared, or "" where nothing is left."""
    return " ".join(token.norm for token in tokens_of(shape_of(counterparty)))


def _words(named: Sequence[Token]) -> frozenset[str]:
    """The words that tell a shape apart: its own, without the one-letter codes."""
    return frozenset(token.norm for token in named if len(token.norm) > 1)


def _common_opening(members: Collection[Sequence[Token]]) -> int:
    """How many leading tokens every member shares (compared, not printed)."""
    run: Sequence[str] | None = None
    for named in members:
        norms = [token.norm for token in named]
        if run is None:
            run = norms
            continue
        keep = 0
        while keep < min(len(run), len(norms)) and run[keep] == norms[keep]:
            keep += 1
        run = run[:keep]
    return len(run or [])


#: How many of the commonest words across all shapes are too ordinary to say who a payee is. A
#: word printed in only one shape is never counted common, however few words the store holds:
#: with nothing repeated there is no ordinary word, and a tie among singletons would be settled by
#: the alphabet.
COMMON_TOKENS = 50

#: Fewest shapes that must open with one distinctive word for it alone to join them. Two shapes
#: that share a single word are far more often two payees than one; three is the first count a
#: coincidence stops explaining, measured by the owner's reading of thirteen towns of one chain.
MIN_ONE_WORD_VARIANTS = 3


def common_tokens(named: Mapping[str, Sequence[Token]]) -> frozenset[str]:
    """The `COMMON_TOKENS` words found in the most shapes, among those found in at least two."""
    found: Counter[str] = Counter()
    for shape_tokens in named.values():
        found.update({token.norm for token in shape_tokens})
    ranked = sorted((n for n in found if found[n] >= 2), key=lambda n: (-found[n], n))
    return frozenset(ranked[:COMMON_TOKENS])


def is_distinctive(norm: str, common: Collection[str]) -> bool:
    """Whether a compared word can say who a payee is: not one letter, not a payment method, not
    a country or company code, and not one of the commonest words."""
    return (
        len(norm) > 1
        and norm not in common
        and norm in distinctive_words((Token(norm, norm),))
    )


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


#: Most transactions listed under one name before "and N more": enough to see what a merge would
#: capture, and few enough that a page of proposals stays a page.
COVERED_SHOWN = 10


@dataclass(frozen=True)
class Covered:
    """One transaction a name covers, as far as the page lists it."""

    day: date
    account: str
    #: The account's label as pages show it, "" where it has none.
    account_label: str
    amount_minor: int
    currency: str
    #: The description as the source printed it.
    description: str
    #: The ledger's key for the row (`ledger.row_anchor`), which a link to it names.
    anchor: str


@dataclass(frozen=True)
class EntitiesView:
    """Everything the Entities page says: every shape with its transactions, the entities the
    owner made, and what the rules propose from the shapes not yet under one."""

    counts: Mapping[str, int]
    entities: tuple[Entity, ...]
    proposals: Proposals
    #: The newest `COVERED_SHOWN` transactions of each name, newest first, from the one read of
    #: the transactions the page already makes; `counts` says how many there are in all.
    covers: Mapping[str, tuple[Covered, ...]] = field(default_factory=dict)
    #: The shapes that are mostly the legs of the owner's own transfers, offered before any payee.
    owner: OwnerGroup | None = None
    #: Free names that share a distinctive word with an entity, offered under it.
    suggestions: tuple[Suggestion, ...] = ()

    def free_shapes(self) -> list[str]:
        """The shapes under no entity, most-used first."""
        held = {shape for entity in self.entities for shape in entity.shapes}
        return sorted((s for s in self.counts if s not in held), key=lambda s: (-self.counts[s], s))


@dataclass(frozen=True)
class OwnerGroup:
    """Shapes whose transactions are mostly legs of confirmed transfers between the owner's
    accounts, so the payee is the owner and not anyone else."""

    #: Most-used first, then alphabetical.
    shapes: tuple[str, ...]
    transactions: int
    #: How many of those transactions are transfer legs.
    legs: int


def owner_group(
    counts: Mapping[str, int], legs: Mapping[str, int], taken: Collection[str]
) -> OwnerGroup | None:
    """The free shapes more than half of whose transactions are transfer legs, or None.

    More than half and not at least half: a shape that is as often a payment to someone as a leg
    of a transfer is not shown to be the owner's, and the owner is the one to say so.
    """
    mine = sorted(
        (s for s, n in counts.items() if s not in taken and legs.get(s, 0) * 2 > n),
        key=lambda s: (-counts[s], s),
    )
    if not mine:
        return None
    return OwnerGroup(
        shapes=tuple(mine),
        transactions=sum(counts[s] for s in mine),
        legs=sum(legs[s] for s in mine),
    )


def view_of(
    counts: Mapping[str, int],
    entities: Iterable[Entity],
    legs: Mapping[str, int] | None = None,
    covers: Mapping[str, tuple[Covered, ...]] | None = None,
    counterparties: Mapping[str, Mapping[str, int]] | None = None,
) -> EntitiesView:
    """The page's view of the shapes held and the entities made from them.

    `counterparties` is `shape_counterparties`: the merchants the bank states for each shape.

    `legs` is, for each shape, how many of its transactions are legs of confirmed transfers
    between the owner's own accounts; the shapes it makes the owner's are set apart before the
    rules look for payees, so they are never offered as one.
    """
    made = tuple(entities)
    taken = {shape for entity in made for shape in entity.shapes}
    owner = owner_group(counts, legs or {}, taken)
    set_apart = taken | set(owner.shapes if owner else ())
    proposals = propose_groups(counts, taken=set_apart, counterparties=counterparties)
    offered = {s for g in (*proposals.groups, *proposals.too_broad) for s in g.shapes}
    return EntitiesView(
        counts=counts,
        entities=made,
        proposals=proposals,
        covers=covers or {},
        owner=owner,
        suggestions=suggest_for_entities(counts, made, set_apart | offered),
    )


@dataclass(frozen=True)
class Suggestion:
    """Free names that share a distinctive word with an entity the owner already made."""

    entity: Entity
    #: Most-used first, then alphabetical.
    shapes: tuple[str, ...]
    #: The compared words shared with the entity's name or shapes, across the names.
    tokens: tuple[str, ...]


def _entity_words(entity: Entity, common: Collection[str]) -> frozenset[str]:
    """The distinctive compared words of an entity's name and of each shape under it."""
    texts = [normalise_description(entity.name), *entity.shapes]
    return frozenset(
        word
        for text in texts
        for word in distinctive_words(tokens_of(text))
        if is_distinctive(word, common)
    )


def suggest_for_entities(
    counts: Mapping[str, int], entities: Sequence[Entity], set_apart: Collection[str]
) -> tuple[Suggestion, ...]:
    """A free name goes under the entity whose name or shapes it shares the most distinctive
    words with (the first by name where two share as many); names the owner has already placed,
    the owner's own, and names in a proposed group (`set_apart`) are not offered.

    A word is distinctive as `is_distinctive` says, so the commonest words of the store
    (`COMMON_TOKENS`) never join a name to an entity. The owner entity is never a target: its
    shapes are transfers, and a name that shares a word with one is not shown to be the owner's.
    """
    targets = [e for e in entities if e.role != OWNER_ROLE and e.shapes]
    if not targets:
        return ()
    common = common_tokens(
        align_initials({shape: tokens_of(shape) for shape in counts})
    )
    words = {e.id: _entity_words(e, common) for e in targets}
    held = {shape for e in entities for shape in e.shapes}
    found: dict[int, list[tuple[str, frozenset[str]]]] = {}
    for shape in sorted(counts, key=lambda s: (-counts[s], s)):
        if shape in held or shape in set_apart or is_method_only(shape):
            continue
        mine = frozenset(
            w for w in distinctive_words(tokens_of(shape)) if is_distinctive(w, common)
        )
        best: tuple[int, str, int] | None = None
        for target in targets:
            shared = mine & words[target.id]
            if shared:
                key = (-len(shared), target.name.casefold(), target.id)
                if best is None or key < best:
                    best = key
        if best is not None:
            target_id = best[2]
            found.setdefault(target_id, []).append((shape, mine & words[target_id]))
    by_id = {e.id: e for e in targets}
    return tuple(
        Suggestion(
            entity=by_id[target_id],
            shapes=tuple(shape for shape, _ in members),
            tokens=tuple(sorted({t for _, shared in members for t in shared})),
        )
        for target_id, members in sorted(
            found.items(), key=lambda item: (by_id[item[0]].name.casefold(), item[0])
        )
    )


def propose_groups(
    counts: Mapping[str, int],
    *,
    taken: Collection[str] = frozenset(),
    counterparties: Mapping[str, Mapping[str, int]] | None = None,
) -> Proposals:
    """The groups of free shapes that plain text analysis says are one counterparty.

    Two shapes join where they share their first `MIN_OPENING_WORDS` words, or where they hold the
    same words once one-letter codes are set aside, or (`BANK_NAMES`) where every row of each
    states the same merchant, `counterparties` being each shape's stated merchants and their row
    counts; joining is transitive, so a group is every shape reachable through any rule. Shapes
    in `taken` (already under an entity) are shown where they are and never proposed again. A
    group is at least two shapes and at most `MAX_SHAPES_PROPOSED`, largest first.
    """
    stated_by = counterparties or {}
    named = align_initials({shape: tokens_of(shape) for shape in counts})
    common = common_tokens(named)
    free = sorted(
        shape
        for shape in counts
        if shape not in taken and not is_method_only(shape) and _words(named[shape])
    )
    parent = {shape: shape for shape in free}

    def root(shape: str) -> str:
        while parent[shape] != shape:
            parent[shape] = parent[parent[shape]]
            shape = parent[shape]
        return shape

    # (rule, key) -> the shapes that share it, and how many it takes to join them.
    buckets: dict[tuple[str, object], list[str]] = {}
    needs: dict[tuple[str, object], int] = {}
    for shape in free:
        tokens = named[shape]
        if len(tokens) >= MIN_OPENING_WORDS:
            opening = tuple(t.norm for t in tokens[:MIN_OPENING_WORDS])
            buckets.setdefault((OPENING_WORDS, opening), []).append(shape)
            needs[(OPENING_WORDS, opening)] = 2
        first = tokens[0].norm
        if is_distinctive(first, common):
            buckets.setdefault((OPENING_WORDS, (first,)), []).append(shape)
            needs[(OPENING_WORDS, (first,))] = MIN_ONE_WORD_VARIANTS
        buckets.setdefault((SAME_WORDS, _words(tokens)), []).append(shape)
        needs[(SAME_WORDS, _words(tokens))] = 2
        stated = {_bank_key(raw) for raw in stated_by.get(shape, {})}
        if len(stated) == 1 and "" not in stated:
            (said,) = stated
            if not is_method_only(said):
                buckets.setdefault((BANK_NAMES, said), []).append(shape)
                needs[(BANK_NAMES, said)] = 2
    for key, members in buckets.items():
        if len(members) < needs[key]:
            continue
        for other in members[1:]:
            parent[root(other)] = root(members[0])

    groups: dict[str, list[str]] = {}
    for shape in free:
        groups.setdefault(root(shape), []).append(shape)
    found: list[Proposal] = []
    broad: list[Proposal] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        rules = frozenset(
            rule
            for (rule, key), joined in buckets.items()
            if len(joined) >= needs[(rule, key)] and root(joined[0]) == root(members[0])
        )
        ordered = tuple(sorted(members, key=lambda s: (-counts[s], s)))
        # The commonest shape, of those the shortest, since a longer one carries a town or a
        # branch: its printed words name the group, cut to what every member opens with.
        best = min(members, key=lambda s: (-counts[s], len(named[s]), s))
        shared = _common_opening([named[s] for s in members])
        printed = [token.printed for token in named[best]]
        words = " ".join(printed[:shared] if shared else printed).split()
        bank_name = ""
        if BANK_NAMES in rules:
            agreed = {
                key
                for (rule, key), joined in buckets.items()
                if rule == BANK_NAMES and len(joined) >= 2 and root(joined[0]) == root(members[0])
            }
            spellings: Counter[str] = Counter()
            for shape in members:
                for raw, rows in stated_by.get(shape, {}).items():
                    if _bank_key(raw) in agreed:
                        spelt = " ".join(w.capitalize() for w in core_words(shape_of(raw)))
                        spellings[spelt] += rows
            bank_name = min(spellings, key=lambda s: (-spellings[s], s))
        proposal = Proposal(
            name=bank_name or " ".join(word.capitalize() for word in words),
            shapes=ordered,
            rules=rules,
            transactions=sum(counts[s] for s in members),
            opening=" ".join(" ".join(printed[:shared]).split()),
            bank_name=bank_name,
        )
        # A group held together by an opening word that says who (the brand of thirteen towns) is
        # as wide as the chain is; one held together by anything else is capped.
        by_brand = (shared > 0 and is_distinctive(named[best][0].norm, common)) or bool(
            bank_name
        )
        if len(members) <= MAX_SHAPES_PROPOSED or by_brand:
            found.append(proposal)
        else:
            broad.append(proposal)
    found.sort(key=lambda p: (-p.transactions, p.name, p.shapes))
    broad.sort(key=lambda p: (-p.transactions, p.name, p.shapes))
    return Proposals(groups=tuple(found), too_broad=tuple(broad))
