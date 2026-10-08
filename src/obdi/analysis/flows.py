"""How a commitment's money moves, leg by leg, and what the household's spaces must hold.

A commitment's FLOW is its legs (`commitment_records.Leg`): money from one end to another by a day
of the month, an amount or a share of the commitment's. This module lays each leg on the days it
falls due, finds the transaction that met it, and says which did not happen.

THE MATCHER asks what the detector's occurrences ask, and reuses their placing and tolerances
rather than keeping its own: the days a payment falls due are `free_position.due_days`; an amount
meets a leg when it is within the detector's `CHANGE_PERCENT` of what the leg expects; and a
transaction meets it when it is on the leg's held account, moves money the leg's way, and is to or
from the leg's entity (`Mover.party`, named by the detector's own naming) or, for a move between
two held accounts, is the transfer to the other one (`Mover.other`). A transaction on a day from
`EARLY_DAYS` before the leg's day to its tolerance and `LATE_DAYS` after is on its day, so a
transfer made early is met and one that arrives a few days after it was reported missing is met
too. A transaction meets one leg only, the nearest in time first.

AN INSTANCE IS PENDING until its day and tolerance have passed, MISSING after, but only where the
account's transactions reach past that day: an account whose transactions stop earlier has not
shown the leg did not happen, and the sentence says so. An EXTERNAL leg (no held account at either
end) never becomes an instance: it is a fact declared and is never checked or reported.

A LEG WHOSE ENTITY IS NOT KNOWN is not a fault of the matcher: no transaction carries an entity the
detector has never gathered a name under, so the leg stays pending and then missing, and its
sentence says that no payment is attributed to that entity yet. Naming the payer on the Entities
page is what lets it match.

THE SPACE'S NEED is the sum over commitments whose flow stashes a share in a space (a leg between
two held accounts) of the stash, for the draw-downs still to come in the month: the month of
today until the 25th, the month after from the 25th (the plan's "from the 25th of the month
before"). It is funded when the space holds at least that, and its surplus is what it holds beyond
it. The earliest draw-down day is the day it is needed by.

NOT HANDLED: a stash between two held accounts is met only where the transfer is a confirmed pair
or states the other account's identifier (`Mover.other`); an unpaired stash reads as missing once
its day passes. A leg has no end date of its own: it follows its commitment's windows.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ..core.masking import Structural, Total
from ..ingest.commitment_records import Commitment, Leg, Window
from ..read.account_about import ExpectedFold, ExpectedLine
from ..read.ledger import Money
from .free_position import CURRENCY, AccountFigures, due_days
from .ownership import scaled, share_words
from .recurring import CHANGE_PERCENT, Mover

MATCHED = "matched"
PENDING = "pending"
MISSING = "missing"

INCOMING = "incoming"
OUTGOING = "outgoing"
STASH = "stash"

#: How many days before its day a leg's transaction may be and still be it: a transfer made ahead
#: of "by the 28th".
EARLY_DAYS = 14
#: How many days after its tolerance a transaction may arrive and still meet a leg already reported
#: missing, so a late payment closes what it was late for.
LATE_DAYS = 7
#: How far from today a leg's day is looked at: past it, a missing leg stops being news.
LOOKBACK_DAYS = 45
LOOKAHEAD_DAYS = 45
#: From this day of the month, the space's need is next month's.
LOOKS_AHEAD_FROM_DAY = 25

_NO_PAYER_YET = "No payment is attributed to that entity yet, so nothing can be matched to it."
_NO_SUCH_ENTITY = "That entity no longer exists, so nothing can be matched to it."
_NO_ENTITY = "No one is named at the other end, so nothing can be matched to it."
_NOT_HELD = "The account holds no transactions, so whether this happened is not known."
_NOT_REACHED = (
    "The account's transactions run to {reach}, before this was due, so it may have happened and "
    "not yet been seen."
)


@dataclass(frozen=True)
class LegInstance:
    """One leg on one of its days: what it expects, and what became of it."""

    #: The commitment's name, and the entity at the far end: a payee and a person, so values.
    commitment: str
    party: str
    kind: Structural[str]
    #: "half" and so on where the leg is a share, else "".
    share_word: Structural[str]
    #: "October": the month of the payment the leg belongs to.
    month: Structural[str]
    due: Structural[str]
    state: Structural[str]
    matched_on: Structural[str]
    account: Structural[str]
    #: Where a stash is meant to arrive (a space), else "".
    space: Structural[str]
    amount: Total[Money]
    #: A sentence that holds no name and no amount: why the leg cannot be judged, or "".
    said: Structural[str]


@dataclass(frozen=True)
class NeedLine:
    """One commitment's stash in a space, and the day it is drawn down."""

    commitment: str
    day: Structural[str]
    amount: Total[Money]


@dataclass(frozen=True)
class SpaceNeed:
    """What a space must hold for the draw-downs still to come, against what it holds."""

    ref: Structural[str]
    label: Structural[str]
    needed: Total[Money]
    held: Total[Money | None]
    held_known: Structural[bool]
    #: The earliest draw-down day, ISO.
    by: Structural[str]
    funded: Structural[bool]
    #: What it holds beyond the need, nil where it holds no more.
    surplus: Total[Money]
    lines: Structural[tuple[NeedLine, ...]]


@dataclass(frozen=True)
class OwedLine:
    """Money owed to the household: who owes it, for what, by when, and how much."""

    who: str
    what: str
    due: Structural[str]
    amount: Total[Money]
    overdue: Structural[bool]
    #: `LEG` for a share of a commitment, `TRANSACTION` for one declared on a transaction.
    source: Structural[str]


LEG = "leg"
TRANSACTION = "transaction"


@dataclass(frozen=True)
class FlowReading:
    """Everything the legs say, read once for the pages that show it."""

    legs: Structural[tuple[LegInstance, ...]]
    spaces: Structural[tuple[SpaceNeed, ...]]
    owed: Structural[tuple[OwedLine, ...]]
    owed_total: Total[Money]
    #: How many legs are missing; the count is structure and is what Today's rail counts.
    missing: Structural[int]


NOTHING = FlowReading((), (), (), Money(0, CURRENCY), 0)


def has_checked_legs(commitments: Sequence[Commitment]) -> bool:
    """Whether any commitment declares a leg that is checked (not external): the cheap question
    asked before the transactions are read for the matcher."""
    return any(not leg.external for c in commitments for leg in c.legs)


def month_end(day: date) -> date:
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def leg_due(occurrence: date, leg: Leg) -> date:
    """The day a leg is due by, for the payment due on `occurrence`: `leg.day` of the month
    `leg.months_before` before it, clamped to the month's length."""
    index = occurrence.year * 12 + occurrence.month - 1 - leg.months_before
    year, month = divmod(index, 12)
    return date(year, month + 1, min(leg.day, calendar.monthrange(year, month + 1)[1]))


def leg_amount(leg: Leg, window: Window) -> int:
    """What the leg expects: its fixed amount, or its share of the window's."""
    if leg.amount_minor is not None:
        return leg.amount_minor
    return scaled(window.amount_minor, leg.share_percent or 0)


def amount_meets(actual_minor: int, expected_minor: int) -> bool:
    """Whether an amount is the one expected, within the detector's tolerance on a price."""
    return abs(actual_minor - expected_minor) * 100 <= CHANGE_PERCENT * expected_minor


def kind_of(leg: Leg) -> str:
    if leg.incoming:
        return INCOMING
    return STASH if leg.between_accounts else OUTGOING


@dataclass(frozen=True)
class _Pending:
    """A leg on one of its days, before it is matched."""

    commitment: Commitment
    leg: Leg
    occurrence: date
    due: date
    expected: int


def _instances_of(
    commitments: Sequence[Commitment], today: date
) -> list[_Pending]:
    first, last = today - timedelta(days=LOOKBACK_DAYS), today + timedelta(days=LOOKAHEAD_DAYS)
    found: list[_Pending] = []
    for commitment in commitments:
        checked = [leg for leg in commitment.legs if not leg.external]
        if not checked:
            continue
        for window in commitment.windows:
            # A payment's legs fall in the month before it as well, so the payments are looked for
            # a month either side of the days the legs are looked at.
            reach = timedelta(days=31)
            for occurrence in due_days(window, first - reach, last + reach):
                for leg in checked:
                    due = leg_due(occurrence, leg)
                    if first <= due <= last:
                        found.append(
                            _Pending(commitment, leg, occurrence, due, leg_amount(leg, window))
                        )
    found.sort(key=lambda p: (p.due, p.commitment.id, p.leg.position, p.leg.id))
    return found


def _why_unmatchable(leg: Leg, names: Mapping[int, str], seen: set[int]) -> str:
    if leg.between_accounts:
        return ""
    if leg.party is None:
        return _NO_ENTITY
    if leg.party not in names:
        return _NO_SUCH_ENTITY
    if leg.party not in seen:
        return _NO_PAYER_YET
    return ""


def evaluate_legs(
    commitments: Sequence[Commitment],
    movers: Sequence[Mover],
    names: Mapping[int, str],
    today: date,
) -> list[LegInstance]:
    """Every checked leg on each of its days near `today`, matched against the transactions.

    `names` is each entity's name by id. Instances are returned in the order of their days."""
    pending = _instances_of(commitments, today)
    if not pending:
        return []
    by_account: dict[str, list[Mover]] = defaultdict(list)
    reach: dict[str, date] = {}
    seen: set[int] = set()
    for mover in movers:
        by_account[mover.account].append(mover)
        if mover.day > reach.get(mover.account, date.min):
            reach[mover.account] = mover.day
        if mover.party:
            seen.add(mover.party)
    used: set[str] = set()
    found: list[LegInstance] = []
    for item in pending:
        leg = item.leg
        outgoing = bool(leg.from_account)
        best: tuple[int, int, str, date] | None = None
        for mover in by_account.get(leg.held_account, ()):
            if mover.row in used or (mover.amount_minor < 0) != outgoing:
                continue
            if leg.between_accounts:
                if mover.other != leg.to_account:
                    continue
            elif leg.party is None or mover.party != leg.party:
                continue
            if not amount_meets(abs(mover.amount_minor), item.expected):
                continue
            offset = (mover.day - item.due).days
            if offset < -EARLY_DAYS or offset > leg.tolerance_days + LATE_DAYS:
                continue
            candidate = (
                abs(offset),
                abs(abs(mover.amount_minor) - item.expected),
                mover.row,
                mover.day,
            )
            if best is None or candidate < best:
                best = candidate
        said = ""
        matched_on = ""
        state = PENDING
        if best is not None:
            used.add(best[2])
            state, matched_on = MATCHED, best[3].isoformat()
        else:
            deadline = item.due + timedelta(days=leg.tolerance_days)
            held_reach = reach.get(leg.held_account)
            if today > deadline:
                if held_reach is None:
                    said = _NOT_HELD
                elif held_reach < deadline:
                    said = _NOT_REACHED.format(reach=held_reach.isoformat())
                else:
                    state = MISSING
            said = said or _why_unmatchable(leg, names, seen)
        share = share_words(leg.share_percent) if leg.share_percent is not None else ""
        found.append(
            LegInstance(
                commitment=item.commitment.name,
                party=names.get(leg.party, "") if leg.party is not None else "",
                kind=kind_of(leg),
                share_word=share,
                month=calendar.month_name[item.occurrence.month],
                due=item.due.isoformat(),
                state=state,
                matched_on=matched_on,
                account=leg.held_account,
                space=leg.to_account if leg.between_accounts else "",
                amount=Money(item.expected, CURRENCY),
                said=said,
            )
        )
    return found


def missing_sentence(
    kind: str, commitment: str, party: str, share_word: str, month: str, due: str, space: str
) -> str:
    """The sentence for a leg that did not happen. The names are passed in as the page shows them
    (masked on a masked page); `space` is the account a stash was meant to reach, said as the page
    names accounts. An incoming leg is a receivable not yet received, an outgoing leg is the
    payment that did not go out, and a stash is the move to a space that did not happen."""
    if kind == INCOMING:
        what = f"{party}'s {share_word}" if share_word else f"{party}'s payment"
        return f"{what} for {month} has not arrived (expected by {due})."
    if kind == STASH:
        return (
            f"Nothing has moved to {space} for {month}'s {commitment} (expected by {due})."
        )
    return f"{month}'s {commitment} did not go out on {due}."


def space_needs(
    commitments: Sequence[Commitment],
    accounts: Mapping[str, AccountFigures],
    today: date,
) -> list[SpaceNeed]:
    """What each space must hold for the draw-downs still to come, from the commitments' stash
    legs: the sum by space, its earliest draw-down day, and whether what it holds covers it."""
    ahead = today.day >= LOOKS_AHEAD_FROM_DAY
    next_first = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    # From the 25th the month asked of is the next one alone: what is still to be drawn this month
    # was asked of before, and counting it again would ask the space for two months at once.
    first_day = next_first if ahead else today
    target_end = month_end(next_first if ahead else today)
    wanted: dict[str, list[tuple[Commitment, int, date]]] = defaultdict(list)
    for commitment in commitments:
        window = commitment.current
        if window is None or window.to_day is not None:
            continue
        for leg in commitment.legs:
            if not leg.between_accounts:
                continue
            for day in due_days(window, first_day, target_end):
                wanted[leg.to_account].append((commitment, leg_amount(leg, window), day))
    found: list[SpaceNeed] = []
    for ref, items in sorted(wanted.items()):
        figures = accounts.get(ref)
        total = sum(amount for _c, amount, _d in items)
        held: Money | None = None
        if figures is not None and figures.held_known and figures.held is not None:
            signed = -figures.held.minor if figures.held_direction == "out" else figures.held.minor
            held = Money(signed, CURRENCY)
        funded = held is not None and held.minor >= total
        surplus = Money(max(held.minor - total, 0) if held is not None else 0, CURRENCY)
        lines = tuple(
            NeedLine(c.name, d.isoformat(), Money(amount, CURRENCY))
            for c, amount, d in sorted(items, key=lambda i: (i[2], i[0].name))
        )
        found.append(
            SpaceNeed(
                ref=ref,
                label=figures.label if figures is not None else ref,
                needed=Money(total, CURRENCY),
                held=held,
                held_known=held is not None,
                by=min(d for _c, _a, d in items).isoformat(),
                funded=funded,
                surplus=surplus,
                lines=lines,
            )
        )
    return found


def expected_for(ref: str, reading: FlowReading) -> ExpectedFold | None:
    """What `ref` is expected to hold or receive, from the flows already read: the stashes its
    space's bills ask of it with the need, the holding, and the surplus beyond; and the shares
    others owe it that have not arrived. None where nothing touches the account."""
    lines: list[ExpectedLine] = []
    need = next((n for n in reading.spaces if n.ref == ref), None)
    if need is not None:
        lines.extend(
            ExpectedLine(line.commitment, "", line.day, line.amount.minor, STASH)
            for line in need.lines
        )
    for item in reading.legs:
        if item.kind == INCOMING and item.account == ref and item.state != MATCHED:
            lines.append(
                ExpectedLine(
                    item.commitment,
                    item.party,
                    item.due,
                    item.amount.minor,
                    "in",
                    late=item.state == MISSING,
                )
            )
    if need is None and not lines:
        return None
    lines.sort(key=lambda line: (line.day, line.what))
    return ExpectedFold(
        lines=tuple(lines),
        needed_minor=need.needed.minor if need is not None else None,
        held_minor=need.held.minor if need is not None and need.held is not None else None,
        surplus_minor=need.surplus.minor if need is not None else 0,
        by=need.by if need is not None else "",
    )


def read_flows(
    commitments: Sequence[Commitment],
    movers: Sequence[Mover],
    names: Mapping[int, str],
    accounts: Mapping[str, AccountFigures],
    today: date,
) -> FlowReading:
    """The legs matched, the spaces' needs, and what is owed to the household, as of `today`: an
    incoming leg is owed from its day, not before, and no longer once it has arrived.

    `accounts` are Position's own figures by account reference (`FreeFigures.accounts`): a space's
    holding is read from them and never worked out again."""
    instances = evaluate_legs(commitments, movers, names, today)
    spaces = space_needs(commitments, accounts, today)
    owed = [
        OwedLine(
            who=item.party or "someone",
            what=f"{item.commitment} for {item.month}",
            due=item.due,
            amount=item.amount,
            overdue=item.state == MISSING,
            source=LEG,
        )
        for item in instances
        if item.kind == INCOMING and item.state != MATCHED and date.fromisoformat(item.due) <= today
    ]
    return FlowReading(
        legs=tuple(instances),
        spaces=tuple(spaces),
        owed=tuple(owed),
        owed_total=Money(sum(line.amount.minor for line in owed), CURRENCY),
        missing=sum(item.state == MISSING for item in instances),
    )
