"""Where each goal stands: how much of it is done, how much is left, and whether that is ahead of
or behind a straight line from the day it was declared to the day it is wanted by.

A goal is the owner's (`ingest.goal_records`); this module reads it against what Position holds
and keeps nothing. It is the accrual engine `plan.md` section 8 item 5 asks for, made general: the
same straight line that spreads a prepaid domain over its months (worked example A: 108 over 36
months is 3.00 a month) spreads a debt to clear, a fund to build, and a saving over theirs.

THE LINE is whole calendar months. A goal declared on a day with a date `t` months on is at
`k / t` of the way after `k` whole months, and the amount on the line is `amount * k // t` in
pence, so 1,200.00 over twelve months is 100.00 a month and 400.00 after four, not 404.38 by the
day count. Whole months are what the owner reckons in, and what a standing order is.

  CLEAR  achieved is the owed balance when declared less the owed balance now; the line is the
         share of what was owed when declared that should be cleared by now.
  BUILD  achieved is what the account holds towards it; the line runs from what it held when
         declared to the target.
  SAVE   achieved is what the account holds towards it; the line runs from nothing to the target,
         since a saving is the amount SET ASIDE from the declaration on and not the balance the
         account already had.

AHEAD IS ON OR ABOVE THE LINE. A goal exactly on its line is "ahead by nil", and the words are the
owner's: a goal is ahead or behind, and neither is a fault. A goal with no date has no line and so
no stance; one whose balance (or starting balance) is unknown has none either, and says why.

SEVERAL GOALS ON ONE ACCOUNT are funded in the order they were declared. Each takes from what the
account holds what it is owed so far (its line, or its whole target where it has no date); what a
goal is shown as holding is what is left for it after the earlier ones, so two goals cannot both
claim the same pound. A debt takes nothing from the balance, since it is about what is owed.

THIS MONTH'S SHARE is the step the line takes from this month's start to its end, the same whether
the goal is ahead or behind: catching up is a choice, not an obligation, and "the monthly rate
needed from now" is shown beside it.

NOT HANDLED, and said on the page where it matters: money moved between an account's goals is
not tracked (an account's balance is one figure); a goal's progress after its date has passed is
measured against the target, with no new line; interest on a debt is not projected.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ..core.masking import Structural, Total
from ..core.money import AmountParseError, parse_amount
from ..ingest.goal_records import BUILD, CLEAR, SAVE, Goal, GoalRefused
from ..ingest.store import Store
from ..read.ledger import Money
from ..read.position import AccountPosition, Position

CURRENCY = "GBP"

AHEAD = "ahead"
BEHIND = "behind"


def months_between(first: date, last: date) -> int:
    """Whole calendar months from `first` to `last`, never below nil. A month is complete on the
    same day of the month, or on the last day of a shorter month."""
    months = (last.year - first.year) * 12 + last.month - first.month
    complete = min(first.day, calendar.monthrange(last.year, last.month)[1])
    if last.day < complete:
        months -= 1
    return max(months, 0)


@dataclass(frozen=True)
class Line:
    """A goal's straight line, in whole months."""

    total: int
    elapsed: int

    @property
    def over(self) -> bool:
        return self.elapsed >= self.total


def line_of(declared_on: date, target_date: date, today: date) -> Line:
    total = max(months_between(declared_on, target_date), 1)
    return Line(total, min(months_between(declared_on, today), total))


@dataclass(frozen=True)
class GoalProgress:
    """One goal against the account it is about. The sentences hold no amount."""

    id: Structural[int]
    name: Structural[str]
    kind: Structural[str]
    account: Structural[str]
    account_label: Structural[str]
    #: ISO, or "" where the goal has none.
    target_date: Structural[str]
    declared_on: Structural[str]
    #: `AHEAD`, `BEHIND`, or "" where there is no line (no date, or something is not known).
    stance: Structural[str]
    #: Whether the target is reached.
    done: Structural[bool]
    #: Whether the progress could be measured: the balance (and, for a line, the starting
    #: balance) is known.
    measured: Structural[bool]
    #: A debt whose owed balance is above what it was when declared.
    slipped: Structural[bool]
    #: What is missing or what the stance rests on, with no amount; "" when there is nothing to say.
    said: Structural[str]
    #: Whole months from today to the date, nil once it has passed, or 0 for a goal with no date.
    months_left: Structural[int]
    target: Total[Money]
    #: What a debt owed, or a fund held, when the goal was declared; None where not known.
    start: Total[Money | None]
    #: Owed now (a debt) or held towards it (a fund or saving); None where not known.
    now: Total[Money | None]
    #: Cleared, built, or set aside so far; for a slipped debt, how much MORE is owed than when
    #: declared.
    achieved: Total[Money | None]
    to_go: Total[Money | None]
    #: The monthly amount that reaches the target by its date from here; None with no date.
    per_month: Total[Money | None]
    #: How far ahead of or behind the line, as a magnitude (`stance` says which).
    gap: Total[Money | None]
    #: This month's step along the line; None with no line or once the goal is done or over.
    share: Total[Money | None]


@dataclass(frozen=True)
class GoalAccount:
    """An account a goal can be about: the live accounts Position lists."""

    ref: Structural[str]
    label: Structural[str]


@dataclass(frozen=True)
class GoalsView:
    as_of: Structural[str]
    goals: Structural[tuple[GoalProgress, ...]]
    #: The sum of this month's shares, and how many goals it is made from.
    share_total: Total[Money | None]
    sharing: Structural[int]
    #: The accounts the Add form offers.
    accounts: Structural[tuple[GoalAccount, ...]] = ()


def _accounts_of(position: Position) -> dict[str, AccountPosition]:
    found = {a.ref: a for g in position.groups for a in g.accounts}
    found.update({a.ref: a for a in position.uncounted})
    return found


def _step(start: int, end: int, line: Line) -> tuple[int, int]:
    """The line's value now and its step over this month, between `start` and `end` amounts."""
    now = start + (end - start) * line.elapsed // line.total
    nxt = start + (end - start) * (line.elapsed + 1) // line.total
    return now, nxt - now


def _ceil_div(value: int, by: int) -> int:
    return -(-value // by)


@dataclass
class _Draft:
    """The numbers of one goal before they are sealed into a `GoalProgress`."""

    now: int | None = None
    achieved: int | None = None
    to_go: int | None = None
    per_month: int | None = None
    gap: int | None = None
    share: int | None = None
    stance: str = ""
    done: bool = False
    measured: bool = False
    slipped: bool = False
    said: str = ""
    #: What the goal is owed from the account so far, taken off what later goals see.
    claim: int = 0


def _dated(
    draft: _Draft, by: date, today: date, line_now: int, step: int, line: Line
) -> None:
    """The stance, rate, and share of a goal wanted by `by` whose line value now is `line_now`."""
    if draft.achieved is None or draft.to_go is None:
        return
    left = max(months_between(today, by), 1) if today < by else 0
    if draft.to_go and left:
        draft.per_month = _ceil_div(draft.to_go, left)
    if draft.done:
        return
    signed = -draft.achieved if draft.slipped else draft.achieved
    draft.gap = signed - line_now
    draft.stance = AHEAD if draft.gap >= 0 else BEHIND
    draft.gap = abs(draft.gap)
    draft.share = None if line.over else max(step, 0)


def _clear(goal: Goal, balance: int | None, today: date) -> _Draft:
    draft = _Draft()
    if balance is None:
        draft.said = "The balance owed is not known, so progress cannot be measured."
        return draft
    owed = max(-balance, 0)
    start = goal.start_minor or 0
    draft.measured, draft.now = True, owed
    cleared = start - owed
    draft.slipped = cleared < 0
    draft.achieved = abs(cleared)
    draft.to_go = owed
    draft.done = owed == 0
    if goal.target_date is not None and start:
        line = line_of(goal.declared_on, goal.target_date, today)
        done_now, step = _step(0, start, line)
        _dated(draft, goal.target_date, today, done_now, step, line)
    return draft


def _held(
    goal: Goal, balance: int | None, remaining: int | None, today: date
) -> tuple[_Draft, int]:
    """A fund or a saving: what is held towards it after the goals before it, and the claim it
    makes on the account in turn."""
    draft = _Draft()
    if balance is None or remaining is None:
        draft.said = "The balance held is not known, so progress cannot be measured."
        return draft, 0
    towards = min(max(remaining, 0), goal.target_minor)
    draft.measured, draft.now, draft.achieved = True, towards, towards
    draft.to_go = max(goal.target_minor - towards, 0)
    draft.done = towards >= goal.target_minor
    wanted = goal.target_minor
    if goal.target_date is not None:
        line = line_of(goal.declared_on, goal.target_date, today)
        if goal.kind == SAVE:
            start = 0
        elif goal.start_minor is None:
            start = None
        else:
            start = goal.start_minor
        if start is None:
            draft.said = (
                "The balance it started from was not known, so there is no line to be ahead "
                "of or behind."
            )
        else:
            line_now, step = _step(start, goal.target_minor, line)
            wanted = line_now
            _dated(draft, goal.target_date, today, line_now, step, line)
    return draft, min(towards, wanted)


def _progress(
    goal: Goal, draft: _Draft, account: AccountPosition | None, today: date
) -> GoalProgress:
    def money(minor: int | None) -> Money | None:
        return None if minor is None else Money(minor, CURRENCY)

    return GoalProgress(
        id=goal.id,
        name=goal.name,
        kind=goal.kind,
        account=goal.account,
        account_label=account.label if account is not None else goal.account,
        target_date=goal.target_date.isoformat() if goal.target_date else "",
        declared_on=goal.declared_on.isoformat(),
        stance=draft.stance,
        done=draft.done,
        measured=draft.measured,
        slipped=draft.slipped,
        said=draft.said,
        months_left=(
            max(months_between(today, goal.target_date), 0)
            if goal.target_date is not None and today < goal.target_date
            else 0
        ),
        target=Money(goal.target_minor, CURRENCY),
        start=money(goal.start_minor if goal.kind != SAVE else None),
        now=money(draft.now),
        achieved=money(draft.achieved),
        to_go=money(draft.to_go),
        per_month=money(draft.per_month),
        gap=money(draft.gap),
        share=money(draft.share),
    )


def build_goals(goals: Sequence[Goal], position: Position, *, today: date) -> GoalsView:
    """Every goal's progress against the balances in `position`, in the order declared, and the
    sum of this month's shares."""
    accounts = _accounts_of(position)
    remaining: dict[str, int | None] = {}
    made: list[GoalProgress] = []
    for goal in sorted(goals, key=lambda g: g.id):
        account = accounts.get(goal.account)
        balance = account.balance.minor if account and account.balance is not None else None
        if account is None:
            draft = _Draft(said="The account is not among those Position holds.")
        elif goal.kind == CLEAR:
            draft = _clear(goal, balance, today)
        else:
            left = remaining.setdefault(goal.account, balance)
            draft, claim = _held(goal, balance, left, today)
            if left is not None:
                remaining[goal.account] = left - claim
        made.append(_progress(goal, draft, account, today))
    shares = [int(p.share.minor) for p in made if p.share is not None and p.share.minor]
    live = sorted(
        (a for a in accounts.values() if not a.archived), key=lambda a: (a.label.casefold(), a.ref)
    )
    return GoalsView(
        as_of=today.isoformat(),
        goals=tuple(made),
        share_total=Money(sum(shares), CURRENCY) if shares else None,
        sharing=len(shares),
        accounts=tuple(GoalAccount(a.ref, a.label) for a in live),
    )


KINDS_WORDS = {
    CLEAR: "debt to clear",
    BUILD: "fund to build",
    SAVE: "saving",
}

#: The presses the Goals page makes.
ACT_ADD = "add"
ACT_EDIT = "edit"
ACT_REMOVE = "remove"


def _field(form: dict[str, list[str]], name: str) -> str:
    return (form.get(name) or [""])[0].strip()


def _amount(text: str) -> int:
    try:
        return parse_amount(text)
    except AmountParseError as exc:
        raise GoalRefused("That is not an amount in pounds and pence.") from exc


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise GoalRefused("A date is written year-month-day, like 2027-01-15.") from exc


def _goal_id(form: dict[str, list[str]]) -> int:
    try:
        return int(_field(form, "goal"))
    except ValueError as exc:
        raise GoalRefused("There is no such goal; it may have been removed.") from exc


def _starting_balance(kind: str, account: AccountPosition | None) -> int | None:
    """What a goal starts from: the amount owed (a debt) or held (a fund), or None where the
    account's balance is not known. A saving starts from nothing set aside, whatever is held."""
    if kind == SAVE:
        return 0
    if account is None or account.balance is None:
        return None
    return max(-account.balance.minor, 0) if kind == CLEAR else max(account.balance.minor, 0)


def apply_press(
    store: Store,
    position: Position,
    action: str,
    form: dict[str, list[str]],
    *,
    today: date,
) -> str:
    """Do what one press on the Goals page asks, and say what was done in a sentence that holds
    no name and no amount. Refused with `GoalRefused` for an action that is not one, and for
    whatever the action itself refuses, with nothing changed."""
    if action == ACT_ADD:
        kind, account = _field(form, "kind"), _field(form, "account")
        given, by = _field(form, "amount"), _field(form, "date")
        if account not in _accounts_of(position):
            raise GoalRefused("A goal is about an account you hold; there is no such account.")
        store.declare_goal(
            _field(form, "name"),
            kind=kind,
            account=account,
            target_minor=_amount(given) if given else 0,
            target_date=_day(by) if by else None,
            declared_on=today,
            start_minor=_starting_balance(kind, _accounts_of(position).get(account)),
        )
        return "Goal added."
    if action == ACT_EDIT:
        given, by = _field(form, "amount"), _field(form, "date")
        store.edit_goal(
            _goal_id(form),
            name=_field(form, "name") or None,
            target_minor=_amount(given) if given else None,
            target_date=_day(by) if by else None,
            no_date=_field(form, "no_date") == "1",
        )
        return "Goal changed."
    if action == ACT_REMOVE:
        store.remove_goal(_goal_id(form))
        return "Goal removed."
    raise GoalRefused("That is not something this page does.")
