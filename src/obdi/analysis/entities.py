"""Who a payment was to: the shapes counterparties print, and the entities they are gathered into.

An ENTITY is one name over every shape the sources print for it. The owner decides which shapes
belong together (`Store.create_entity` and its neighbours keep that decision across every rebuild
from raw); this module holds what is computed from the transactions alone.
"""

from __future__ import annotations

import heapq
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import TYPE_CHECKING

from ..core.plural import plural
from ..ingest.entity_records import (
    BEGINS,
    CONTAINS,
    OWNER_ROLE,
    RULE_KINDS,
    Entity,
    EntityRefused,
    EntityRule,
)
from ..ingest.identity import NORMALISATION_STEPS, normalise_description
from .entity_tokens import (
    MIN_DISTINCTIVE_LETTERS,
    Token,
    distinctive_words,
    fused_letters,
    is_method_only,
    tokens_of,
)
from .payment_methods import strip_leading_methods

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from ..ingest.store import Store

#: The rules that can join two shapes into one proposed group, said once here and by name
#: everywhere else. OPENING_WORDS: they begin with the same two or more words. SAME_WORDS: once
#: one-letter codes are set aside, they are made of the same words in whatever order.
OPENING_WORDS = "opening words"
SAME_WORDS = "same words"

#: Fewest words at the start that two shapes must share to be one proposed group. A single shared
#: word is two retailers that begin alike far more often than one retailer, and a wrong merge is
#: the owner's to find, so the rule leaves it unproposed.
MIN_OPENING_WORDS = 2


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


def _drop_digit_words(text: str) -> str:
    return " ".join(w for w in text.split() if not any(c.isdigit() for c in w))


def _keep_fused_letters(text: str) -> str:
    return " ".join(w for w in map(fused_letters, text.split()) if w)


def _drop_printed_dates(text: str) -> str:
    words = text.split()
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


#: The steps that make a shape from a printed description, in order, each with the sentence the
#: Entities page says it in: `identity.NORMALISATION_STEPS`, then these two. `shape_of` applies
#: exactly these, so the page's account of how a name is made cannot drift from the code.
SHAPE_STEPS: tuple[tuple[str, Callable[[str], str]], ...] = (
    *NORMALISATION_STEPS,
    ("words holding a digit are dropped", _drop_digit_words),
    (
        "a printed date such as “on 12 apr”, and a month or weekday left at the end, are dropped",
        _drop_printed_dates,
    ),
)


def _shape(text: str) -> str:
    for _sentence, step in SHAPE_STEPS:
        text = step(text)
    return text


def reading_of(text: str) -> str:
    """What names are COMPARED on for a printed description: its shape, except that a word
    fused to a number keeps its letters (`fused_letters`). A bank printing "M&S BANK0806249308"
    has the shape "m s", two bare initials that meet every name beginning "m" and "s"; its
    reading is "m s bank". The reading is never a name's identity: rows are grouped by the
    shape, so the key does not move for the sake of comparison."""
    return _read_by(text, SHAPE_STEPS)


def _read_by(text: str, steps: Sequence[tuple[str, Callable[[str], str]]]) -> str:
    for _sentence, step in steps:
        text = _keep_fused_letters(text) if step is _drop_digit_words else step(text)
    return text


def shape_readings(descriptions: Iterable[str]) -> dict[str, str]:
    """For each shape, the reading its rows most often have (`reading_of`); of equally common
    readings the one of fewer words, then the first alphabetically, so the answer does not
    depend on the order of the rows. Shapes whose reading is their own are included."""
    seen: dict[str, Counter[str]] = {}
    for text in descriptions:
        shape = _shape(text)
        if shape:
            seen.setdefault(shape, Counter())[reading_of(text)] += 1
    return {
        shape: min(counted, key=lambda r: (-counted[r], len(r.split()), r))
        for shape, counted in seen.items()
    }


#: Where a name comes from, as the noun the page puts after "the" ("from the description",
#: "whose description"). A name's `Derivation` carries its own source, so a name made from
#: another field says so without the page changing.
DESCRIPTION_SOURCE = "description"


@dataclass(frozen=True)
class Derivation:
    """How a name came to be: the field it was read from, the distinct texts printed there that
    produced it, and the steps that changed any of them, in the order they are applied."""

    source: str
    #: Up to `DERIVATION_TEXTS_SHOWN` of the distinct printed texts, newest first.
    printed: tuple[str, ...]
    #: How many more distinct texts the page holds for the name than are shown.
    more: int
    #: The sentences of the steps that changed at least one printed text.
    steps: tuple[str, ...]
    name: str
    #: The kinds (`LADDER`) of the rows the derivation was made from, strongest first.
    kinds: tuple[str, ...] = ()


DERIVATION_TEXTS_SHOWN = 3


def _printed_text(row: Covered) -> str:
    """The text a covered row's name was read from: the counterparty it states where that is
    what named it (`STATED_NAME`), else its description."""
    return row.counterparty if row.kind == STATED_NAME else row.description


def derivation_of(
    shape: str, covered: Sequence[Covered], *, source: str | None = None
) -> Derivation:
    """The derivation of a name from the transactions the page holds for it (`covered`): each
    shown row's text is reduced by the steps of the kind that named it (`COUNTERPARTY_STEPS` for
    a stated name, `SHAPE_STEPS` otherwise). The source is said from `KIND_SENTENCES` for the
    kinds of the rows shown unless one is given."""
    distinct: dict[str, Covered] = {}
    for row in covered:
        distinct.setdefault(_printed_text(row), row)
    shown = list(distinct.items())[:DERIVATION_TEXTS_SHOWN]
    changed: set[str] = set()
    for text, row in shown:
        steps = COUNTERPARTY_STEPS if row.kind == STATED_NAME else SHAPE_STEPS
        for sentence, step in steps:
            after = step(text)
            if after != text:
                changed.add(sentence)
            text = after
    in_order = dict.fromkeys(sentence for sentence, _step in (*SHAPE_STEPS, *COUNTERPARTY_STEPS))
    kinds = [k for k in LADDER if any(row.kind == k for row in covered)]
    if source is None:
        support = sum({row.via: row.support for row in covered if row.kind == ALIAS}.values())
        source = ", and from the ".join(
            KIND_SENTENCES[kind].format(payments=plural(support, "payment")) for kind in kinds
        )
    return Derivation(
        source=source or DESCRIPTION_SOURCE,
        printed=tuple(text for text, _row in shown),
        more=len(distinct) - len(shown),
        steps=tuple(sentence for sentence in in_order if sentence in changed),
        name=shape,
        kinds=tuple(kinds),
    )


def shape_of(text: str) -> str:
    """The shape of a counterparty as printed: the normalised description without any word that
    holds a digit, since a reference, a store number, a mandate, or a card tail is what changes
    between two sightings of one payee, and without the date a card payment prints ("on 12 apr").
    Empty where nothing but codes is left. `SHAPE_STEPS` lists the steps in the order applied,
    each with the sentence the page states it in.

    This is the shape of a DESCRIPTION alone, and a row's name is not always that: `name_of`
    prefers the counterparty a source states, and uses this only where a row states none.
    """
    return _shape(text)


#: The kinds of identifier a row can be named by, strongest first
#: (`docs/design/2026-10-commitments/entities.md` section 2 is the reasoning). `LADDER` is the
#: order `name_of` walks: the first rung a row carries names it, and `Named.kind` says which.
#: ACCOUNT and SOURCE_ID are on the ladder so that a row carrying them is named by them first; no
#: derived row carries either yet, so they never match, and giving the rows those columns is a
#: read added to `name_of` and nothing more. ALIAS is a weaker identifier (a description-shape)
#: linked to a stronger one by rows seen by two sources (`learned_links`). RULE is an entity's
#: rule matching a name, which acts on names and not on rows, so it is a kind of link and not a
#: rung.
ACCOUNT = "account"
SOURCE_ID = "source id"
STATED_NAME = "stated name"
ALIAS = "alias"
DESCRIPTION = "description"
RULE = "rule"
LADDER = (ACCOUNT, SOURCE_ID, STATED_NAME, ALIAS, DESCRIPTION)

#: What each kind is said to be on a page, as the noun phrase after "from the" ("from the bank's
#: merchant name"): the one table every page reads, so no page words a kind itself.
KIND_SENTENCES: dict[str, str] = {
    ACCOUNT: "other party's account number",
    SOURCE_ID: "source's own identifier for the other party",
    STATED_NAME: "bank's merchant name",
    ALIAS: "description, named by the bank's merchant name through {payments} seen by both",
    DESCRIPTION: "description",
    RULE: "rule",
}


def _set_aside_methods(text: str) -> str:
    return " ".join(strip_leading_methods(text.split()))


#: The steps that make a name from a stated counterparty, in order, each with the sentence the
#: Entities page says it in: the description's steps up to the digit step, then the payment
#: method set aside. Not the printed-date cut, which a merchant name has no use for. A
#: counterparty that nothing is left of (only digits, only payment methods) names no row.
COUNTERPARTY_STEPS: tuple[tuple[str, Callable[[str], str]], ...] = (
    *SHAPE_STEPS[: len(NORMALISATION_STEPS) + 1],
    (
        "a payment method printed before the name is set aside",
        _set_aside_methods,
    ),
)


def counterparty_name(counterparty: str) -> str:
    """A stated counterparty reduced to a name (`COUNTERPARTY_STEPS`); "" where none is left."""
    text = counterparty
    for _sentence, step in COUNTERPARTY_STEPS:
        text = step(text)
    return text


@dataclass(frozen=True)
class Alias:
    """The stronger identifier a weaker one (a description-shape) is learned to stand for, the kind
    of that stronger identifier, and how many rows it is learned from: rows seen by two sources,
    which carry both."""

    name: str
    rows: int
    kind: str = STATED_NAME


@dataclass(frozen=True)
class Named:
    """The name of one row and the kind of identifier it came from (`LADDER`). `name` is "" where
    nothing is left to name the row. For an `ALIAS` name, `via` is the description-shape it was
    learned for, `support` the rows it was learned from, and `linked_by` the kind of identifier
    the shape was linked to."""

    name: str
    kind: str
    via: str = ""
    support: int = 0
    linked_by: str = ""


def _identifier_name(kind: str, text: str) -> str:
    if kind == STATED_NAME:
        return counterparty_name(text)
    if kind == ACCOUNT:
        return "".join(text.split()).replace("-", "").casefold()
    return text.strip()


def name_of(
    description: str,
    counterparty: str = "",
    aliases: Mapping[str, Alias] | None = None,
    *,
    account: str = "",
    source_id: str = "",
) -> Named:
    """What a row is called: the strongest rung of `LADDER` it carries - the other party's
    account, a source's identifier for it, the counterparty name it states - else the identifier
    learned for its description's shape (`ALIAS`), else that shape (`DESCRIPTION`).

    The stated identifier is the primary one and the description only elaborates: two rows
    printing one reference to different counterparties are two payees, and a payee printing a
    different reference every month is one. A bank states a counterparty only where it identified
    one and a statement row states none, so a name read from the counterparty alone gave one
    payee two names by source and split its series into a stopped half and a new half (measured
    on the first real store). The alias is the join: a payment seen by a feed and a statement is
    one held row carrying the feed's counterparty (`learned_links`).
    """
    for kind, text in ((ACCOUNT, account), (SOURCE_ID, source_id), (STATED_NAME, counterparty)):
        stated = _identifier_name(kind, text)
        if stated:
            return Named(stated, kind)
    shape = _shape(description)
    learned = (aliases or {}).get(shape)
    if shape and learned is not None:
        return Named(
            learned.name, ALIAS, via=shape, support=learned.rows, linked_by=learned.kind
        )
    return Named(shape, DESCRIPTION)


def learned_links(rows: Iterable[tuple[str, str]]) -> dict[str, Alias]:
    """For each description-shape, the one stronger identifier the rows that carry both say it
    stands for, with how many rows say so; from (description, counterparty) rows.

    A payment seen by a feed and a statement is ONE held row carrying the feed's counterparty
    (`ingest.matching`), so each row with a stated counterparty AND a description is evidence
    that the two identifiers are one party. It is derived from the rows, never declared, and
    rebuilt wherever the rows are read. A shape whose rows state two different counterparties
    is ambiguous and links to none (a rent reference paid to two housemates), and a shape whose
    only counterparty is its own name has nothing to link. The links are for the rows that lack
    the stronger identifier: `name_of` uses one only where the row states none itself.
    """
    seen: dict[str, Counter[str]] = {}
    for description, counterparty in rows:
        stated = counterparty_name(counterparty)
        shape = _shape(description)
        if stated and shape:
            seen.setdefault(shape, Counter())[stated] += 1
    return {
        shape: Alias(name, counted[name], STATED_NAME)
        for shape, counted in seen.items()
        if len(counted) == 1
        for name in counted
        if name != shape
    }


def names_of(rows: Sequence[tuple[str, str]]) -> list[Named]:
    """The name of each (description, counterparty) row, the links learned from all of them
    (`learned_links`) used for the rows that state none."""
    links = learned_links(rows)
    return [name_of(description, counterparty, links) for description, counterparty in rows]


def count_shapes(descriptions: Iterable[str]) -> dict[str, int]:
    """How many of the descriptions have each shape; descriptions with no shape are not counted."""
    counted = Counter(shape_of(text) for text in descriptions)
    counted.pop("", None)
    return dict(counted)


def count_row_legs(named: Iterable[tuple[str, bool]]) -> dict[str, int]:
    """For each name, how many of the (name, is a transfer leg) rows that have it are legs;
    names with no leg are left out."""
    counted: Counter[str] = Counter()
    for name, leg in named:
        if leg:
            counted[name] += 1
    counted.pop("", None)
    return dict(counted)


@dataclass(frozen=True)
class NameOrigin:
    """How the rows of one name came to have it: how many state it themselves (`STATED_NAME`),
    how many take it through a link (`ALIAS`), how many are its description's shape, and how many
    rows of both kinds the links were learned from."""

    stated: int = 0
    linked: int = 0
    described: int = 0
    through: int = 0

    @property
    def rows(self) -> int:
        return self.stated + self.linked + self.described

    @property
    def kind(self) -> str:
        """The strongest kind any row of the name has (`LADDER`)."""
        if self.stated:
            return STATED_NAME
        return ALIAS if self.linked else DESCRIPTION


def name_origins(named: Iterable[Named]) -> dict[str, NameOrigin]:
    """For each name, how its rows came to have it; rows with no name are not counted."""
    stated: Counter[str] = Counter()
    linked: Counter[str] = Counter()
    described: Counter[str] = Counter()
    supports: dict[str, dict[str, int]] = {}
    for item in named:
        if not item.name:
            continue
        if item.kind == ALIAS:
            linked[item.name] += 1
            supports.setdefault(item.name, {})[item.via] = item.support
        elif item.kind == DESCRIPTION:
            described[item.name] += 1
        else:
            stated[item.name] += 1
    return {
        name: NameOrigin(
            stated[name],
            linked[name],
            described[name],
            sum(supports.get(name, {}).values()),
        )
        for name in {*stated, *linked, *described}
    }


def name_readings(
    rows: Sequence[tuple[str, str]], named: Sequence[Named]
) -> dict[str, str]:
    """For each name, the text it is COMPARED on (`reading_of`): the reading of the counterparty
    or description it was read from, most common first, then of fewer words, then alphabetical,
    so the answer does not depend on the order of the rows. A row named through a link adds
    nothing: the name's own rows say how it is read."""
    seen: dict[str, Counter[str]] = {}
    for (description, counterparty), item in zip(rows, named, strict=True):
        if not item.name or item.kind == ALIAS:
            continue
        if item.kind == DESCRIPTION:
            read = reading_of(description)
        else:
            read = _read_by(counterparty, COUNTERPARTY_STEPS)
        seen.setdefault(item.name, Counter())[read] += 1
    return {
        name: min(counted, key=lambda r: (-counted[r], len(r.split()), r))
        for name, counted in seen.items()
    }


def clean_rule(kind: str, words: str) -> tuple[str, str]:
    """The kind and the words of a rule as they are kept; refused where no name could match.

    The words are held the way a name is (`_shape`: case folded, numbers and dates left out), so
    a rule and the names it is read against are spelt alike. A rule must hold at least one word
    that tells a payee apart (`distinctive_words`): one made of payment methods ("faster
    payment") or single letters would match every payee that was paid that way.
    """
    if kind not in RULE_KINDS:
        raise EntityRefused("A rule either begins with some words or contains them.")
    text = _shape(words)
    if not distinctive_words(tokens_of(text)):
        raise EntityRefused(
            "A rule needs at least one word that tells a payee apart; a payment method, a "
            "single letter, or a number does not."
        )
    return kind, text


def rule_parts(
    kind: str, *, source: str = DESCRIPTION_SOURCE, reduced: bool = True
) -> tuple[str, str]:
    """What a rule does, as the words before and after its own words: the field it reads
    (`source`), that the field is reduced to a name first (`reduced`; left out where the line
    is on a phone and the reduction is stated beside it), and how the name is compared."""
    reads = f"any transaction whose {source}"
    if reduced:
        reads += ", reduced to a name as above,"
    if kind == BEGINS:
        return f"{reads} begins with", ""
    return f"{reads} holds the words", " in any order"


def rule_phrase(
    kind: str, words: str, *, source: str = DESCRIPTION_SOURCE, reduced: bool = True
) -> str:
    """What a rule matches, said as a noun phrase: the one place a rule is put into words, so the
    tick that offers it and the sentence that confirms it read alike."""
    before, after = rule_parts(kind, source=source, reduced=reduced)
    return f"{before} “{words}”{after}"


def _rule_norms(words: str) -> tuple[str, ...]:
    return tuple(token.norm for token in tokens_of(words))


def _matches(kind: str, wanted: Sequence[str], norms: Sequence[str]) -> bool:
    if kind == BEGINS:
        return len(norms) >= len(wanted) and tuple(norms[: len(wanted)]) == tuple(wanted)
    return set(wanted) <= set(norms)


def rule_matches(kind: str, words: str, shape: str) -> bool:
    """Whether a rule of this kind and words matches the name (compared word by word as names
    are compared elsewhere: a plural, a payment method before it, and a company form after it
    make no difference)."""
    wanted = _rule_norms(words)
    return bool(wanted) and _matches(kind, wanted, _rule_norms(shape))


def with_rules(
    entities: Iterable[Entity],
    rules: Iterable[EntityRule],
    exclusions: Collection[tuple[int, str]],
    known: Iterable[str],
) -> list[Entity]:
    """The entities with every name a live rule matches added to the names attached by hand.

    THE ONE PLACE an entity's names are decided: a name is under an entity when it was attached
    to it by hand, or when one of its rules matches it and the owner has not split it apart
    from that entity (`exclusions` holds the entity and name). A name attached by hand to one
    entity stays there whatever another entity's rule says. Where rules of two entities match
    one name the rule of more words wins, then the older rule, so the answer is the same on
    every read. A name two rules of one entity match is listed once. `known` is every name the
    transactions hold; a rule matches nothing that is not among them.
    """
    made = list(entities)
    held = {shape for entity in made for shape in entity.shapes}
    live = {entity.id for entity in made}
    ranked = sorted(
        (
            (rule, _rule_norms(rule.words))
            for rule in rules
            if rule.entity_id in live and rule.kind in RULE_KINDS
        ),
        key=lambda pair: (-len(pair[1]), pair[0].id),
    )
    won: dict[int, list[str]] = {}
    if ranked:
        for shape in sorted(set(known) - held):
            norms = _rule_norms(shape)
            for rule, wanted in ranked:
                if (
                    wanted
                    and (rule.entity_id, shape) not in exclusions
                    and _matches(rule.kind, wanted, norms)
                ):
                    won.setdefault(rule.entity_id, []).append(shape)
                    break
    return [
        replace(
            entity,
            shapes=tuple(sorted({*entity.shapes, *won.get(entity.id, ())})),
            by_rule=tuple(won.get(entity.id, ())),
        )
        for entity in made
    ]


def entities_of(store: Store, known: Iterable[str] = ()) -> list[Entity]:
    """Every entity not removed, by name, each with the names under it now: those attached by
    hand, and every name among `known` (the names the transactions hold) that one of its rules
    matches, less those split apart from it (`with_rules`).

    Asked with no `known`, no rule has a name to match and the answer is the hand-attached names
    alone. Everything that asks what is under an entity asks here; the store holds the three row
    sets this reads (the entities with their hand-attached names, the live rules, and the
    exclusions) and decides nothing about them. The rules and exclusions are not read where there
    is nothing for them to act on.
    """
    made = store.entities_with_shapes()
    names = tuple(known)
    if not names or not made:
        return made
    rules = store.entity_rules()
    if not rules:
        return made
    return with_rules(made, rules, store.entity_exclusions(), names)


def shape_entities(store: Store, known: Iterable[str] = ()) -> dict[str, tuple[int, str]]:
    """Each name under an entity now (`entities_of`), to the id and name of its entity."""
    return {
        shape: (entity.id, entity.name)
        for entity in entities_of(store, known)
        for shape in entity.shapes
    }


def _rule_matches_for(store: Store, entity: int, shape: str) -> bool:
    return any(
        rule_matches(rule.kind, rule.words, shape)
        for rule in store.entity_rules()
        if rule.entity_id == entity
    )


def detach_shape(store: Store, shape: str, *, now: datetime | None = None) -> bool:
    """Split `shape` apart from the entity it was attached to by hand, and commit; False where it
    belonged to none (`Store.detach_shape` says what else is kept).

    A name a rule of its entity also matches would be attached again by that rule on the next
    read, so the store is told to record the exclusion as well: whether a rule matches is the
    analysis's to say, and the store does what it is told in the one commit.
    """
    held = store.shape_entities().get(shape)
    if held is None:
        return False
    return store.detach_shape(shape, exclude=_rule_matches_for(store, held[0], shape), now=now)


def exclude_shape(
    store: Store, entity: int, shape: str, *, now: datetime | None = None
) -> None:
    """Stop the rules of an entity attaching `shape`, and commit; the name was under it by rule
    and is under no entity afterwards (unless another entity's rule matches it).

    Refused, with nothing written, for a name none of the entity's rules matches: there is
    nothing to exclude it from, and a name attached by hand is split apart with `detach_shape`.
    An entity that is missing or removed is refused by the store.
    """
    store.refuse_missing_entity(entity)
    if not _rule_matches_for(store, entity, shape):
        raise EntityRefused("No rule of this entity matches that name; the page may have changed.")
    store.exclude_shape(entity, shape, now=now)


@dataclass(frozen=True)
class RuleTrial:
    """What a rule would do if it were kept, worked out over the names held and written nowhere."""

    #: The names that would come under the entity that are not under it now.
    attach: tuple[str, ...]
    #: Names the rule matches that are already under the entity.
    already: int
    #: Names the rule matches that stay under another entity (attached to it by hand, or won by
    #: its rule).
    elsewhere: int


def trial_rule(
    entities: Sequence[Entity],
    rules: Sequence[EntityRule],
    exclusions: Collection[tuple[int, str]],
    known: Collection[str],
    entity_id: int,
    kind: str,
    words: str,
) -> RuleTrial:
    """What keeping the rule on the entity would attach, by resolving the names twice: as they
    stand, and with the rule added. `entities` are the entities with only their hand-attached
    names; nothing is written, and a rule that cannot be kept is refused as keeping it would be."""
    kept_kind, kept_words = clean_rule(kind, words)
    if not any(entity.id == entity_id for entity in entities):
        raise EntityRefused("There is no such entity; it may have been removed.")
    before = with_rules(entities, rules, exclusions, known)
    after = with_rules(
        entities, [*rules, EntityRule(0, entity_id, kept_kind, kept_words)], exclusions, known
    )
    now = {name for entity in before if entity.id == entity_id for name in entity.shapes}
    then = {name for entity in after if entity.id == entity_id for name in entity.shapes}
    matched = {
        shape
        for shape in known
        if (entity_id, shape) not in exclusions and rule_matches(kept_kind, kept_words, shape)
    }
    return RuleTrial(
        attach=tuple(sorted(then - now)),
        already=len(matched & now),
        elsewhere=len(matched - then),
    )


@dataclass(frozen=True)
class Proposal:
    """Shapes the rules think are one counterparty, for the owner to accept, trim, or ignore."""

    #: The opening words the shapes share, said as a name; the owner may change it.
    name: str
    #: Most-used first, then alphabetical.
    shapes: tuple[str, ...]
    #: The one rule (`OPENING_WORDS`, `SAME_WORDS`) that holds for every shape.
    rules: frozenset[str]
    #: Transactions across the shapes.
    transactions: int
    #: The words every shape in the group begins with; empty where the group was joined through
    #: words reordered, which share no opening.
    opening: str = ""
    #: The distinctive words every shape in the group holds, in the order the commonest one prints
    #: them; what a "contains" rule is made of where the group has no opening.
    shared: str = ""

    def rule(self) -> tuple[str, str] | None:
        """The rule that joined the group, in the form the store keeps it, or None where the
        reason gives none: all begin with the same words (a rule that begins with them), or the
        same words in another order (a rule that contains them). None as well where those words
        could not be kept as a rule (`clean_rule`), so the page never offers a rule the store
        would refuse."""
        for kind, words in (
            (BEGINS, self.opening),
            (CONTAINS, self.shared if SAME_WORDS in self.rules else ""),
        ):
            if not words:
                continue
            try:
                return clean_rule(kind, words)
            except EntityRefused:
                return None
        return None


def _words(named: Sequence[Token]) -> frozenset[str]:
    """The words that tell a shape apart: its own, without the one-letter codes."""
    return frozenset(token.norm for token in named if len(token.norm) > 1)


def _tells_apart(norms: Collection[str]) -> bool:
    """Whether the compared words hold one of `MIN_DISTINCTIVE_LETTERS` letters or more. Two
    shapes that share only initials and short codes ("m s", "ab cd") are not shown to be one
    payee, since the same few letters open a great many unrelated names."""
    return any(len(norm) >= MIN_DISTINCTIVE_LETTERS for norm in norms)


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


#: Fewest different opening words a word must follow, in the names it is not the opening word of,
#: to be too ordinary to say who a payee is ("payment", a town, a branch word). Measured on the
#: invented large store: a town follows 3 or 4 different brands ("london" 3, "reading" 4), while
#: every planted retailer follows none - they open names and are not found after anything - so 3
#: is the largest floor under which the towns are common there, and it is the count at which a
#: coincidence stops explaining a shared word (`MIN_ONE_WORD_VARIANTS`). "payment" and "gbr"
#: follow no brand in the invented stores, so the floor is not measured against them; they are
#: kept out of the distinctive words independently (`distinctive_words`). Replaces the fifty most
#: frequent words, which on a real store were the brands with the most variants: a retailer's
#: twenty towns were "too broad" (6 groups of 89 names) because the retailer was among the
#: commonest words. A brand opens many names however frequent it is and so stays distinctive.
COMMON_AFTER_OPENINGS = 3

#: Fewest shapes that must open with one distinctive word for it alone to join them. Two shapes
#: that share a single word are far more often two payees than one; three is the first count a
#: coincidence stops explaining, measured by the owner's reading of thirteen towns of one chain.
MIN_ONE_WORD_VARIANTS = 3


def common_tokens(named: Mapping[str, Sequence[Token]]) -> frozenset[str]:
    """The words too ordinary to say who a payee is: those that follow at least
    `COMMON_AFTER_OPENINGS` different opening words, in the names they do not open. A word is
    common by where it appears and not by how often: a brand that opens a great many names is
    distinctive however many there are."""
    following: dict[str, set[str]] = {}
    for shape_tokens in named.values():
        if len(shape_tokens) < 2:
            continue
        opening = shape_tokens[0].norm
        for token in shape_tokens[1:]:
            if token.norm != opening:
                following.setdefault(token.norm, set()).add(opening)
    return frozenset(
        n for n, openings in following.items() if len(openings) >= COMMON_AFTER_OPENINGS
    )


def is_distinctive(norm: str, common: Collection[str]) -> bool:
    """Whether a compared word can say who a payee is: not one letter, not a payment method, not
    a country or company code, and not a word that follows many brands (`common_tokens`)."""
    return norm not in common and norm in distinctive_words((Token(norm, norm),))


#: Most shapes a proposed group may hold. Measured on the invented household's large store: the
#: opening words "faster payment" joined 25 different merchants, because they name how a payment
#: was made and not who it was to, while the real variants of one retailer were four towns. A
#: group above this is counted and left unproposed (`Proposals.too_broad`), never merged on the
#: owner's one press; the figure is a threshold to relax on evidence, not a property of retailers.
#: A withheld group is still shown, in a closed fold with nothing ticked, so the owner can read
#: it and merge the names that are one payee; stripping method words has since removed most of
#: what this cap was measured against.
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
    #: The counterparty the row states, "" where it states none.
    counterparty: str = ""
    #: The kind of identifier (`LADDER`) that named the row.
    kind: str = DESCRIPTION
    #: For a row named through a link: the description-shape it was linked for, and the rows
    #: the link was learned from.
    via: str = ""
    support: int = 0


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
    #: How each name's rows came to have it (`name_origins`); empty where the view was made from
    #: counts alone, and the page then says nothing about sources.
    origins: Mapping[str, NameOrigin] = field(default_factory=dict)

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


@dataclass(frozen=True)
class RuleLine:
    """A rule an entity keeps and how many of the entity's names it matches now."""

    rule: EntityRule
    matches: int


@dataclass(frozen=True)
class EntityPage:
    """Everything one entity's page says: the entity with every name under it, where it sits in
    the family of entities, and its rules."""

    #: The entity with its names resolved (`with_rules`).
    entity: Entity
    parent: Entity | None
    children: tuple[Entity, ...]
    rules: tuple[RuleLine, ...]
    #: The counts and newest transactions of the entity's own names, in the form the Entities page
    #: lists them, so a name opens to its transactions the same way on both.
    view: EntitiesView

    @property
    def transactions(self) -> int:
        return sum(self.view.counts.get(shape, 0) for shape in self.entity.shapes)

    @property
    def orphaned(self) -> tuple[str, ...]:
        """The names under the entity that no transaction has now. A name attached by hand was
        the description's shape before rows were named by what a source states, and a row that
        states its counterparty no longer has that shape as its name; such an attachment is
        listed, never dropped, so the owner sees what no longer attaches to anything."""
        return tuple(s for s in self.entity.shapes if s not in self.view.counts)


def entity_page_of(
    entity_id: int,
    entities: Sequence[Entity],
    rules: Sequence[EntityRule],
    counts: Mapping[str, int],
    covers: Mapping[str, tuple[Covered, ...]],
    origins: Mapping[str, NameOrigin] | None = None,
) -> EntityPage | None:
    """The page of one entity, or None where no entity not removed has that id. `entities` are
    resolved (`with_rules`) and `rules` are the live rules of all of them."""
    found = next((e for e in entities if e.id == entity_id), None)
    if found is None:
        return None
    lines = tuple(
        RuleLine(rule, sum(rule_matches(rule.kind, rule.words, s) for s in found.shapes))
        for rule in rules
        if rule.entity_id == entity_id
    )
    return EntityPage(
        entity=found,
        parent=next((e for e in entities if e.id == found.parent_id), None),
        children=tuple(e for e in entities if e.parent_id == entity_id),
        rules=lines,
        view=EntitiesView(
            counts=counts,
            entities=(found,),
            proposals=Proposals(groups=(), too_broad=()),
            covers=covers,
            origins=origins or {},
        ),
    )


def view_of(
    counts: Mapping[str, int],
    entities: Iterable[Entity],
    legs: Mapping[str, int] | None = None,
    covers: Mapping[str, tuple[Covered, ...]] | None = None,
    origins: Mapping[str, NameOrigin] | None = None,
    readings: Mapping[str, str] | None = None,
) -> EntitiesView:
    """The page's view of the names held and the entities made from them.

    `origins` is `name_origins`: how each name's rows came to have it.
    `readings` is `name_readings`: the text each name is compared on where that is not the name.

    `legs` is, for each shape, how many of its transactions are legs of confirmed transfers
    between the owner's own accounts; the shapes it makes the owner's are set apart before the
    rules look for payees, so they are never offered as one.
    """
    made = tuple(entities)
    taken = {shape for entity in made for shape in entity.shapes}
    owner = owner_group(counts, legs or {}, taken)
    set_apart = taken | set(owner.shapes if owner else ())
    proposals = propose_groups(counts, taken=set_apart, readings=readings)
    offered = {s for g in (*proposals.groups, *proposals.too_broad) for s in g.shapes}
    return EntitiesView(
        counts=counts,
        entities=made,
        proposals=proposals,
        covers=covers or {},
        origins=origins or {},
        owner=owner,
        suggestions=suggest_for_entities(counts, made, set_apart | offered, readings),
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
    counts: Mapping[str, int],
    entities: Sequence[Entity],
    set_apart: Collection[str],
    readings: Mapping[str, str] | None = None,
) -> tuple[Suggestion, ...]:
    """A free name goes under the entity whose name or shapes it shares the most distinctive
    words with (the first by name where two share as many); names the owner has already placed,
    the owner's own, and names in a proposed group (`set_apart`) are not offered.

    A word is distinctive as `is_distinctive` says, so a word that follows many different
    brands (`common_tokens`) never joins a name to an entity. The owner entity is never a
    target: its shapes are transfers, and a name that shares a word with one is not shown to
    be the owner's.
    """
    targets = [e for e in entities if e.role != OWNER_ROLE and e.shapes]
    if not targets:
        return ()
    read = readings or {}
    named = {shape: tokens_of(read.get(shape, shape)) for shape in counts}
    common = common_tokens(named)
    words = {e.id: _entity_words(e, common) for e in targets}
    held = {shape for e in entities for shape in e.shapes}
    found: dict[int, list[tuple[str, frozenset[str]]]] = {}
    for shape in sorted(counts, key=lambda s: (-counts[s], s)):
        if shape in held or shape in set_apart or is_method_only(shape):
            continue
        mine = frozenset(w for w in distinctive_words(named[shape]) if is_distinctive(w, common))
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


def _key_size(key: object) -> int:
    """How many words a bucket's key holds: a longer key is the more specific reason."""
    if isinstance(key, str):
        return len(key.split())
    if isinstance(key, tuple | frozenset):
        return len(key)
    return 0


def _key_text(key: object) -> str:
    if isinstance(key, str):
        return key
    if isinstance(key, tuple | frozenset):
        return " ".join(sorted(str(word) for word in key))
    return str(key)


def _one_rule_groups(
    buckets: Mapping[tuple[str, object], list[str]],
    needs: Mapping[tuple[str, object], int],
    counts: Mapping[str, int],
) -> list[tuple[tuple[str, object], list[str]]]:
    """The buckets that become groups, each with the shapes it keeps, every shape in at most one.

    A group is the members of ONE bucket (one rule, one key), so the sentence that says why is
    true of every member. A shape in several buckets goes to the bucket with the most
    transactions, then the most members, then the longer key; a bucket left with fewer shapes
    than it needs after that is not a group, and its remaining shapes stay free. Taking the
    best bucket first and re-ranking what is left is lazy: a bucket's rank only ever falls as
    shapes are taken, so a bucket popped at an unchanged rank is the best remaining.
    """
    assigned: set[str] = set()
    chosen: list[tuple[tuple[str, object], list[str]]] = []
    index = list(buckets)

    def ranked(position: int, live: Sequence[str]) -> tuple[int, int, int, str, str]:
        rule, key = index[position]
        return (
            -sum(counts[shape] for shape in live),
            -len(live),
            -_key_size(key),
            rule,
            _key_text(key),
        )

    heap = [(ranked(i, buckets[key]), i) for i, key in enumerate(index)]
    heapq.heapify(heap)
    while heap:
        rank, position = heapq.heappop(heap)
        key = index[position]
        live = [shape for shape in buckets[key] if shape not in assigned]
        if len(live) < needs[key]:
            continue
        current = ranked(position, live)
        if current != rank:
            heapq.heappush(heap, (current, position))
            continue
        assigned.update(live)
        chosen.append((key, live))
    return chosen


def propose_groups(
    counts: Mapping[str, int],
    *,
    taken: Collection[str] = frozenset(),
    readings: Mapping[str, str] | None = None,
) -> Proposals:
    """The groups of free shapes that plain text analysis says are one counterparty.

    A group is the shapes that share ONE reason: their first `MIN_OPENING_WORDS` words, or the
    same words once one-letter codes are set aside. Rows that share a stated counterparty are
    one name already (`name_of`), so there is no reason to propose them together.
    Reasons are never chained (`_one_rule_groups` says how a shape in two goes to one), so
    the sentence for a group is true of every member. Shapes in `taken` (already under an
    entity) are shown where they are and never proposed again. A group is at least two shapes
    and at most `MAX_SHAPES_PROPOSED`, largest first.

    Shapes are compared on their reading (`readings`, from `name_readings`) where one is given
    and on the name itself otherwise; the groups still hold names, the identity.
    """
    read = readings or {}
    named = {shape: tokens_of(read.get(shape, shape)) for shape in counts}
    common = common_tokens(named)
    free = sorted(
        shape
        for shape in counts
        if shape not in taken and not is_method_only(shape) and _words(named[shape])
    )
    # (rule, key) -> the shapes that share it, and how many it takes to make a group of them.
    buckets: dict[tuple[str, object], list[str]] = {}
    needs: dict[tuple[str, object], int] = {}
    for shape in free:
        tokens = named[shape]
        if len(tokens) >= MIN_OPENING_WORDS:
            opening = tuple(t.norm for t in tokens[:MIN_OPENING_WORDS])
            if _tells_apart(opening):
                buckets.setdefault((OPENING_WORDS, opening), []).append(shape)
                needs[(OPENING_WORDS, opening)] = 2
        first = tokens[0].norm
        if is_distinctive(first, common):
            buckets.setdefault((OPENING_WORDS, (first,)), []).append(shape)
            needs[(OPENING_WORDS, (first,))] = MIN_ONE_WORD_VARIANTS
        if _tells_apart(_words(tokens)):
            buckets.setdefault((SAME_WORDS, _words(tokens)), []).append(shape)
            needs[(SAME_WORDS, _words(tokens))] = 2
    # The longer opening wins where it reaches its need: the one-word bucket keeps only the shapes
    # that no qualifying two-word opening holds.
    longer = {
        shape
        for (rule, key), members in buckets.items()
        if rule == OPENING_WORDS and _key_size(key) > 1 and len(members) >= needs[(rule, key)]
        for shape in members
    }
    for (rule, key), members in buckets.items():
        if rule == OPENING_WORDS and _key_size(key) == 1:
            members[:] = [shape for shape in members if shape not in longer]

    found: list[Proposal] = []
    broad: list[Proposal] = []
    for (rule, _bucket_key), members in _one_rule_groups(buckets, needs, counts):
        rules = frozenset({rule})
        ordered = tuple(sorted(members, key=lambda s: (-counts[s], s)))
        # The commonest shape, of those the shortest, since a longer one carries a town or a
        # branch: its printed words name the group, cut to what every member opens with.
        best = min(members, key=lambda s: (-counts[s], len(named[s]), s))
        shared = _common_opening([named[s] for s in members])
        printed = [token.printed for token in named[best]]
        words = " ".join(printed[:shared] if shared else printed).split()
        held_by_all = frozenset.intersection(*(_words(named[s]) for s in members))
        proposal = Proposal(
            shared=" ".join(token.printed for token in named[best] if token.norm in held_by_all),
            name=" ".join(word.capitalize() for word in words),
            shapes=ordered,
            rules=rules,
            transactions=sum(counts[s] for s in members),
            opening=" ".join(" ".join(printed[:shared]).split()),
        )
        # A group held together by an opening word that says who (the brand of thirteen towns) is
        # as wide as the chain is; one held together by anything else is capped.
        by_brand = shared > 0 and is_distinctive(named[best][0].norm, common)
        if len(members) <= MAX_SHAPES_PROPOSED or by_brand:
            found.append(proposal)
        else:
            broad.append(proposal)
    found.sort(key=lambda p: (-p.transactions, p.name, p.shapes))
    broad.sort(key=lambda p: (-p.transactions, p.name, p.shapes))
    return Proposals(groups=tuple(found), too_broad=tuple(broad))
