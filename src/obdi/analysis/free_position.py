"""What is held, what is owed, what is committed before the next income, and what is free.

The question is the one a budgeting tool collapses to one number ("available to spend"), answered
from facts obdi verifies and nothing else: each figure per account and in total, with the basis it
rests on said beside it. It decides nothing about where money SHOULD go (`plan.md` section 6a: obdi
owns facts, the budgeting tool owns allocation); it subtracts what is confirmed due.

HELD is the account's position balance (`read.position`), and its basis is the newest known
balance that balance was tested against and how it is known ("stated by you on D", "statement to
D", "feed 2 days ago"), followed by the day the transactions since it run to. A balance nobody
knows is not nil: it is said as not known, with the day its history began, and no free figure is
made from it.

OWED is a card's or a loan's negative balance. What is a card is the declared kind (`owes_kind`,
free text, so a word match), because the balance's sign cannot tell an overdrawn current account
from a card.

COMMITTED BEFORE THE NEXT INCOME is the sum of the confirmed commitments (`Commitment`), leaving
the account in question, whose next due day falls before its next income. The due day comes from
the open window's cadence and usual day (`next_due`); the next income is the earliest next day of
the confirmed income commitments paid into the account, or, where none is confirmed, the detector's
next expected income on it (`Series.next_expected`), and the figure says which. A card is paid
into by no income, so its commitments are counted to the earliest next income across the
household. A commitment due on the income's own day is not counted before it.

FREE is held less committed. For a card it is the limit less what is owed, where a limit is
handed in (`limits`, made by `limit_in_force` from the account's declared limit windows); a card
with none in force on the day says "No limit declared."

A FIGURE THAT CANNOT BE MADE IS SAID, never dashed: `*_said` holds the sentence naming what is
missing. A total counts only the accounts its figure was made for, and carries how many that was
of how many there are.

NOT HANDLED, and said on the page where it matters: a commitment already paid this cycle is
counted again if its payment is not yet in the balance's rows; a commitment with no usual day
cannot be placed and is listed as not placed; an income that is late is counted to today.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ..core.masking import Structural, Total
from ..ingest.accounts import LimitWindow
from ..ingest.commitment_records import Commitment, Window
from ..read.ledger import Money, direction_of
from ..read.position import AccountPosition, Position
from ..verify.balance_anchors import BANK, EXPORT, STATED, STATEMENT, STATEMENT_OPENING
from .recurring import Series

CURRENCY = "GBP"

#: Words in a declared kind that say the balance is owed: a card, a loan, a mortgage.
_OWING_WORDS = ("card", "loan", "mortgage", "credit")

#: Cadences counted in days, as `recurring` names them, and the days between occurrences.
_DAY_PERIODS = {"weekly": 7, "fortnightly": 14, "four-weekly": 28}
#: Cadences counted in months: the months between occurrences.
_MONTH_PERIODS = {"monthly": 1, "quarterly": 3, "yearly": 12}

CONFIRMED = "confirmed"
DETECTED = "detected"
NONE = "none"


def owes_kind(kind: str) -> bool:
    """Whether a declared kind says the account's balance is owed (a card, a loan)."""
    lowered = kind.casefold()
    return any(word in lowered for word in _OWING_WORDS)


@dataclass(frozen=True)
class DeclaredLimit:
    """The limit in force on a day: its amount, and the day it came into force where the window
    names one."""

    amount_minor: int
    since: date | None


def limit_in_force(windows: Sequence[LimitWindow], today: date) -> DeclaredLimit | None:
    """The declared limit window covering `today` (the newest-starting where several do), or None.

    A window with no first day has always held and one with no last day holds still; the kind
    (credit, overdraft) is not asked, since the account's own kind already says what it is."""
    covering = [
        w
        for w in windows
        if (w.window_from is None or w.window_from <= today)
        and (w.window_to is None or today <= w.window_to)
    ]
    if not covering:
        return None
    newest = max(covering, key=lambda w: (w.window_from or date.min, w.amount_minor))
    return DeclaredLimit(newest.amount_minor, newest.window_from)


def _on_month(index: int, day: int) -> date:
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day, calendar.monthrange(year, month + 1)[1]))


def next_due(window: Window, today: date) -> date | None:
    """The next day on or after `today` the window's payment falls due, or None where its cadence
    or day cannot place one.

    A cadence counted in days is kept in step with the window's first day (the first sighting it
    was written from), because a fortnightly or four-weekly rhythm has a phase the weekday alone
    does not carry. A cadence counted in months falls on the usual day of the month (clamped to
    the month's length), in the months the window's first month steps to."""
    if window.cadence in _DAY_PERIODS:
        period = _DAY_PERIODS[window.cadence]
        if window.from_day >= today:
            return window.from_day
        behind = (today - window.from_day).days
        return window.from_day + timedelta(days=-(-behind // period) * period)
    step = _MONTH_PERIODS.get(window.cadence)
    if step is None or not 1 <= window.usual_day <= 31:
        return None
    start = window.from_day.year * 12 + window.from_day.month - 1
    if window.cadence == "yearly" and 1 <= window.usual_month <= 12:
        start = window.from_day.year * 12 + window.usual_month - 1
    index = today.year * 12 + today.month - 1
    index -= (index - start) % step
    for _ in range(3):
        due = _on_month(index, window.usual_day)
        if due >= today:
            return due
        index += step
    return None


def due_days(window: Window, first: date, last: date) -> list[date]:
    """Every day from `first` to `last` (both included) the window's payment falls due, and
    that is not before the window began or after it closed. The same placing as `next_due`, which
    is asked again from the day after each answer, so the calendar and the Position figures cannot
    place a payment on different days."""
    found: list[date] = []
    cursor = first
    while cursor <= last:
        due = next_due(window, cursor)
        if due is None or due > last:
            break
        if due >= window.from_day and (window.to_day is None or due <= window.to_day):
            found.append(due)
        cursor = due + timedelta(days=1)
    return found


@dataclass(frozen=True)
class DueLine:
    """One confirmed commitment counted against an account: what it is, when, and how much."""

    name: str
    due: Structural[str]
    amount: Total[Money]


@dataclass(frozen=True)
class AccountFigures:
    """One account's four figures, each with its basis; the sentences hold no amount."""

    ref: Structural[str]
    label: Structural[str]
    is_card: Structural[bool]
    #: How the held figure is known, or why it is not ("not known since D ...").
    held_basis: Structural[str]
    held_known: Structural[bool]
    held: Total[Money | None]
    held_direction: Structural[str]
    #: A card's or loan's balance owed; None for any other account, and for an owed account whose
    #: balance is not known.
    owed: Total[Money | None]
    #: `CONFIRMED`, `DETECTED`, or `NONE`: where the next income came from.
    income_from: Structural[str]
    #: The next income's day, ISO, or "".
    income_on: Structural[str]
    income_said: Structural[str]
    #: None where it cannot be made (`committed_said` says why).
    committed: Total[Money | None]
    committed_said: Structural[str]
    due: Structural[tuple[DueLine, ...]]
    #: Confirmed commitments with no day that can be placed.
    unplaced: Structural[int]
    free: Total[Money | None]
    free_short: Structural[bool]
    free_said: Structural[str]
    #: A card's declared limit in force today, or None where there is none.
    limit: Total[Money | None] = None


@dataclass(frozen=True)
class FigureTotal:
    """A total over the accounts its figure was made for."""

    #: The signed sum (`Money` holds the sum's sign; `direction` says which way it sits).
    amount: Total[Money | None]
    direction: Structural[str]
    counted: Structural[int]
    of: Structural[int]

    @property
    def left_out(self) -> int:
        return int(self.of) - int(self.counted)


@dataclass(frozen=True)
class FreeFigures:
    as_of: Structural[str]
    accounts: Structural[tuple[AccountFigures, ...]]
    held: Structural[FigureTotal]
    owed: Structural[FigureTotal]
    committed: Structural[FigureTotal]
    free: Structural[FigureTotal]


def _ago(day: date, today: date) -> str:
    days = (today - day).days
    if days <= 0:
        return "today" if days == 0 else day.isoformat()
    return "yesterday" if days == 1 else f"{days} days ago"


def held_basis(account: AccountPosition, today: date) -> str:
    """How a known balance is known, in the account's newest known balance's own words."""
    if not account.anchor_day:
        return "the transactions alone"
    day = date.fromisoformat(account.anchor_day)
    if account.anchor_basis == STATED:
        said = f"stated by you on {day.isoformat()}"
    elif account.anchor_basis in (STATEMENT, STATEMENT_OPENING):
        said = f"statement to {day.isoformat()}"
    elif account.anchor_basis in (BANK, EXPORT):
        said = f"feed {_ago(day, today)}"
    else:
        said = f"{account.anchor_basis} on {day.isoformat()}"
    if account.rows_through and account.rows_through > account.anchor_day:
        said += f", and the transactions to {account.rows_through}"
    return said


def _unknown_basis(account: AccountPosition) -> str:
    since = f" since {account.first_row}" if account.first_row else ""
    why = (
        "no opening balance could be derived"
        if account.state == "withheld"
        else "no balance has been stated for it"
    )
    return f"not known{since}: {why}"


@dataclass(frozen=True)
class _Next:
    on: date | None
    source: str


def _earliest(days: Sequence[date]) -> date | None:
    return min(days) if days else None


def _open_window(commitment: Commitment) -> Window | None:
    window = commitment.current
    return window if window is not None and window.to_day is None else None


def _next_income(
    refs: Sequence[str],
    commitments: Sequence[Commitment],
    detected: Sequence[Series],
    today: date,
) -> _Next:
    """The earliest next income paid into any of `refs`: from confirmed income commitments if any
    gives a day, else from the detector's income series that have not stopped."""
    confirmed = []
    for commitment in commitments:
        window = _open_window(commitment)
        if commitment.direction == "in" and commitment.account in refs and window is not None:
            due = next_due(window, today)
            if due is not None:
                confirmed.append(due)
    if confirmed:
        return _Next(min(confirmed), CONFIRMED)
    seen = [
        max(s.next_expected, today)
        for s in detected
        if s.is_income and not s.stopped and s.direction == "in" and s.account in refs
    ]
    found = _earliest(seen)
    return _Next(found, DETECTED if found is not None else NONE)


def wants_detector(
    position: Position, commitments: Sequence[Commitment], *, today: date
) -> bool:
    """Whether the detector's income series would be read: some account has a confirmed outgoing
    commitment and no confirmed income to count it to. Asked first, so a household that has
    confirmed its incomes (or nothing) does not pay for a whole-table detection on every GET."""
    outgoing = {
        c.account for c in commitments if c.direction == "out" and _open_window(c) is not None
    }
    cards = {a.ref for a in _live(position) if owes_kind(a.kind)}
    household_income = _next_income([c.account for c in commitments], commitments, (), today)
    for ref in outgoing:
        if ref in cards:
            if household_income.on is None:
                return True
        elif _next_income([ref], commitments, (), today).on is None:
            return True
    return False


def _live(position: Position) -> list[AccountPosition]:
    counted = [a for g in position.groups for a in g.accounts if not a.archived]
    return [*counted, *(a for a in position.uncounted if not a.archived)]


def _income_said(found: _Next, today: date, *, card: bool) -> str:
    where = "any account" if card else "this account"
    if found.on is None:
        return (
            f"No income is confirmed or detected into {where}, so there is no next income to "
            "count commitments to."
        )
    if found.source == CONFIRMED:
        return f"Next income {found.on.isoformat()}, from a confirmed income."
    late = " (late: counted to today)" if found.on == today else ""
    return (
        f"No income is confirmed; the next expected by rhythm is {found.on.isoformat()}{late}."
    )


def _figures_of(
    account: AccountPosition,
    commitments: Sequence[Commitment],
    detected: Sequence[Series],
    household: _Next,
    limits: Mapping[str, DeclaredLimit],
    today: date,
) -> AccountFigures:
    card = owes_kind(account.kind)
    known = account.balance is not None
    minor = account.balance.minor if account.balance is not None else 0
    income = household if card else _next_income([account.ref], commitments, detected, today)
    mine = [
        (c, w)
        for c in commitments
        if c.account == account.ref and c.direction == "out"
        for w in [_open_window(c)]
        if w is not None
    ]
    lines: list[DueLine] = []
    unplaced = 0
    for commitment, window in mine:
        due = next_due(window, today)
        if due is None:
            unplaced += 1
        elif income.on is not None and due < income.on:
            amount = Money(window.amount_minor, CURRENCY)
            lines.append(DueLine(commitment.name, due.isoformat(), amount))
    lines.sort(key=lambda line: (line.due, line.name))
    income_said = _income_said(income, today, card=card)
    committed: Money | None
    if income.on is None:
        committed = None
        committed_said = income_said
    else:
        before = income.on.isoformat()
        committed = Money(sum(line.amount.minor for line in lines), CURRENCY)
        if not mine:
            committed_said = (
                f"No commitment is confirmed on this account, so none is counted before {before}."
            )
        elif lines:
            committed_said = f"{len(lines)} confirmed to leave before {before}."
        else:
            committed_said = f"Nothing confirmed is due before {before}."
    owed = Money(max(-minor, 0), CURRENCY) if card and known else None
    free: Money | None = None
    short = False
    limit = limits.get(account.ref) if card else None
    if card:
        if not known:
            free_said = "Free: cannot be worked out, the balance owed is not known."
        elif limit is None:
            free_said = "No limit declared."
        else:
            left = limit.amount_minor - max(-minor, 0)
            free, short = Money(abs(left), CURRENCY), left < 0
            since = f" in force from {limit.since.isoformat()}" if limit.since else ""
            free_said = f"The limit you declared{since}, less what is owed."
    elif not known:
        free_said = "Cannot be worked out: the balance is not known."
    elif committed is None:
        free_said = "Cannot be worked out: there is no next income to count commitments to."
    else:
        left = minor - committed.minor
        free, short = Money(abs(left), CURRENCY), left < 0
        free_said = "Held less committed." if not short else "Held less committed: short of it."
    return AccountFigures(
        ref=account.ref,
        label=account.label,
        is_card=card,
        held_basis=held_basis(account, today) if known else _unknown_basis(account),
        held_known=known,
        held=Money(abs(minor), CURRENCY) if known else None,
        held_direction=direction_of(minor) if known else "",
        owed=owed,
        income_from=income.source,
        income_on=income.on.isoformat() if income.on else "",
        income_said=income_said,
        committed=committed,
        committed_said=committed_said,
        due=tuple(lines),
        unplaced=unplaced,
        free=free,
        free_short=short,
        free_said=free_said,
        limit=Money(limit.amount_minor, CURRENCY) if limit is not None else None,
    )


def _total(values: Sequence[int | None], of: int) -> FigureTotal:
    made = [v for v in values if v is not None]
    return FigureTotal(
        Money(sum(made), CURRENCY) if made else None,
        direction_of(sum(made)) if made else "",
        len(made),
        of,
    )


def build_free(
    position: Position,
    commitments: Sequence[Commitment],
    detected: Sequence[Series],
    *,
    today: date,
    limits: Mapping[str, DeclaredLimit] | None = None,
) -> FreeFigures:
    """The four figures for every live (not archived) account and their totals.

    `detected` are the detector's series, read only where `wants_detector` said so; handing none
    is the honest answer that no income was detected."""
    wanted = limits or {}
    live = _live(position)
    household = _next_income(
        sorted({a.ref for a in live} | {c.account for c in commitments}),
        commitments,
        detected,
        today,
    )
    accounts = tuple(_figures_of(a, commitments, detected, household, wanted, today) for a in live)
    plain = [a for a in accounts if not a.is_card]
    cards = [a for a in accounts if a.is_card]

    def signed(a: AccountFigures) -> int | None:
        if a.held is None:
            return None
        return -a.held.minor if a.held_direction == "out" else a.held.minor

    return FreeFigures(
        as_of=today.isoformat(),
        accounts=accounts,
        held=_total([signed(a) for a in plain], len(plain)),
        owed=_total([a.owed.minor if a.owed else None for a in cards], len(cards)),
        committed=_total(
            [a.committed.minor if a.committed else None for a in accounts], len(accounts)
        ),
        free=_total(
            [
                (-a.free.minor if a.free_short else a.free.minor) if a.free else None
                for a in accounts
            ],
            len(accounts),
        ),
    )
