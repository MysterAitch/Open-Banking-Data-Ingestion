"""Which accounts' transactions state their other party, read through the names the ladder gives.

The question - is this row named by something a source stated, or by the printed description
alone? - is answered by `entities.name_of` and nobody else: a row is DESCRIBED exactly when its
name's kind is the bottom rung, so this cannot disagree with the Entities page's "from the
description" count. What a stated party IS is `read.party_coverage`'s to say.

The rows are the ones the Entities page names (booked, not history), read as three columns in one
statement for the whole store, because the links that name a described row from a stated one are
learned across accounts (a payment seen by a feed in one account's month and a statement in
another's). The result is the same for every account and is held by the caller while the
transactions are unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..ingest.store import Store
from ..read.coverage_timeline import AGGREGATOR, EXPORT, FEED, kind_of_source
from ..read.party_coverage import PartyStated, party_stated
from .entities import DESCRIPTION, learned_links, name_of

#: The ways in that can carry a stated party where a statement reader states none.
_CAN_STATE = frozenset({FEED, AGGREGATOR, EXPORT})


def party_stated_by_account(store: Store) -> dict[str, PartyStated]:
    """Every account's `PartyStated`, from one read of the booked transactions."""
    rows = store.connection.execute(
        "SELECT account_id, value_date, description, counterparty FROM transactions "
        "WHERE status = 'booked'"
    ).fetchall()
    links = learned_links((str(r["description"]), str(r["counterparty"])) for r in rows)
    held: dict[str, list[tuple[date, bool]]] = defaultdict(list)
    for r in rows:
        named = name_of(str(r["description"]), str(r["counterparty"]), links)
        held[str(r["account_id"])].append(
            (date.fromisoformat(str(r["value_date"])), named.kind != DESCRIPTION)
        )
    ways: dict[str, set[str]] = defaultdict(set)
    for r in store.connection.execute(
        "SELECT DISTINCT t.account_id AS account, s.source AS source "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id"
    ):
        ways[str(r["account"])].add(kind_of_source(str(r["source"])))
    return {
        account: party_stated(found, askable=bool(ways.get(account, set()) & _CAN_STATE))
        for account, found in held.items()
    }
