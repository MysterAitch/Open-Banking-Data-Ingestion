"""Money is always an integer count of minor units (pence for GBP).

Floats are never used for amounts anywhere in this codebase. Rounding noise in
float amounts is a documented cause of failed transaction matching in other
personal-finance importers: two records for the same payment differ by a
fraction of a penny and never dedupe.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# Currencies whose minor unit is not 1/100 of the major unit are not supported
# until one is actually encountered; failing loudly beats a silent 100x error.
MINOR_UNITS_PER_MAJOR = 100


class AmountParseError(ValueError):
    """Raised when a source amount cannot be read exactly."""


def parse_amount(text: str, *, currency: str = "GBP") -> int:
    """Parse a decimal amount string into minor units.

    Accepts the shapes UK bank exports actually emit: thousands separators,
    a currency symbol, parenthesised negatives, and a leading plus.
    """
    if currency != "GBP":
        # Guard rather than guess. GBX/pence-quoted values in particular are a
        # known 100x hazard when treated as major units.
        raise AmountParseError(f"currency {currency!r} not supported yet")

    cleaned = text.strip().replace(",", "").replace("£", "").replace("+", "")
    if not cleaned:
        raise AmountParseError("empty amount")

    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1].strip()

    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise AmountParseError(f"cannot parse amount {text!r}") from exc

    minor = value.scaleb(2)
    if minor != minor.to_integral_value():
        raise AmountParseError(f"amount {text!r} has sub-penny precision")

    result = int(minor)
    return -result if negative else result


def format_amount(minor_units: int, *, currency: str = "GBP") -> str:
    """Render minor units for display. Never used as a storage format."""
    sign = "-" if minor_units < 0 else ""
    whole, part = divmod(abs(minor_units), MINOR_UNITS_PER_MAJOR)
    symbol = "£" if currency == "GBP" else ""
    return f"{sign}{symbol}{whole}.{part:02d}"
