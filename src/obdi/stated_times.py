"""Every date and instant a source states for a payment, kept as it was stated.

A feed item states several moments: when the payment was made, when it settled,
when the bank last touched the record. Keeping only the one that became the
row's date throws the others away, and a later rule that needs one of them (the
settlement day, which is the day the bank's export lists a card payment under)
then has nothing to read. So every top-level field of the item whose value
parses as an ISO instant or date is kept, found by parsing and not by a list of
names, so a field the bank adds later is kept too.
A field nested at any depth is kept under its path ("meta.provider_date").

WHICH SOURCES. Every source is recorded (`recorded_for` says how each is read):
a feed item and an aggregator item by parsing every field;
a statement's row likewise, from the dates its parser puts in the row's record
(the transaction date, and the posting date where the format states one);
a file export's row from the date columns its parser declares (`date_fields`), because
a file dates in the format its parser pins and not in ISO, and each is kept in ISO.

THE SETTLEMENT DAY. The one field the matcher reads is `SETTLEMENT_FIELD`, by
`settlement_days`, which is where its rule is stated and the only place that
says which days count.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .errors import DataError
from .jsontypes import JsonObject
from .models import Transaction
from .payment_links import FIRST_PARTY_FEEDS

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


def _walk(path: str, value: object, found: list[StatedTime]) -> None:
    if isinstance(value, Mapping):
        for key, inner in value.items():
            _walk(f"{path}.{key}" if path else str(key), inner, found)
    elif isinstance(value, list):
        for position, inner in enumerate(value):
            _walk(f"{path}[{position}]", inner, found)
    elif isinstance(value, str):
        parsed = _parse(value.strip())
        if parsed is not None:
            found.append(StatedTime(path, value.strip(), parsed[0], _zone_of(value.strip())))


def stated_times(raw: Mapping[str, object]) -> list[StatedTime]:
    """Each field of a record, at any depth, that states a date or an instant, in field order."""
    found: list[StatedTime] = []
    _walk("", raw, found)
    return found


def _file_moments(transaction: Transaction) -> list[StatedTime] | None:
    """The date columns of a file export's row, or None where the source is not a file parser.

    Each is read by the parser that pinned its format and kept in ISO form, which is what
    the other kinds of source state and what every reader of this table parses.
    A cell that is empty or not a date in the pinned format is left out: a file that
    cannot be read at all is refused at import, long before this.
    """
    from .parsers.uk_banks import PARSERS

    for parser_class in PARSERS:
        if parser_class.source != transaction.source:
            continue
        parser = parser_class()
        found: list[StatedTime] = []
        for field in parser.date_fields:
            cell = transaction.raw.get(field)
            if not isinstance(cell, str) or not cell.strip():
                continue
            try:
                day = parser.parse_stated_date(cell)
            except DataError:
                continue
            found.append(StatedTime(field, day.isoformat(), DATE, ""))
        return found
    return None


def recorded_for(transaction: Transaction) -> list[StatedTime]:
    """What to keep of this sighting: every date and instant its source states for it.

    A round-up leg is derived from a feed item and is not the item, so the item's moments
    are not its own.
    """
    from .providers.starling import ROUND_UP_LEG_SUFFIX

    if transaction.source in FIRST_PARTY_FEEDS:
        if not transaction.source_id or transaction.source_id.endswith(ROUND_UP_LEG_SUFFIX):
            return []
        return stated_times(transaction.raw)
    from_file = _file_moments(transaction)
    return stated_times(transaction.raw) if from_file is None else from_file


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
    changeover = timedelta(hours=1)
    start = datetime.combine(_last_sunday(utc.year, 3), datetime.min.time(), UTC) + changeover
    end = datetime.combine(_last_sunday(utc.year, 10), datetime.min.time(), UTC) + changeover
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


def settlement_days_of(transaction: Transaction) -> frozenset[date]:
    """The days a sighting's own settlement could be listed under, empty for any source but
    the bank's own feed and for a derived leg (`recorded_for` says why)."""
    if transaction.source not in FIRST_PARTY_FEEDS or not recorded_for(transaction):
        return frozenset()
    return settlement_days(transaction.raw)


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
