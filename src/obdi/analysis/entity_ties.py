"""Which names the payments themselves say are one party.

A payment that carries two identifiers of different kinds - a source's id for the party beside the
name the source states, the party's account beside the id - is evidence that those identifiers are
one party (`docs/design/2026-10-commitments/entities.md` section 4). `name_of` already resolves a
row to ONE name by the strongest identifier it carries, and `learned_links` already joins a
description-shape or a stated name to the one party rows say it stands for. What is left is the
names that stay distinct after both although payments tie them: a merchant with two ids for one
stated name, an account and an id seen together on some payments and apart on others, a shape
seen mostly with one id and once with another (which `learned_links` refuses as ambiguous).

Computed from the rows already read for the page, in memory: it asks the store nothing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import combinations

from ..ingest.entity_records import DESCRIPTION, IDENTIFIER_KINDS
from .entities import MIN_SHARED_ROWS, Fields, Named, Tie, identifiers_of

_Token = tuple[str, str]


def row_ties(fields: Sequence[Fields], named: Sequence[Named]) -> tuple[Tie, ...]:
    """The groups of names that payments tie together, each with the kinds of identifier that
    tie them and how many payments carry two of those.

    Two identifiers are tied when at least `MIN_SHARED_ROWS` payments carry both. A description's
    shape is the least reliable identifier (two housemates paid under one reference both print
    it), so a shape whose tying payments belong to more than one name is dropped as ambiguous,
    exactly as `learned_links` drops a shape seen with two parties; a stated name is not, since a
    bank states one merchant consistently, and a merchant with two ids is the case this finds.

    `named` is `name_rows`' answer for the same rows, in the same order.
    """
    carried = [identifiers_of(row) for row in fields]
    pairs: Counter[tuple[_Token, _Token]] = Counter()
    holders: dict[_Token, list[int]] = {}
    for index, tokens in enumerate(carried):
        for token in tokens:
            holders.setdefault(token, []).append(index)
        pairs.update(combinations(tokens, 2))
    kept = {pair for pair, shared in pairs.items() if shared >= MIN_SHARED_ROWS}
    if not kept:
        return ()

    support: dict[tuple[_Token, _Token], list[int]] = {pair: [] for pair in kept}
    for index, tokens in enumerate(carried):
        for pair in combinations(tokens, 2):
            if pair in support:
                support[pair].append(index)

    def names_of(rows: Sequence[int]) -> set[str]:
        return {named[i].name for i in rows if named[i].name}

    # A shape is ambiguous when the payments that tie it, across all its pairs, are named for more
    # than one party; one pair's payments naming one party do not make it so.
    tying: dict[_Token, list[int]] = {}
    for pair, rows in support.items():
        for token in pair:
            if token[0] == DESCRIPTION:
                tying.setdefault(token, []).extend(rows)
    ambiguous = {token for token, rows in tying.items() if len(names_of(rows)) > 1}
    # A token stands for a party where it IS the name of some row (an id, an account, a stated
    # name nothing outranks, a shape nothing links), or where the rows carrying it are named
    # differently: the bridge between names. A token that is neither (the shape a feed prints for
    # rows an id already names) is carried along by the rows and says nothing of its own, so it
    # neither joins two tokens nor is counted as evidence.
    own = {
        token
        for pair in support
        for token in pair
        if any(named[i].name == token[1] for i in holders[token])
    }

    def stands(token: _Token) -> bool:
        return token in own or len(names_of(holders[token])) > 1

    live = {
        pair: rows
        for pair, rows in support.items()
        if not set(pair) & ambiguous and stands(pair[0]) and stands(pair[1])
    }

    parent: dict[_Token, _Token] = {}

    def find(token: _Token) -> _Token:
        parent.setdefault(token, token)
        while parent[token] != token:
            parent[token] = parent[parent[token]]
            token = parent[token]
        return token

    for first, second in live:
        parent[find(first)] = find(second)

    grouped: dict[_Token, list[_Token]] = {}
    for token in parent:
        grouped.setdefault(find(token), []).append(token)

    ties: list[Tie] = []
    for joined in grouped.values():
        members = set(joined)
        tied_rows = {i for pair, found in live.items() if pair[0] in members for i in found}
        names = names_of(sorted(tied_rows)) | {token[1] for token in members if token in own}
        if len(names) < 2:
            continue
        kinds = {kind for kind, _value in members}
        ties.append(
            Tie(
                names=tuple(sorted(names)),
                kinds=tuple(kind for kind in IDENTIFIER_KINDS if kind in kinds),
                rows=len(tied_rows),
            )
        )
    return tuple(sorted(ties, key=lambda t: (-t.rows, t.names)))
