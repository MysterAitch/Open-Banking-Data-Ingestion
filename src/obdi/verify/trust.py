"""How far an account can be trusted: the stretches of its history, the marks on them, and the
one sentence that says it.

ONE IMPLEMENTATION. Today reads it now; an account's page and Bring in read it later. It reads no
store and holds no figure: dates and counts in, dates and words out, so it says the same thing
masked or not.

THE RUNGS, as far as the code can establish them. There are three, and a bare line for the fourth:

- NOTHING HELD: no transaction is held for the stretch. Drawn as the bare line every bar has.
- HELD: transactions are held and nothing has tested them yet: they come after the last day that
  adds up, or the account has no known balance to add up to.
- ADDS UP: the transactions add up to the known balances (`agreement`: every known balance met, at
  least one tested, no two sources in conflict, the movement checks holding). A person has not
  vouched for anything: obdi worked it out, by arithmetic, against the bank's own figures.
- LOCKED IN: a person accepted the stretch that adds up (`protection`), so a later change to it is
  reported loudly. Locking in is offered only for days that add up, so a locked stretch lies inside
  the stretch that adds up.

Days tested by a statement's own listing count as adding up (`Agreement.listing_tested`): the
Agreement's `through` is already set for an account tested only so, and its tested days begin on
the statement's first day and not on the day of its closing balance. The earlier proof rail drew
such an account as wholly unchecked.

"Up to date" and "complete" are NOT rungs: the code cannot prove either as a property of a
stretch. What it can say it knows of is a MARK: a file wanted for some days (`fetch_gaps`), and
"no mark" is not proof that nothing is missing.

THE SCALE IS SHARED. Every bar runs over the same last twelve months ending today, so two accounts
with the same dates draw bars of the same widths and a bar's length is never a comparison of
histories. An account younger than the window shows its bare line before its first transaction;
older history is a mark at the left edge (`Trust.earlier`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from ..core.page_times import date_with_age
from .standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    AccountStanding,
    verification_of,
)

#: The days a bar spans: the last twelve months ending today, today included.
WINDOW_DAYS = 365

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: A month name this close to the window's left edge would print over the next one's.
_CROWDED_DAYS = 6

#: A month name starting further right than this percent would run off the bar's right edge.
_RIGHT_LIMIT = 94.0


class Rung(StrEnum):
    HELD = "held"
    ADDS_UP = "adds-up"
    LOCKED = "locked"


class MarkKind(StrEnum):
    #: The transactions stop adding up from this day.
    NOT_ADDING_UP = "not-adding-up"
    #: A stretch that was locked in has changed since.
    LOCK_CHANGED = "lock-changed"
    #: A file is wanted for these days.
    FILE_WANTED = "file-wanted"


@dataclass(frozen=True)
class Stretch:
    """The days `start` to `end` inclusive, at one rung."""

    rung: Rung
    start: date
    end: date


@dataclass(frozen=True)
class Mark:
    kind: MarkKind
    start: date
    end: date


@dataclass(frozen=True)
class Trust:
    """An account's trust: what to draw, and what to say."""

    #: Disjoint, in date order, and none drawn where nothing is held.
    stretches: tuple[Stretch, ...]
    marks: tuple[Mark, ...]
    #: The whole sentence, for an account's page and the key.
    sentence: str
    #: The shortened sentence for a list row: what is locked and what adds up, without the wait,
    #: which the row's own slot says beside the account's name.
    short: str
    nothing_held: bool
    locked_to: date | None = None
    adds_up_to: date | None = None
    #: The first day of what is held with nothing yet testing it, when something is.
    waiting_since: date | None = None
    #: Whether history before the window began is held, so the bar draws a mark at its left edge.
    earlier: bool = False


def _iso(day: date) -> str:
    return day.isoformat()


def _stretches(
    first: date,
    newest: date,
    adds_from: date | None,
    through: date | None,
    locked_to: date | None,
) -> tuple[Stretch, ...]:
    """The disjoint stretches, each rung drawn only where no higher one covers the day."""
    found: list[Stretch] = []
    one = timedelta(days=1)
    top = max((d for d in (through, locked_to) if d is not None), default=None)
    lower = adds_from if through is not None and adds_from is not None else None
    if lower is not None and first < lower:
        found.append(Stretch(Rung.HELD, first, min(lower - one, newest)))
    if locked_to is not None:
        start = lower if lower is not None else first
        found.append(Stretch(Rung.LOCKED, min(start, locked_to), locked_to))
    if through is not None and lower is not None:
        start = lower if locked_to is None else locked_to + one
        if start <= through:
            found.append(Stretch(Rung.ADDS_UP, start, through))
    after = first if top is None else top + one
    if after <= newest:
        found.append(Stretch(Rung.HELD, after, newest))
    return tuple(s for s in found if s.start <= s.end)


def trust_of(
    *,
    first: date | None,
    newest: date | None,
    standing: AccountStanding | None,
    wanted: Sequence[tuple[date, date]] = (),
    today: date,
    closed: date | None = None,
) -> Trust:
    """The trust of one account.

    `first` and `newest` are its first and newest transaction days (None where nothing is held),
    `standing` what `standing_data` read of it (None where that could not be read, which says
    nothing rather than something false), `wanted` the days a file is wanted for, `closed` the
    day an archived account closed, where known. A balance stated after the close (a document
    issued later still printing the account's balance) is one the account adds up to, and the
    sentence says so beside the close rather than naming a day after it as if the account ran on.
    """
    marks = [Mark(MarkKind.FILE_WANTED, start, end) for start, end in sorted(wanted)]
    if first is None or newest is None:
        return Trust((), tuple(marks), "Nothing held yet.", "Nothing held yet.", True)
    own = standing.standing.own if standing is not None else None
    through = own.through if own is not None else None
    adds_from = None
    if own is not None and through is not None:
        starts = [own.known_from, *(t.start for t in own.listing_tested)]
        adds_from = min((d for d in starts if d is not None), default=None)
        if adds_from is not None:
            adds_from = max(adds_from, first)
    locked_to = standing.protected_through if standing is not None else None
    broken = standing.protection_broken if standing is not None else False
    verdict = verification_of(standing)

    stretches = _stretches(first, newest, adds_from, through, locked_to)
    if own is not None and own.held is not None:
        marks.append(Mark(MarkKind.NOT_ADDING_UP, own.held.day, own.held.day))
    if locked_to is not None and broken:
        marks.append(Mark(MarkKind.LOCK_CHANGED, locked_to, locked_to))

    top = max((d for d in (through, locked_to) if d is not None), default=None)
    waiting_since = first if top is None else (top if newest > top else None)

    parts: list[str] = []
    if locked_to is not None:
        parts.append(
            f"Locked in to {_iso(locked_to)}, but that stretch has changed since."
            if broken
            else f"Locked in to {_iso(locked_to)}."
        )
    if through is not None and (locked_to is None or through > locked_to):
        if closed is not None and through > closed:
            parts.append(
                f"{ADDS_UP.capitalize()} to every known balance through its close on "
                f"{_iso(closed)}, and to one stated after it, on {_iso(through)}."
            )
        else:
            parts.append(f"{ADDS_UP.capitalize()} to the known balances to {_iso(through)}.")
    short = list(parts)
    if verdict == DOES_NOT_ADD_UP:
        held = own.held.day if own is not None and own.held is not None else None
        said = (
            f"{DOES_NOT_ADD_UP.capitalize()} from {_iso(held)}."
            if held
            else (f"{DOES_NOT_ADD_UP.capitalize()}.")
        )
        parts.append(said)
        short.append(said)
    elif waiting_since is not None:
        if top is None:
            said = f"{NOTHING_TO_CHECK_AGAINST.capitalize()}."
            parts.append(said)
            short.append(said)
        else:
            parts.append(
                f"{NOTHING_TO_CHECK_AGAINST.capitalize()} since {date_with_age(top, today)}."
            )

    window_start = today - timedelta(days=WINDOW_DAYS - 1)
    return Trust(
        stretches=stretches,
        marks=tuple(marks),
        sentence=" ".join(parts),
        short=" ".join(short),
        nothing_held=False,
        locked_to=locked_to,
        adds_up_to=through,
        waiting_since=waiting_since,
        earlier=first < window_start,
    )


# ------------------------------------------------------------------------------ The shared scale


@dataclass(frozen=True)
class Placed:
    """Where something is drawn on the scale, as percentages of the bar's width."""

    left: float
    width: float


def window_start(today: date) -> date:
    return today - timedelta(days=WINDOW_DAYS - 1)


def place(
    start: date, end: date, today: date, span: tuple[date, date] | None = None
) -> Placed | None:
    """The days `start` to `end` inclusive on the scale, or None where they lie outside it.

    The scale is the shared twelve months ending `today`, or with `span` the days from its first
    to its last (an account closed before the shared months begin is drawn over its own life, by
    the same drawing on another scale). A day is a whole step of the scale, so on the shared one
    a day is `100 / WINDOW_DAYS` percent wide.
    """
    begin, last = span if span is not None else (window_start(today), today)
    days = (last - begin).days + 1
    low = max(start, begin)
    high = min(end, last)
    if high < low:
        return None
    left = (low - begin).days / days * 100
    width = ((high - low).days + 1) / days * 100
    return Placed(left, width)


def month_marks(today: date) -> tuple[tuple[str, float], ...]:
    """The month names to print once above a list of bars, each with its left edge in percent.

    The first month is named at the left edge, since the window begins part-way through it.
    """
    begin = window_start(today)
    marks: list[tuple[str, float]] = []
    day = date(begin.year + (begin.month == 12), begin.month % 12 + 1, 1)
    if (day - begin).days >= _CROWDED_DAYS:
        marks.append((_MONTHS[begin.month - 1], 0.0))
    while day <= today:
        left = (day - begin).days / WINDOW_DAYS * 100
        if left <= _RIGHT_LIMIT:
            marks.append((_MONTHS[day.month - 1], left))
        day = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return tuple(marks)
