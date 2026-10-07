"""Tiered fetch windows for transaction-date providers.

Starling got a rolling cursor because its filter keys on update time -
proven, not assumed. TrueLayer's from/to filter on TRANSACTION date, so
no cursor is possible there: an amendment to a Tuesday transaction
arrives only through a window that covers Tuesday. The always-90-days
answer to that was ~360 sightings per transaction; the tiered answer
keeps the coverage and drops the volume roughly tenfold:

  frequent   every cycle     ~3 days   new transactions, fast amendments
             (fuel preauths settling, same-day corrections)
  daily      once a day      ~7 days   transit-cap batching that rolls
             over several days, weekend settlement
  weekly     once a week    ~56 days   late clearings, months-late
             refunds, anything slow

Each wider tier deliberately re-covers the narrower ones - that overlap
is the coverage guarantee, and sightings deduplicate it for free. The
tier chosen is stated in the pull notes with its window, so the attempt
ledger and the notes together always answer "what did we ask, and why
that much".

Quota-neutral by construction: tiering changes the WINDOW of each ask,
never the number of asks - the unattended cap spends exactly as before.

State lives in provider_facts, like the Starling cursor: which tier
last ran and when, per connection. No stamps at all means a first run,
which takes the widest window - the safe direction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .store import Store

DEFAULT_FREQUENT_DAYS = 3
DEFAULT_DAILY_DAYS = 7
DEFAULT_WEEKLY_DAYS = 56


def _env_days(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(int(raw), 1) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class TierChoice:
    label: str
    days: int


def _parse(stamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _due(store: Store, source: str, connection_id: str, label: str, interval: timedelta) -> bool:
    raw = store.provider_fact(source, connection_id, f"tier-last-{label}")
    if not raw:
        return True
    last = _parse(raw)
    return last is None or datetime.now(UTC) - last >= interval


def select(store: Store, source: str, connection_id: str) -> TierChoice:
    """Which window this cycle should ask for.

    Widest-due wins: a weekly slot that has come round outranks the
    daily one, which outranks the every-cycle default. A first run has
    no stamps, so both cadences read as due and the weekly window runs -
    over-fetching once is the safe direction to start from.
    """
    if _due(store, source, connection_id, "weekly", timedelta(days=7)):
        return TierChoice("weekly", _env_days("OBDI_TL_WEEKLY_DAYS", DEFAULT_WEEKLY_DAYS))
    if _due(store, source, connection_id, "daily", timedelta(days=1)):
        return TierChoice("daily", _env_days("OBDI_TL_DAILY_DAYS", DEFAULT_DAILY_DAYS))
    return TierChoice("frequent", _env_days("OBDI_TL_FREQUENT_DAYS", DEFAULT_FREQUENT_DAYS))


def stamp(store: Store, source: str, connection_id: str, choice: TierChoice) -> None:
    """Record that this tier's slot was spent - AFTER the pull succeeded.

    Stamped on completion rather than selection so a refused cycle does
    not burn its tier: the next cycle simply offers the same window
    again. A wider tier also satisfies the narrower cadences beneath it,
    because its window covers theirs.
    """
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if choice.label == "weekly":
        store.record_provider_fact(source, connection_id, "tier-last-weekly", now)
        store.record_provider_fact(source, connection_id, "tier-last-daily", now)
    elif choice.label == "daily":
        store.record_provider_fact(source, connection_id, "tier-last-daily", now)
