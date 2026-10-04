"""Every date and instant a source states for a payment, kept as it was stated.

A feed item states several moments: when the payment was made, when it settled,
when the bank last touched the record. Keeping only the one that became the
row's date throws the others away, and a later rule that needs one of them (the
settlement day, which is the day the bank's export lists a card payment under)
then has nothing to read. So every top-level field of the item whose value
parses as an ISO instant or date is kept, found by parsing and not by a list of
names, so a field the bank adds later is kept too.

WHICH SOURCES. `RECORDING_SOURCES` names the sources whose items are read this
way.
The feed is the only one so far; the aggregator, the export, and the statements
state their dates in other shapes and are not yet recorded.

THE SETTLEMENT DAY. The one field the matcher reads is `SETTLEMENT_FIELD`, by
`settlement_days`, which is where its rule is stated and the only place that
says which days count.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .jsontypes import JsonObject
from .models import Transaction

#: Sources whose items state their moments in ISO form and are recorded.
RECORDING_SOURCES = frozenset({"starling"})

SETTLEMENT_FIELD = "settlementTime"

INSTANT = "instant"
DATE = "date"


@dataclass(frozen=True)
class StatedTime:
    """One field as the source stated it."""

    field: str
    #: The text exactly as the source gave it.
    stated: str
    kind: str
    #: The offset the text carries ("Z" or "+01:00"), empty for a date or an instant without one.
    zone: str


def _zone_of(text: str) -> str:
    if text.endswith(("Z", "z")):
        return "Z"
    tail = text[-6:]
    if len(tail) == 6 and tail[0] in "+-" and tail[3] == ":":
        return tail
    return ""


def _parse(text: str) -> tuple[str, datetime | date] | None:
    if len(text) == 10:
        try:
            return DATE, date.fromisoformat(text)
        except ValueError:
            return None
    if len(text) < 11 or text[10] not in "Tt":
        return None
    try:
        return INSTANT, datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None


def stated_times(raw: Mapping[str, object]) -> list[StatedTime]:
    """Each top-level field of a record that states a date or an instant, in field order."""
    found: list[StatedTime] = []
    for field, value in raw.items():
        if not isinstance(value, str):
            continue
        parsed = _parse(value.strip())
        if parsed is not None:
            found.append(StatedTime(field, value.strip(), parsed[0], _zone_of(value.strip())))
    return found


def recorded_for(transaction: Transaction) -> list[StatedTime]:
    """What to keep of this sighting: nothing unless its source is recorded and it is
    a record of the source's own item.
    A round-up leg is derived from an item and is not the item, so the item's moments
    are not its own."""
    from .providers.starling import ROUND_UP_LEG_SUFFIX

    if transaction.source not in RECORDING_SOURCES or not transaction.source_id:
        return []
    if transaction.source_id.endswith(ROUND_UP_LEG_SUFFIX):
        return []
    return stated_times(transaction.raw)


def _last_sunday(year: int, month: int) -> date:
    day = date(year, month + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def london_date(instant: datetime) -> date:
    """The date the instant falls on in Europe/London.

    By the rule in force since 1996 (summer time from the last Sunday of March to
    the last Sunday of October, both at 01:00 UTC), because the zone database is
    not installed everywhere this runs.
    """
    utc = instant.astimezone(UTC)
    start = datetime.combine(_last_sunday(utc.year, 3), datetime.min.time(), UTC) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(utc.year, 10), datetime.min.time(), UTC) + timedelta(hours=1)
    summer = start <= utc < end
    return (utc + timedelta(hours=1 if summer else 0)).date()


def days_of(stated: StatedTime) -> frozenset[date]:
    """The days a stated moment could be listed under: its date, and for an instant
    both its UTC date and its Europe/London date."""
    parsed = _parse(stated.stated)
    if parsed is None:
        return frozenset()
    kind, value = parsed
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return frozenset({value.astimezone(UTC).date(), london_date(value)})
    return frozenset({value}) if kind == DATE else frozenset()


def settlement_days(raw: JsonObject | Mapping[str, object]) -> frozenset[date]:
    """The days a record's settlement could be listed under, empty where it states none.

    THE RULE. A bank's export dates a card payment by its SETTLEMENT day, and
    the feed states that moment as `SETTLEMENT_FIELD`, in UTC.
    The export's day is accepted in either zone because it is not known which
    the export uses: a payment settled at 23:30 UTC in summer is the next day in London.
    The matcher reads this and nothing else about the record's moments
    (`matching.settles_together` states what it does with the days).
    """
    for item in stated_times(raw):
        if item.field == SETTLEMENT_FIELD:
            return days_of(item)
    return frozenset()
