"""Assets that are observed rather than transacted.

A current account is a ledger: the balance is what the movements sum to. A
pension pot, a fund or a property is the opposite - the value is what you
observe, and the movements behind it are hidden. The change between two
observations mixes contributions, growth and fees, and a statement usually
cannot decompose it.

Flattening the second kind into the first destroys information. A plug
transaction can express a delta and nothing else: no units, no unit price, no
provenance, no split between contribution and growth. So valuations are their
own record type, and their own table.

Defined benefit is different again, and the difference matters. There is no pot
at all - only a promise of future income - and no agreed way to capitalise one.
The UK alone uses at least four incompatible conventions, each right for its
own purpose. So the promise is stored as the fact a statement actually gives,
and any capital figure is derived from a multiplier held as configuration,
labelled derived, and re-runnable when the question changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from .errors import DataError
from .store import Store


class ValuationError(DataError):
    """An observation could not be recorded as described."""


class AssetKind(StrEnum):
    #: A pot with a value, usually unitised. Models properly.
    DEFINED_CONTRIBUTION = "defined_contribution"

    #: A promise of income with no pot behind it. Capitalising is a modelling
    #: choice, not an observation.
    DEFINED_BENEFIT = "defined_benefit"

    #: A forecast, not an entitlement. Excluded from wealth entirely and
    #: tracked as income, on the same reasoning wealth surveys use: without a
    #: contractual right it is a social benefit rather than pension wealth.
    STATE_PENSION = "state_pension"

    INVESTMENT = "investment"
    PROPERTY = "property"
    OTHER = "other"


#: Kinds that have no pot, so a pot value cannot be observed for them.
_INCOME_ONLY = {AssetKind.DEFINED_BENEFIT, AssetKind.STATE_PENSION}


@dataclass(frozen=True)
class Asset:
    """Something whose value is observed rather than derived from movements."""

    asset_id: str
    kind: AssetKind
    label: str = ""

    #: How many years of income a defined benefit entitlement is treated as
    #: worth. Held as data because no convention is agreed: HMRC's annual
    #: allowance test uses 16, the abolished lifetime allowance used 20, a
    #: scheme's own transfer value is lower again and actuaries warn it
    #: understates member benefits, and the statistics agencies discount future
    #: cash flows instead. The right answer depends on the question, so the
    #: figure must be re-runnable rather than baked in.
    capitalisation_multiplier: int | None = None


def capital_value_of(asset: Asset, *, annual_income_minor: int) -> int:
    """A notional capital value for an income entitlement.

    Always derived, never stored as though observed. Two guards, because both
    mistakes are easy and both produce a confident wrong number.
    """
    if asset.kind is AssetKind.STATE_PENSION:
        raise ValuationError(
            f"{asset.asset_id} is a state pension forecast, which is not wealth: there is "
            "no contractual entitlement, so it is tracked as projected income and never "
            "capitalised"
        )
    if asset.capitalisation_multiplier is None:
        raise ValuationError(
            f"{asset.asset_id} has no capitalisation multiplier. There is no agreed "
            "convention to fall back on - 16, 20 and a transfer value are all defensible "
            "for different purposes - so the choice has to be made explicitly."
        )
    return annual_income_minor * asset.capitalisation_multiplier


def record_observation(
    store: Store,
    asset: Asset,
    *,
    observed_at: date,
    source: str,
    value_minor: int | None = None,
    annual_income_minor: int | None = None,
    units: str | None = None,
    unit_price_minor: int | None = None,
    document_ref: str = "",
    currency: str = "GBP",
) -> None:
    """Record one observation of an asset.

    `units` and `unit_price_minor` are stored whenever a statement supplies
    them, even though nothing reads them yet. Keeping only the total forecloses
    unit-and-price modelling permanently, and the alternative costs two columns.
    """
    if currency != "GBP":
        raise ValuationError(f"{asset.asset_id}: only GBP is supported, got {currency}")

    if observed_at > datetime.now(UTC).date():
        # An observation dated ahead is a typo, and it would sort to the end of
        # the series and be read as the current value.
        raise ValuationError(
            f"{asset.asset_id}: observed_at {observed_at.isoformat()} is in the future"
        )

    if asset.kind in _INCOME_ONLY:
        if value_minor is not None:
            raise ValuationError(
                f"{asset.asset_id} is a {asset.kind.value} entitlement and has no pot to "
                "value. Record the accrued annual income instead; a capital figure is "
                "derived from a multiplier, not observed."
            )
        if annual_income_minor is None:
            raise ValuationError(f"{asset.asset_id}: no annual income given")
    elif value_minor is None:
        raise ValuationError(f"{asset.asset_id}: no value given")

    store.record_valuation_row(
        asset_id=asset.asset_id,
        kind=asset.kind.value,
        observed_at=observed_at,
        value_minor=value_minor,
        annual_income_minor=annual_income_minor,
        currency=currency,
        units=units,
        unit_price_minor=unit_price_minor,
        source=source,
        document_ref=document_ref,
    )
