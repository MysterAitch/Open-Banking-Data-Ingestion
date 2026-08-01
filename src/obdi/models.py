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

from .jsontypes import JsonObject


class SourceTier(StrEnum):
    """How much a source's own notion of transaction identity can be trusted.

    Naming the tier keeps the rules that follow from it in one place. Inferred
    per comparison instead, adjacent rules drift apart and end up contradicting
    each other.

    The same three-way split as YNAB's published import identity design, arrived
    at separately: their import id combines amount, date and an occurrence
    counter, and they match imported transactions against hand-entered ones over
    a wider date window. Convergence from two directions is worth something.
    """

    #: The source supplies a durable, stable id of its own - Monzo's
    #: transaction id, an Amex reference, a provider's uid. Within one source
    #: that id is decisive in BOTH directions: equal means one payment,
    #: different means two.
    AUTHORITATIVE = "authoritative"

    #: No id exists, so one is derived from content and an occurrence counter.
    #: Stable across re-parses of the same export, but it describes the payment
    #: rather than naming it, so two genuinely identical payments are
    #: indistinguishable except by how many times the content occurs.
    SYNTHETIC = "synthetic"

    #: Entered or adjusted by a person. The least precise and the most
    #: authoritative about intent: never merged with another manual entry,
    #: because a person meant to record two things.
    MANUAL = "manual"


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
    #: JSON: circumstances of the request that produced this payload - the
    #: trigger (scheduled / cli / post-auth-backfill / web-extend), the
    #: attended declaration made, which connection's token was used, and the
    #: app version that fetched. Provenance of HOW beside origin's WHAT: if
    #: scheduled and attended responses ever differ, this column is what
    #: makes the difference findable.
    request_meta: str = ""


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
    tier: SourceTier = SourceTier.SYNTHETIC
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
    # The provider's own record, kept verbatim for provenance. Typed as
    # unnarrowed on purpose: nothing here should read a field out of it
    # without narrowing first, and the raw layer is the place for that.
    raw: JsonObject = field(default_factory=dict)

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
