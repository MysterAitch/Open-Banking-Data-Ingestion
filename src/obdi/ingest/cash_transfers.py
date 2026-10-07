"""A cash withdrawal is a transfer to the cash account, and a cash deposit a transfer back.

The money a cash machine pays out leaves the current account and arrives in the cash account, so
net worth does not change on the day and what is later spent from cash is what reduces it. A
source STATES that a payment is a cash movement (`cash_withdrawals` says which words, and what
makes a row qualify); this module makes the other side.

A STORED LEG, NOT A DERIVED ONE. The leg is a row of the cash account, of source
`namespaces.CASH_LEG_SOURCE`, so every reader that reads an account's rows (its page, Position,
the push to Actual, the movement checks, protection) sees an ordinary row without being taught
about it. The alternative was a derived row that is never stored, as the unitemised changes of a
balance-only account are. Rejected: transfer pairs are rows of two accounts keyed by entity id
(`transfer_pairs`), the movement checks and the push join them to the stored rows, and a pair
naming a row that is not stored would have to be understood by each of them, where a stored
leg is paired by the pass that pairs everything else. The cost of storing it is that the leg is
a consequence and not evidence, which is why it is DERIVED on every pass, never read back as
evidence, and removed the moment its withdrawal stops qualifying.

WHEN IT RUNS: at the head of `ingest.pair_transfers_across_store`, the pass every door that
changes the rows already ends with (a rebuild, the scheduled cycle's pairing step, a typed
transaction), so a leg is made live and by a rebuild by the same code and cannot differ. It
reads the rows that state a cash word by one indexed read and the legs already held by
another, so its cost is in proportion to the cash movements held and never to the rows.

THE LEG'S IDENTITY is derived from its withdrawal's own identity (the account it is in, its
content key, and its occurrence), which is what the withdrawal's own imported id is made of. It
is therefore the same after a rebuild, after a second source sights the withdrawal, and after a
repeated fetch. When the withdrawal's date or amount moves its content key moves, so the leg is
a new row beside the withdrawal's own new id, as the withdrawal's imported id is: Actual sees
both rows replaced and not one stranded.

THE PAIR is made here and not by the generic pairing, which would pair a leg with any other row
of its size inside a window: `CashLegs.pairs` names the withdrawal and its own leg, and the
generic pass is given neither.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..core.models import SourceTier, Transaction, TransactionStatus
from ..core.namespaces import CASH_LEG_SOURCE
from .cash_withdrawals import (
    DEPOSIT,
    LEG,
    WITHDRAWAL,
    Reading,
    choose_cash_account,
    read_candidates,
)
from .identity import entity_id_for
from .store import Store

#: The words a leg is described in, which a page shows as the row's payee. The account the money
#: moved from is named in the leg's own text so the cash account's page says where it came from.
_DESCRIPTIONS = {WITHDRAWAL: "Cash withdrawn from {account}", DEPOSIT: "Cash paid in to {account}"}


@dataclass
class CashLegs:
    """What one pass found: the legs that should exist, and what it changed to make it so."""

    #: (debit entity, credit entity) for every leg held: the withdrawal and its own leg.
    pairs: list[tuple[str, str]] = field(default_factory=list)
    #: Entities the generic pairing must not be offered: every withdrawal made a leg, and every leg.
    entities: set[str] = field(default_factory=set)
    made: int = 0
    removed: int = 0
    kept: int = 0


def leg_source_id(withdrawal: Transaction) -> str:
    """The provider-side name of a leg: where it came from, as the withdrawal's imported id is."""
    return f"{withdrawal.account_id}:{withdrawal.content_key}:{withdrawal.occurrence}"


def leg_content_key(withdrawal: Transaction) -> str:
    """A content key of the leg's own, so its imported id never equals the withdrawal's."""
    material = f"{CASH_LEG_SOURCE}\x1f{leg_source_id(withdrawal)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def leg_for(reading: Reading, cash_account: str, movement: str) -> Transaction:
    """The leg a withdrawal or deposit gets: the same size the other way, on the same dates."""
    withdrawal = reading.row
    key = leg_content_key(withdrawal)
    source_id = leg_source_id(withdrawal)
    return Transaction(
        account_id=cash_account,
        amount_minor=-withdrawal.amount_minor,
        currency=withdrawal.currency,
        value_date=withdrawal.value_date,
        booking_date=withdrawal.booking_date,
        description=_DESCRIPTIONS[movement].format(account=withdrawal.account_id),
        source=CASH_LEG_SOURCE,
        tier=SourceTier.AUTHORITATIVE,
        status=TransactionStatus.BOOKED,
        source_id=source_id,
        artefact_digest="",
        entity_id=entity_id_for(
            account_id=cash_account,
            source=CASH_LEG_SOURCE,
            source_id=source_id,
            content_key_value=key,
            occurrence=0,
            first_artefact_digest="",
        ),
        content_key=key,
        occurrence=0,
        raw={"derivedFrom": source_id, "movement": movement},
    )


def reconcile_cash_legs(store: Store) -> CashLegs:
    """Make exactly the legs the stated words call for, and remove any that no longer are.

    Nothing is made with no cash account or with several (`choose_cash_account`), and a cash
    account that has been closed takes no leg dated after it closed; the legs it already holds
    stay. Idempotent: a second pass over the same store changes nothing.
    """
    choice = choose_cash_account(store.declared_accounts())
    legs = CashLegs()
    wanted: dict[str, tuple[Transaction, Reading]] = {}
    if choice.ref is not None:
        for reading in read_candidates(store, str(choice.ref), choice.until):
            if reading.judgement.outcome != LEG:
                continue
            leg = leg_for(reading, str(choice.ref), reading.judgement.movement)
            wanted[leg.entity_id] = (leg, reading)
    held = {
        t.entity_id: t
        for t in store.transactions_with_source(CASH_LEG_SOURCE)
    }
    for entity in sorted(set(held) - set(wanted)):
        store.delete_derived_row(entity)
        legs.removed += 1
    for entity in sorted(wanted):
        leg, reading = wanted[entity]
        existing = held.get(entity)
        if existing is None or _differs(existing, leg):
            store.upsert_transaction(leg, match_tier="unresolved")
            store.record_source(leg, basis="founded")
            legs.made += 1 if existing is None else 0
        else:
            legs.kept += 1
        # The withdrawal is the debit when money left its account, the credit when it arrived.
        if reading.row.amount_minor < 0:
            legs.pairs.append((reading.row.entity_id, entity))
        else:
            legs.pairs.append((entity, reading.row.entity_id))
        legs.entities.update({reading.row.entity_id, entity})
    store.connection.commit()
    return legs


def _differs(held: Transaction, wanted: Transaction) -> bool:
    return (
        held.amount_minor != wanted.amount_minor
        or held.value_date != wanted.value_date
        or held.status is not wanted.status
        or held.description != wanted.description
    )
