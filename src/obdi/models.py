"""Records the store holds.

Two kinds, deliberately kept apart:

  Transaction  a movement of money, observed as an event in a stream.
  Valuation    a value observed at a point in time (pension pot, fund,
               property). The delta between two valuations mixes
               contributions, growth and fees and usually cannot be
               decomposed from the statement.

Forcing a valuation into a transaction destroys information: units, unit price,
provenance and the contributions/growth split all vanish, leaving only a delta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class TransactionStatus(StrEnum):
    PENDING = "pending"
    BOOKED = "booked"
    REVERSED = "reversed"


class MatchTier(StrEnum):
    """How a transaction was linked to an existing record.

    Recorded on every link so a wrong match can be found and reversed later.
    """

    SOURCE_ID = "source_id"
    CONTENT_KEY = "content_key"
    FUZZY = "fuzzy"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class RawArtefact:
    """An immutable landed payload. The canonical copy; everything else derives."""

    source: str
    account_ref: str
    fetched_at: datetime
    media_type: str
    digest: str
    payload: bytes
    origin: str = ""


@dataclass(frozen=True)
class Transaction:
    """A normalised money movement.

    `booking_date` is when the bank posted it; `value_date` is when it counted.
    They differ, and the difference is what shifts when a pending transaction
    settles - which is why `value_date` alone feeds the content key.
    """

    account_id: str
    amount_minor: int
    value_date: date
    booking_date: date
    description: str
    source: str
    currency: str = "GBP"
    status: TransactionStatus = TransactionStatus.BOOKED
    source_id: str | None = None
    artefact_digest: str = ""
    entity_id: str = ""
    content_key: str = ""
    # Which occurrence of this content this is within its batch, counting from
    # zero. Two identical purchases and one payment appearing in two
    # overlapping exports are indistinguishable by content alone; they differ
    # only in how MANY times the content occurs. Matching the nth occurrence
    # against the nth settles both cases, and is stable across re-parses.
    occurrence: int = 0
    is_internal_transfer: bool = False
    counterparty: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_credit(self) -> bool:
        return self.amount_minor > 0


@dataclass(frozen=True)
class Valuation:
    """A point-in-time observation of an asset's value.

    `units` and `unit_price_minor` are captured whenever the statement gives
    them, even though nothing consumes them yet. Storing them is what preserves
    the option of proper unit-and-price modelling later; a bare total forecloses
    it permanently.
    """

    asset_id: str
    observed_at: date
    value_minor: int
    source: str
    currency: str = "GBP"
    units: str | None = None
    unit_price_minor: int | None = None
    document_ref: str = ""
    ingested_at: datetime | None = None
