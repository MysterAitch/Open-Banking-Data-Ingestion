"""A balance stated by a source that cannot see Spaces is the balance of the whole family.

A Starling account is a MAIN account plus one account per Space. The certified
statement, the CSV export and the aggregator describe the whole account as one
ledger of external movements: a payment from a Space moves their balance, a
transfer between main and a Space never does. A balance they state is therefore
the balance of the FAMILY, the main account plus every known Space, and filing
it as the main account's own derived an opening wrong by whatever the Spaces
held at that date. Measured on the deployed store, the certified statement's 466
rows held 114 Space payments, and its own arithmetic (opening plus every row
equals closing, the end-of-day column walked) passed with them included.

WHICH SOURCES. Decided from the account map exactly as the fold decides which
rows are copies: a source is BLIND to a main account's Spaces when the map binds
it to none of them (`Families.blind`, built on `space_parents` and the same
`accounts_by_source` reading). Nothing here names a source. A file import is
filed under the account it was imported for and carries its parser's source
name, which is the name looked up; an unbound source is blind, as it is in the
fold. A source that feeds a Space is not blind and keeps its anchors as the
main account's own.

WHICH BALANCES, all derived on demand from the held artefacts and never stored:

  certified statement   its opening (the end of the day before its first row),
                        each printed end-of-day balance that agrees with the
                        statement's own rows, and its closing balance
  CSV export            the balance after the last row of each day. "Last" is
                        found without trusting file order: each row gives the
                        balance before it and after it, and the day's closing
                        is the figure no row consumes (`_chain_ends`)
  aggregator            the bank's earliest opening and latest closing, which
                        `balance_reconciliation` already derives. Only those
                        two: a day holding a folded row has a broken chain and
                        is ambiguous there, so no per-day figure is offered.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from .accounts import AccountMap
from .balance_reconciliation import _chain_ends
from .parsers.uk_banks import StarlingCsvParser
from .space_attribution import space_parents
from .statement_terms import statement_day_balances
from .store import Store


@dataclass(frozen=True)
class Families:
    """Which accounts are Spaces of which, and which sources feed which accounts."""

    #: Each known Space's main account (`space_parents`).
    parents: Mapping[str, str]
    #: Each source's every bound account, as the fold reads the map.
    feeds: Mapping[str, frozenset[str]]

    def spaces_of(self, main: str) -> tuple[str, ...]:
        """The known Spaces of `main`; none for an account that is itself a Space."""
        if main in self.parents:
            return ()
        return tuple(sorted(space for space, owner in self.parents.items() if owner == main))

    def blind(self, source: str, main: str) -> bool:
        """Whether `source` is bound to none of `main`'s known Spaces."""
        fed = self.feeds.get(source, frozenset())
        return not any(space in fed for space in self.spaces_of(main))


def families_of(store: Store, account_map: AccountMap) -> Families:
    return Families(
        parents=space_parents(store, account_map),
        feeds={
            source: frozenset(str(account) for account in accounts)
            for source, accounts in account_map.accounts_by_source().items()
        },
    )


@dataclass(frozen=True)
class FamilyAnchor:
    """The FAMILY's balance at the END of `day`, as `source` states it."""

    day: date
    balance_minor: int
    source: str


@dataclass(frozen=True)
class FamilyAnchors:
    anchors: tuple[FamilyAnchor, ...] = ()
    #: Printed end-of-day balances refused because they disagreed with their
    #: own statement's rows. A count, so a quiet walk is not mistaken for one
    #: that had every figure to work with.
    refused_figures: int = 0


#: Artefact digest -> (source, balances) for a CSV export, or None when the
#: bytes are not a Starling export or state no balance. The bytes never change,
#: so a reading is good for the life of the process.
_CSV_BY_DIGEST: dict[str, tuple[str, tuple[tuple[date, int], ...]] | None] = {}


def _csv_days(
    store: Store, digest: str, account: str
) -> tuple[str, tuple[tuple[date, int], ...]] | None:
    if digest not in _CSV_BY_DIGEST:
        row = store.connection.execute(
            "SELECT payload FROM raw_artefacts "
            "WHERE digest = ? AND account_ref = ? AND media_type = 'text/csv' LIMIT 1",
            (digest, account),
        ).fetchone()
        parser = StarlingCsvParser()
        held: tuple[str, tuple[tuple[date, int], ...]] | None = None
        if row is not None and parser.sniff(bytes(row["payload"])):
            try:
                figures = parser.running_balances(bytes(row["payload"]))
            except Exception as exc:
                print(
                    f"artefact {digest[:12]}: {parser.source} balances could not be read - {exc}",
                    file=sys.stderr,
                )
                figures = []
            held = (parser.source, _closing_balances(figures)) if figures else None
        _CSV_BY_DIGEST[digest] = held
    return _CSV_BY_DIGEST[digest]


def _closing_balances(rows: list[tuple[date, int, int]]) -> tuple[tuple[date, int], ...]:
    """Each day's closing balance, and the earliest day's opening.

    A day whose balances do not form one chain is left out, never guessed: the
    file's order is not trusted, so a day with two candidate ends has no
    answer.
    """
    by_day: dict[date, list[tuple[int, int]]] = defaultdict(list)
    for day, amount, balance in rows:
        by_day[day].append((balance - amount, balance))
    found: dict[date, int] = {}
    for day in sorted(by_day):
        opening, closing, _ = _chain_ends(by_day[day])
        if opening is None or closing is None:
            continue
        if not found:
            found[day - timedelta(days=1)] = opening
        found[day] = closing
    return tuple(sorted(found.items()))


def _held_csv_digests(store: Store, account: str) -> list[str]:
    return [
        str(row["digest"])
        for row in store.connection.execute(
            "SELECT DISTINCT digest FROM raw_artefacts "
            "WHERE account_ref = ? AND media_type = 'text/csv'",
            (account,),
        )
    ]


def family_anchors(store: Store, main: str, families: Families) -> FamilyAnchors:
    """The family balances `main`'s held statements and exports state, from
    sources blind to its Spaces. Empty for an account with no known Spaces."""
    if not families.spaces_of(main):
        return FamilyAnchors()
    found: set[FamilyAnchor] = set()
    statements, refused = statement_day_balances(store, main)
    for item in statements:
        if families.blind(item.source, main):
            found.add(FamilyAnchor(item.day, item.balance_minor, item.source))
    for digest in _held_csv_digests(store, main):
        held = _csv_days(store, digest, main)
        if held is not None and families.blind(held[0], main):
            found.update(FamilyAnchor(day, minor, held[0]) for day, minor in held[1])
    ordered = sorted(found, key=lambda a: (a.day, a.balance_minor, a.source))
    return FamilyAnchors(tuple(ordered), refused)


__all__ = [
    "Families",
    "FamilyAnchor",
    "FamilyAnchors",
    "families_of",
    "family_anchors",
]
