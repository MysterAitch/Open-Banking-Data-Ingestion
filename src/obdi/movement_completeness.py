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

from .accounts import AccountMap, AccountRef
from .matching import INTERNAL_TRANSFER_WINDOW_DAYS
from .models import Transaction
from .store import FOLDED_SIGHTING_PREFIX, Store

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
_PDF = "application/pdf"


def _direction(minor: int) -> str:
    return OUT if minor < 0 else IN


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


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

    def says(self) -> str:
        where = f"{self.day.isoformat()} {self.account} via {self.source} ({self.direction})"
        if self.kind in (DATED_LATER, DATED_EARLIER):
            when = "the next day" if self.kind == DATED_LATER else "the day before"
            return (
                f"{where}: {_plural(self.listed, 'row')} of one size and direction "
                f"listed, held dated {when}"
            )
        return (
            f"{where}: {_plural(self.listed, 'row')} of one size and direction "
            f"listed, {self.held} held"
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

    def says(self) -> str:
        line = (
            f"{self.day.isoformat()} {self.from_account} to {self.to_account}: "
            f"{self.leaving} leave, {self.arriving} arrive"
        )
        if self.leaving == self.arriving:
            line += " - the same number, but not of the same sizes"
        return line


@dataclass
class MovementCompleteness:
    # Check 1.
    artefacts_listed: int = 0
    artefacts_unread: int = 0
    rows_listed: int = 0
    rows_held: int = 0
    held_as_history: int = 0
    row_faults: list[RowCountFault] = field(default_factory=list)
    # Check 2.
    legs: int = 0
    legs_verified: int = 0
    legs_unverifiable: int = 0
    pairs_unverifiable: int = 0
    leg_faults: list[LegFault] = field(default_factory=list)
    # Check 3.
    chain_days: int = 0
    chain_faults: list[ChainFault] = field(default_factory=list)

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
#: of the process; an unreadable statement is not kept, because a reading may be
#: kept for it later.
_LISTED_BY_DIGEST: dict[tuple[str, str], Counter[_ListKey] | None] = {}


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


def _listing_of(
    store: Store, artefact: sqlite3.Row, account: str
) -> Counter[_ListKey] | None:
    """What one artefact lists, by (source, day, direction, size); None when unreadable."""
    from .rebuild import parse_artefact_transactions

    source, digest = str(artefact["source"]), str(artefact["digest"])
    memo_key = (source, digest)
    if memo_key in _LISTED_BY_DIGEST:
        return _LISTED_BY_DIGEST[memo_key]
    media = str(artefact["media_type"])
    rows: list[Transaction] | None
    if media == _PDF:
        rows = _statement_rows(store, digest, account)
    else:
        found = store.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE digest = ? AND account_ref = ? "
            "AND source = ? LIMIT 1",
            (digest, str(artefact["account_ref"]), source),
        ).fetchone()
        try:
            rows = (
                None
                if found is None
                else parse_artefact_transactions(source, bytes(found["payload"]), account, digest)
            )
        except Exception as exc:
            print(f"artefact {digest[:12]}: {source} could not be listed - {exc}", file=sys.stderr)
            rows = None
    if rows is None:
        if media != _PDF:
            _LISTED_BY_DIGEST[memo_key] = None
        return None
    listing: Counter[_ListKey] = Counter(
        (r.source, r.value_date, _direction(r.amount_minor), abs(r.amount_minor)) for r in rows
    )
    _LISTED_BY_DIGEST[memo_key] = listing
    return listing


def _held_by_key(
    store: Store, digest_accounts: dict[str, set[str]]
) -> tuple[Counter[tuple[str, str, str, int, date]], Counter[tuple[str, str, str, int, date]]]:
    """(held, held as history) per (account, source, direction, size, day).

    An entity is counted once per source and day however many artefacts of that
    source sighted it. The account is the artefact's own where the row sits
    elsewhere (a Space-blind row merged onto a Space row), the row's where the
    artefact was filed under it.
    """
    held: Counter[tuple[str, str, str, int, date]] = Counter()
    history: Counter[tuple[str, str, str, int, date]] = Counter()
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
        accounts = digest_accounts.get(str(row["digest"]))
        if accounts is None:
            continue
        account = str(row["account"]) if str(row["account"]) in accounts else min(accounts)
        day = date.fromisoformat(str(row["observed"] or row["value_date"]))
        marker = (account, str(row["source"]), str(row["entity_id"]), day)
        if marker in seen:
            continue
        seen.add(marker)
        minor = int(row["minor"])
        key = (account, str(row["source"]), _direction(minor), abs(minor), day)
        held[key] += 1
        if str(row["status"]) in ("void", "folded", "reversed"):
            history[key] += 1
    return held, history


def check_rows(
    store: Store, canonical_for_ref: Callable[[str], str] | None
) -> MovementCompleteness:
    from .namespaces import UNASSIGNED_ACCOUNT
    from .rebuild import _READS_NO_ROWS, _starling_defaults, resolve_artefact_ref

    report = MovementCompleteness()
    account_map = _CanonicalMap(canonical_for_ref or (lambda ref: ref))
    artefacts = store.connection.execute(
        "SELECT source, account_ref, digest, origin, media_type FROM raw_artefacts"
    ).fetchall()
    defaults = _starling_defaults(
        store.connection.execute(
            "SELECT source, payload FROM raw_artefacts WHERE source = 'starling-accounts'"
        ).fetchall()
    )

    listed: dict[tuple[str, str], Counter[tuple[date, str, int]]] = defaultdict(Counter)
    digest_accounts: dict[str, set[str]] = defaultdict(set)
    read: set[tuple[str, str]] = set()
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
        listing = _listing_of(store, artefact, account)
        if listing is None:
            report.artefacts_unread += 1
            continue
        report.artefacts_listed += 1
        digest_accounts[digest].add(account)
        for (row_source, day, direction, size), count in listing.items():
            union = listed[(account, row_source)]
            union[(day, direction, size)] = max(union[(day, direction, size)], count)

    held, history = _held_by_key(store, digest_accounts)
    wanted: Counter[tuple[str, str, str, int, date]] = Counter()
    for (account, source), union in listed.items():
        for (day, direction, size), count in union.items():
            wanted[(account, source, direction, size, day)] = count
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


def check_chains(
    rows: Iterable[Transaction], resolver: Callable[[str], str | None] | None
) -> tuple[int, list[ChainFault]]:
    """(account-pair days compared, faults) for the legs that name an account."""
    leaving: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    arriving: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for row in rows:
        if not _is_leg(row):
            continue
        named = _named_account(row, resolver)
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
        out_over, in_over = _unmatched(out_legs, in_legs)
        for day in sorted({r.value_date for r in (*out_over, *in_over)}):
            faults.append(
                ChainFault(
                    flow[0],
                    flow[1],
                    day,
                    sum(1 for r in out_legs if r.value_date == day),
                    sum(1 for r in in_legs if r.value_date == day),
                    sum(1 for r in out_over if r.value_date == day),
                    sum(1 for r in in_over if r.value_date == day),
                )
            )
    faults.sort(key=lambda f: (f.day, f.from_account, f.to_account))
    return compared, faults


def movement_completeness(
    store: Store, canonical_for_ref: Callable[[str], str] | None = None
) -> MovementCompleteness:
    """The three checks over the whole store.

    `canonical_for_ref` turns a ledger `source:provider-id` ref into the
    canonical account it lands under (`cli._canonical_for_ref`); without it no
    provider account is bound, so no artefact is attributed to an account and no
    leg names one that can be resolved.
    """
    from .space_attribution import category_resolver

    report = check_rows(store, canonical_for_ref)
    rows = store.all_transactions()
    resolver = (
        category_resolver(store, _CanonicalMap(canonical_for_ref)) if canonical_for_ref else None
    )
    legs, verified, unverifiable, unverifiable_pairs, leg_faults = check_legs(
        rows, store.confirmed_transfer_pairs(), resolver
    )
    report.legs, report.legs_verified, report.legs_unverifiable = legs, verified, unverifiable
    report.pairs_unverifiable, report.leg_faults = unverifiable_pairs, leg_faults
    report.chain_days, report.chain_faults = check_chains(rows, resolver)
    return report
