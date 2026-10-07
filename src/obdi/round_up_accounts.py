"""Which round-ups did not become a paired leg, and why, in counts and days.

The ledger already says how many feed rows carry a round-up, how many legs are
held, and how many are paired (`family_anchors.RoundUpTally`). This module says
what became of the rest, from the landed feeds and the confirmed pairs, so a
difference that is a round-up can be told from one that is not.

THE FEED, NOT THE ROW. A carrier is read from the landed feed artefacts and
never from a stored row's raw: a row keeps the raw of whichever sighting created
it, so a payment an export reported first would show no round-up of its own and
the count would change with the order the sources arrived in
(`family_anchors.feed_round_ups` is the reason, and the same rule).

A carrier seen in several fetches, as pending and then settled, is one carrier;
what is said of it is the union of what its fetches said, so the answer cannot
depend on which fetch came first.

THREE QUESTIONS, each answered once for the whole account:

  carriers with no leg   why a feed row that carries a round-up holds none:
                         the round-up is of nothing, the item is incoming, the
                         item was reversed or declined, the round-up cannot be
                         read, or none of those
  legs with no pair      why a held round-up leg has no row in a Space: its
                         payment was reversed or dropped (a counter-example to
                         the leg's evidence would show here), its Space's rows
                         are not held, or neither
  Space legs, no main    the incoming transfer legs in a Space with no partner in
                         the main account: round-ups the main feed did not
                         report, or transfers whose main row is missing

Every field is structural (`masking`): a count, a date, or nothing else.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from .core.masking import Structural
from .core.models import Transaction
from .family_anchors import feed_digests, feed_payload
from .providers.starling import payment_unsettled, round_up_of
from .store import Store

#: How many dates are named before the rest are only counted.
NAMED_DAYS = 20

_ROUND_UP_KEY = b'"roundUp"'


@dataclass(frozen=True)
class Carrier:
    """What the feed fetches said of one item that carries a round-up."""

    #: Some fetch gave a round-up that moves money.
    moves: bool = False
    unreadable: bool = False
    #: Some fetch gave the item as money coming in.
    incoming: bool = False
    #: Some fetch gave the item as reversed or declined.
    unsettled: bool = False

    def merged(self, other: Carrier) -> Carrier:
        return Carrier(
            self.moves or other.moves,
            self.unreadable or other.unreadable,
            self.incoming or other.incoming,
            self.unsettled or other.unsettled,
        )


#: Artefact digest -> its carriers by feed item uid. The bytes never change.
_CARRIERS_BY_DIGEST: dict[str, dict[str, Carrier]] = {}


def _carriers_in(payload: bytes) -> dict[str, Carrier]:
    if _ROUND_UP_KEY not in payload:
        return {}
    try:
        decoded = json.loads(payload)
    except ValueError:
        return {}
    items = decoded.get("feedItems") if isinstance(decoded, dict) else None
    found: dict[str, Carrier] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        reading = round_up_of(item)
        if not reading.carried:
            continue
        uid = str(item.get("feedItemUid") or "")
        if not uid:
            continue
        found[uid] = Carrier(
            moves=reading.moves_money,
            unreadable=reading.unreadable,
            incoming=str(item.get("direction", "")).upper() == "IN",
            unsettled=payment_unsettled(str(item.get("status", ""))),
        )
    return found


def feed_carriers(
    store: Store, account: str, digests: Sequence[str] | None = None
) -> dict[str, Carrier]:
    """Every feed item of the account's landed feeds that carries a round-up, by uid.

    `digests` is `family_anchors.feed_digests` where the caller already has it,
    so the artefacts are listed once.
    """
    merged: dict[str, Carrier] = {}
    for digest in feed_digests(store, account) if digests is None else digests:
        if digest not in _CARRIERS_BY_DIGEST:
            _CARRIERS_BY_DIGEST[digest] = _carriers_in(feed_payload(store, digest))
        for uid, carrier in _CARRIERS_BY_DIGEST[digest].items():
            merged[uid] = merged[uid].merged(carrier) if uid in merged else carrier
    return merged


def legs_by_payment(rows: Iterable[Transaction]) -> dict[str, Transaction]:
    """The round-up legs held, by the feed uid of the payment each came from.

    A leg's raw is its own, built by `starling.to_transactions`, and only that
    source creates it, so reading it from the row is not order-dependent.
    """
    return {
        str(row.raw["roundUpOf"]): row
        for row in rows
        if not row.status.is_history and "roundUpOf" in row.raw
    }


def feed_uids_by_entity(store: Store, account: str) -> dict[str, frozenset[str]]:
    """The feed uids the bank's own feed gave each of the account's rows."""
    found: dict[str, set[str]] = defaultdict(set)
    for row in store.connection.execute(
        "SELECT s.entity_id AS entity_id, s.source_id AS source_id "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE t.account_id = ? AND s.source = 'starling' AND s.source_id IS NOT NULL",
        (account,),
    ):
        found[str(row["entity_id"])].add(str(row["source_id"]))
    return {entity: frozenset(uids) for entity, uids in found.items()}


def carrier_state(
    uids: Collection[str],
    carriers: Mapping[str, Carrier],
    legs: Mapping[str, Transaction],
    paired: Collection[str],
) -> str:
    """What became of the round-up a row carries, or "" when it carries none.

    One of "unreadable", "nothing", "no leg", "leg paired", "leg unpaired".
    """
    for uid in sorted(uids):
        carrier = carriers.get(uid)
        if carrier is None:
            continue
        if carrier.unreadable:
            return "unreadable"
        if not carrier.moves:
            return "nothing"
        leg = legs.get(uid)
        if leg is None:
            return "no leg"
        return "leg paired" if leg.entity_id in paired else "leg unpaired"
    return ""


@dataclass(frozen=True)
class RoundUpGaps:
    """The round-ups that did not become a paired leg, and the Space legs with no main side."""

    #: Carriers with no leg, and why (the five sum to `no_leg`).
    no_leg: Structural[int] = 0
    no_leg_of_nothing: Structural[int] = 0
    no_leg_incoming: Structural[int] = 0
    no_leg_reversed_or_declined: Structural[int] = 0
    no_leg_unreadable: Structural[int] = 0
    no_leg_other: Structural[int] = 0
    #: Legs held with no pair in a Space, and why (the three sum to `unpaired_legs`).
    unpaired_legs: Structural[int] = 0
    unpaired_on_reversed: Structural[int] = 0
    unpaired_to_unheld_space: Structural[int] = 0
    unpaired_other: Structural[int] = 0
    #: Their dates, earliest first, the first `NAMED_DAYS`.
    unpaired_days: Structural[tuple[date, ...]] = field(default=())
    #: Incoming transfer legs in a Space with no partner in the main account.
    space_in_unpaired: Structural[int] = 0
    space_in_unpaired_days: Structural[tuple[date, ...]] = field(default=())


def round_up_gaps(
    *,
    carriers: Mapping[str, Carrier],
    legs: Mapping[str, Transaction],
    paired: Collection[str],
    held_spaces: Collection[str],
    space_rows: Sequence[Transaction],
) -> RoundUpGaps:
    """The three answers, from the carriers, the legs held, and the confirmed pairs.

    `held_spaces` are the category uids of the Spaces whose rows are held; a leg
    to any other is to a Space whose rows are not.
    """
    nothing = incoming = unsettled = unreadable = other = 0
    for uid, carrier in carriers.items():
        if uid in legs:
            continue
        if carrier.unreadable:
            unreadable += 1
        elif not carrier.moves:
            nothing += 1
        elif carrier.incoming:
            incoming += 1
        elif carrier.unsettled:
            unsettled += 1
        else:
            other += 1
    reversed_legs = unheld = left = 0
    days: list[date] = []
    for leg in legs.values():
        if leg.entity_id in paired:
            continue
        days.append(leg.value_date)
        if payment_unsettled(str(leg.raw.get("status", ""))):
            reversed_legs += 1
        elif str(leg.raw.get("counterPartyUid", "")).strip() not in held_spaces:
            unheld += 1
        else:
            left += 1
    orphans = sorted(
        row.value_date
        for row in space_rows
        if not row.status.is_history
        and row.is_internal_transfer
        and row.amount_minor > 0
        and row.entity_id not in paired
    )
    return RoundUpGaps(
        no_leg=nothing + incoming + unsettled + unreadable + other,
        no_leg_of_nothing=nothing,
        no_leg_incoming=incoming,
        no_leg_reversed_or_declined=unsettled,
        no_leg_unreadable=unreadable,
        no_leg_other=other,
        unpaired_legs=len(days),
        unpaired_on_reversed=reversed_legs,
        unpaired_to_unheld_space=unheld,
        unpaired_other=left,
        unpaired_days=tuple(sorted(days)[:NAMED_DAYS]),
        space_in_unpaired=len(orphans),
        space_in_unpaired_days=tuple(orphans[:NAMED_DAYS]),
    )


__all__ = [
    "NAMED_DAYS",
    "Carrier",
    "RoundUpGaps",
    "carrier_state",
    "feed_carriers",
    "feed_uids_by_entity",
    "legs_by_payment",
    "round_up_gaps",
]
