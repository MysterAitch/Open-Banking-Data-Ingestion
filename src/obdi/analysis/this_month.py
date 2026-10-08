"""The month's forward calendar: each confirmed commitment's due days, paid or not, and whether the
account each leaves is funded for what is due before the next income.

"Can I afford this?" answered from facts and nothing else (`plan.md` section 6a: obdi owns facts,
the budgeting tool owns allocation). Nothing here chooses where money should go; it lays the
confirmed commitments on the days they fall due, says which were met, and sets the held balance
against what leaves before the next income.

THE DUE DAYS are `free_position.due_days`, the placing Position's committed figure is made from,
over every window of the commitment that overlaps the month (a price change closes one window and
opens another mid-month, so both are laid on their own days at their own amounts). A commitment
whose last window closed in the month is marked ENDED on the day it closed.

THE STATE OF A DUE DAY comes from the days the commitment's series was seen paid (`Series.
seen_days`, the series being `commitments.match_series`'s answer), not from the detector's own
`stopped` mark, because a commitment asks about one day and the detector about the whole run:

  PAID     a payment was seen within the window's tolerance of the day (the day is said).
  DUE      no payment yet and the day, plus tolerance, has not passed; or it has passed and the
           account's transactions do not reach that far, so absence proves nothing (said).
  OVERDUE  the day plus tolerance has passed, the account's transactions reach past it, and no
           payment was seen.
  NOT TAKEN  overdue by that rule, but the detector explains the slot: a pulled payment whose
           card owed nothing for the cycle (`Series.explained`). Not counted as overdue.
  ENDED    the commitment's last window closed on this day.

FUNDED comes from `free_position`'s own figures, unrecomputed: `AccountFigures.free_short` is the
shortfall, `income_on` the day it is judged to. Only an account that has an outgoing commitment is
judged, and a card is judged only where a limit is declared: a card with none has nothing to be
short of, and the page says so instead of failing the headline.

NOT HANDLED, and said on the page where it matters: a commitment with no day that can be placed
is not on the calendar (it is counted unplaced by Position); the NOT TAKEN rule cannot tell an
old explained slot from a trailing one when a series has both, so it is read as a trailing slot
only from the series' next expected day onwards.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ..core.masking import Structural, Total
from ..ingest.commitment_records import Commitment, Window
from ..read.ledger import Money
from ..read.position import Position
from .commitments import match_series
from .flows import MISSING, NOTHING, FlowReading, LegInstance, SpaceNeed
from .free_position import CURRENCY, AccountFigures, FreeFigures, OwedLine, due_days
from .recurring import PULLED, Series

PAID = "paid"
DUE = "due"
OVERDUE = "overdue"
NOT_TAKEN = "not-taken"
ENDED = "ended"

OUT = "out"
IN = "in"


@dataclass(frozen=True)
class CalendarLine:
    """One commitment on one day: what, where from, how much, and what became of it."""

    #: The commitment's name: the payee as the owner confirmed it, so a value to mask.
    name: str
    account: Structural[str]
    account_label: Structural[str]
    direction: Structural[str]
    #: ISO day.
    due: Structural[str]
    state: Structural[str]
    #: The day the payment was seen, ISO, or "".
    paid_on: Structural[str]
    amount: Total[Money]
    #: A sentence that holds no amount: why a state is what it is, or "".
    said: Structural[str]


@dataclass(frozen=True)
class MonthCounts:
    """The outgoing days by state, which is the headline."""

    total: Structural[int]
    paid: Structural[int]
    due: Structural[int]
    overdue: Structural[int]
    not_taken: Structural[int]
    ended: Structural[int]


@dataclass(frozen=True)
class ThisMonth:
    as_of: Structural[str]
    #: The month shown, "YYYY-MM".
    month: Structural[str]
    #: Whether this is the month after the current one, when nothing can have been paid.
    ahead: Structural[bool]
    lines: Structural[tuple[CalendarLine, ...]]
    counts: Structural[MonthCounts]
    #: Position's own figures, whole, for the funded section.
    free: Structural[FreeFigures]
    #: The accounts whose funding is judged: those leaving a confirmed outgoing commitment, and
    #: the cards with a declared limit.
    judged: Structural[tuple[str, ...]]
    #: How the money of commitments with legs moves: the legs that did not happen, the spaces'
    #: needs, and what is owed to the household (`flows`). Empty where no leg is declared.
    flows: Structural[FlowReading] = NOTHING


@dataclass(frozen=True)
class ShortAccount:
    ref: Structural[str]
    label: Structural[str]
    #: The day of the next income the shortfall is judged to, ISO.
    before: Structural[str]


@dataclass(frozen=True)
class MonthNote:
    """What Today says about the month: how many days are overdue and which accounts are short,
    and no amount."""

    overdue: Structural[int]
    short: Structural[tuple[ShortAccount, ...]]
    unjudged: Structural[int]
    #: The spaces that do not yet hold what the month asks of them (`flows.SpaceNeed`); a funded
    #: space is not here, so Today is silent about it.
    spaces: Structural[tuple[SpaceNeed, ...]] = ()
    #: The legs that did not happen (`flows.LegInstance`, state missing).
    missing: Structural[tuple[LegInstance, ...]] = ()
    #: What is owed to the household and past its day (a thing to do), and what it comes to.
    owed: Structural[tuple[OwedLine, ...]] = ()
    owed_total: Total[Money] = NOTHING.owed_total

    def worth_saying(self) -> bool:
        return bool(self.overdue or self.short or self.spaces or self.missing or self.owed)


def month_bounds(day: date, *, ahead: bool) -> tuple[date, date]:
    """The first and last day of `day`'s month, or of the month after it."""
    index = day.year * 12 + day.month - 1 + (1 if ahead else 0)
    year, month = divmod(index, 12)
    return date(year, month + 1, 1), date(year, month + 1, calendar.monthrange(year, month + 1)[1])


def _seen_near(seen: Sequence[date], due: date, tolerance: int, today: date) -> date | None:
    near = [
        s for s in seen if abs((s - due).days) <= tolerance and s <= today
    ]
    return min(near, key=lambda s: abs((s - due).days)) if near else None


def _state(
    due: date,
    window: Window,
    series: Series | None,
    reach: str,
    today: date,
) -> tuple[str, str, str]:
    """(state, the day it was paid or "", a sentence) for one due day of a window."""
    tolerance = window.tolerance_days
    seen = _seen_near(series.seen_days if series else (), due, tolerance, today)
    if seen is not None:
        return PAID, seen.isoformat(), f"Paid {seen.isoformat()}."
    deadline = due + timedelta(days=tolerance)
    if today <= deadline:
        return DUE, "", ""
    if not reach or date.fromisoformat(reach) < deadline:
        said = (
            f"The account's transactions run to {reach}, before this was due, so it may be paid "
            "and not yet seen."
            if reach
            else "The account holds no transactions, so whether this was paid is not known."
        )
        return DUE, "", said
    if series is None:
        return OVERDUE, "", "No payment of it is found in the transactions held."
    if (
        series.kind == PULLED
        and series.explained > 0
        and not series.stopped
        and series.next_expected <= due
    ):
        return (
            NOT_TAKEN,
            "",
            "Nothing was taken: the card it is collected from owed nothing for the cycle.",
        )
    return OVERDUE, "", f"Last paid {series.last_seen.isoformat()}."


def _lines_of(
    commitment: Commitment,
    series: Series | None,
    reach: dict[str, str],
    called: dict[str, str],
    first: date,
    last: date,
    today: date,
) -> list[CalendarLine]:
    account = commitment.account
    label = called.get(account, account)
    lines: list[CalendarLine] = []
    for window in commitment.windows:
        if window.to_day is not None and window.to_day < first:
            continue
        for due in due_days(window, first, last):
            state, paid_on, said = _state(due, window, series, reach.get(account, ""), today)
            lines.append(
                CalendarLine(
                    commitment.name,
                    account,
                    label,
                    commitment.direction,
                    due.isoformat(),
                    state,
                    paid_on,
                    Money(window.amount_minor, CURRENCY),
                    said,
                )
            )
    closing = commitment.windows[-1].to_day if commitment.windows else None
    if closing is not None and first <= closing <= last and commitment.current is not None:
        lines.append(
            CalendarLine(
                commitment.name,
                account,
                label,
                commitment.direction,
                closing.isoformat(),
                ENDED,
                "",
                Money(commitment.current.amount_minor, CURRENCY),
                "The commitment ended on this day.",
            )
        )
    return lines


def _judged(free: FreeFigures, commitments: Sequence[Commitment]) -> tuple[str, ...]:
    leaving = {
        c.account
        for c in commitments
        if c.direction == OUT and c.current is not None and c.current.to_day is None
    }
    chosen = []
    for account in free.accounts:
        if account.ref not in leaving:
            continue
        if account.is_card and account.free is None:
            continue
        chosen.append(str(account.ref))
    return tuple(chosen)


def build_this_month(
    position: Position,
    commitments: Sequence[Commitment],
    detected: Sequence[Series],
    free: FreeFigures,
    *,
    today: date,
    ahead: bool = False,
    flows: FlowReading = NOTHING,
) -> ThisMonth:
    """The calendar of the month of `today` (or the month after, where `ahead`), with Position's
    `free` figures beside it. `detected` are the detector's series, from which each commitment's
    payments are read; handing none leaves every past day unpaid, which is why a day is only
    called overdue where its account's transactions reach past it."""
    first, last = month_bounds(today, ahead=ahead)
    matches = match_series(detected, commitments)
    by_commitment: dict[int, Series] = {
        m.commitment.id: series for series, m in zip(detected, matches, strict=True) if m
    }
    accounts = [a for g in position.groups for a in g.accounts] + list(position.uncounted)
    reach = {a.ref: a.rows_through for a in accounts}
    # Position's own label for each account, already decided there.
    called = {a.ref: a.label for a in accounts}
    lines: list[CalendarLine] = []
    for commitment in commitments:
        mine = by_commitment.get(commitment.id)
        lines.extend(_lines_of(commitment, mine, reach, called, first, last, today))
    lines.sort(key=lambda line: (str(line.due), line.direction != IN, str(line.name)))
    out = [line for line in lines if line.direction == OUT]

    def count(state: str) -> int:
        return sum(line.state == state for line in out)

    counts = MonthCounts(
        total=sum(line.state != ENDED for line in out),
        paid=count(PAID),
        due=count(DUE),
        overdue=count(OVERDUE),
        not_taken=count(NOT_TAKEN),
        ended=count(ENDED),
    )
    return ThisMonth(
        as_of=today.isoformat(),
        month=f"{first.year:04d}-{first.month:02d}",
        ahead=ahead,
        lines=tuple(lines),
        counts=counts,
        free=free,
        judged=_judged(free, commitments),
        flows=flows,
    )


def judged_accounts(month: ThisMonth) -> list[AccountFigures]:
    """The accounts whose funding is judged, in Position's order."""
    wanted = set(month.judged)
    return [a for a in month.free.accounts if a.ref in wanted]


def is_short(account: AccountFigures) -> bool:
    return account.free is not None and bool(account.free_short)


def is_unjudgeable(account: AccountFigures) -> bool:
    """Judged, and its figure could not be made: no balance known, or no next income to count to."""
    return account.free is None


def note_of(month: ThisMonth) -> MonthNote:
    """What Today says of the month, from the same record the page is made from."""
    judged = judged_accounts(month)
    # Today asks for what has gone past its day; what is merely owed is on the page and Position.
    late = tuple(line for line in month.flows.owed if line.overdue)
    return MonthNote(
        overdue=month.counts.overdue,
        short=tuple(
            ShortAccount(a.ref, a.label, a.income_on) for a in judged if is_short(a)
        ),
        unjudged=sum(is_unjudgeable(a) for a in judged),
        spaces=tuple(s for s in month.flows.spaces if not s.funded),
        missing=tuple(leg for leg in month.flows.legs if leg.state == MISSING),
        owed=late,
        owed_total=Money(sum(line.amount.minor for line in late), CURRENCY),
    )
