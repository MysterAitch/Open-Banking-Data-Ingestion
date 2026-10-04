"""Whether every movement a source reported is in the store, once, and paired as it said.

A balance cannot see a fault that nets to nil, and the balance walk
(`balance_anchors`, `fault_explanation`) is built on balances. Money moving
between an account and its Spaces is the case that exposed it. The owner put it
as: "100 in / 100 out / 100 in / 100 out. This scenario could collapse into just
one out and one in and the daily totals would stay the same. It could also
collapse into zero movements. While it would be effectively the same in the end,
it would be a mismatch." Two movements held as one, a pair of movements missing
altogether, and a leg paired with the wrong partner all leave every balance
agreeing. These checks look at the MOVEMENTS themselves, on every day, whether
or not any balance changed.

THREE CHECKS, each over the whole store and each reporting counts, dates,
accounts, sources, and directions - never a figure, description, or payee. A
size is used to group rows and is never shown (`masking`).

  1  every row a source lists is held once
  2  every transfer leg has exactly one partner, in the account it names
  3  the two sides of a chain agree, movement for movement

CHECK 1 compares what each artefact LISTS with what the store holds from it.
The rows an artefact lists are read through the parser the rebuild uses
(`rebuild.parse_artefact_transactions`), so a source with no provider ids (a CSV
export, a statement) is covered as well as one that has them; for a statement
that is the kept reading (`statement_readings`), never a fresh read of the PDF.
What is held is the entities sighted from those artefacts, not the entities in
the artefact's account, because a Space-blind row may have been merged onto a
row in a Space.
For each (account, source, day, direction, size) the number listed must equal
the number held.

HOW ARTEFACTS OF ONE SOURCE COMBINE. The same bytes landed again are one
artefact (a digest is listed once), and two different artefacts of one source
list the union of what they say: for each key the larger of the counts, so two
overlapping exports that each list one payment list one payment, and two
identical payments listed by both are two. A pending snapshot is set aside, as
`identity_health` does, because its ids and amounts are not the ones kept.
Where a row states an id as durable, the id decides (`_Listing`): within one artefact one
id is one row, however often it is listed, and for a source whose ids are stable for an item's
life (`matching.SETTLEMENT_KEEPS_ID`) every distinct id any artefact listed is a row, so two
payments never listed by one fetch are two. The one thing that stops showing is an item the
bank dropped and made again under another id, which `_reissue_evidence` reports.

CHECK 2 reads the pairs the pairing pass confirmed (`ingest.pair_transfers_across_store`)
and asks of every internal leg the questions that pass is meant to answer
(`matching.pair_transfer_entities`): it has one partner, in the account the leg
names, of the opposite direction and the same size, itself an internal leg, and
within the pairing window. Where a leg names no account that can be resolved,
and for a pair of ordinary rows (a current account paying a card), only what
can be known is checked and the count of what cannot be verified is said.

CHECK 3 compares the legs leaving one account for another with the legs
arriving there, per day. Two legs are the same movement when they are of one
size and the feed's own times agree within `SAME_MOVEMENT`; both sides of one
movement are stamped from one event, so a movement that crosses midnight is not
a fault, while a window of a day would let a missing movement hide behind
another of its size on the next. A leg with no time of its own is judged by the
pairing window, in days.
A leg that names no account is placed by its partner (`_chain_account`).

This module only reports, like `identity_health`: whether a finding is a fault
in the matcher or the pairing is decided from the number.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import parse_qs, urlparse

from .accounts import AccountMap, AccountRef
from .arrival_order import in_arrival_order
from .matching import INTERNAL_TRANSFER_WINDOW_DAYS, SETTLEMENT_KEEPS_ID
from .models import SourceTier, Transaction
from .store import FOLDED_SIGHTING_PREFIX, Store

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .balance_anchors import EffectiveOpening

#: How many faults a check names before it only counts the rest.
NAMED = 20

IN = "in"
OUT = "out"

#: Check 1: how a row count disagrees.
MISSING = "missing"
COLLAPSED = "collapsed"
SURPLUS = "surplus"
DATED_LATER = "dated-later"
DATED_EARLIER = "dated-earlier"
#: A first-party feed's item dropped and made again under another uid (`_reissue_evidence`).
REISSUED = "reissued"

#: Check 2: what is wrong with a leg's pairing.
NO_PARTNER = "no-partner"
SHARED_PARTNER = "shared-partner"
WRONG_DIRECTION = "wrong-direction"
WRONG_SIZE = "wrong-size"
NOT_A_LEG = "not-a-leg"
WRONG_ACCOUNT = "wrong-account"
OUTSIDE_WINDOW = "outside-window"

#: Both sides of one movement are stamped from one event by the feed, seconds
#: apart; this is the slack for that, and the reason it is not a day is in the
#: module docstring.
SAME_MOVEMENT = timedelta(minutes=5)

_PENDING_SOURCES = ("truelayer-pending",)
#: The artefact source of a first-party feed fetch, whose rows' source is `starling`.
FEED_ARTEFACT = "starling-feed"
_PDF = "application/pdf"


def _direction(minor: int) -> str:
    return OUT if minor < 0 else IN


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _listed(names: Sequence[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:-1])}, and {names[-1]}"


@dataclass(frozen=True)
class RowCountFault:
    """One (account, source, day, direction) whose listed and held counts disagree."""

    account: str
    source: str
    day: date
    direction: str
    kind: str
    #: Rows of one size and direction the source listed.
    listed: int
    #: Rows of that size and direction the store holds from it.
    held: int
    #: Of `held`, how many are history (void, folded, or reversed).
    held_as_history: int = 0
    #: Where the sightings are, from `transaction_sources` (`_explain`).
    explanation: str = ""
    #: What the artefacts and the known balances say about which count is right
    #: (`_measure_listing`, `_measure_balances`), each a clause with no figure in it.
    measured: tuple[str, ...] = ()

    def says(self) -> str:
        where = f"{self.day.isoformat()} {self.account} via {self.source} ({self.direction})"
        if self.kind == REISSUED:
            return (
                f"{where}: a row whose id the feed stopped listing when another id of the same "
                f"size and recipient appeared: possibly one payment held twice ({self.explanation})"
            )
        if self.kind in (DATED_LATER, DATED_EARLIER):
            when = "the next day" if self.kind == DATED_LATER else "the day before"
            return (
                f"{where}: {_plural(self.listed, 'row')} of one size and direction "
                f"listed, held dated {when}"
            )
        line = (
            f"{where}: {_plural(self.listed, 'row')} of one size and direction "
            f"listed, {self.held} held"
        )
        clauses = [clause for clause in (self.explanation, *self.measured) if clause]
        return f"{line}: {'; '.join(clauses)}" if clauses else line


@dataclass(frozen=True)
class RepeatedItems:
    """Items listed more than once in one artefact under one id: information, not a fault."""

    account: str
    source: str
    day: date
    items: int
    #: Whether every one was listed exactly twice.
    twice: bool

    def says(self) -> str:
        times = "twice" if self.twice else "more than once"
        one = "it is one row" if self.items == 1 else "each is one row"
        return (
            f"{self.source} listed {_plural(self.items, 'item')} {times} in one artefact on "
            f"{self.day.isoformat()}; {one}"
        )


@dataclass(frozen=True)
class LegFault:
    """One transfer leg, or one pair of ordinary rows, that is not what it should be."""

    account: str
    day: date
    direction: str
    kind: str
    #: "transfer leg", "round-up leg", or "payment pair".
    what: str
    #: The account the leg names, where it names one that can be resolved.
    names: str | None = None
    #: The account its partner is in, where it has a partner.
    partner_account: str | None = None

    def says(self) -> str:
        head = f"{self.day.isoformat()} {self.account} ({self.direction}, {self.what})"
        named = self.names or "an account that cannot be resolved"
        return {
            NO_PARTNER: f"{head}: no partner",
            SHARED_PARTNER: f"{head}: its partner is also claimed by another leg",
            WRONG_DIRECTION: f"{head}: its partner moves the same way",
            WRONG_SIZE: f"{head}: its partner is of another size",
            NOT_A_LEG: f"{head}: its partner is an ordinary payment, not a transfer leg",
            WRONG_ACCOUNT: (
                f"{head}: names {named} but its partner is in {self.partner_account}"
            ),
            OUTSIDE_WINDOW: f"{head}: its partner is further away than the pairing window",
        }[self.kind]


@dataclass(frozen=True)
class ChainFault:
    """Legs leaving one account for another and legs arriving there do not agree."""

    from_account: str
    to_account: str
    day: date
    #: Legs leaving `from_account` for `to_account` that day, and arriving in
    #: `to_account` from it.
    leaving: int
    arriving: int
    #: Of those, how many have no counterpart of their own size on the other side.
    leaving_unmatched: int
    arriving_unmatched: int
    #: What the unmatched legs are, so a line says where to look.
    round_up_legs: int = 0
    transfer_legs: int = 0
    #: Of the unmatched legs, how many the pairing pass confirmed a partner for.
    paired: int = 0
    #: The accounts those partners sit in, sorted.
    partner_accounts: tuple[str, ...] = ()

    def says(self) -> str:
        line = (
            f"{self.day.isoformat()} {self.from_account} to {self.to_account}: "
            f"{self.leaving} leave, {self.arriving} arrive"
        )
        if self.leaving == self.arriving:
            line += " - the same number, but not of the same sizes"
        explained = self._explanation()
        return f"{line}: {explained}" if explained else line

    def _explanation(self) -> str:
        total = self.round_up_legs + self.transfer_legs
        if not total:
            return ""
        kinds = [
            _plural(count, name)
            for count, name in (
                (self.round_up_legs, "round-up leg"),
                (self.transfer_legs, "transfer leg"),
            )
            if count
        ]
        text = " and ".join(kinds)
        everyone = {1: "", 2: "both "}.get(total, "all ")
        nobody = {1: "un", 2: "neither "}.get(total, "none ")
        if self.paired == total:
            text += f", {everyone}paired"
        elif not self.paired:
            text += f", {nobody}paired" if total > 1 else ", unpaired"
        else:
            text += f", {self.paired} paired"
        if self.partner_accounts:
            text += (
                f", its partner in {_listed(self.partner_accounts)}"
                if self.paired == 1
                else f", their partners in {_listed(self.partner_accounts)}"
            )
        return text


@dataclass
class MovementCompleteness:
    # Check 1.
    artefacts_listed: int = 0
    artefacts_unread: int = 0
    rows_listed: int = 0
    rows_held: int = 0
    held_as_history: int = 0
    row_faults: list[RowCountFault] = field(default_factory=list)
    #: Items a source repeated inside one artefact, which are one row each and no fault.
    repeated: list[RepeatedItems] = field(default_factory=list)
    # Check 2.
    legs: int = 0
    legs_verified: int = 0
    legs_unverifiable: int = 0
    pairs_unverifiable: int = 0
    leg_faults: list[LegFault] = field(default_factory=list)
    # Check 3.
    chain_days: int = 0
    chain_faults: list[ChainFault] = field(default_factory=list)
    #: Seconds each of the three checks took, and when the report was worked out; unset on a
    #: report built by hand.
    check_seconds: tuple[float, float, float] | None = None
    worked_out_at: datetime | None = None

    def timing_detail(self) -> str:
        """The seconds of each check, for the line a computation says on stderr."""
        if self.check_seconds is None:
            return ""
        rows, legs, chains = self.check_seconds
        return f"rows {rows:.1f} s, legs {legs:.1f} s, chains {chains:.1f} s"

    @property
    def faults(self) -> int:
        return len(self.row_faults) + len(self.leg_faults) + len(self.chain_faults)

    @property
    def accounts(self) -> tuple[str, ...]:
        found = {f.account for f in self.row_faults} | {f.account for f in self.leg_faults}
        for chain in self.chain_faults:
            found.update((chain.from_account, chain.to_account))
        return tuple(sorted(found))

    def describe(self) -> str:
        lines = ["Every row a source lists is held once:"]
        lines.append(
            f"  {_plural(self.artefacts_listed, 'artefact')} read, "
            f"{_plural(self.rows_listed, 'row')} listed, {self.rows_held} held"
        )
        if self.artefacts_unread:
            lines.append(
                f"  {_plural(self.artefacts_unread, 'artefact')} could not be read into "
                "rows (a statement with no kept reading, or a file no parser reads) "
                "and are not compared - this is not a pass for them"
            )
        if self.held_as_history:
            lines.append(
                f"  {_plural(self.held_as_history, 'listed row')} held as history "
                "(void, folded, or reversed): held, and not a fault"
            )
        lines += [f"  {item.says()}" for item in self.repeated[:NAMED]]
        if len(self.repeated) > NAMED:
            lines.append(f"  ... and {len(self.repeated) - NAMED} more repeats")
        lines += _named([f.says() for f in self.row_faults], "every listed row is held once")

        lines += ["", "Every transfer leg has exactly one partner, in the account it names:"]
        lines.append(
            f"  {_plural(self.legs, 'leg')}: {self.legs_verified} verified against the "
            f"account each names; {self.legs_unverifiable} name no account that can be "
            "resolved, so their partner's account cannot be verified"
        )
        if self.pairs_unverifiable:
            lines.append(
                f"  {_plural(self.pairs_unverifiable, 'pair')} of ordinary rows (a "
                "current account paying a card): the counterpart account cannot be "
                "verified, only that the two are opposite and equal"
            )
        lines += _named([f.says() for f in self.leg_faults], "every leg has its partner")

        lines += ["", "The two sides of a chain agree, movement for movement:"]
        lines.append(f"  {_plural(self.chain_days, 'account-pair day')} compared")
        lines += _named([f.says() for f in self.chain_faults], "both sides agree every day")
        if self.check_seconds is not None and self.worked_out_at is not None:
            moment = self.worked_out_at.astimezone(UTC).strftime("%H:%MZ")
            lines += ["", f"Worked out in {sum(self.check_seconds):.1f} s at {moment}."]
        return "\n".join(lines)


def _named(says: Sequence[str], clean: str) -> list[str]:
    if not says:
        return [f"  {clean}"]
    lines = [f"  {line}" for line in says[:NAMED]]
    if len(says) > NAMED:
        lines.append(f"  ... and {len(says) - NAMED} more")
    return lines


class _CanonicalMap(AccountMap):
    """An account map that answers from a ledger-ref translation.

    The Overview holds `canonical_for_ref` and not the map, and the rebuild's own
    helpers ask the map, so this lets both go through the one reading of a
    provider account instead of a second copy of it.
    """

    def __init__(self, canonical_for_ref: Callable[[str], str]) -> None:
        super().__init__()
        self._canonical_for_ref = canonical_for_ref

    def resolve(self, source: str, provider_account_id: str) -> AccountRef:
        return AccountRef(self._canonical_for_ref(f"{source}:{provider_account_id}"))


# ---------------------------------------------------------------------------
# Check 1
# ---------------------------------------------------------------------------

_ListKey = tuple[str, date, str, int]

#: (artefact source, digest) -> the rows that artefact lists, or None when it
#: could not be read. The bytes never change, so a listing is good for the life
#: of the process. Statements are not held here (see `_listing_of`).
_LISTED_BY_DIGEST: dict[tuple[str, str], _Listing | None] = {}


def _statement_rows(store: Store, digest: str, account: str) -> list[Transaction] | None:
    """The rows a held statement's kept reading lists, as the import would land them.

    None where nothing is kept. A reading its parser would refuse lists nothing:
    the import lands no rows from it.
    """
    from .parsers.pdf_statements import PDF_PARSERS
    from .parsers.statement_reading import reading_from_json

    stored = store.stored_statement_reading(digest)
    if stored is None:
        return None
    parser = next((p() for p in PDF_PARSERS if p().source == stored[0]), None)
    try:
        reading = reading_from_json(stored[1])
    except (ValueError, KeyError, TypeError):
        return None
    if parser is None:
        return None
    if parser.refusal_of(reading):
        return []
    return list(parser.rows_of(reading, account))


def _rows_listed(store: Store, artefact: sqlite3.Row, account: str) -> list[Transaction] | None:
    """The rows one artefact lists, as the rebuild reads them; None when unreadable."""
    from .rebuild import parse_artefact_transactions

    source, digest = str(artefact["source"]), str(artefact["digest"])
    if str(artefact["media_type"]) == _PDF:
        return _statement_rows(store, digest, account)
    found = store.connection.execute(
        "SELECT payload FROM raw_artefacts WHERE digest = ? AND account_ref = ? "
        "AND source = ? LIMIT 1",
        (digest, str(artefact["account_ref"]), source),
    ).fetchone()
    try:
        if found is not None:
            return parse_artefact_transactions(source, bytes(found["payload"]), account, digest)
    except Exception as exc:
        print(f"artefact {digest[:12]}: {source} could not be listed - {exc}", file=sys.stderr)
    return None


def _listing_of(store: Store, artefact: sqlite3.Row, account: str) -> _Listing | None:
    """What one artefact lists, by (source, day, direction, size); None when unreadable."""
    source, digest = str(artefact["source"]), str(artefact["digest"])
    if str(artefact["media_type"]) == _PDF:
        # Never memoised here: the store's kept reading is the memo, and it is
        # wiped and rewritten by a rebuild, which a process-long copy would outlive.
        return _counted(_rows_listed(store, artefact, account))
    memo_key = (source, digest)
    if memo_key not in _LISTED_BY_DIGEST:
        _LISTED_BY_DIGEST[memo_key] = _counted(_rows_listed(store, artefact, account))
    return _LISTED_BY_DIGEST[memo_key]


@dataclass(frozen=True)
class _Listing:
    """What one artefact lists, per (source, day, direction, size).

    THE RULE OF ONE ARTEFACT. Rows that state the same own id are one listed row: a source that
    names an item by an id it states as durable (`SourceTier.AUTHORITATIVE`) and lists it twice
    in one response has repeated itself, and `matching.could_be_one_payment` already reads two
    records with one id as one payment. Measured on the deployed store: "2 rows of one size and
    direction listed, 1 held ... listed by 1 artefact, stating 1 distinct id ... met by the rows
    as held" - one card item listed twice, the store right.
    Rows that state no id are each a row, and two different ids are two rows.
    """

    #: Rows that state no id (or one not stated as durable), each a row.
    anonymous: Counter[_ListKey]
    #: The distinct ids of the rows that do.
    ids: dict[_ListKey, frozenset[str]]
    #: Ids listed more than once in this artefact, with how many times.
    repeated: dict[_ListKey, dict[str, int]]

    def cells(self) -> set[_ListKey]:
        return set(self.anonymous) | set(self.ids)

    def count(self, key: _ListKey) -> int:
        return self.anonymous[key] + len(self.ids.get(key, ()))


def _counted(rows: list[Transaction] | None) -> _Listing | None:
    if rows is None:
        return None
    anonymous: Counter[_ListKey] = Counter()
    seen: dict[_ListKey, Counter[str]] = defaultdict(Counter)
    for r in rows:
        key = (r.source, r.value_date, _direction(r.amount_minor), abs(r.amount_minor))
        if r.source_id and r.tier is SourceTier.AUTHORITATIVE:
            seen[key][r.source_id] += 1
        else:
            anonymous[key] += 1
    return _Listing(
        anonymous,
        {key: frozenset(ids) for key, ids in seen.items()},
        {
            key: {sid: n for sid, n in ids.items() if n > 1}
            for key, ids in seen.items()
            if any(n > 1 for n in ids.values())
        },
    )


class _Sighting(NamedTuple):
    """One artefact's sighting of one stored row, as `transaction_sources` holds it."""

    entity_id: str
    source: str
    digest: str
    observed: date
    #: The account the count attributes the sighting to.
    account: str
    #: The account the stored row actually sits in.
    stored_account: str
    value_date: date
    status: str
    direction: str
    size: int


@dataclass
class _Held:
    held: Counter[tuple[str, str, str, int, date]] = field(default_factory=Counter)
    history: Counter[tuple[str, str, str, int, date]] = field(default_factory=Counter)
    sightings: list[_Sighting] = field(default_factory=list)
    #: How many sources sighted each stored row, which says how well a row is supported.
    support: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def _held_by_key(store: Store, digest_accounts: dict[str, set[str]]) -> _Held:
    """The sightings of listed artefacts, counted per (account, source, direction, size, day).

    An entity is counted once per source and day however many artefacts of that
    source sighted it. The account is the artefact's own where the row sits
    elsewhere (a Space-blind row merged onto a Space row), the row's where the
    artefact was filed under it.
    """
    found = _Held()
    seen: set[tuple[str, str, str, date]] = set()
    for row in store.connection.execute(
        "SELECT s.entity_id AS entity_id, s.source AS source, "
        "s.artefact_digest AS digest, s.observed_date AS observed, "
        "t.account_id AS account, t.amount_minor AS minor, t.status AS status, "
        "t.value_date AS value_date "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE s.source_id IS NULL OR s.source_id NOT LIKE ?",
        (FOLDED_SIGHTING_PREFIX + "%",),
    ):
        found.support[str(row["entity_id"])].add(str(row["source"]))
        accounts = digest_accounts.get(str(row["digest"]))
        if accounts is None:
            continue
        account = str(row["account"]) if str(row["account"]) in accounts else min(accounts)
        day = date.fromisoformat(str(row["observed"] or row["value_date"]))
        minor = int(row["minor"])
        found.sightings.append(
            _Sighting(
                str(row["entity_id"]),
                str(row["source"]),
                str(row["digest"]),
                day,
                account,
                str(row["account"]),
                date.fromisoformat(str(row["value_date"])),
                str(row["status"]),
                _direction(minor),
                abs(minor),
            )
        )
        marker = (account, str(row["source"]), str(row["entity_id"]), day)
        if marker in seen:
            continue
        seen.add(marker)
        key = (account, str(row["source"]), _direction(minor), abs(minor), day)
        found.held[key] += 1
        if str(row["status"]) in _HISTORY:
            found.history[key] += 1
    return found


_HISTORY = ("void", "folded", "reversed")


def _dates(days: Iterable[date]) -> str:
    return _listed(sorted({d.isoformat() for d in days}))


def _status_word(status: str) -> str:
    return "history" if status in _HISTORY else status


@dataclass(frozen=True)
class _Cell:
    """The (account, source, direction, size, day) a count disagrees on, with both counts."""

    account: str
    source: str
    direction: str
    size: int
    day: date
    listed: int
    held: int


def _ranked_rows(
    fault: _Cell, held: _Held, shape: list[_Sighting]
) -> tuple[dict[str, list[_Sighting]], list[str]]:
    """The stored rows a cell holds, with their sightings, best supported first.

    The first `fault.listed` are taken as the listed ones and the rest are the surplus
    (`_explain` says what that choice rests on).
    """
    here_rows: dict[str, list[_Sighting]] = defaultdict(list)
    for sighting in shape:
        if sighting.account == fault.account and sighting.observed == fault.day:
            here_rows[sighting.entity_id].append(sighting)
    ranked = sorted(
        here_rows,
        key=lambda e: (
            _status_word(here_rows[e][0].status) != "booked",
            _status_word(here_rows[e][0].status) != "pending",
            -len(held.support[e]),
            e,
        ),
    )
    return here_rows, ranked


def _explain(
    fault: _Cell,
    *,
    wanted: Counter[tuple[str, str, str, int, date]],
    held: _Held,
    by_shape: dict[tuple[str, str, int], list[_Sighting]],
    listing_digests: set[str],
) -> str:
    """Where the sightings of the rows an artefact lists are, for a fault of the counts.

    Listed more than held: the sightings of the listing's artefacts that match its rows are on
    one stored row dated D, on a stored row of another day or account, or on none.
    A sighting counts as elsewhere only when its own day holds more than is listed there, so an
    equal payment the same artefact lists on another day is not mistaken for the missing one.
    Held more than listed: the stored rows are ranked by status and by how many sources sighted
    them, the best supported are taken as the listed ones, and the rest are the surplus. Which
    of two equally supported rows is the surplus is therefore decided by that rank and not by
    the evidence, and the line names only what the surplus row's own sightings say.
    Counts, dates, account and source names only.
    """
    size = fault.size
    shape = by_shape.get((fault.source, fault.direction, size), [])
    if fault.listed > fault.held:
        mine = [s for s in shape if s.digest in listing_digests]
        here = {
            s.entity_id: s for s in mine if s.account == fault.account and s.observed == fault.day
        }
        elsewhere: dict[tuple[str | None, date], None] = {}
        for sighting in mine:
            if sighting.entity_id in here:
                continue
            cell = (sighting.account, fault.source, fault.direction, size, sighting.observed)
            if held.held[cell] > wanted.get(cell, 0):
                same = sighting.stored_account == fault.account
                elsewhere[(None if same else sighting.stored_account, sighting.observed)] = None
        if fault.listed == 1:
            subject = "the listed row is"
        elif fault.listed == 2:
            subject = "both listed rows are"
        else:
            subject = f"all {fault.listed} listed rows are"
        places = [
            f"a stored row of another account, {account}, observed {day.isoformat()}"
            if account is not None
            else f"a stored row of another day, observed {day.isoformat()}"
            for account, day in sorted(elsewhere, key=lambda k: (k[1], k[0] or ""))
        ]
        if here:
            rows = "one stored row" if len(here) == 1 else f"{len(here)} stored rows"
            placed = f"{rows}, dated {_dates(s.value_date for s in here.values())}"
            if places:
                subject = "the listed rows are"
                placed += ", and on " + " and ".join(places)
            return f"{subject} sighted on {placed}"
        if places:
            subject = "the listed row is" if fault.listed == 1 else "the listed rows are"
            return f"{subject} sighted on {' and '.join(places)}"
        return f"{subject} sighted on no stored row"

    here_rows, ranked = _ranked_rows(fault, held, shape)
    surplus = ranked[fault.listed :]
    kept_digests = {s.digest for e in ranked[: fault.listed] for s in here_rows[e]}
    others = [s for e in surplus for s in here_rows[e] if s.digest not in kept_digests]
    states = Counter(_status_word(here_rows[e][0].status) for e in surplus)
    named = " and ".join(f"{n} {word}" for word, n in sorted(states.items()))
    what = (
        f"the surplus row is {next(iter(states))}"
        if len(surplus) == 1
        else f"the {len(surplus)} surplus rows are {named}"
    )
    artefacts = len({s.digest for s in others})
    if not artefacts:
        return f"{what}, sighted by no artefact other than those that sight the listed row"
    return (
        f"{what}, sighted by {_plural(artefacts, 'other artefact')} of {fault.source} "
        f"(observed {_dates(s.observed for s in others)})"
    )


_Cell5 = tuple[str, str, str, int, date]


def _measure_listing(
    store: Store,
    cell: _Cell5,
    digests: Iterable[str],
    artefact_of: dict[tuple[str, str], sqlite3.Row],
) -> str:
    """Whether the rows a cell lists come from one artefact or several, and what ids they state.

    Measured because a line of "N listed, fewer held" cannot say whether the source listed N
    payments or one payment N times: two items of one artefact with ids of their own are two
    payments, and one id stated by two artefacts is one. Counts only, never an id.
    """
    account, source, direction, size, day = cell
    wanted = (source, day, direction, size)
    stated: list[str | None] = []
    artefacts = 0
    for digest in sorted(digests):
        artefact = artefact_of.get((digest, account))
        rows = _rows_listed(store, artefact, account) if artefact is not None else None
        mine = [
            r.source_id
            for r in rows or []
            if (r.source, r.value_date, _direction(r.amount_minor), abs(r.amount_minor)) == wanted
        ]
        if mine:
            artefacts += 1
            stated.extend(mine)
    if not stated:
        return ""
    named = [i for i in stated if i]
    if not named:
        ids = "stating no id"
    else:
        ids = f"stating {_plural(len(set(named)), 'distinct id')}"
        if len(named) < len(stated):
            ids += f" and {len(stated) - len(named)} stating none"
    return f"listed by {_plural(artefacts, 'artefact')}, {ids}"


def _asked_days(origin: str) -> tuple[date, date | None, str] | None:
    """The days a feed fetch asked for, as (first, last or None, kind), from its recorded origin.

    A `changesSince` ask runs from its stamp to now, and a bounded window names both ends
    (`providers.starling.parse_window_spec`); any other origin says nothing about its reach.
    """
    from .providers.starling import WINDOW_MAX_PARAM, WINDOW_MIN_PARAM

    query = parse_qs(urlparse(origin).query)

    def day_of(values: list[str]) -> date | None:
        try:
            return datetime.fromisoformat(values[0].replace("Z", "+00:00")).date()
        except (IndexError, ValueError):
            return None

    since = day_of(query.get("changesSince", []))
    if since is not None:
        return since, None, "changes"
    first, last = day_of(query.get(WINDOW_MIN_PARAM, [])), day_of(query.get(WINDOW_MAX_PARAM, []))
    if first is not None and last is not None:
        return first, last, "window"
    return None


def _reissue_evidence(
    cell: _Cell5,
    uids: Iterable[str],
    fetches: Sequence[sqlite3.Row],
    rows_of: Callable[[sqlite3.Row], list[Transaction] | None],
) -> str | None:
    """Whether the bank's own feed dropped an item and made it again under another uid.

    THE SIGNATURE of a re-issue, the one thing counting a first-party feed's rows by uid stops
    seeing: an item's uid is absent from the fetches after the one that last listed it, and the
    first of those fetches lists an item of the same size, direction, day, and recipient under a
    uid no earlier fetch listed.
    WHAT COUNTS AS ABSENCE. Only a fetch whose way of asking lists what EXISTS in a window of
    transaction time (`_asked_days`, kind "window"). A `changesSince` fetch lists what CHANGED
    since its stamp, so it is silent about an item that has not changed, and is treated as saying
    nothing about it either way (`pending_lifecycle` holds the same of absence there). A re-issue
    seen only by `changesSince` fetches is therefore not seen: the new uid appears, the old one is
    not asked about, and the store holds two rows, correctly as far as anything shows.
    None where the signature is not found, else the evidence as a clause of counts.
    """
    from .matching import same_payee

    _account, source, direction, size, day = cell
    wanted = (day, direction, size)
    listing = [
        [
            r
            for r in rows_of(f) or []
            if r.source == source
            and (r.value_date, _direction(r.amount_minor), abs(r.amount_minor)) == wanted
        ]
        for f in fetches
    ]
    for uid in sorted(uids):
        first = next(
            (i for i, rows in enumerate(listing) if any(r.source_id == uid for r in rows)), None
        )
        if first is None:
            continue
        old = next(r for r in listing[first] if r.source_id == uid)
        asking = [
            index
            for index in range(first + 1, len(fetches))
            if (asked := _asked_days(str(fetches[index]["origin"]))) is not None
            and asked[2] == "window"
            and asked[0] <= day
            and (asked[1] is None or day <= asked[1])
        ]
        silent = [i for i in asking if not any(r.source_id == uid for r in listing[i])]
        if not silent:
            continue
        seen_before = {r.source_id for earlier in listing[: silent[0]] for r in earlier}
        if any(
            r.source_id != uid and r.source_id not in seen_before and same_payee(old, r)
            for r in listing[silent[0]]
        ):
            return (
                f"its id is absent from {_the_fetches(len(silent), len(asking))}, "
                "asking by transaction-time window"
            )
    return None


def _the_fetches(silent: int, asked: int) -> str:
    """"all 3 later fetches that ask for its day", with the number and verb agreeing."""
    one = asked == 1
    subject = "the 1 later fetch" if one else f"all {asked} later fetches"
    if silent != asked:
        subject = f"{silent} of the {asked} later fetches"
    return f"{subject} that ask{'s' if one else ''} for its day"


def _family_accounts(store: Store, account_map: AccountMap) -> frozenset[str]:
    """Accounts that are a Space or have one, whose balances are read through the family."""
    from .space_attribution import space_parents

    parents = space_parents(store, account_map)
    return frozenset(parents) | frozenset(parents.values())


def _measure_balances(
    openings: dict[str, EffectiveOpening],
    store: Store,
    cell: _Cell5,
    *,
    kind: str,
    count: int,
    row_days: Iterable[date],
) -> str:
    """Whether the known balances either side of a fault's day are met with the rows as held.

    THE ARITHMETIC. The known balances before and after the day state a change in the account;
    the rows as held predict one; the two differ by the amount of what is missing (a row the
    store lacks) or surplus (a row it holds twice). With a missing row added the rows as held
    would differ from the balances by exactly that row, so balances met as held say the source
    listed one payment more than there was, and balances short by exactly the row say the store
    lost it. Where neither holds, something else lies between the two balances and this
    does not say which count is right.
    An account read through its family is not measured here (`_family_accounts`).
    """
    from .balance_anchors import ASSUMED_NIL, effective_opening
    from .family_anchors import OPENED

    account, _source, direction, size, day = cell
    if account not in openings:
        openings[account] = effective_opening(store, account)
    opening = openings[account]
    known = sorted(
        (r for r in opening.readings if r.anchor.basis not in (OPENED, ASSUMED_NIL)),
        key=lambda r: (r.anchor.day, r.anchor.source),
    )
    days = [day, *row_days]
    before = [r for r in known if r.anchor.day < min(days)]
    after = [r for r in known if r.anchor.day >= max(days)]
    if not before or not after:
        return "the known balances do not stand on both sides of the day, so they cannot say"
    first, last = before[-1], after[0]
    change = (last.difference_minor or 0) - (first.difference_minor or 0)
    which = "surplus" if kind == SURPLUS else "missing"
    rows = "row" if count == 1 else "rows"
    between = (
        f"the known balances of {first.anchor.day.isoformat()} and {last.anchor.day.isoformat()}"
    )
    signed = (-size if direction == OUT else size) * count
    if change == 0:
        word = "fewer" if kind == SURPLUS else "more"
        it = "it" if count == 1 else "them"
        return (
            f"{between} are met by the rows as held, and with {count} {word} {rows} of that "
            f"size and direction they would differ by exactly {it}"
        )
    if change == (-signed if kind == SURPLUS else signed):
        return f"{between} differ from the rows as held by exactly the {which} {rows}"
    return f"{between} differ from the rows as held by an amount that is not the {which} {rows}"


def check_rows(
    store: Store, canonical_for_ref: Callable[[str], str] | None
) -> MovementCompleteness:
    from .namespaces import UNASSIGNED_ACCOUNT
    from .rebuild import _READS_NO_ROWS, _starling_defaults, resolve_artefact_ref

    report = MovementCompleteness()
    account_map = _CanonicalMap(canonical_for_ref or (lambda ref: ref))
    artefacts = store.connection.execute(
        "SELECT rowid, fetched_at, source, account_ref, digest, origin, media_type "
        "FROM raw_artefacts"
    ).fetchall()
    defaults = _starling_defaults(
        store.connection.execute(
            "SELECT source, payload FROM raw_artefacts WHERE source = 'starling-accounts'"
        ).fetchall()
    )

    listed: dict[tuple[str, str], Counter[tuple[date, str, int]]] = defaultdict(Counter)
    digest_accounts: dict[str, set[str]] = defaultdict(set)
    read: set[tuple[str, str]] = set()
    artefact_of: dict[tuple[str, str], sqlite3.Row] = {}
    fetches_of: dict[str, list[sqlite3.Row]] = defaultdict(list)
    listing_digests: dict[tuple[str, str, str, int, date], set[str]] = defaultdict(set)
    stable_ids: dict[tuple[str, str, str, int, date], set[str]] = defaultdict(set)
    repeated: dict[tuple[str, str, date], dict[str, int]] = defaultdict(dict)
    for artefact in artefacts:
        source = str(artefact["source"])
        if source in _READS_NO_ROWS or source in _PENDING_SOURCES:
            continue
        account = resolve_artefact_ref(artefact, account_map, defaults)
        if account == UNASSIGNED_ACCOUNT:
            continue
        digest = str(artefact["digest"])
        if (digest, account) in read:
            continue
        read.add((digest, account))
        artefact_of[(digest, account)] = artefact
        if source == FEED_ARTEFACT:
            fetches_of[account].append(artefact)
        listing = _listing_of(store, artefact, account)
        if listing is None:
            report.artefacts_unread += 1
            continue
        report.artefacts_listed += 1
        digest_accounts[digest].add(account)
        for key in listing.cells():
            row_source, day, direction, size = key
            cell_key = (account, row_source, direction, size, day)
            union = listed[(account, row_source)]
            if row_source in SETTLEMENT_KEEPS_ID:
                # Ids stable for an item's life identify it across artefacts: what is listed is
                # every distinct id any fetch listed, never the most one fetch listed together.
                union[(day, direction, size)] = max(
                    union[(day, direction, size)], listing.anonymous[key]
                )
                stable_ids[cell_key] |= listing.ids.get(key, frozenset())
            else:
                union[(day, direction, size)] = max(
                    union[(day, direction, size)], listing.count(key)
                )
            listing_digests[cell_key].add(digest)
        for key, again in listing.repeated.items():
            seen = repeated[(account, key[0], key[1])]
            for item_id, times in again.items():
                seen[item_id] = max(seen.get(item_id, 0), times)

    sighted = _held_by_key(store, digest_accounts)
    held, history = sighted.held, sighted.history
    by_shape: dict[tuple[str, str, int], list[_Sighting]] = defaultdict(list)
    for sighting in sighted.sightings:
        by_shape[(sighting.source, sighting.direction, sighting.size)].append(sighting)

    def explained(cell: tuple[str, str, str, int, date], listed: int, kept: int) -> str:
        return _explain(
            _Cell(*cell, listed=listed, held=kept),
            wanted=wanted,
            held=sighted,
            by_shape=by_shape,
            listing_digests=listing_digests.get(cell, set()),
        )

    openings: dict[str, EffectiveOpening] = {}
    in_families: frozenset[str] | None = None

    parsed: dict[str, list[Transaction] | None] = {}

    def rows_of(fetch: sqlite3.Row) -> list[Transaction] | None:
        digest = str(fetch["digest"])
        if digest not in parsed:
            account = resolve_artefact_ref(fetch, account_map, defaults)
            parsed[digest] = _rows_listed(store, fetch, account)
        return parsed[digest]

    def measured(cell: _Cell5, kind: str, count: int) -> tuple[str, ...]:
        nonlocal in_families
        if in_families is None:
            in_families = _family_accounts(store, account_map)
        clauses: list[str] = []
        if kind != SURPLUS:
            digests = listing_digests.get(cell, ())
            clauses.append(_measure_listing(store, cell, digests, artefact_of))
        if cell[0] not in in_families:
            row_days = [
                s.value_date
                for s in by_shape.get((cell[1], cell[2], cell[3]), [])
                if s.account == cell[0] and s.observed == cell[4]
            ]
            clauses.append(
                _measure_balances(
                    openings, store, cell, kind=kind, count=count, row_days=row_days
                )
            )
        return tuple(clause for clause in clauses if clause)

    wanted: Counter[tuple[str, str, str, int, date]] = Counter()
    for (account, source), union in listed.items():
        for (day, direction, size), count in union.items():
            cell_key = (account, source, direction, size, day)
            wanted[cell_key] = count + len(stable_ids.get(cell_key, ()))
    report.repeated = [
        RepeatedItems(account, source, day, len(items), all(n == 2 for n in items.values()))
        for (account, source, day), items in sorted(repeated.items(), key=lambda kv: kv[0][2])
    ]
    report.rows_listed = sum(wanted.values())
    report.rows_held = sum(held.values())

    short: dict[tuple[str, str, str, int], dict[date, int]] = defaultdict(dict)
    extra: dict[tuple[str, str, str, int], dict[date, int]] = defaultdict(dict)
    faults: list[RowCountFault] = []
    for cell in sorted(set(wanted) | set(held), key=lambda k: (k[4], k[0], k[1], k[2], k[3])):
        account, source, direction, size, day = cell
        listed_count, held_count = wanted.get(cell, 0), held.get(cell, 0)
        if listed_count and held_count:
            report.held_as_history += min(history.get(cell, 0), listed_count)
        if listed_count > held_count:
            short[(account, source, direction, size)][day] = listed_count - held_count
        elif held_count > listed_count:
            extra[(account, source, direction, size)][day] = held_count - listed_count

    for group, days in short.items():
        account, source, direction, size = group
        for day, missing in sorted(days.items()):
            cell = (account, source, direction, size, day)
            for step, kind in ((1, DATED_LATER), (-1, DATED_EARLIER)):
                over = extra[group].get(day + timedelta(days=step), 0)
                moved = min(missing, over)
                if moved:
                    extra[group][day + timedelta(days=step)] = over - moved
                    missing -= moved
                    faults.append(
                        RowCountFault(account, source, day, direction, kind, moved, 0)
                    )
            if missing:
                held_count = held.get(cell, 0)
                faults.append(
                    RowCountFault(
                        account,
                        source,
                        day,
                        direction,
                        COLLAPSED if held_count else MISSING,
                        wanted[cell],
                        held_count,
                        history.get(cell, 0),
                        explained(cell, wanted[cell], held_count),
                        measured(cell, COLLAPSED if held_count else MISSING, missing),
                    )
                )
    for group, days in extra.items():
        account, source, direction, size = group
        for day, over in sorted(days.items()):
            if over:
                cell = (account, source, direction, size, day)
                faults.append(
                    RowCountFault(
                        account,
                        source,
                        day,
                        direction,
                        SURPLUS,
                        wanted.get(cell, 0),
                        held[cell],
                        history.get(cell, 0),
                        explained(cell, wanted.get(cell, 0), held[cell]),
                        measured(cell, SURPLUS, over),
                    )
                )
    for cell, uids in stable_ids.items():
        if len(uids) < 2:
            continue
        evidence = _reissue_evidence(cell, uids, in_arrival_order(fetches_of[cell[0]]), rows_of)
        if evidence is not None:
            account, source, direction, _size, day = cell
            faults.append(
                RowCountFault(
                    account,
                    source,
                    day,
                    direction,
                    REISSUED,
                    wanted[cell],
                    held.get(cell, 0),
                    history.get(cell, 0),
                    evidence,
                )
            )
    report.row_faults = sorted(faults, key=lambda f: (f.day, f.account, f.source, f.direction))
    return report


# ---------------------------------------------------------------------------
# Check 2
# ---------------------------------------------------------------------------


def _is_leg(row: Transaction) -> bool:
    return row.is_internal_transfer and not row.status.is_history


def _named_account(
    row: Transaction, resolver: Callable[[str], str | None] | None
) -> str | None:
    named = row.raw.get("counterPartyUid")
    if resolver is None or not isinstance(named, str) or not named:
        return None
    return resolver(named)


def check_legs(
    rows: Iterable[Transaction],
    pairs: Sequence[tuple[str, str]],
    resolver: Callable[[str], str | None] | None,
) -> tuple[int, int, int, int, list[LegFault]]:
    """(legs, verified, unverifiable legs, unverifiable pairs, faults) from rows and pairs.

    The window is the pairing pass's own (`INTERNAL_TRANSFER_WINDOW_DAYS`).
    """
    by_entity = {row.entity_id: row for row in rows}
    partners: dict[str, list[str]] = defaultdict(list)
    for debit, credit in pairs:
        partners[debit].append(credit)
        partners[credit].append(debit)
    claimed = Counter(entity for pair in pairs for entity in pair)
    window = timedelta(days=INTERNAL_TRANSFER_WINDOW_DAYS)

    faults: list[LegFault] = []
    legs = verified = unverifiable = 0
    for row in by_entity.values():
        if not _is_leg(row):
            continue
        legs += 1
        names = _named_account(row, resolver)
        if names is None:
            unverifiable += 1
        what = "round-up leg" if "roundUpOf" in row.raw else "transfer leg"
        direction = _direction(row.amount_minor)

        def fault(
            kind: str,
            partner: Transaction | None = None,
            *,
            row: Transaction = row,
            names: str | None = names,
            what: str = what,
            direction: str = direction,
        ) -> LegFault:
            return LegFault(
                row.account_id,
                row.value_date,
                direction,
                kind,
                what,
                names,
                None if partner is None else partner.account_id,
            )

        found = [by_entity[p] for p in partners.get(row.entity_id, []) if p in by_entity]
        if not found:
            faults.append(fault(NO_PARTNER))
            continue
        partner = found[0]
        bad: list[LegFault] = []
        if len(found) > 1 or any(claimed[p.entity_id] > 1 for p in found):
            bad.append(fault(SHARED_PARTNER, partner))
        if _direction(partner.amount_minor) == direction:
            bad.append(fault(WRONG_DIRECTION, partner))
        elif abs(partner.amount_minor) != abs(row.amount_minor):
            bad.append(fault(WRONG_SIZE, partner))
        if not _is_leg(partner):
            bad.append(fault(NOT_A_LEG, partner))
        if names is not None and partner.account_id != names:
            bad.append(fault(WRONG_ACCOUNT, partner))
        if abs(partner.value_date - row.value_date) > window:
            bad.append(fault(OUTSIDE_WINDOW, partner))
        faults.extend(bad)
        if not bad and names is not None:
            verified += 1

    unverifiable_pairs = 0
    for debit_id, credit_id in pairs:
        paid, received = by_entity.get(debit_id), by_entity.get(credit_id)
        if paid is None or received is None or _is_leg(paid) or _is_leg(received):
            continue
        unverifiable_pairs += 1
        kind = None
        if _direction(paid.amount_minor) == _direction(received.amount_minor):
            kind = WRONG_DIRECTION
        elif abs(paid.amount_minor) != abs(received.amount_minor):
            kind = WRONG_SIZE
        if kind is not None:
            faults.append(
                LegFault(
                    paid.account_id,
                    paid.value_date,
                    _direction(paid.amount_minor),
                    kind,
                    "payment pair",
                    None,
                    received.account_id,
                )
            )
    faults.sort(key=lambda f: (f.day, f.account, f.kind))
    return legs, verified, unverifiable, unverifiable_pairs, faults


# ---------------------------------------------------------------------------
# Check 3
# ---------------------------------------------------------------------------


def _instant(row: Transaction) -> datetime | None:
    text = row.raw.get("transactionTime") or row.raw.get("settlementTime")
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _stamp(row: Transaction) -> datetime:
    return _instant(row) or datetime.combine(row.value_date, time(), tzinfo=UTC)


def _same_movement(left: Transaction, right: Transaction) -> bool:
    first, second = _instant(left), _instant(right)
    if first is not None and second is not None:
        return abs(first - second) <= SAME_MOVEMENT
    return abs(left.value_date - right.value_date) <= timedelta(days=INTERNAL_TRANSFER_WINDOW_DAYS)


def _unmatched(
    leaving: list[Transaction], arriving: list[Transaction]
) -> tuple[list[Transaction], list[Transaction]]:
    """The legs of each side with no leg of their own size on the other, in time order."""
    left_over: list[Transaction] = []
    right_over: list[Transaction] = []
    sizes = {abs(r.amount_minor) for r in leaving} | {abs(r.amount_minor) for r in arriving}
    for size in sizes:
        outs = sorted((r for r in leaving if abs(r.amount_minor) == size), key=_stamp)
        ins = sorted((r for r in arriving if abs(r.amount_minor) == size), key=_stamp)
        taken = [False] * len(ins)
        for out in outs:
            for index, candidate in enumerate(ins):
                if not taken[index] and _same_movement(out, candidate):
                    taken[index] = True
                    break
            else:
                left_over.append(out)
        right_over.extend(c for index, c in enumerate(ins) if not taken[index])
    return left_over, right_over


def _partners(
    rows: Sequence[Transaction], pairs: Sequence[tuple[str, str]]
) -> dict[str, list[Transaction]]:
    by_entity = {row.entity_id: row for row in rows}
    found: dict[str, list[Transaction]] = defaultdict(list)
    for debit, credit in pairs:
        if debit in by_entity and credit in by_entity:
            found[debit].append(by_entity[credit])
            found[credit].append(by_entity[debit])
    return found


def _paired_across(
    out_legs: Sequence[Transaction],
    in_legs: Sequence[Transaction],
    partners: dict[str, list[Transaction]],
) -> set[str]:
    """The legs of one flow that are each other's ONE confirmed partner.

    Such a pair is one movement whatever the two sides' stamps say, and is not put to the
    size-and-time comparison (`_unmatched`), which is for legs the pairing pass left alone.
    Measured on the deployed store: a round-up's leaving leg is stamped when the payment was
    made and its arrival in the Space when the round-up was swept, more than `SAME_MOVEMENT`
    later, and three such pairs read "the same number, but not of the same sizes" though each
    was paired with the other, which held the account's agreement at the first of them.
    That every leg has exactly one partner, of its own size, is the leg check's (`check_legs`).
    """
    leaving = {r.entity_id for r in out_legs}
    arriving = {r.entity_id for r in in_legs}
    joined: set[str] = set()
    for row in out_legs:
        found = partners.get(row.entity_id, [])
        if len(found) != 1 or found[0].entity_id not in arriving:
            continue
        back = partners.get(found[0].entity_id, [])
        if len(back) == 1 and back[0].entity_id in leaving:
            joined.update((row.entity_id, found[0].entity_id))
    return joined


def _chain_account(
    row: Transaction,
    resolver: Callable[[str], str | None] | None,
    partners: dict[str, list[Transaction]],
) -> str | None:
    """The account a leg moved to or from, for the chain check.

    A leg names its own counterpart where it can.
    A leg that names no account that resolves is still the other side of a movement when its ONE
    paired partner is a leg that names the very account this one sits in: the Space's arriving
    item of a round-up names nothing that resolves to the main account, while the main account's
    leaving leg names the Space.
    Measured on the deployed store: 612 legs named no resolvable account, every leg had its
    partner, and 231 account-pair days read "1 leave, 0 arrive" because the arrival was never
    counted.
    A leg whose partner sits in another account than the one the partner names is left uncounted
    here, and it is a fault of the leg check (`check_legs`).
    """
    named = _named_account(row, resolver)
    if named is not None:
        return named
    found = partners.get(row.entity_id, [])
    if len(found) != 1:
        return None
    partner = found[0]
    if _is_leg(partner) and _named_account(partner, resolver) == row.account_id:
        return partner.account_id
    return None


def check_chains(
    rows: Iterable[Transaction],
    resolver: Callable[[str], str | None] | None,
    pairs: Sequence[tuple[str, str]] = (),
) -> tuple[int, list[ChainFault]]:
    """(account-pair days compared, faults) for the legs that name an account.

    `pairs` are the confirmed transfer pairs (`ingest.pair_transfers_across_store`); with them,
    a leg that names no account is placed by its partner (`_chain_account`) and an unmatched leg
    says whether it is paired and where its partner sits.
    """
    held = list(rows)
    partners = _partners(held, pairs)
    leaving: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    arriving: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for row in held:
        if not _is_leg(row):
            continue
        named = _chain_account(row, resolver, partners)
        if named is None or named == row.account_id:
            continue
        if row.amount_minor < 0:
            leaving[(row.account_id, named)].append(row)
        else:
            arriving[(named, row.account_id)].append(row)

    compared = 0
    faults: list[ChainFault] = []
    for flow in sorted(set(leaving) | set(arriving)):
        out_legs, in_legs = leaving.get(flow, []), arriving.get(flow, [])
        compared += len({r.value_date for r in (*out_legs, *in_legs)})
        joined = _paired_across(out_legs, in_legs, partners)
        out_over, in_over = _unmatched(
            [r for r in out_legs if r.entity_id not in joined],
            [r for r in in_legs if r.entity_id not in joined],
        )
        for day in sorted({r.value_date for r in (*out_over, *in_over)}):
            left = [r for r in out_over if r.value_date == day]
            right = [r for r in in_over if r.value_date == day]
            unmatched = [*left, *right]
            partner_rows = [p for r in unmatched for p in partners.get(r.entity_id, [])]
            faults.append(
                ChainFault(
                    flow[0],
                    flow[1],
                    day,
                    sum(1 for r in out_legs if r.value_date == day),
                    sum(1 for r in in_legs if r.value_date == day),
                    len(left),
                    len(right),
                    round_up_legs=sum(1 for r in unmatched if "roundUpOf" in r.raw),
                    transfer_legs=sum(1 for r in unmatched if "roundUpOf" not in r.raw),
                    paired=sum(1 for r in unmatched if partners.get(r.entity_id)),
                    partner_accounts=tuple(sorted({p.account_id for p in partner_rows})),
                )
            )
    faults.sort(key=lambda f: (f.day, f.from_account, f.to_account))
    return compared, faults


def movement_completeness(
    store: Store,
    canonical_for_ref: Callable[[str], str] | None = None,
    *,
    clock: Callable[[], float] = perf_counter,
    stamp: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MovementCompleteness:
    """The three checks over the whole store.

    `canonical_for_ref` turns a ledger `source:provider-id` ref into the
    canonical account it lands under (`cli._canonical_for_ref`); without it no
    provider account is bound, so no artefact is attributed to an account and no
    leg names one that can be resolved.
    `clock` and `stamp` time the checks and say when the report was worked out.
    """
    from .space_attribution import category_resolver

    began = clock()
    report = check_rows(store, canonical_for_ref)
    after_rows = clock()
    rows = store.all_transactions()
    resolver = (
        category_resolver(store, _CanonicalMap(canonical_for_ref)) if canonical_for_ref else None
    )
    pairs = store.confirmed_transfer_pairs()
    legs, verified, unverifiable, unverifiable_pairs, leg_faults = check_legs(
        rows, pairs, resolver
    )
    report.legs, report.legs_verified, report.legs_unverifiable = legs, verified, unverifiable
    report.pairs_unverifiable, report.leg_faults = unverifiable_pairs, leg_faults
    after_legs = clock()
    report.chain_days, report.chain_faults = check_chains(rows, resolver, pairs)
    ended = clock()
    report.check_seconds = (after_rows - began, after_legs - after_rows, ended - after_legs)
    report.worked_out_at = stamp()
    return report
