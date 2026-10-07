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

BLIND DOES NOT MEAN WHOLE-ACCOUNT. That a source cannot see Spaces says what it
lists, not what its balance column adds up: it may be the main account's own
balance. `balance_meaning` decides each source's reading from its own
arithmetic; what this module returns are CANDIDATES, and only those a source
read as the whole account's become family anchors.

WHICH BALANCES, all derived on demand from the held artefacts and never stored:

  certified statement   its opening (the end of the day before its first row),
                        each printed end-of-day balance that agrees with the
                        statement's own rows, and its closing balance
  CSV export            the balance after the last row of each day that its own
                        sequence cuts cleanly (`cut_anchors`)
  aggregator            the bank's earliest opening and the closing of every day
                        its chain has one end for
                        (`AccountReconciliation.balances` states which days). A
                        day holding a folded row or a row lost from its middle
                        has a broken chain, and states nothing.

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
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import accumulate, pairwise
from urllib.parse import parse_qs, urlparse

from ..balance_reconciliation import _chain_ends
from ..core.models import Transaction
from .accounts import AccountMap
from .parsers.uk_banks import StarlingCsvParser
from .providers.starling import round_up_of
from .space_attribution import space_parents
from .spaces import LISTING_SOURCE, SPACE_COUNTERPARTY, _listed_uids, historical_spaces
from .statement_terms import statement_day_balances
from .store import Store

#: The basis (and source name) of the nil anchor at an account's creation.
OPENED = "opened"

#: The CSV export's source name, which `balance_anchors` bases its own anchors on.
CSV_SOURCE = StarlingCsvParser().source


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

    def blind_in(self, source: str, account: str) -> bool:
        """Whether `source` cannot see Spaces in `account`, which is how the matcher asks.

        `blind` says it of a main account.
        A Space has no Spaces of its own, so `blind` would call every source
        blind there, the feed that fills it included; the matcher's rule is about
        what a source can see of a main account and not about a Space's own rows.
        """
        return account not in self.parents and self.blind(source, account)


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
    #: Stated for the end of the day itself rather than after its last row.
    #: Not part of equality: it says how the figure was stated, not what it is.
    day_end: bool = field(default=False, compare=False)
    #: The instant a balance stated for a moment was fetched (`bank_balances`).
    #: Not part of equality, like `day_end`.
    at: datetime | None = field(default=None, compare=False)


@dataclass(frozen=True)
class OpeningEvidence:
    """Whether the family's history is held from its first day (`opening_evidence`)."""

    #: The creation date the provider states, when it states exactly one.
    created: date | None
    #: Nil at the end of the day before `created`; None when `missing` says why not.
    opened: FamilyAnchor | None
    missing: str
    #: Category uids that are accounted for apart from the family's Spaces
    #: (which count only once they hold rows, `walk_family`): every main
    #: account's own, and every Space each account's newest listing still
    #: names. A Space transfer naming any other category has no leg held.
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
    created: set[datetime] = set()
    defaults: set[str] = set()
    # Only each account's NEWEST listing says what is listed now: an earlier one
    # still names a Space that has since closed.
    newest_listing: dict[str, tuple[str, object]] = {}
    for row in store.connection.execute(
        "SELECT source, account_ref, fetched_at, payload FROM raw_artefacts "
        "WHERE source IN (?, 'starling-accounts') ORDER BY fetched_at, rowid",
        (LISTING_SOURCE,),
    ):
        if row["source"] == LISTING_SOURCE:
            newest_listing[str(row["account_ref"])] = (str(row["fetched_at"]), row["payload"])
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
    for _, payload in newest_listing.values():
        known |= _listed_uids(payload) or frozenset()
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
class SpaceFetch:
    """The last time the provider was asked for a closed Space's own feed."""

    #: "refused" or "empty": an answer with rows leaves nothing unheld.
    outcome: str
    on: date


@dataclass(frozen=True)
class UnheldLegs:
    """Movements to or from a Space whose own rows are not held."""

    legs: int = 0
    first: date | None = None
    #: The category uids of those Spaces.
    uids: tuple[str, ...] = ()
    #: What asking the provider for them last produced, one per Space asked.
    fetches: tuple[SpaceFetch, ...] = ()
    #: The date of every leg, earliest first.
    days: tuple[date, ...] = ()


@dataclass(frozen=True)
class RoundUpTally:
    """How the feed's round-ups stand, in counts.

    Said even when every count is nil, because a feed that carries none is the
    one thing that would show the round-up reading of the feed to be wrong.
    """

    #: Feed items that carried a round-up, of any amount.
    carried: int = 0
    #: Round-up legs held in the main account.
    legs: int = 0
    #: Of those, the legs paired with a row of a Space.
    paired: int = 0
    #: Feed items carrying a round-up that could not be read, so hold no leg.
    unreadable: int = 0


#: Feed artefact digest -> (uids of its items that carry a round-up, uids of
#: those whose round-up cannot be read). The bytes never change, so a reading
#: is good for the life of the process.
_FEED_ROUND_UPS: dict[str, tuple[frozenset[str], frozenset[str]]] = {}

_ROUND_UP_KEY = b'"roundUp"'


def _round_ups_in(payload: bytes) -> tuple[frozenset[str], frozenset[str]]:
    # Most feeds carry no round-up at all, and a search of the bytes is far
    # cheaper than decoding them to learn that.
    if _ROUND_UP_KEY not in payload:
        return frozenset(), frozenset()
    try:
        decoded = json.loads(payload)
    except ValueError:
        return frozenset(), frozenset()
    items = decoded.get("feedItems") if isinstance(decoded, dict) else None
    carried: set[str] = set()
    unreadable: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        reading = round_up_of(item)
        if not reading.carried:
            continue
        # An item with no uid of its own still counts, once per artefact.
        uid = str(item.get("feedItemUid") or f"unnamed-{len(carried)}")
        carried.add(uid)
        if reading.unreadable:
            unreadable.add(uid)
    return frozenset(carried), frozenset(unreadable)


def feed_digests(store: Store, account: str) -> list[str]:
    """The landed feed artefacts that sighted a row of `account`, each once."""
    return [
        str(row["artefact_digest"])
        for row in store.connection.execute(
            "SELECT DISTINCT s.artefact_digest FROM transaction_sources s "
            "JOIN transactions t ON t.entity_id = s.entity_id "
            "WHERE t.account_id = ? AND s.source = 'starling'",
            (account,),
        )
        if row["artefact_digest"]
    ]


def feed_payload(store: Store, digest: str) -> bytes:
    """The landed bytes of an artefact, empty when none are held."""
    row = store.connection.execute(
        "SELECT payload FROM raw_artefacts WHERE digest = ? LIMIT 1", (digest,)
    ).fetchone()
    payload = row["payload"] if row is not None else b""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return bytes(payload or b"")


def feed_round_ups(
    store: Store, account: str, digests: Sequence[str] | None = None
) -> tuple[int, int]:
    """(feed items carrying a round-up, those whose round-up cannot be read),
    among the feed artefacts that sighted the account's rows.

    Counted from the landed feeds and never from a stored row's raw:
    a row keeps the raw of whichever sighting created it, so a payment an
    export or an aggregator reported first shows no round-up of its own, and a
    count taken from rows changed with the order the sources arrived in.
    Each item is counted once however many fetches returned it.
    """
    carried: set[str] = set()
    unreadable: set[str] = set()
    for digest in feed_digests(store, account) if digests is None else digests:
        if digest not in _FEED_ROUND_UPS:
            _FEED_ROUND_UPS[digest] = _round_ups_in(feed_payload(store, digest))
        found, unread = _FEED_ROUND_UPS[digest]
        carried |= found
        unreadable |= unread
    return len(carried), len(unreadable)


def round_up_tally(
    rows: Iterable[Transaction],
    paired_entities: frozenset[str],
    feed: tuple[int, int] = (0, 0),
) -> RoundUpTally:
    """The legs held and the legs confirmed paired, beside what the feed carries.

    `feed` is `feed_round_ups`: the count of round-ups carried and unreadable
    comes from the landed feeds, and only the legs come from the rows.
    Every leg proves a payment that carried one, so `carried` is never fewer
    than the legs held.
    """
    legs = paired = 0
    for row in rows:
        if row.status.is_history or "roundUpOf" not in row.raw:
            continue
        legs += 1
        paired += row.entity_id in paired_entities
    carried, unreadable = feed
    return RoundUpTally(max(carried, legs), legs, paired, unreadable)


def space_fetches(store: Store, uids: Iterable[str]) -> tuple[SpaceFetch, ...]:
    """The newest recorded ask of each closed Space's feed that did not yield rows.

    Read from the attempt ledger rows `pull.py` writes for a closed Space
    (their `detail` begins with its mark); a Space whose last ask yielded rows
    is not here, since its rows are then held.
    """
    found: list[SpaceFetch] = []
    for uid in sorted(set(uids)):
        row = store.connection.execute(
            "SELECT attempted_at, outcome, detail FROM fetch_attempts "
            "WHERE source = 'starling-feed' AND account_ref = ? AND detail LIKE 'closed-space%' "
            "ORDER BY attempted_at DESC LIMIT 1",
            (f"starling:{uid}",),
        ).fetchone()
        if row is None:
            continue
        outcome = (
            "refused"
            if row["outcome"] == "refused"
            else "empty"
            if str(row["detail"]).startswith("closed-space: answered empty")
            else ""
        )
        when = _instant(str(row["attempted_at"]))
        if outcome and when is not None:
            found.append(SpaceFetch(outcome, when.date()))
    return tuple(found)


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
    return UnheldLegs(
        len(legs),
        min((t.value_date for t in legs), default=None),
        tuple(sorted({str(t.raw.get("counterPartyUid", "")).strip() for t in legs})),
        days=tuple(sorted(t.value_date for t in legs)),
    )


@dataclass(frozen=True)
class ExportRow:
    """One row of an export, with the balance it states before and after it."""

    day: date
    amount_minor: int
    before_minor: int
    after_minor: int


@dataclass(frozen=True)
class ExportReading:
    """What a held export states: its rows in its own sequence and the anchors cut from it."""

    source: str
    #: The export's rows in the order its balance column follows, oldest first.
    rows: tuple[ExportRow, ...]
    #: (day, balance at the END of that day), only at clean cuts (`cut_anchors`).
    anchors: tuple[tuple[date, int], ...]
    #: Rows dated earlier than a row the sequence has already passed.
    out_of_order: int = 0
    #: Days that hold a row but have no clean cut, so state no anchor.
    uncut_days: int = 0


#: Artefact digest -> the export's reading, or None when the bytes are not a
#: Starling export, state no balance, or follow no single sequence. The bytes
#: never change, so a reading is good for the life of the process.
_CSV_BY_DIGEST: dict[str, ExportReading | None] = {}


def _csv_days(store: Store, digest: str, account: str) -> ExportReading | None:
    if digest not in _CSV_BY_DIGEST:
        row = store.connection.execute(
            "SELECT payload FROM raw_artefacts "
            "WHERE digest = ? AND account_ref = ? AND media_type = 'text/csv' LIMIT 1",
            (digest, account),
        ).fetchone()
        parser = StarlingCsvParser()
        held: ExportReading | None = None
        if row is not None and parser.sniff(bytes(row["payload"])):
            try:
                figures = parser.running_balances(bytes(row["payload"]))
            except Exception as exc:
                print(
                    f"artefact {digest[:12]}: {parser.source} balances could not be read - {exc}",
                    file=sys.stderr,
                )
                figures = []
            held = _read_export(parser.source, figures) if figures else None
        _CSV_BY_DIGEST[digest] = held
    return _CSV_BY_DIGEST[digest]


def _links(ordered: Sequence[ExportRow]) -> int:
    """How many rows' balance before them is the previous row's balance after it."""
    return sum(1 for a, b in pairwise(ordered) if a.after_minor == b.before_minor)


def export_sequence(rows: Sequence[ExportRow]) -> tuple[ExportRow, ...] | None:
    """The rows oldest first in the file's own order, newest first reversed, or None.

    The file's order is the sequence its balance column follows. Which way it
    runs is the way more rows follow each other (not all: a balance that moves
    with a transfer the export does not list breaks a link, and a main-only
    export breaks many). Where the links cannot tell, the dates do, and a file
    whose dates run neither way is refused, because a guessed sequence would
    place a day's cut wrongly.
    """
    forward, backward = tuple(rows), tuple(reversed(rows))
    ahead, behind = _links(forward), _links(backward)
    if ahead != behind:
        return forward if ahead > behind else backward
    days = [r.day for r in rows]
    if days == sorted(days):
        return forward
    if days == sorted(days, reverse=True):
        return backward
    return None


def cut_anchors(
    sequence: Sequence[ExportRow],
) -> tuple[tuple[tuple[date, int], ...], int, int]:
    """The balances an export states at the end of a day, as (anchors, rows out of
    date order, days left without one).

    THE RULE, stated here and only here: an export's balance after row k is the
    sum of its first k rows, which is "every row dated on or before D" only where
    no row dated after D comes before a row dated on or before it. A day is
    therefore stated only at a CLEAN CUT of the export's own sequence, where its
    last row is followed by nothing dated on or before it; the balance is the one
    after that row. The day before the earliest row is always a clean cut, and
    states the balance before the first row of the sequence. A day with no clean
    cut states nothing, because a figure there would be the balance of rows
    nobody could name.

    A cut whose last row is dated that day is also refused where the day's own
    rows form one chain that ends elsewhere: the file lists that day's rows in
    an order its balances contradict, so which row is last is not known.

    REJECTED. Taking each day's chain end from the rows dated that day alone
    (what stood before): on an export whose sequence runs 2nd, 3rd, 2nd the
    3rd's single row closes at a figure that omits the later 2nd-dated row, so
    every balance it stated differed from the rows by that row's amount, and
    the 2nd was either dropped or opened from the wrong figure.
    """
    if not sequence:
        return (), 0, 0
    days = sorted(r.day for r in sequence)
    running = list(accumulate((r.day for r in sequence), max))
    by_day: dict[date, list[tuple[int, int]]] = defaultdict(list)
    for row in sequence:
        by_day[row.day].append((row.before_minor, row.after_minor))
    found: dict[date, int] = {}
    opening, _, _ = _chain_ends(by_day[days[0]])
    if sequence[0].day != days[0] or opening is None or opening == sequence[0].before_minor:
        found[days[0] - timedelta(days=1)] = sequence[0].before_minor
    uncut = 0
    for day in sorted(set(days)):
        count = bisect_right(days, day)
        last = sequence[count - 1]
        if running[count - 1] > day:
            uncut += 1
            continue
        if last.day == day:
            _, closing, _ = _chain_ends(by_day[day])
            if closing is not None and closing != last.after_minor:
                uncut += 1
                continue
        found[day] = last.after_minor
    out_of_order = sum(
        1 for row, passed in zip(sequence[1:], running, strict=False) if row.day < passed
    )
    return tuple(sorted(found.items())), out_of_order, uncut


def _read_export(source: str, figures: Sequence[tuple[date, int, int]]) -> ExportReading | None:
    rows = [ExportRow(day, amount, balance - amount, balance) for day, amount, balance in figures]
    sequence = export_sequence(rows)
    if sequence is None:
        return None
    anchors, out_of_order, uncut = cut_anchors(sequence)
    return ExportReading(source, sequence, anchors, out_of_order, uncut)


def _held_csv_digests(store: Store, account: str) -> list[str]:
    return [
        str(row["digest"])
        for row in store.connection.execute(
            "SELECT DISTINCT digest FROM raw_artefacts "
            "WHERE account_ref = ? AND media_type = 'text/csv'",
            (account,),
        )
    ]


def held_exports(store: Store, account: str) -> list[tuple[str, ExportReading]]:
    """Each held export of `account` that states balances, with its digest, parsed once."""
    found = []
    for digest in _held_csv_digests(store, account):
        held = _csv_days(store, digest, account)
        if held is not None:
            found.append((digest, held))
    return found


def family_anchors(store: Store, main: str, families: Families) -> FamilyAnchors:
    """The family balances `main`'s held statements and exports state, from
    sources blind to its Spaces. Empty for an account with no known Spaces."""
    if not families.spaces_of(main):
        return FamilyAnchors()
    found: set[FamilyAnchor] = set()
    statements, refused = statement_day_balances(store, main)
    for item in statements:
        if families.blind(item.source, main):
            found.add(FamilyAnchor(item.day, item.balance_minor, item.source, day_end=True))
    for digest in _held_csv_digests(store, main):
        held = _csv_days(store, digest, main)
        if held is not None and families.blind(held.source, main):
            found.update(FamilyAnchor(day, minor, held.source) for day, minor in held.anchors)
    ordered = sorted(found, key=lambda a: (a.day, a.balance_minor, a.source))
    return FamilyAnchors(tuple(ordered), refused, opening_evidence(store, main, families))


__all__ = [
    "CSV_SOURCE",
    "OPENED",
    "ExportReading",
    "ExportRow",
    "Families",
    "FamilyAnchor",
    "FamilyAnchors",
    "OpeningEvidence",
    "RoundUpTally",
    "SpaceFetch",
    "UnheldLegs",
    "cut_anchors",
    "export_sequence",
    "families_of",
    "family_anchors",
    "feed_digests",
    "feed_payload",
    "feed_round_ups",
    "held_exports",
    "opening_evidence",
    "round_up_tally",
    "space_fetches",
    "unheld_space_legs",
]
