"""Which day a row counts on when a SOURCE's stated balance is the test.

A merged row carries one date, the date of whichever sighting wrote it last
(in practice the bank's feed, by transaction time), but every sighting records
the date ITS OWN source gave the payment, and an export or an aggregator dates
the same payment a day or two either side: settlement against transaction
time, local against UTC. A day-end balance a source states is the sum of the
rows as that source dated them, so judging it against rows dated by another
source makes every payment in flight across a day end a false difference, of an
amount that changes from one balance to the next and whose sign flips.

THE RULE, stated here and only here: a balance a source states is judged with
each row placed on the day THAT source gave it where that source sighted the
row, and on the row's stored date where it did not. A row the source does not
sight then keeps its own date, so a row the source lists that the store does
not count, or one the store counts that the source does not list, still shows
as the difference it is.

Anchors from a held statement are placed by which statement lists a row
(`statement_membership`), and a stated or bank anchor has no source: both go by
stored date. Everything else that names a source takes this placement:
`balance_anchors.derive_opening` for the main account's own anchors and
`balance_anchors.walk_family` for the family's, so the anchor list and the walk
cannot give different answers for one balance.

REJECTED. Re-dating the stored row to the stating source's date: a row has one
date for the ledger and Actual, and two sources would then fight over it. Reading
the sighting date in SQL per anchor: that is a query per anchor, where one read
of the family's sightings serves every anchor.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date

from .core.models import Transaction
from .store import Store


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class SightingPlacement:
    """Source -> entity id -> the day that source gave the row."""

    days: Mapping[str, Mapping[str, date]] = field(default_factory=dict)

    def places(self, source: str) -> bool:
        """Whether `source` dates any row, so its balances take this placement."""
        return bool(source) and bool(self.days.get(source))

    def day(self, source: str, row: Transaction) -> date:
        return self.days.get(source, {}).get(row.entity_id, row.value_date)


def sighting_placement(
    store: Store, accounts: Collection[str], sources: Collection[str]
) -> SightingPlacement:
    """The days `sources` gave the rows of `accounts`, in one read."""
    if not sources:
        return SightingPlacement()
    found = store.sighting_days(accounts, sources)
    return SightingPlacement(
        {
            source: {
                entity: day for entity, text in dated.items() if (day := _day(text)) is not None
            }
            for source, dated in found.items()
        }
    )
