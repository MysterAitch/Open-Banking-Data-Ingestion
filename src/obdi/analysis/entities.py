"""Who a payment was to: the shapes counterparties print, and the entities they are gathered into.

An ENTITY is one name over every shape the sources print for it. The owner decides which shapes
belong together (`Store.create_entity` and its neighbours keep that decision across every rebuild
from raw); this module holds what is computed from the transactions alone.
"""

from __future__ import annotations

import hashlib
import heapq
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import TYPE_CHECKING, NamedTuple, TypeVar

from ..core.models import Transaction
from ..core.plural import plural
from ..ingest.entity_records import (
    ACCOUNT,
    BEGINS,
    CONTAINS,
    DECLARED,
    DESCRIPTION,
    IDENTIFIER_KINDS,
    OWNER_ROLE,
    RULE_KINDS,
    SOURCE_ID,
    STATED_NAME,
    Entity,
    EntityRefused,
    EntityRule,
    Identifier,
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

#: The reasons that can join names into one proposed group, said once here and by name everywhere
#: else. OPENING_WORDS: they begin with the same two or more words. SAME_WORDS: once one-letter
#: codes are set aside, they are made of the same words in whatever order. Both are similarity of
#: text. SAME_ROWS is evidence and not similarity: payments carry identifiers of two different
#: kinds that these names stand for (`entity_ties.row_ties`), and a group of it outranks the two
#: text reasons.
OPENING_WORDS = "opening words"
SAME_WORDS = "same words"
SAME_ROWS = "same rows"

#: Fewest payments that must carry two identifiers for them to be offered as one party. Two is the
#: smallest count a coincidence is not the only explanation of: one payment carrying a merchant's
#: id beside another merchant's name is an error, a payment made on somebody's behalf, or a
#: processor shared by two shops, and a proposal is ticked by default, so a single such row would
#: put a wrong merge one press from done. Three was rejected as a floor because a party seen for
#: two months (a short habit, a new landlord) would never be offered; the figure is a threshold
#: to relax or tighten on evidence from the real store, not a property of payees.
MIN_SHARED_ROWS = 2

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
    what named it (`STATED_NAME`), else its description. A row named by an identifier is SHOWN
    under the counterparty it states, else its description (`display_names`), and that is the
    text listed for it; the identifier itself is never listed."""
    return row.counterparty if _reads_counterparty(row) else row.description


def _reads_counterparty(row: Covered) -> bool:
    """Whether the text a row's name was read from is the counterparty it states: it named the
    row (`STATED_NAME`), or it is what the row is shown under, or it is what linked the row to an
    identifier (an `ALIAS` whose `via` is that stated name and not a description-shape)."""
    if row.kind == STATED_NAME:
        return True
    if row.kind in (ACCOUNT, SOURCE_ID):
        return bool(row.counterparty)
    return (
        row.kind == ALIAS
        and bool(row.counterparty)
        and row.via == counterparty_name(row.counterparty)
    )


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
        steps = COUNTERPARTY_STEPS if _reads_counterparty(row) else SHAPE_STEPS
        for sentence, step in steps:
            after = step(text)
            if after != text:
                changed.add(sentence)
            text = after
    in_order = dict.fromkeys(sentence for sentence, _step in (*SHAPE_STEPS, *COUNTERPARTY_STEPS))
    kinds = [k for k in LADDER if any(row.kind == k for row in covered)]
    if source is None:
        support = sum({row.via: row.support for row in covered if row.kind == ALIAS}.values())
        linked = next((r for r in covered if r.kind == ALIAS), None)

        def said(kind: str) -> str:
            if kind == ALIAS and linked is not None:
                return alias_sentence(
                    _reads_counterparty(linked),
                    linked.linked_by or STATED_NAME,
                    plural(support, "payment"),
                )
            return KIND_SENTENCES[kind].format(payments=plural(support, "payment"), name=shape)

        source = ", and from the ".join(said(kind) for kind in kinds)
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
#: ACCOUNT and SOURCE_ID are the columns `Transaction.party_account` and `.party_source_id`. A
#: party named by either is IDENTIFIED by it but never SHOWN by it (`Named.display`, `labels`): an
#: account number or a source's id is a key, and no page prints one as a name. ALIAS is a weaker
#: identifier (a description-shape) linked to a stronger one by rows seen by two sources
#: (`learned_links`). RULE is an entity's rule matching a name, which acts on names and not on
#: rows, so it is a kind of link and not a rung.
#: ACCOUNT, SOURCE_ID, STATED_NAME and DESCRIPTION are also the kinds of identifier an entity holds
#: (`entity_records.IDENTIFIER_KINDS`, where they are defined once and imported here).
ALIAS = "alias"
#: A description-only row whose description, compared as names are compared (`entity_tokens`),
#: equals one stated party's name compared the same way (`learned_links`). Weaker than ALIAS,
#: which rests on payments seen by both sources, and stronger than the bare description.
MATCHED_NAME = "matched name"
#: A description-only row whose description, compared the same way, is a strict opening of
#: exactly one stated party's name (`_truncation_parties` states the rule): a statement column
#: that cuts a merchant at a fixed width. Weaker than MATCHED_NAME, which is exact.
TRUNCATED_NAME = "truncated name"
RULE = "rule"
LADDER = (ACCOUNT, SOURCE_ID, STATED_NAME, ALIAS, MATCHED_NAME, TRUNCATED_NAME, DESCRIPTION)
#: The kinds whose name is an identifier, not text: shown by a label (`display_names`).
_STRONG = (ACCOUNT, SOURCE_ID)

#: What each kind is said to be on a page, as the noun phrase after "from the" ("from the bank's
#: merchant name"): the one table every page reads, so no page words a kind itself.
KIND_SENTENCES: dict[str, str] = {
    ACCOUNT: "other party's account number",
    SOURCE_ID: "bank's own id for the party",
    STATED_NAME: "bank's merchant name",
    ALIAS: "description, named by the bank's merchant name through {payments} seen by both",
    MATCHED_NAME: (
        "description, which matches the bank's merchant name “{name}” exactly"
    ),
    TRUNCATED_NAME: "description, a truncation of the bank's merchant name “{name}”",
    DESCRIPTION: "description",
    RULE: "rule",
}

#: What each kind of identifier is called in the reason a group of `SAME_ROWS` gives
#: (`tie_sentence`): the one table the page reads, as the sentences above are.
TIE_NOUNS: dict[str, str] = {
    ACCOUNT: "the other party's account number",
    SOURCE_ID: "the bank's own id for the party",
    STATED_NAME: "the stated name",
    DESCRIPTION: "the printed description",
}


def tie_sentence(payments: int, kinds: Sequence[str]) -> str:
    """The reason a `SAME_ROWS` group is offered: how many payments carry two of the identifiers
    and which kinds those are ("4 payments carry both the stated name and the printed
    description"). Refused for fewer than two kinds, since one kind ties nothing to another."""
    if len(kinds) < 2:
        raise ValueError(f"a tie joins two kinds of identifier at least: {tuple(kinds)!r}")
    nouns = [TIE_NOUNS[kind] for kind in kinds]
    count = plural(payments, "payment")
    if len(nouns) == 2:
        return f"{count} carry both {nouns[0]} and {nouns[1]}"
    return f"{count} carry two of {', '.join(nouns[:-1])}, and {nouns[-1]}"


#: How a transaction is said to be linked to an entity, by the kind of identifier that linked it:
#: the one table every page reads (NF-TRACE-01), so no page words a link itself.
LINK_SENTENCES: dict[str, str] = {
    ACCOUNT: "by the other party's account number",
    SOURCE_ID: "by the source's own identifier for the other party",
    STATED_NAME: "by the bank's name",
    DESCRIPTION: "by the printed description",
    RULE: "by rule",
}

#: What an entity page puts above the identifiers of each kind it holds, in the order they are
#: listed (strongest first, then names a rule matches).
IDENTIFIER_HEADINGS: dict[str, str] = {
    ACCOUNT: "Account",
    SOURCE_ID: "Known to a source as",
    STATED_NAME: "Named by the bank as",
    DESCRIPTION: "Printed as",
    RULE: "Matched by a rule as",
}

#: The kind an attachment is read as when no identifier of the kind a row links through holds its
#: name. Attachments made before kinds existed were all moved across as description-kind
#: (`Store._migrate_entity_identifiers`), yet what they held was the name a row had when the owner
#: pressed the button, which after stated names existed could be a stated party's. Without this
#: fallback every such attachment would stop linking its rows on the day of the migration.
LEGACY_KIND = DESCRIPTION


def identifier_kind(kind: str) -> str:
    """The kind of identifier a row named by `kind` (`LADDER`) links to an entity through: a row
    named through a learned, matched, or truncated link links through the party it resolved to,
    whose name is a stated name, so those three are `STATED_NAME`."""
    return STATED_NAME if kind in (ALIAS, MATCHED_NAME, TRUNCATED_NAME) else kind


def link_keys(kind: str, name: str) -> tuple[tuple[str, str], ...]:
    """The identifiers (kind, value) that link a row of this name and `LADDER` kind to an
    entity, strongest first: the row's own kind, then the legacy description kind
    (`LEGACY_KIND`), then a rule matching the name (`RULE`)."""
    own = identifier_kind(kind)
    keys = [(own, name)]
    if own != LEGACY_KIND:
        keys.append((LEGACY_KIND, name))
    keys.append((RULE, name))
    return tuple(keys)


_Held = TypeVar("_Held")


def entity_of(
    held: Mapping[tuple[str, str], _Held], kind: str, name: str
) -> tuple[str, _Held] | None:
    """The entity (or whatever `held` maps identifiers to) a row of this name and kind is linked
    to, with the kind of identifier that linked it; None where no identifier it carries is held.
    `held` is `shape_entities`."""
    for key in link_keys(kind, name):
        if key in held:
            return key[0], held[key]
    return None


def held_kind(entity: Entity, shape: str) -> str:
    """The kind an entity holds a name as: the strongest kind of identifier with that value,
    `RULE` for a name only a rule of the entity attaches, "" for a name it does not hold."""
    held = {identifier.kind for identifier in entity.identifiers if identifier.value == shape}
    for kind in IDENTIFIER_KINDS:
        if kind in held:
            return kind
    return RULE if shape in entity.by_rule else ""


def name_shown(name: str, kind: str) -> str:
    """A name as a page prints it: an account number only by its ending, whatever the page's
    mode, since the digits of the other party's account are never the page's to print.

    A name that is an identifier KEY (`is_identifier_key`: what `name_of` makes of the account or
    source-id column) is never printed either, and this has no label to print in its place, so it
    says `UNNAMED_PARTY`; a page that holds the labels (`display_names`) prints the label instead
    of calling this. A name of an ACCOUNT kind that is not a number is a label already and is
    printed as it is."""
    if is_identifier_key(name):
        return UNNAMED_PARTY
    if identifier_kind(kind) == ACCOUNT and _is_account_number(name):
        return f"ending {name[-ACCOUNT_ENDING:]}"
    return name


def _is_account_number(name: str) -> bool:
    return "".join(c for c in name if c not in "- ").isdigit()


#: How many digits of an account number a page prints.
ACCOUNT_ENDING = 4

#: What a form carries in place of an account number, so the number is in no attribute of a page
#: either: a press names it by a digest, and `resolve_form_value` finds the number again among the
#: names the transactions hold.
ACCOUNT_REFERENCE = "account-ref:"


def form_value(name: str, kind: str) -> str:
    """The value a form carries for a name: the name itself, except an account number, which is
    carried as a reference to it (`ACCOUNT_REFERENCE`). The key `name_of` makes of an account is
    already a digest, never the number, and is carried as it is."""
    if identifier_kind(kind) != ACCOUNT or is_identifier_key(name):
        return name
    return ACCOUNT_REFERENCE + hashlib.sha256(name.encode()).hexdigest()[:16]


def resolve_form_value(value: str, known: Iterable[str]) -> str:
    """The name a form value stands for: itself, or for a reference to an account number
    (`form_value`) the one name among `known` it was made from. Refused where none is, as a
    name the transactions no longer hold is."""
    if not value.startswith(ACCOUNT_REFERENCE):
        return value
    found = [name for name in known if form_value(name, ACCOUNT) == value]
    if len(found) != 1:
        raise EntityRefused(
            "A name in that group is no longer in the transactions; reload the page and try again."
        )
    return found[0]


def alias_sentence(reads_counterparty: bool, linked_by: str, payments: str) -> str:
    """How a row taken through a link says so: what it was read from (its description, or the
    merchant name it states) and the kind of identifier that links it to its party."""
    read = KIND_SENTENCES[STATED_NAME] if reads_counterparty else KIND_SENTENCES[DESCRIPTION]
    return f"{read}, named by the {KIND_SENTENCES[linked_by]} through {payments} seen by both"


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
    #: How the shape came to stand for the name: `ALIAS` (rows seen by two sources say so) or
    #: `MATCHED_NAME` (the text matches) or `TRUNCATED_NAME` (the text is an opening of the name);
    #: `rows` is 0 for the two, since no row says so.
    by: str = ALIAS


@dataclass(frozen=True)
class Named:
    """The name of one row and the kind of identifier it came from (`LADDER`). `name` is "" where
    nothing is left to name the row. For an `ALIAS`, `MATCHED_NAME`, or `TRUNCATED_NAME` name,
    `via` is the description-shape it was learned for, `support` the rows an `ALIAS` was learned
    from, and `linked_by` the kind of identifier the shape was linked to."""

    name: str
    kind: str
    via: str = ""
    support: int = 0
    linked_by: str = ""

    @property
    def is_key(self) -> bool:
        """Whether `name` is an identifier (an account, a source's id) and not a name: such a
        name groups rows and is never printed, its readable form being `display_names`'."""
        return is_identifier_key(self.name)


#: What a page says in place of an identifier key it holds no readable name for.
UNNAMED_PARTY = "a party no name is held for"


def is_identifier_key(name: str) -> bool:
    """Whether a name is an identifier `name_of` made from the account or source-id column.

    A description-shape or a stated name drops every word holding a digit and is stripped of
    punctuation (`SHAPE_STEPS`), so a name that opens with the account prefix and a digest, or
    with a source's prefix and a colon, was never made from text. Said once here so a page asked to
    print a name it has no label for can refuse to print a key."""
    if name.startswith(ACCOUNT_KEY_PREFIX):
        digest = name[len(ACCOUNT_KEY_PREFIX) :]
        hexadecimal = all(c in "0123456789abcdef" for c in digest)
        return len(digest) == _ACCOUNT_DIGEST_LENGTH and hexadecimal
    return ":" in name and name.split(":", 1)[0].isalpha()


#: An account's key is a digest of its canonical form, never the number: an account is a permanent
#: identifier (`core.classification.PERMANENT_ID`), and the key is what a form posts back and an
#: entity keeps, so it must be neither shown nor stored in the clear. A pseudonym, not a secret:
#: the number space is small enough that someone holding a key could try every number.
ACCOUNT_KEY_PREFIX = "acct-"
_ACCOUNT_DIGEST_LENGTH = 24


def _identifier_name(kind: str, text: str) -> str:
    if kind == STATED_NAME:
        return counterparty_name(text)
    if kind == ACCOUNT:
        canonical = "".join(text.split()).replace("-", "").casefold()
        if not canonical:
            return ""
        digest = hashlib.sha256(f"obdi.party-account|{canonical}".encode()).hexdigest()
        return ACCOUNT_KEY_PREFIX + digest[:_ACCOUNT_DIGEST_LENGTH]
    return text.strip()


def name_of(
    description: str,
    counterparty: str = "",
    aliases: Mapping[str, Alias] | None = None,
    *,
    account: str = "",
    source_id: str = "",
    held: str = "",
) -> Named:
    """What a row is called: the strongest rung of `LADDER` it carries - the other party's
    account, a source's identifier for it, the counterparty name it states - else the identifier
    learned for its description's shape (`ALIAS`), else the stated name its description matches
    exactly (`MATCHED_NAME`), else the one it is the cut-off opening of (`TRUNCATED_NAME`), else
    that shape (`DESCRIPTION`).

    The stated identifier is the primary one and the description only elaborates: two rows
    printing one reference to different counterparties are two payees, and a payee printing a
    different reference every month is one. A bank states a counterparty only where it identified
    one and a statement row states none, so a name read from the counterparty alone gave one
    payee two names by source and split its series into a stopped half and a new half (measured
    on the first real store). The alias is the join: a payment seen by a feed and a statement is
    one held row carrying the feed's counterparty (`learned_links`).

    `held` is the account of the household's own that the row's other side is (`held_counterparts`):
    a transfer between the household's accounts is a transfer, its other party an ACCOUNT named by
    that account and never by a name printed on the row.
    """
    if held:
        return Named(HELD_PREFIX + held, ACCOUNT)
    for kind, text in ((ACCOUNT, account), (SOURCE_ID, source_id), (STATED_NAME, counterparty)):
        stated = _identifier_name(kind, text)
        if stated and kind == STATED_NAME:
            standing = (aliases or {}).get(STATED_PREFIX + stated)
            if standing is not None:
                return Named(
                    standing.name,
                    ALIAS,
                    via=stated,
                    support=standing.rows,
                    linked_by=standing.kind,
                )
        if stated:
            return Named(stated, kind)
    shape = _shape(description)
    learned = (aliases or {}).get(shape)
    if shape and learned is not None:
        return Named(
            learned.name, learned.by, via=shape, support=learned.rows, linked_by=learned.kind
        )
    return Named(shape, DESCRIPTION)


class Fields(NamedTuple):
    """What a row states about its other party, as `name_of` reads it: the printed description,
    the counterparty name, the party's account, and the source's id for the party."""

    description: str
    counterparty: str = ""
    account: str = ""
    source_id: str = ""
    #: The household account the row's other side is, where it is a transfer between its own.
    held: str = ""


#: Begins the key, in the links `learned_links` returns, of a STATED NAME that stands for a party
#: named by an identifier; a description-shape has no punctuation, so it cannot be mistaken for one.
STATED_PREFIX = "stated: "

#: Begins the key of a party that is one of the household's own accounts; the rest is the
#: account's name. Its label is "your <account's label>" (`display_names`).
HELD_PREFIX = "held:"


def _strong_name(row: Fields) -> Named | None:
    """The row named by its account or source id, or None where it states neither."""
    if row.held:
        return Named(HELD_PREFIX + row.held, ACCOUNT)
    for kind, text in ((ACCOUNT, row.account), (SOURCE_ID, row.source_id)):
        stated = _identifier_name(kind, text)
        if stated:
            return Named(stated, kind)
    return None


def identifiers_of(row: Fields) -> tuple[tuple[str, str], ...]:
    """Every identifier a row carries, as (kind, value) strongest first: the party's account, a
    source's id for it, the counterparty name it states, and the shape of its description, each
    as a name would be made of it. A transfer between the household's own accounts carries none,
    since its other party is an account and nothing about the row's text is evidence of it."""
    if row.held:
        return ()
    found: list[tuple[str, str]] = []
    for kind, text in (
        (ACCOUNT, row.account),
        (SOURCE_ID, row.source_id),
        (STATED_NAME, row.counterparty),
        (DESCRIPTION, row.description),
    ):
        value = _shape(text) if kind == DESCRIPTION else _identifier_name(kind, text)
        if value:
            found.append((kind, value))
    return tuple(found)


def learned_links(rows: Iterable[Fields | tuple[str, str]]) -> dict[str, Alias]:
    """For each description-shape, the one stronger identifier the rows that carry both say it
    stands for, with how many rows say so; from `Fields` rows (a bare (description,
    counterparty) pair is read as one that states no account or id).

    The stronger identifier is the row's account, else its source's id, else its stated name
    (`name_of`'s own order): a transfer seen by a feed (which states the account) and by a
    statement (which states only a description) maps the statement's description-shape to the
    account-named party, exactly as a stated name is mapped.

    A payment seen by a feed and a statement is ONE held row carrying the feed's counterparty
    (`ingest.matching`), so each row with a stated counterparty AND a description is evidence
    that the two identifiers are one party. It is derived from the rows, never declared, and
    rebuilt wherever the rows are read. A shape whose rows state two different counterparties
    is ambiguous and links to none (a rent reference paid to two housemates); a shape whose
    only counterparty is its own name links to it all the same, so a row printing the party
    exactly is named by the party and not "by the description". The links are for the rows that
    lack the stronger identifier: `name_of` uses one only where the row states none itself.

    A weaker link is added for a description-shape that no row states a counterparty with
    (`MATCHED_NAME`): where its description, compared as names are compared (`entity_tokens`),
    equals the comparison of exactly one stated party's name. It is a text comparison and exact
    after that reduction - no shared prefix, no shared words, which are proposals for the owner
    to accept - and where two different stated parties compare alike the shape links to neither.
    A statement and a feed print one party's name in different shapes (a country code, a town),
    so a party's months from the feed and from the statement alone were two names, and a weekly
    habit of 38 weeks in 52 was whole in neither (measured on the real store after R2c).

    A last link (`TRUNCATED_NAME`, `_truncation_parties`) is for a description that is not any
    stated party's form but a cut-off opening of exactly one: a statement column that stops a
    merchant at a fixed width. It is built from the same stated forms, indexed, with no further
    query. A form that IS a stated party's form belongs to the exact rung alone, even where that
    rung refused it for being ambiguous.
    """
    seen: dict[str, Counter[str]] = {}
    kind_of: dict[str, str] = {}
    stated_forms: dict[tuple[str, ...], set[str]] = {}
    form_cache: dict[str, tuple[str, ...]] = {}
    bare: dict[str, Counter[tuple[str, ...]]] = {}
    held = [Fields(*row) for row in rows]
    # A stated name that rows carrying an account or id state with ONE of them stands for that
    # party: the same bank states the name on months it states no id (an export covering years
    # the feed does not), and without this join those months are a second name with the same
    # label and a series splits into a stopped half and a new one (measured on the invented
    # large store: 40 names became 78). A name stated with two different identifiers (two people
    # sharing one) stands for neither.
    carried: dict[str, Counter[str]] = {}
    loose: set[str] = set()
    for fields in held:
        stated = counterparty_name(fields.counterparty)
        strong = _strong_name(fields)
        if strong is not None:
            kind_of[strong.name] = strong.kind
            if stated:
                carried.setdefault(stated, Counter())[strong.name] += 1
        elif stated:
            loose.add(stated)
    standing = {name: next(iter(found)) for name, found in carried.items() if len(found) == 1}
    stated_links = {
        STATED_PREFIX + name: Alias(key, carried[name][key], kind_of[key])
        for name, key in standing.items()
        if name in loose
    }
    for fields in held:
        description, counterparty = fields.description, fields.counterparty
        stated = counterparty_name(counterparty)
        strong = _strong_name(fields)
        party = strong.name if strong is not None else standing.get(stated, stated)
        shape = _shape(description)
        if party and shape:
            seen.setdefault(shape, Counter())[party] += 1
        if stated:
            form = _comparison_form(_read_by(counterparty, COUNTERPARTY_STEPS), form_cache)
            if form:
                stated_forms.setdefault(form, set()).add(party)
        elif shape and strong is None:
            form = _comparison_form(reading_of(description), form_cache)
            if form:
                bare.setdefault(shape, Counter())[form] += 1
    # A shape equal to the party's own name still links: the NAME is the same string either
    # way, but the KIND is not, and a statement row printing a merchant exactly as the feed
    # states it was being counted as "named by the description only" on the Entities page and
    # on the account's "Party stated" bar, and asked for an export file that would say nothing
    # new. The link is the one case where the answer is certain.
    links = {
        shape: Alias(name, counted[name], kind_of.get(name, STATED_NAME))
        for shape, counted in seen.items()
        if len(counted) == 1
        for name in counted
    }
    links.update(stated_links)
    index: _OpeningIndex | None = None
    for shape, forms in bare.items():
        if shape in seen:
            continue
        form = min(forms, key=lambda f: (-forms[f], f))
        parties = stated_forms.get(form, ())
        by = MATCHED_NAME
        if not parties:
            index = index or _opening_index(stated_forms)
            parties = _truncation_parties(form, index, stated_forms)
            by = TRUNCATED_NAME
        if len(parties) == 1:
            (party,) = parties
            links[shape] = Alias(party, 0, kind_of.get(party, STATED_NAME), by)
    return links


#: A cut that leaves fewer letters than this on the last word is not a truncation: two letters
#: open too many names. A lone word is only a truncation from `LONE_OPENING_LETTERS`, since one
#: word, however long, is otherwise a shared opening - a proposal for the owner, not a link.
TRUNCATION_MIN_LETTERS = 3
LONE_OPENING_LETTERS = 8

#: Stated forms indexed two ways: by first word (a description of two words or more must open
#: with the party's own first word) and, for single-word forms only, by the first
#: `LONE_OPENING_LETTERS` letters (a lone word is itself the cut).
_OpeningIndex = tuple[
    dict[str, list[tuple[str, ...]]], dict[str, list[tuple[str, ...]]]
]


def _opening_index(stated_forms: Mapping[tuple[str, ...], set[str]]) -> _OpeningIndex:
    by_first: dict[str, list[tuple[str, ...]]] = {}
    by_lone: dict[str, list[tuple[str, ...]]] = {}
    for form in stated_forms:
        by_first.setdefault(form[0], []).append(form)
        if len(form) == 1 and len(form[0]) >= LONE_OPENING_LETTERS:
            by_lone.setdefault(form[0][:LONE_OPENING_LETTERS], []).append(form)
    return by_first, by_lone


def _truncation_parties(
    form: tuple[str, ...], index: _OpeningIndex, stated_forms: Mapping[tuple[str, ...], set[str]]
) -> set[str]:
    """The stated parties whose name `form` is a strict opening of: every word equal to the
    party's word at that position except the last, which is either also equal with the party
    having further words, or a prefix of the party's word there of at least
    `TRUNCATION_MIN_LETTERS` letters; or, for a lone word of `LONE_OPENING_LETTERS` letters or
    more, a strict prefix of a party's single word. Two parties in the answer mean the caller
    links to neither."""
    by_first, by_lone = index
    found: set[str] = set()
    if len(form) == 1:
        word = form[0]
        if len(word) < LONE_OPENING_LETTERS:
            return found
        for party_form in by_lone.get(word[:LONE_OPENING_LETTERS], ()):
            if len(party_form) == 1 and party_form[0] != word and party_form[0].startswith(word):
                found |= stated_forms[party_form]
        return found
    last = len(form) - 1
    for party_form in by_first.get(form[0], ()):
        if len(party_form) < len(form) or party_form[:last] != form[:last]:
            continue
        cut, whole = form[last], party_form[last]
        if cut == whole:
            opens = len(party_form) > len(form)
        else:
            opens = len(cut) >= TRUNCATION_MIN_LETTERS and whole.startswith(cut)
        if opens:
            found |= stated_forms[party_form]
    return found


def _comparison_form(
    reading: str, cache: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """What a reading is compared on as a name: its tokens' comparable words (`tokens_of`), or
    () where none of them can tell one payee from another (`distinctive_words`), so a reading
    of only initials, methods, or codes matches no party."""
    found = cache.get(reading)
    if found is None:
        tokens = tokens_of(reading)
        found = tuple(t.norm for t in tokens) if distinctive_words(tokens) else ()
        cache[reading] = found
    return found


def names_of(rows: Sequence[Fields | tuple[str, str]]) -> list[Named]:
    """The name of each row (a `Fields`, or a bare (description, counterparty) pair), the links
    learned from all of them (`learned_links`) used for the rows that state none."""
    links = learned_links(rows)
    fields = [Fields(*row) for row in rows]
    return [
        name_of(
            f.description,
            f.counterparty,
            links,
            account=f.account,
            source_id=f.source_id,
            held=f.held,
        )
        for f in fields
    ]


def held_counterparts(rows: Sequence[Transaction], pairs: Iterable[tuple[str, str]]) -> list[str]:
    """For each row, the household account its other side is, or "" where it is not a transfer
    between the household's own accounts.

    Two kinds of evidence, both already held: the confirmed transfer pair the row is a leg of (its
    other side is the opposite leg's account), and the account identifier the row states
    (`Transaction.party_account`) where the pairs show that identifier to be one of the
    household's accounts - a leg that states it is the other account's, so every row that states
    the same identifier is a transfer to that account, paired or not. An identifier the pairs
    show leading to two accounts is ambiguous and counts as neither, and a row is never its own
    account's counterpart.

    The household's own accounts' sort codes and numbers are not held anywhere (`ingest.identifiers`
    reads them from landed account payloads and keeps nothing), so an identifier is learned only
    from a pair that has been confirmed; a transfer to an account of the household that has no
    confirmed leg and states an identifier no pair states is not known to be one.
    """
    by_id = {row.entity_id: row for row in rows if row.entity_id}
    other: dict[str, str] = {}
    leading: dict[str, set[str]] = {}
    for leaving, arriving in pairs:
        first, second = by_id.get(leaving), by_id.get(arriving)
        if first is None or second is None or first.account_id == second.account_id:
            continue
        other[leaving], other[arriving] = second.account_id, first.account_id
        if first.party_account:
            leading.setdefault(first.party_account, set()).add(second.account_id)
        if second.party_account:
            leading.setdefault(second.party_account, set()).add(first.account_id)
    known = {
        identifier: next(iter(found)) for identifier, found in leading.items() if len(found) == 1
    }
    result = []
    for row in rows:
        found = other.get(row.entity_id) or known.get(row.party_account, "")
        result.append("" if found == row.account_id else found)
    return result


def name_rows(
    rows: Sequence[Transaction],
    pairs: Iterable[tuple[str, str]] = (),
    links: Mapping[str, Alias] | None = None,
) -> tuple[list[Fields], Mapping[str, Alias], list[Named]]:
    """What each held row states about its other party, the links learned from all of them, and
    the name of each: the one place a row becomes a name, so the Entities page and the detector
    cannot disagree. `pairs` is the pairing pass's (leaving, arriving) entity for each proved
    transfer (`held_counterparts`); `links` is given where the caller has them already."""
    held = held_counterparts(rows, pairs)
    fields = [
        Fields(r.description, r.counterparty, r.party_account, r.party_source_id, account)
        for r, account in zip(rows, held, strict=True)
    ]
    learned = learned_links(fields) if links is None else links
    named = [
        name_of(
            f.description,
            f.counterparty,
            learned,
            account=f.account,
            source_id=f.source_id,
            held=f.held,
        )
        for f in fields
    ]
    return fields, learned, named


def display_names(
    rows: Sequence[Fields],
    named: Sequence[Named],
    held_labels: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """For each name that is an identifier (an account, a source's id), the readable name its
    rows are SHOWN under: the counterparty name most of them state, else the shape of the
    description most of them print, else `UNNAMED_PARTY`. Never the identifier itself.

    Most common first, then alphabetical, so the answer does not depend on the order of the rows.
    A party's identity is its identifier; this is only what it is called to a reader, and two
    parties may be called alike (two people who share a stated name), which is why identity does
    not rest on it.

    A household account is called "your <label>", the label from `held_labels` (an account's
    name to the label pages show for it; the account's own name where none is given) - never the
    account's number, and never a name printed on the transfer's rows."""
    stated: dict[str, Counter[str]] = {}
    described: dict[str, Counter[str]] = {}
    held = {
        item.name: "your " + ((held_labels or {}).get(account) or account)
        for item in named
        if item.name.startswith(HELD_PREFIX)
        for account in [item.name[len(HELD_PREFIX) :]]
    }
    for row, item in zip(rows, named, strict=True):
        if item.kind not in (ACCOUNT, SOURCE_ID) or not item.name or item.name in held:
            continue
        stated.setdefault(item.name, Counter())
        described.setdefault(item.name, Counter())
        name = counterparty_name(row.counterparty)
        if name:
            stated[item.name][name] += 1
        shape = _shape(row.description)
        if shape:
            described[item.name][shape] += 1

    def best(counted: Counter[str]) -> str:
        return min(counted, key=lambda text: (-counted[text], text))

    labels = {
        key: best(stated[key]) if stated[key] else best(described[key]) if described[key]
        else UNNAMED_PARTY
        for key in stated
    }
    return {**labels, **held}


def count_shapes(descriptions: Iterable[str]) -> dict[str, int]:
    """How many of the descriptions have each shape; descriptions with no shape are not counted."""
    counted = Counter(shape_of(text) for text in descriptions)
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
    #: Rows named by the description matching this name exactly (`MATCHED_NAME`).
    matched: int = 0
    #: Rows named by the description being a cut-off opening of this name (`TRUNCATED_NAME`).
    truncated: int = 0
    #: Rows named by the other party's account (`ACCOUNT`) or the source's own id for the party
    #: (`SOURCE_ID`), which name them before anything they print.
    account: int = 0
    source_id: int = 0
    #: The first source (alphabetically) that stated this name or the source's id, "" where none
    #: did or the name is an account number, which is not per source.
    source: str = ""

    @property
    def rows(self) -> int:
        return (
            self.stated + self.linked + self.described + self.matched + self.truncated
            + self.account + self.source_id
        )

    @property
    def kind(self) -> str:
        """The strongest kind any row of the name has (`LADDER`)."""
        if self.account:
            return ACCOUNT
        if self.source_id:
            return SOURCE_ID
        if self.stated:
            return STATED_NAME
        if self.linked:
            return ALIAS
        if self.matched:
            return MATCHED_NAME
        return TRUNCATED_NAME if self.truncated else DESCRIPTION


def name_origins(
    named: Iterable[Named], sources: Iterable[str] | None = None
) -> dict[str, NameOrigin]:
    """For each name, how its rows came to have it; rows with no name are not counted.

    `sources` is the source of each row, in the order of `named`, where the caller has them: the
    source of a stated name or a source's id is kept for the identifier a merge attaches."""
    stated: Counter[str] = Counter()
    linked: Counter[str] = Counter()
    described: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    truncated: Counter[str] = Counter()
    accounts: Counter[str] = Counter()
    source_ids: Counter[str] = Counter()
    supports: dict[str, dict[str, int]] = {}
    first_source: dict[str, str] = {}
    rows = list(named)
    each = list(sources) if sources is not None else [""] * len(rows)
    for item, source in zip(rows, each, strict=True):
        if not item.name:
            continue
        if item.kind == ACCOUNT:
            accounts[item.name] += 1
        elif item.kind == MATCHED_NAME:
            matched[item.name] += 1
        elif item.kind == TRUNCATED_NAME:
            truncated[item.name] += 1
        elif item.kind == ALIAS:
            linked[item.name] += 1
            supports.setdefault(item.name, {})[item.via] = item.support
        elif item.kind == DESCRIPTION:
            described[item.name] += 1
        else:
            if item.kind == SOURCE_ID:
                source_ids[item.name] += 1
            else:
                stated[item.name] += 1
            if source and (item.name not in first_source or source < first_source[item.name]):
                first_source[item.name] = source
    return {
        name: NameOrigin(
            stated[name],
            linked[name],
            described[name],
            sum(supports.get(name, {}).values()),
            matched[name],
            truncated[name],
            accounts[name],
            source_ids[name],
            first_source.get(name, ""),
        )
        for name in {
            *stated, *linked, *described, *matched, *truncated, *accounts, *source_ids
        }
    }


def identifier_for(name: str, origin: NameOrigin | None) -> Identifier:
    """The identifier a merge attaches for a ticked name: the strongest kind its rows carry,
    never a string of unknown kind. A name stated, linked, or matched to a party is attached as
    that party's stated name (with the source that stated it); a name made from a description
    alone as a description-shape. A name the page holds no origin for is a description-shape,
    which is all that can be said of it."""
    if origin is None:
        return Identifier(DESCRIPTION, name)
    kind = identifier_kind(origin.kind)
    source = origin.source if kind in (STATED_NAME, SOURCE_ID) else ""
    return Identifier(kind, name, source, DECLARED, origin.rows)


def carried_by(identifier: Identifier, origin: NameOrigin | None) -> bool:
    """Whether some row now carries the identifier: a row of the name that the identifier's kind
    links (`link_keys`). Without origins (a view made from counts alone) the name is all that can
    be checked, and a name with rows is carried."""
    if origin is None:
        return True
    return any(
        key == (identifier.kind, identifier.value)
        for kind in LADDER
        if _origin_has(origin, kind)
        for key in link_keys(kind, identifier.value)
    )


def _origin_has(origin: NameOrigin, kind: str) -> bool:
    return bool(
        {
            ACCOUNT: origin.account,
            SOURCE_ID: origin.source_id,
            STATED_NAME: origin.stated,
            ALIAS: origin.linked,
            MATCHED_NAME: origin.matched,
            TRUNCATED_NAME: origin.truncated,
            DESCRIPTION: origin.described,
        }[kind]
    )


def name_readings(
    rows: Sequence[Fields | tuple[str, str]], named: Sequence[Named]
) -> dict[str, str]:
    """For each name, the text it is COMPARED on (`reading_of`): the reading of the counterparty
    or description it was read from, most common first, then of fewer words, then alphabetical,
    so the answer does not depend on the order of the rows. A row named through a link adds
    nothing: the name's own rows say how it is read."""
    seen: dict[str, Counter[str]] = {}
    for row, item in zip(rows, named, strict=True):
        description, counterparty = row[0], row[1]
        if not item.name or item.kind in (ALIAS, MATCHED_NAME, TRUNCATED_NAME, ACCOUNT, SOURCE_ID):
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
    """What a rule does, as the words before and after its own words: that it reads the
    transaction's NAME - the strongest field the row carries, made as the page states above
    (`reduced`; left out where the line is on a phone and the making is stated beside it) - and
    how the name is compared. `source` names the field only where a rule is tied to one; a rule
    of the two kinds kept today reads the name whatever its kind."""
    reads = "any transaction whose name" if source == DESCRIPTION_SOURCE else (
        f"any transaction whose {source}"
    )
    if reduced:
        reads += ", made as above,"
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


def shape_entities(
    store: Store, known: Iterable[str] = ()
) -> dict[tuple[str, str], tuple[int, str]]:
    """Each identifier under an entity now (`entities_of`), as (kind, value), to the id and name
    of its entity; a name an entity holds only because a rule matches it is keyed (`RULE`, name).
    A row finds its entity through `entity_of`, which tries the kinds it carries in order."""
    held: dict[tuple[str, str], tuple[int, str]] = {}
    for entity in entities_of(store, known):
        for identifier in entity.identifiers:
            held[(identifier.kind, identifier.value)] = (entity.id, entity.name)
        for name in entity.by_rule:
            held[(RULE, name)] = (entity.id, entity.name)
    return held


def holder_of(store: Store, shape: str, known: Iterable[str] = ()) -> tuple[int, str] | None:
    """The id and name of the entity a name is under now, whatever the kind it is held as."""
    return next(
        (
            (entity.id, entity.name)
            for entity in entities_of(store, known)
            if shape in entity.shapes
        ),
        None,
    )


def _rule_matches_for(store: Store, entity: int, shape: str) -> bool:
    return any(
        rule_matches(rule.kind, rule.words, shape)
        for rule in store.entity_rules()
        if rule.entity_id == entity
    )


def detach_shape(
    store: Store, shape: str, *, now: datetime | None = None, kind: str | None = None
) -> bool:
    """Split the identifier whose value is `shape` (of the `kind` given, else the strongest held)
    apart from the entity it was attached to by hand, and commit; False where it belonged to none
    (`Store.detach_shape` says what else is kept).

    A name a rule of its entity also matches would be attached again by that rule on the next
    read, so the store is told to record the exclusion as well: whether a rule matches is the
    analysis's to say, and the store does what it is told in the one commit.
    """
    held = store.shape_entities().get(shape)
    if held is None:
        return False
    return store.detach_shape(
        shape, exclude=_rule_matches_for(store, held[0], shape), now=now, kind=kind
    )


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
    #: For a `SAME_ROWS` group: the kinds of identifier (`IDENTIFIER_KINDS`) the payments tie
    #: together, strongest first, and how many payments carry two of them.
    kinds: tuple[str, ...] = ()
    shared_rows: int = 0

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


@dataclass(frozen=True)
class Tie:
    """Names that payments tie together: the rows carry identifiers of different kinds that these
    names stand for, on at least `MIN_SHARED_ROWS` payments (`entity_ties.row_ties` finds them)."""

    names: tuple[str, ...]
    #: The kinds of identifier that join them, strongest first.
    kinds: tuple[str, ...]
    #: The payments that carry two of those identifiers.
    rows: int


def evidence_proposals(
    ties: Sequence[Tie],
    counts: Mapping[str, int],
    origins: Mapping[str, NameOrigin],
    shown_as: Mapping[str, str],
    taken: Collection[str],
) -> tuple[Proposal, ...]:
    """The groups payments themselves say are one party (`SAME_ROWS`), for the owner to accept.

    A name is in at most one group (the tie of more payments takes it), and one the owner has
    already placed under an entity is left where it is. Members are listed strongest kind first,
    then most used, so a tick attaches each as the kind its rows carry (`identifier_for`) in that
    order. A group is named by the readable label of its first member.
    """
    rank = {kind: position for position, kind in enumerate(LADDER)}

    def strength(name: str) -> tuple[int, int, str]:
        origin = origins.get(name)
        return (rank[origin.kind] if origin else len(rank), -counts[name], name)

    def readable(name: str) -> str:
        found = shown_as.get(name)
        if found is not None:
            return found
        return UNNAMED_PARTY if is_identifier_key(name) else name

    assigned: set[str] = set()
    found: list[Proposal] = []
    for tie in sorted(ties, key=lambda t: (-t.rows, t.names)):
        free = [n for n in tie.names if n in counts and n not in taken and n not in assigned]
        if len(free) < 2:
            continue
        assigned.update(free)
        ordered = tuple(sorted(free, key=strength))
        label = next(
            (readable(n) for n in ordered if readable(n) != UNNAMED_PARTY), UNNAMED_PARTY
        )
        found.append(
            Proposal(
                name=" ".join(word.capitalize() for word in label.split()),
                shapes=ordered,
                rules=frozenset({SAME_ROWS}),
                transactions=sum(counts[n] for n in ordered),
                kinds=tie.kinds,
                shared_rows=tie.rows,
            )
        )
    found.sort(key=lambda p: (-p.transactions, p.name, p.shapes))
    return tuple(found)


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
    #: For a row named through a link: the kind of identifier (`LADDER`) the link leads to.
    linked_by: str = ""


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
    #: Free names that share a distinctive word with an entity, offered under it.
    suggestions: tuple[Suggestion, ...] = ()
    #: How each name's rows came to have it (`name_origins`); empty where the view was made from
    #: counts alone, and the page then says nothing about sources.
    origins: Mapping[str, NameOrigin] = field(default_factory=dict)
    #: The readable name of each name that is an identifier (`display_names`). Such a name groups
    #: rows and is never printed: a page asks `label`.
    labels: Mapping[str, str] = field(default_factory=dict)

    def label(self, name: str) -> str:
        """What a page prints for a name: its label where it is an identifier, the name itself
        otherwise, and `UNNAMED_PARTY` for an identifier no row holds a label for now (an entity
        still holding a party whose payments have gone) - never the identifier."""
        found = self.labels.get(name)
        if found is not None:
            return found
        return UNNAMED_PARTY if is_identifier_key(name) else name

    def free_shapes(self) -> list[str]:
        """The shapes under no entity, most-used first."""
        held = {shape for entity in self.entities for shape in entity.shapes}
        return sorted((s for s in self.counts if s not in held), key=lambda s: (-self.counts[s], s))


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

    @property
    def orphaned_identifiers(self) -> tuple[Identifier, ...]:
        """The identifiers attached by hand that no transaction carries now, by kind: a stated
        name whose rows are all described only is as orphaned as a name no row has, because the
        row would link through its description and not through the party (`carried_by`). Where
        the view holds no origins, the name alone is checked."""
        origins = self.view.origins
        return tuple(
            identifier
            for identifier in self.entity.identifiers
            if identifier.value not in self.view.counts
            or not carried_by(identifier, origins.get(identifier.value) if origins else None)
        )


def entity_page_of(
    entity_id: int,
    entities: Sequence[Entity],
    rules: Sequence[EntityRule],
    counts: Mapping[str, int],
    covers: Mapping[str, tuple[Covered, ...]],
    origins: Mapping[str, NameOrigin] | None = None,
    labels: Mapping[str, str] | None = None,
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
            labels=labels or {},
        ),
    )


def view_of(
    counts: Mapping[str, int],
    entities: Iterable[Entity],
    covers: Mapping[str, tuple[Covered, ...]] | None = None,
    origins: Mapping[str, NameOrigin] | None = None,
    readings: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
    ties: Sequence[Tie] = (),
) -> EntitiesView:
    """The page's view of the names held and the entities made from them.

    `origins` is `name_origins`: how each name's rows came to have it.
    `readings` is `name_readings`: the text each name is compared on where that is not the name.
    `labels` is `display_names`. A name that is an identifier is an exact link and not text, so
    it is never proposed for a merge by what its label looks like: nothing is asked of the owner
    about a party the account or the bank's own id already settled. That includes a transfer
    between the household's own accounts, whose other party is an account (`held_accounts`).
    `ties` is what payments say, not what names look like (`entity_ties.row_ties`): the one thing
    that can propose an identifier name beside another, and it leads the proposals, the names it
    holds being left out of the text reasons.
    """
    made = tuple(entities)
    set_apart = {shape for entity in made for shape in entity.shapes}
    evidence = evidence_proposals(ties, counts, origins or {}, labels or {}, set_apart)
    tied = {name for group in evidence for name in group.shapes}
    text_names = {name: n for name, n in counts.items() if not is_identifier_key(name)}
    text = propose_groups(text_names, taken=set_apart | tied, readings=readings)
    proposals = Proposals(groups=(*evidence, *text.groups), too_broad=text.too_broad)
    offered = {s for g in (*proposals.groups, *proposals.too_broad) for s in g.shapes}
    return EntitiesView(
        counts=counts,
        entities=made,
        proposals=proposals,
        covers=covers or {},
        origins=origins or {},
        labels=labels or {},
        suggestions=suggest_for_entities(text_names, made, set_apart | offered, readings),
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
