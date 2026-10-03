"""Which days of an aggregator account or card no landed ask has ever covered.

The routine tiers ask for the most recent days only, so a feed that goes
unasked for longer than the widest tier leaves a span nothing will ever ask
for again, while "covered to" still names today.
Three credit cards went sixty days with nothing asking for them, and once
asking resumed only the last few days were requested: the months in between
held no rows from July to September, and no page said so.

THE RULE, stated here and nowhere else: for each account and card, the windows
that LANDED should be contiguous from the oldest landed edge to the newest.
A day inside that span that no landed window covers is a hole.
A hole is HEALABLE while the provider still serves its days unattended
(`UNATTENDED_REACH_DAYS` back from today), and LOST to unattended fetching once
it has passed out of that reach; a lost hole needs an attended extend or a
statement.
The trailing edge (newest landed day to today) is not a hole: it is the
lag the Connections page already reports as stale.

Everything is read from the attempt ledger, by the dates each ask named, and
the interval arithmetic is the closed Spaces' (`space_windows.uncovered`).
An ask whose recorded window cannot be read (a refusal, or a ledger row from
before windows were recorded) covers nothing: claiming coverage that cannot be
shown is the wrong direction to be wrong in, and the price of being wrong the
other way is one extra ask that then lands with its window.

This module reads and plans; `pull.py` asks and lands.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qs

from .accounts import AccountMap
from .providers.truelayer import ROUTINE_WINDOW_DAYS
from .space_windows import WindowAttempt, uncovered
from .store import Store

#: How far back from today the provider serves transaction history without the
#: person present (the regulatory ninety days; `ROUTINE_WINDOW_DAYS` owns the
#: figure and says why). A day older than this cannot be asked for unattended.
UNATTENDED_REACH_DAYS = ROUTINE_WINDOW_DAYS

#: Healing asks one connection may spend in one routine cycle, on top of the
#: tier asks.
#: The unattended allowance is real and a refusal is evidence, so the spend is
#: capped well below a store-shaped worst case: two covers a bank's current
#: account hole and its card hole together (the real shape), and a longer queue
#: simply continues next cycle, oldest first, while the reach rolls forward by
#: no more than a day between cycles.
HEAL_ASKS_PER_CONNECTION = 2

#: A healing window reaches this many days past each edge of its hole.
#: A window's edges are inclusive dates on the provider's side and a boundary
#: payment has been seen to arrive on the wrong side of one, so the overlap
#: costs nothing (sightings deduplicate it) and removes the question.
HEAL_OVERLAP_DAYS = 1

#: The ledger sources whose landed asks carry a booked-transaction window.
#: Pending asks have no window (the endpoint returns the whole current set).
BOOKED_SOURCES = ("truelayer-booked", "truelayer-card-booked")

DayWindow = tuple[date, date]


@dataclass(frozen=True)
class Hole:
    """Days no landed ask covered, first to last inclusive."""

    first: date
    last: date
    #: Whether an unattended ask can still reach these days.
    within_reach: bool

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


@dataclass(frozen=True)
class Coverage:
    """The span an account's landed asks run over, and what is missing inside it."""

    first: date
    last: date
    holes: tuple[Hole, ...]

    @property
    def reachable(self) -> tuple[Hole, ...]:
        return tuple(hole for hole in self.holes if hole.within_reach)

    @property
    def lost(self) -> tuple[Hole, ...]:
        return tuple(hole for hole in self.holes if not hole.within_reach)

    @property
    def missing_days(self) -> int:
        return sum(hole.days for hole in self.holes)


@dataclass(frozen=True)
class HealAsk:
    """One window to ask for, on behalf of the account or card named."""

    account: str
    first: date
    last: date


def asked_days(asked: str) -> DayWindow | None:
    """The dates a recorded ask named, or None when it named no readable window."""
    query = parse_qs(asked)
    try:
        first = date.fromisoformat(query["from"][0])
        last = date.fromisoformat(query["to"][0])
    except (KeyError, IndexError, ValueError):
        return None
    return (first, last) if first <= last else None


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def coverage_of(windows: Iterable[DayWindow], today: date) -> Coverage | None:
    """The coverage of one account's landed windows, None when none is readable."""
    spans = list(windows)
    if not spans:
        return None
    first = min(start for start, _ in spans)
    last = max(end for _, end in spans)
    one_day = timedelta(days=1)
    landed = [
        WindowAttempt(
            window=(_midnight(start), _midnight(end + one_day)),
            at="",
            outcome="landed",
            status=200,
            detail="",
        )
        for start, end in spans
    ]
    reach_edge = today - timedelta(days=UNATTENDED_REACH_DAYS)
    holes: list[Hole] = []
    for gap_start, gap_end in uncovered((_midnight(first), _midnight(last + one_day)), landed):
        low = gap_start.date()
        high = (gap_end - one_day).date()
        if low < reach_edge:
            holes.append(Hole(low, min(high, reach_edge - one_day), within_reach=False))
        if high >= reach_edge:
            holes.append(Hole(max(low, reach_edge), high, within_reach=True))
    return Coverage(first=first, last=last, holes=tuple(holes))


def canonical_resolver(account_map: AccountMap) -> Callable[[str], str]:
    """Ledger ref to canonical account, for a ref written as `truelayer:<id>`.

    A bind moves ledger rows to the canonical name and later asks are filed
    under the qualified name again, so both must read as one account.
    """

    def canonical_of(ref: str) -> str:
        source, separator, provider_id = ref.partition(":")
        if separator and source == "truelayer":
            return str(account_map.resolve("truelayer", provider_id))
        return ref

    return canonical_of


def coverage_by_account(
    store: Store, canonical_of: Callable[[str], str], today: date
) -> dict[str, Coverage]:
    """Every account and card with a readable landed window, by canonical name."""
    windows: dict[str, list[DayWindow]] = defaultdict(list)
    for row in store.connection.execute(
        "SELECT account_ref, asked FROM fetch_attempts "
        "WHERE outcome = 'landed' AND source IN (?, ?)",
        BOOKED_SOURCES,
    ):
        window = asked_days(str(row["asked"]))
        if window is not None:
            windows[canonical_of(str(row["account_ref"]))].append(window)
    found: dict[str, Coverage] = {}
    for account, spans in windows.items():
        coverage = coverage_of(spans, today)
        if coverage is not None:
            found[account] = coverage
    return found


def heal_plan(coverage: Mapping[str, Coverage], today: date) -> list[HealAsk]:
    """The windows that would close every healable hole, oldest first.

    Oldest first because the reach rolls forward daily: of two holes, the one
    whose days are nearest passing out of reach is the one that must not wait.
    A window never starts before the reach edge, so it is one the provider
    serves unattended.
    """
    reach_edge = today - timedelta(days=UNATTENDED_REACH_DAYS)
    overlap = timedelta(days=HEAL_OVERLAP_DAYS)
    asks = [
        HealAsk(
            account=account,
            first=max(hole.first - overlap, reach_edge),
            last=min(hole.last + overlap, today),
        )
        for account, found in coverage.items()
        for hole in found.reachable
    ]
    return sorted(asks, key=lambda ask: (ask.first, ask.account, ask.last))


def describe_spans(holes: Iterable[Hole], *, cap: int = 3) -> str:
    """The holes as "A to B (note), C to D (note), and N more", figure-free."""
    ordered = list(holes)
    parts = [
        f"{hole.first.isoformat()} to {hole.last.isoformat()} "
        + (
            "(still within unattended reach)"
            if hole.within_reach
            else "(passed out of unattended reach: needs an attended extend or a statement)"
        )
        for hole in ordered[:cap]
    ]
    if len(ordered) > cap:
        parts.append(f"and {len(ordered) - cap} more")
    return ", ".join(parts)
