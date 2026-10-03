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

THE OPENED ANCHOR. Starling's own feed holds every movement of the main account
and of every Space since the account was created, so a family whose complete
history is held opened at NIL, and no stated balance has to define the opening:
each is a test. That is claimed only when two facts are both held
(`opening_evidence`), and neither is ever assumed:

  the account's creation   the `createdAt` the provider states for the account
                           in a landed `starling-accounts` artefact, found by
                           the account's own `accountUid` (`Families.provider_ids`)
  the feed's reach         the earliest `changesSince` lower bound among the
                           landed `starling-feed` origins (`artefact_origins`)
                           asked of that account's DEFAULT category, which must
                           be at or before the creation instant

The anchor is nil at the END of the day before the creation date (UTC, the day
the rows are dated by). The creation day itself is not the anchor's day because
an account is usually funded the day it is made: a nil balance at the end of the
creation day would exclude, and so absorb, exactly those movements.

What the evidence cannot show, and the walk therefore says rather than hides:
a Space that took part in a transfer but whose own feed is not held (see
`unheld_space_legs`), after which the family's rows cannot reach the stated
balance however complete the main account's feed is.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from .accounts import AccountMap
from .balance_reconciliation import _chain_ends
from .models import Transaction
from .parsers.uk_banks import StarlingCsvParser
from .space_attribution import space_parents
from .spaces import LISTING_SOURCE, SPACE_COUNTERPARTY, _listed_uids, historical_spaces
from .statement_terms import statement_day_balances
from .store import Store

#: The basis (and source name) of the nil anchor at an account's creation.
OPENED = "opened"


@dataclass(frozen=True)
class Families:
    """Which accounts are Spaces of which, and which sources feed which accounts."""

    #: Each known Space's main account (`space_parents`).
    parents: Mapping[str, str]
    #: Each source's every bound account, as the fold reads the map.
    feeds: Mapping[str, frozenset[str]]
    #: Each account's own identifiers in the Starling API: an account uid for a
    #: main account, a category uid for a Space.
    provider_ids: Mapping[str, frozenset[str]]

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
        provider_ids={
            str(ref): ids for ref, ids in account_map.provider_ids("starling").items()
        },
    )


@dataclass(frozen=True)
class FamilyAnchor:
    """The FAMILY's balance at the END of `day`, as `source` states it."""

    day: date
    balance_minor: int
    source: str


@dataclass(frozen=True)
class OpeningEvidence:
    """Whether the family's history is held from its first day (`opening_evidence`)."""

    #: The creation date the provider states, when it states exactly one.
    created: date | None
    #: Nil at the end of the day before `created`; None when `missing` says why not.
    opened: FamilyAnchor | None
    missing: str
    #: Category uids that are accounted for: the family's own, every main
    #: account's, and every Space the provider still lists. A Space transfer
    #: naming any other category has no leg held.
    known_categories: frozenset[str]


@dataclass(frozen=True)
class FamilyAnchors:
    anchors: tuple[FamilyAnchor, ...] = ()
    #: Printed end-of-day balances refused because they disagreed with their
    #: own statement's rows. A count, so a quiet walk is not mistaken for one
    #: that had every figure to work with.
    refused_figures: int = 0
    #: What the held evidence says about the account's opening; None for an
    #: account with no known Spaces.
    evidence: OpeningEvidence | None = None

    @property
    def opened(self) -> FamilyAnchor | None:
        return self.evidence.opened if self.evidence else None


def _instant(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


def opening_evidence(store: Store, main: str, families: Families) -> OpeningEvidence:
    """Whether `main`'s family can be shown to have opened at nil.

    Reads, and only these: the `createdAt` and `defaultCategory` of the
    `starling-accounts` artefacts' entries whose `accountUid` is one of the
    account's own provider ids; the `starling-spaces` listings' Space uids; and
    the `changesSince` lower bound of every `starling-feed` origin naming that
    account's uid and default category. Several artefacts stating different
    creation dates is no evidence at all, and a lower bound that cannot be read
    is not counted, so every doubt lands on the side of no anchor.
    """
    # Imported here: the rebuild module imports the layers above this one.
    from .rebuild import _FEED_ORIGIN

    own = families.provider_ids.get(main, frozenset())
    known: set[str] = set(own)
    for space in families.spaces_of(main):
        known |= families.provider_ids.get(space, frozenset())
    created: set[datetime] = set()
    defaults: set[str] = set()
    for row in store.connection.execute(
        "SELECT source, payload FROM raw_artefacts WHERE source IN (?, 'starling-accounts')",
        (LISTING_SOURCE,),
    ):
        if row["source"] == LISTING_SOURCE:
            known |= _listed_uids(row["payload"]) or frozenset()
            continue
        try:
            decoded = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        accounts = decoded.get("accounts") if isinstance(decoded, dict) else None
        for account in accounts if isinstance(accounts, list) else []:
            if not isinstance(account, dict):
                continue
            category = str(account.get("defaultCategory") or "")
            known.add(category)
            if str(account.get("accountUid") or "") not in own:
                continue
            defaults.add(category)
            stamp = _instant(str(account.get("createdAt") or ""))
            if stamp is not None:
                created.add(stamp)
    known.discard("")

    asked: list[datetime] = []
    for row in store.connection.execute(
        "SELECT DISTINCT origin FROM artefact_origins WHERE source = 'starling-feed'"
    ):
        origin = str(row["origin"])
        found = _FEED_ORIGIN.search(origin)
        if not found or found.group(1) not in own or found.group(2) not in defaults:
            continue
        for value in parse_qs(urlparse(origin).query).get("changesSince", []):
            moment = _instant(value)
            if moment is not None:
                asked.append(moment)

    categories = frozenset(known)
    if not created:
        return OpeningEvidence(None, None, "no creation date is held for the account", categories)
    if len(created) > 1:
        return OpeningEvidence(
            None, None, "the provider states more than one creation date for the account",
            categories,
        )
    opened_at = next(iter(created))
    opened_on = opened_at.date()
    if not asked:
        return OpeningEvidence(
            opened_on, None,
            "no feed request is held for the account's own category, so how far the feed "
            "reaches back is unknown",
            categories,
        )
    if min(asked) > opened_at:
        return OpeningEvidence(
            opened_on, None,
            f"the feed does not reach back to the opening: the earliest request held asked "
            f"from {min(asked).date().isoformat()} and the account opened on "
            f"{opened_on.isoformat()}",
            categories,
        )
    nil = FamilyAnchor(opened_on - timedelta(days=1), 0, OPENED)
    return OpeningEvidence(opened_on, nil, "", categories)


@dataclass(frozen=True)
class UnheldLegs:
    """Movements to or from a Space whose own rows are not held."""

    legs: int = 0
    first: date | None = None


def unheld_space_legs(rows: Iterable[Transaction], known_categories: frozenset[str]) -> UnheldLegs:
    """The counted rows whose counterparty is a Space the family does not hold.

    Recognised by `spaces.historical_spaces`, the rule `recover-spaces` uses: a
    counterparty of type CATEGORY that is neither a main account's own category
    nor a Space still listed. `known_categories` adds the family's own
    accounts, so a held Space is never reported even where its listing was not
    landed. Counts and the first date only, never a figure.
    """
    counted = [t for t in rows if not t.status.is_history]
    gone = {
        space.uid
        for space in historical_spaces(
            feed_items=[t.raw for t in counted], current_space_uids=set(known_categories)
        )
    }
    legs = [
        t
        for t in counted
        if str(t.raw.get("counterPartyType", "")).upper() == SPACE_COUNTERPARTY
        and str(t.raw.get("counterPartyUid", "") or "").strip() in gone
    ]
    return UnheldLegs(len(legs), min((t.value_date for t in legs), default=None))


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
    return FamilyAnchors(tuple(ordered), refused, opening_evidence(store, main, families))


__all__ = [
    "OPENED",
    "Families",
    "FamilyAnchor",
    "FamilyAnchors",
    "OpeningEvidence",
    "UnheldLegs",
    "families_of",
    "family_anchors",
    "opening_evidence",
    "unheld_space_legs",
]
