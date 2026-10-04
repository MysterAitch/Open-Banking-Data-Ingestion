"""A closed Space's history, asked for in bounded windows.

The provider's feed answers `changesSince` from a stamp to now and refuses any
such window beyond its maximum range (between 180 and 365 days), so a Space
that closed years ago can never be reached that way. Its history is instead
asked for in windows that name both ends, laid over the span its movements are
known to cover.

Everything here is derived from the attempt ledger: which windows have landed
(rows or empty), which were refused, how long a window the provider has been
seen to accept. Nothing is kept anywhere else, so an interrupted run resumes at
the first window not yet landed and a finished Space is recognised without a
single ask.

This module reads and plans; `pull.py` asks and lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from .plural import plural as _plural
from .providers import starling
from .spaces import HistoricalSpace
from .store import Store

#: Prefixes the `detail` of every attempt-ledger row for a CLOSED Space's feed,
#: so the three answers the provider can give stay distinguishable without a
#: new outcome value (coverage and the timeline read anything but "landed" as
#: a failure, and an empty feed is an ordinary landing): a refusal is outcome
#: "refused", an answer with rows is "landed" with the mark, and an empty
#: answer is "landed" with the mark and the empty word after it. The unheld-leg
#: note on the ledger page reads these rows back (`family_anchors.space_fetches`).
CLOSED_SPACE_MARK = "closed-space"
CLOSED_SPACE_EMPTY = "closed-space: answered empty"

#: A closed Space's settled answer (a refusal that is not the provider's quota
#: or range answer) is not asked for again for this long.
#: Nothing can arrive in a closed Space's feed, so asking every cycle spends
#: quota to learn what was already learnt; a month later a changed answer, such
#: as access granted or the endpoint appearing, is still noticed.
CLOSED_SPACE_RETRY_DAYS = 30

#: What the provider's refusal says when the ask exceeds its maximum range.
#: The ladders key on it and so does the attempts page, which marks these
#: refusals as the ladder working rather than as a fault.
RANGE_REFUSAL_MARK = "QUERY_EXCEEDING_MAX_TIME_RANGE"

#: Window lengths to try, longest first. The provider's own `changesSince`
#: ceiling was measured on 2026-08-05..09 as landing at 180 days and refusing
#: 365; 180 is the longest length known to be acceptable to the same feed, so
#: it is the first guess for a window ask whose own limit is unmeasured. A
#: range refusal moves one rung down, and the shortest rung that is still
#: refused ends the category's cycle rather than looping.
WINDOW_LADDER_DAYS = (180, 90, 30, 7)

#: Either side of a Space's known movements. The span is recovered from the main
#: account's transfers, so it holds the first and last TRANSFER but not a
#: payment made from the Space's own card or an interest credit after the last
#: transfer, nor an opening balance moved in before the first. A month absorbs
#: those at the price of at most one extra window per Space.
WINDOW_MARGIN_DAYS = 30

#: Asks a category may spend in one cycle, refused rungs included. A seven-year
#: Space is some fifteen windows at the first length; four closed categories
#: with that history would otherwise spend sixty calls in one cycle on top of
#: the routine feeds, and a 429 on the first of them has already shown how the
#: provider answers a burst. Six keeps a category's share of one cycle near the
#: routine pull's own and lets a multi-year Space finish over a few cycles.
WINDOW_ASKS_PER_CYCLE = 6

Window = tuple[datetime, datetime]


def span_of(space: HistoricalSpace, now: datetime) -> Window:
    """First known movement to last, widened by the margin and never past now."""
    start = datetime.combine(
        space.first_seen - timedelta(days=WINDOW_MARGIN_DAYS), time.min, tzinfo=UTC
    )
    end = datetime.combine(
        space.last_seen + timedelta(days=WINDOW_MARGIN_DAYS + 1), time.min, tzinfo=UTC
    )
    return start, min(end, now.astimezone(UTC))


@dataclass(frozen=True)
class WindowAttempt:
    """One recorded ask of a bounded window."""

    window: Window
    at: str
    outcome: str
    status: int | None
    detail: str

    @property
    def landed(self) -> bool:
        return self.outcome == "landed"

    @property
    def empty(self) -> bool:
        return self.landed and self.detail.startswith(CLOSED_SPACE_EMPTY)

    @property
    def range_refused(self) -> bool:
        return self.outcome == "refused" and RANGE_REFUSAL_MARK in self.detail

    @property
    def settled_refusal(self) -> bool:
        """A refusal that waiting within a cycle cannot change.

        Not the quota answer (429) and not the range answer, which the ladder
        owns; a server fault (5xx) is retried next cycle like any transient.
        """
        return (
            self.outcome == "refused"
            and isinstance(self.status, int)
            and 400 <= self.status < 500
            and self.status != 429
            and not self.range_refused
        )


def window_attempts(store: Store, refs: tuple[str, str]) -> list[WindowAttempt]:
    """Every bounded-window ask recorded for a Space, oldest first.

    `refs` is the provider-qualified name and the bound account's, both read,
    because `Store.rebind_account` moves attempt rows from the first to the
    second on a bind while later asks are filed under the first again: either
    alone loses some of them, and a reader of the first alone asks every
    window again.
    """
    found: list[WindowAttempt] = []
    for row in store.connection.execute(
        "SELECT attempted_at, outcome, http_status, detail, asked FROM fetch_attempts "
        "WHERE source = 'starling-feed' AND account_ref IN (?, ?) "
        "ORDER BY attempted_at, rowid",
        refs,
    ):
        window = starling.parse_window_spec(str(row["asked"]))
        if window is None:
            continue
        found.append(
            WindowAttempt(
                window=window,
                at=str(row["attempted_at"]),
                outcome=str(row["outcome"]),
                status=row["http_status"],
                detail=str(row["detail"]),
            )
        )
    return found


def uncovered(span: Window, attempts: list[WindowAttempt]) -> list[Window]:
    """The parts of the span no landed window covers, oldest first."""
    lo, hi = span
    gaps: list[Window] = []
    reached = lo
    for start, end in sorted(a.window for a in attempts if a.landed):
        if start > reached:
            gaps.append((reached, min(start, hi)))
        reached = max(reached, end)
        if reached >= hi:
            return gaps
    if reached < hi:
        gaps.append((reached, hi))
    return gaps


def working_length(attempts: list[WindowAttempt], now: datetime) -> int:
    """The window length to start from, in days.

    The longest rung strictly shorter than the shortest window the provider
    has refused as too long within the retry period, so a limit learnt in one
    cycle is not paid for again in the next.
    """
    cutoff = (now.astimezone(UTC) - timedelta(days=CLOSED_SPACE_RETRY_DAYS)).isoformat()
    refused = [
        (a.window[1] - a.window[0]) / timedelta(days=1)
        for a in attempts
        if a.range_refused and a.at > cutoff
    ]
    if not refused:
        return WINDOW_LADDER_DAYS[0]
    shortest = min(refused)
    return next((days for days in WINDOW_LADDER_DAYS if days < shortest), WINDOW_LADDER_DAYS[-1])


def narrower(days: int) -> int | None:
    """The next rung down from a length, or None when the ladder is exhausted."""
    return next((rung for rung in WINDOW_LADDER_DAYS if rung < days), None)


def plan(gaps: list[Window], days: int) -> list[Window]:
    """Windows of at most `days` laid over the gaps, oldest first."""
    length = timedelta(days=days)
    windows: list[Window] = []
    for lo, hi in gaps:
        start = lo
        while start < hi:
            end = min(start + length, hi)
            windows.append((start, end))
            start = end
    return windows


def blocked(attempts: list[WindowAttempt], now: datetime) -> bool:
    """Whether the newest answer was a settled refusal still inside the retry period."""
    if not attempts:
        return False
    newest = attempts[-1]
    cutoff = (now.astimezone(UTC) - timedelta(days=CLOSED_SPACE_RETRY_DAYS)).isoformat()
    return newest.settled_refusal and newest.at > cutoff


@dataclass(frozen=True)
class HistoryProgress:
    """Where a closed Space's windowed history stands. Counts only, no figures."""

    #: Landed windows plus those still to ask at the working length.
    windows: int
    with_rows: int
    empty: int
    #: Refusals that were not the provider saying a window was too long.
    refused: int
    #: How many asks were answered "too long", the ladder working.
    too_long: int
    #: Why the newest ask was refused, when it was ("HTTP 404"), else empty.
    latest_refusal: str
    latest_refusal_on: str
    complete: bool
    #: The longest window in days the provider is believed to accept.
    window_days: int

    @property
    def asked(self) -> int:
        return self.with_rows + self.empty + self.refused

    def describe(self) -> str:
        """The progress in words."""
        if self.complete:
            head = (
                f"history complete over {_plural(self.with_rows + self.empty, 'window')} "
                f"({self.with_rows} with rows, {self.empty} empty)"
            )
        else:
            head = (
                f"history incomplete: {self.with_rows + self.empty} of "
                f"{_plural(self.windows, 'window')} landed "
                f"({self.with_rows} with rows, {self.empty} empty)"
            )
        parts = [head]
        if self.refused:
            parts.append(f"{self.refused} refused")
        if self.latest_refusal:
            parts.append(f"newest ask refused ({self.latest_refusal}) on {self.latest_refusal_on}")
        if self.too_long:
            parts.append(
                f"{_plural(self.too_long, 'ask')} too long for the provider, "
                f"now asking {self.window_days}-day windows"
            )
        return "; ".join(parts)


def history_progress(
    store: Store, refs: tuple[str, str], span: Window, now: datetime
) -> HistoryProgress:
    attempts = window_attempts(store, refs)
    gaps = uncovered(span, attempts)
    days = working_length(attempts, now)
    landed = [a for a in attempts if a.landed]
    newest = attempts[-1] if attempts else None
    refusal = ""
    if newest is not None and newest.outcome == "refused":
        refusal = (
            "window too long"
            if newest.range_refused
            else f"HTTP {newest.status}"
            if newest.status
            else "no answer"
        )
    return HistoryProgress(
        windows=len(landed) + len(plan(gaps, days)),
        with_rows=sum(1 for a in landed if not a.empty),
        empty=sum(1 for a in landed if a.empty),
        refused=sum(1 for a in attempts if a.outcome == "refused" and not a.range_refused),
        too_long=sum(1 for a in attempts if a.range_refused),
        latest_refusal=refusal,
        latest_refusal_on=newest.at[:10] if newest is not None and refusal else "",
        complete=not gaps,
        window_days=days,
    )
