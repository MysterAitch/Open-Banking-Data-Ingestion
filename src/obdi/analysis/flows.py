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
from ..ingest.receivable_records import WRITTEN_OFF, Receivable
from ..read.account_about import ExpectedFold, ExpectedLine
from ..read.ledger import Money
from .free_position import CURRENCY, AccountFigures, OwedLine, due_days
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
    #: What became of each receivable declared on a transaction, and the labels' years.
    receivables: Structural[tuple[ReceivableState, ...]] = ()
    labels: Structural[tuple[LabelLine, ...]] = ()


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
    return _evaluate(commitments, movers, names, today)[0]


def _evaluate(
    commitments: Sequence[Commitment],
    movers: Sequence[Mover],
    names: Mapping[int, str],
    today: date,
) -> tuple[list[LegInstance], set[str]]:
    """The legs of `evaluate_legs` and the transactions they used, so that a receivable on a
    transaction is not met by the very payment that met a leg."""
    pending = _instances_of(commitments, today)
    if not pending:
        return [], set()
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
    return found, used


OPEN = "open"
REIMBURSED = "reimbursed"


@dataclass(frozen=True)
class ReceivableState:
    """What became of one receivable: open, met by a transfer from the debtor (which one is kept,
    derived from the transactions on every read), or closed by hand with the owner's reason."""

    id: Structural[int]
    row_ref: Structural[str]
    #: `OPEN`, `REIMBURSED`, or a way of closing by hand (`receivable_records.CLOSE_HOWS`).
    state: Structural[str]
    #: The transaction that met it and its day, for `REIMBURSED`.
    closed_by: Structural[str]
    closed_on: Structural[str]
    #: The owner's words for closing it by hand: a value.
    reason: str
    overdue: Structural[bool]
    #: Why nothing can meet it yet (the debtor has no payments attributed), or "".
    why: Structural[str]


@dataclass(frozen=True)
class LabelLine:
    """What one label comes to this year: the receivables carrying it, what has been reimbursed
    (by a transfer or received elsewhere), what is still owed, and what was written off."""

    label: str
    total: Total[Money]
    reimbursed: Total[Money]
    owed: Total[Money]
    written_off: Total[Money]
    #: Whether anything was written off, which is a fact about the label and not an amount.
    has_written_off: Structural[bool] = False


def settle_receivables(
    receivables: Sequence[Receivable],
    movers: Sequence[Mover],
    names: Mapping[int, str],
    today: date,
    *,
    used: set[str] | None = None,
) -> list[ReceivableState]:
    """Whether each receivable has been met, in the order given.

    A receivable is met by money IN, from its debtor (the entity its payments are gathered
    under), within the detector's tolerance of its amount, on or after the day it was spent. Oldest
    first, each takes the nearest-priced such transfer not already taken, so one transfer closes
    the older of two receivables from one entity and never both, and a transfer from another
    entity closes nothing however well its amount fits. A receivable closed by hand takes no
    transfer."""
    taken = set(used or ())
    seen = {m.party for m in movers if m.party}
    ordered = sorted(
        (r for r in receivables if not r.closed_how), key=lambda r: (r.day, r.id)
    )
    met: dict[int, Mover] = {}
    for item in ordered:
        best: tuple[int, date, str] | None = None
        chosen: Mover | None = None
        for mover in movers:
            if (
                mover.row in taken
                or mover.amount_minor <= 0
                or mover.party != item.debtor
                or mover.day < item.day
                or not amount_meets(mover.amount_minor, item.amount_minor)
            ):
                continue
            rank = (abs(mover.amount_minor - item.amount_minor), mover.day, mover.row)
            if best is None or rank < best:
                best, chosen = rank, mover
        if chosen is not None:
            taken.add(chosen.row)
            met[item.id] = chosen
    states = []
    for item in receivables:
        paid_by = met.get(item.id)
        if item.closed_how:
            states.append(
                ReceivableState(
                    id=item.id,
                    row_ref=item.row_ref,
                    state=item.closed_how,
                    closed_by="",
                    closed_on=item.closed_at[:10],
                    reason=item.closed_reason,
                    overdue=False,
                    why="",
                )
            )
        elif paid_by is not None:
            states.append(
                ReceivableState(
                    id=item.id,
                    row_ref=item.row_ref,
                    state=REIMBURSED,
                    closed_by=paid_by.row,
                    closed_on=paid_by.day.isoformat(),
                    reason="",
                    overdue=False,
                    why="",
                )
            )
        else:
            why = ""
            if item.debtor not in names:
                why = _NO_SUCH_ENTITY
            elif item.debtor not in seen:
                why = _NO_PAYER_YET
            states.append(
                ReceivableState(
                    id=item.id,
                    row_ref=item.row_ref,
                    state=OPEN,
                    closed_by="",
                    closed_on="",
                    reason="",
                    overdue=today > item.expected_day,
                    why=why,
                )
            )
    return states


def label_lines(
    receivables: Sequence[Receivable], states: Sequence[ReceivableState], today: date
) -> list[LabelLine]:
    """Each label's year to date: what the receivables carrying it come to, reimbursed, owed, and
    written off. A label is one label however it is cased; receivables with none are not here,
    and neither are those spent in another year."""
    by_id = {state.id: state for state in states}
    sums: dict[str, list[int]] = {}
    shown: dict[str, str] = {}
    for item in receivables:
        if not item.label or item.day.year != today.year:
            continue
        key = item.label.casefold()
        shown.setdefault(key, item.label)
        total = sums.setdefault(key, [0, 0, 0, 0])
        total[0] += item.amount_minor
        state = by_id[item.id].state
        slot = 2 if state == OPEN else 3 if state == WRITTEN_OFF else 1
        total[slot] += item.amount_minor
    return [
        LabelLine(
            shown[key],
            Money(sums[key][0], CURRENCY),
            Money(sums[key][1], CURRENCY),
            Money(sums[key][2], CURRENCY),
            Money(sums[key][3], CURRENCY),
            has_written_off=sums[key][3] > 0,
        )
        for key in sorted(sums)
    ]


def label_sentence(label: str, total: str, reimbursed: str, owed: str) -> str:
    """"volunteering this year: £N, of which £M reimbursed, £K owed", the figures as the page
    shows them."""
    return f"{label} this year: {total}, of which {reimbursed} reimbursed, {owed} owed"


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
    receivables: Sequence[Receivable] = (),
) -> FlowReading:
    """The legs matched, the spaces' needs, and what is owed to the household, as of `today`: an
    incoming leg is owed from its day, not before, and no longer once it has arrived; a receivable
    declared on a transaction is owed from the day it was declared until a transfer from its debtor
    meets it or the owner closes it.

    `accounts` are Position's own figures by account reference (`FreeFigures.accounts`): a space's
    holding is read from them and never worked out again."""
    instances, used = _evaluate(commitments, movers, names, today)
    spaces = space_needs(commitments, accounts, today)
    states = settle_receivables(receivables, movers, names, today, used=used)
    by_id = {r.id: r for r in receivables}
    owed_on_transactions = [
        OwedLine(
            who=names.get(by_id[state.id].debtor, "someone"),
            what=by_id[state.id].label or f"an expense on {by_id[state.id].day.isoformat()}",
            due=by_id[state.id].expected_day.isoformat(),
            amount=Money(by_id[state.id].amount_minor, CURRENCY),
            overdue=state.overdue,
            source=TRANSACTION,
            receivable=state.id,
        )
        for state in states
        if state.state == OPEN
    ]
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
    owed.extend(owed_on_transactions)
    return FlowReading(
        legs=tuple(instances),
        spaces=tuple(spaces),
        owed=tuple(owed),
        owed_total=Money(sum(line.amount.minor for line in owed), CURRENCY),
        missing=sum(item.state == MISSING for item in instances),
        receivables=tuple(states),
        labels=tuple(label_lines(receivables, states, today)),
    )
