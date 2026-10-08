"""What recurs in the transactions held: payments, transfers, and income that come round again.

A MEASUREMENT, not a declaration. This reads the transactions and reports what looks like a
series; nothing is stored, and nothing here says a person has agreed that it is one. The owner
reads the findings against his own knowledge to judge whether finding them is a good enough way
in before anything is declared (`docs/design/2026-10-commitments/notes.md` says what is decided
and what cannot be told).

A SERIES is the transactions, in any account, that share a payee shape and a direction, coming
round on a regular cadence, at least `MIN_OCCURRENCES` times and spanning `MIN_SPAN_SLOTS`
slots. It belongs to a payee and not to an
account: a subscription normally paid from one account and, this month, from another is one
series with an occurrence marked as paid from elsewhere (`Series.off_account`), never a missed
month followed by a new series. The series reports the account it is usually paid from.

The payee shape is `identity.normalise_description` (the normaliser the matcher's content key
already uses) with every word that holds a digit taken out, since a reference, a mandate
number, or a card tail is what changes between two sightings of one payee. Two payees that
merely share words have different shapes and are never joined.

CADENCE is decided from where the occurrences fall, never from a label:

- weekly, fortnightly, four-weekly: every gap between occurrences is a whole number of periods,
  within a day, so that a missed period (a gap of two) is a miss and not a break, and at least
  three quarters of the occurrences fall on one weekday;
- monthly, quarterly, yearly: the usual day is the commonest day of the month, and every
  occurrence is within a few days of that day in its own month, which allows the move to the
  next working day (or, for money in, the previous one) that a weekend causes, and which clamps
  a 31st to a short month.

In both families no more than a third of the expected occurrences may be missing, and two
occurrences in one expected slot are not a series (the same payee taking two payments in a
month is something else).

WHEN NO WHOLE GROUP FITS the transactions of the group are tried again by exact amount, then by
account, so that a fixed monthly charge among a payee's random purchases, or two subscriptions
to one payee at different prices (or, at one price, in different accounts), are found by the one
thing that tells them apart.

A TRANSFER is a movement the provider calls internal or the pairing pass proved. A pair of legs
is one series, reported under the account the money left, with the account it went to beside it;
the opposite leg is not an occurrence of its own.

A KIND says who starts the payments (`PULLED`, `SCHEDULED`, `HABIT`, decided in `_kind_of`).

STOPPED is judged, for pulled and scheduled series only, against the newest day the account
holds, capped at today, because a feed that stopped arriving says nothing about what stopped
being paid.

A PULLED PAYMENT CAN SKIP FOR A REASON THE STORE HOLDS. A card provider collects nothing when the
card was paid off and its statement closed at nil, so for a pulled series paid to a card account
(a transfer the pairing pass proved) a slot is EXPLAINED, and counts as neither missed nor
stopped, when that card's held statement for the cycle closed at exactly nil (`_nothing_due`).
Nothing else explains a slot yet: `docs/design/2026-10-commitments/notes.md` says what the
remaining cases need.

AN AMOUNT'S DRIFT is a percentage, which is not a figure: it says how far the latest amount sits
from the usual, never what either is.
"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from itertools import pairwise
from statistics import median_low

from ..core.models import Transaction, TransactionStatus
from ..ingest.commitment_records import Commitment, Dismissal
from ..ingest.stated_words import words_in
from .entities import HELD_PREFIX, LEARNED_RULE, Alias, display_names, entity_of, name_rows
from .payment_methods import METHODS

#: Fewest occurrences that make a series. Two is a coincidence of a payee and a gap.
MIN_OCCURRENCES = 3

#: Of the slots a series should have filled between its first and last occurrence, the share that
#: must have been filled. Below it the gaps are the finding and "regular" is not.
MIN_PRESENT_SHARE = 2 / 3

#: Fewest slots of its cadence a series must span, first occurrence to last, counting the slots
#: that were missed: three gaps, so four occurrences for an unbroken run and three seen over four
#: slots with one missed. The first measurement on the real store found "weekly, Tuesdays - 3
#: times over 2 weeks", which three occurrences in a fortnight made out to be a rhythm.
MIN_SPAN_SLOTS = 4

#: How far the latest amount may sit from the usual before the series is said to have changed,
#: and how near two amounts must be to count as one usual amount.
CHANGE_PERCENT = 3.0

#: Of all the amounts, the share the usual amount's cluster must hold for the series to be
#: steady. A bill that is different every time is "varies", and has no change to report.
STEADY_SHARE = 0.5

#: Of the occurrences of a weekly or four-weekly series, the share on its commonest weekday.
WEEKDAY_SHARE = 0.75

#: WHO STARTS A PAYMENT. PULLED: the other side collects on its own timetable (a Direct Debit, a
#: card subscription, a card provider taking what is owed). SCHEDULED: the owner set it up once
#: and the bank runs it (a standing order, a standing transfer). HABIT: the owner pays each time
#: by choice, so skipping a week or taking another route is not a missed payment. Only pulled and
#: scheduled series can stop or miss a period; a habit is a pattern with a share of periods seen.
PULLED = "pulled"
SCHEDULED = "scheduled"
HABIT = "habit"

#: The coded words a source states that decide a kind outright: (field, word) -> (kind, said as).
#: Taken from the words `stated_words.CODED_FIELDS` keeps. Measured on a real store: the feed's
#: `source` DIRECT_DEBIT and `sourceSubType` CARD_SUBSCRIPTION, and the aggregator's
#: `transaction_category` DIRECT_DEBIT. Not yet measured: the aggregator's STANDING_ORDER (its
#: documented word) and any word the bank's feed uses for a standing order, which is why a
#: standing order paid through the feed is told by its shape. A card or faster payment says
#: nothing about who started it, so it is not here.
#:
#: The coded words themselves are `payment_methods.METHODS`', shared with the Entities page, which
#: sets the same methods' printed phrases aside; this table only says what each decides.
_KIND_OF_METHOD: dict[str, tuple[str, str]] = {
    "direct-debit": (PULLED, "Direct Debit"),
    "card-subscription": (PULLED, "card subscription"),
    "standing-order": (SCHEDULED, "standing order"),
}
_TYPE_WORDS: dict[tuple[str, str], tuple[str, str]] = {
    pair: _KIND_OF_METHOD[method.key]
    for method in METHODS
    for pair in method.coded
}

#: WHICH DATE A RHYTHM IS MEASURED ON, decided by kind in `_series_of`. A habit's or a scheduled
#: payment's rhythm is when the owner acted, so it is fitted on the transaction date
#: (`Transaction.value_date`); a pulled payment's day is when the collector took it ("due on the
#: 27th"), so it is fitted on the posting date (`booking_date`). A card feed that posts a Sunday
#: payment on the Monday or Tuesday would otherwise spread a habit across the weekdays, and a
#: Direct Debit whose card statement lists it on the last day of the month would be fitted on
#: that day and not on the 1st it was taken.
DATED_MADE = "on the day the payment was made"
DATED_TAKEN = "on the day it was taken"
#: The source states no transaction date, only the day the payment posted (`Transaction.
#: states_transaction_date`), so a habit's rhythm here is the posting day's.
DATED_POSTED = "on the posting date, the only date its source states"
#: Every row of the series carries one date; there is no second to measure on.
DATED_ONE = "on the one date its rows state"

#: name -> (period in days, tolerance in days) for the cadences counted in days.
_DAY_CADENCES: tuple[tuple[str, int], ...] = (
    ("weekly", 7),
    ("fortnightly", 14),
    ("four-weekly", 28),
)
#: name -> (period in months, tolerance in days) for the cadences counted in calendar months.
_MONTH_CADENCES: tuple[tuple[str, int, int], ...] = (
    ("monthly", 1, 4),
    ("quarterly", 3, 5),
    ("yearly", 12, 7),
)
#: Days past the expected date before a series is called stopped: what a weekend, a bank holiday,
#: and a slow bank explain, which is longer for a longer period.
_GRACE_DAYS = {
    "weekly": 3,
    "fortnightly": 4,
    "four-weekly": 5,
    "monthly": 7,
    "quarterly": 10,
    "yearly": 14,
}


@dataclass(frozen=True)
class Series:
    """One recurring thing: what it is, how often, how much, and what is the matter with it."""

    #: The account most often paid from or into.
    account: str
    #: Occurrences paid from some other account: the series did not stop or start again, it was
    #: paid from elsewhere that time. Nonzero is the "account changed" mark.
    off_account: int
    #: Where a transfer's money went; empty for anything else, and for a transfer seen as one leg
    #: whose other side is no account (`held_account` is the account of a leg that states one).
    other_account: str
    #: The normalised payee the occurrences share; empty for a transfer found by its legs.
    shape: str
    #: The newest occurrence's description as the bank wrote it, a value to mask.
    label: str
    currency: str
    direction: str
    cadence: str
    #: The usual day of the month, or 0 for a weekly cadence.
    usual_day: int
    #: The usual month for a yearly series, otherwise 0.
    usual_month: int
    #: Monday is 0; None for a cadence counted in months.
    weekday: int | None
    #: Magnitudes in minor units: direction is `direction`, never a sign here.
    usual_minor: int
    min_minor: int
    max_minor: int
    latest_minor: int
    #: The latest amount against the usual, as a percentage: positive is larger.
    drift_percent: float
    #: Whether most amounts sit at the usual one; a bill that varies every time is not steady.
    steady: bool
    count: int
    #: PULLED, SCHEDULED, or HABIT, and the signal that decided it, said as the page says it:
    #: "by type: Direct Debit" or "by shape: steady amount, same day".
    kind: str
    basis: str
    #: Slots of the cadence from the first occurrence to the last, seen or not: the denominator of
    #: a habit's "38 of 52 weeks".
    periods: int
    #: Slots a pulled series did not fill because the card account it pays had nothing due for
    #: the cycle (`_nothing_due`), counting a slot after the last occurrence. Not missed.
    explained: int
    #: Expected occurrences between the first and the last that were not seen and that nothing
    #: held explains; always 0 for a habit, which is never missing a payment.
    missed: int
    first_seen: date
    last_seen: date
    next_expected: date
    is_transfer: bool
    is_income: bool
    stopped: bool
    changed: bool
    #: Which date the cadence was fitted on, said as a phrase (`DATED_MADE`, `DATED_TAKEN`,
    #: `DATED_POSTED`, or `DATED_ONE`), so a reader knows whether "most Sundays" is the day the
    #: owner paid or the day the bank posted it.
    dated_on: str = DATED_ONE
    #: For a payee-named series whose payee is one of the household's own accounts (an unpaired
    #: leg that states that account's identifier): that account, which a page shows as "your" and
    #: its label. `shape` is "your <account's name>" until a page has the label.
    held_account: str = ""
    #: The names the series' rows are called by (`name_rows`), which is how a commitment confirmed
    #: from it finds it again. One name for a series of one payee, several for an entity's, and
    #: `TRANSFER_NAME` plus the account the money went to for a transfer.
    name_keys: frozenset[str] = frozenset()
    #: The id of the entity the owner gathered the series' names under, or 0 where none.
    party_entity: int = 0
    #: The first day of the run of newest occurrences at the latest amount (within
    #: `CHANGE_PERCENT` of it), which is the day a changed price began; None where unknown.
    latest_from: date | None = None
    #: The days of the newest occurrences (at most `SEEN_DAYS_KEPT`), oldest first, on the date
    #: the cadence was fitted on. `This month` sets a commitment's due days against these to say
    #: which were paid; a weekly rhythm has several in a month, so the last day alone is not enough.
    seen_days: tuple[date, ...] = ()
    #: How many of the `count` payments were named by an inference from their description
    #: (`entities.LEARNED_RULE`), counted apart so the series never looks better evidenced.
    inferred: int = 0


#: How many of a series' newest occurrence days `Series.seen_days` keeps: enough to cover a month
#: of the shortest cadence (weekly) and the one before it.
SEEN_DAYS_KEPT = 10


@dataclass(frozen=True)
class _Fit:
    cadence: str
    usual_day: int
    usual_month: int
    weekday: int | None
    missed: int
    next_expected: date
    #: The date of each slot between the first and last occurrence that was not filled.
    missing: tuple[date, ...]
    #: The cadence's period, in days for a cadence counted in days and else in months.
    period_days: int
    period_months: int

    def after(self, slot: date) -> date:
        """The slot following `slot`."""
        if self.period_days:
            return slot + timedelta(days=self.period_days)
        return _on_day(_month_index(slot) + self.period_months, self.usual_day)


@dataclass(frozen=True)
class RecurringFindings:
    """What the detector found, and the day it judged "stopped" against."""

    series: list[Series]
    today: date
    #: The label of each account declared external, by its canonical name, for the transfers to
    #: accounts no held account stands for (`Series.other_account`, `Series.held_account`).
    external_labels: Mapping[str, str] = field(default_factory=dict)
    #: The commitments the owner confirmed, which the page sets the series against
    #: (`commitments.match_series`); the detector itself reads none.
    commitments: Sequence[Commitment] = ()
    #: The series the owner said are not commitments, which the page folds away and counts.
    dismissals: Sequence[Dismissal] = ()


def tolerance_days(cadence: str) -> int:
    """How many days off its usual day an occurrence of `cadence` may fall and still be on time:
    the tolerance the fit allows (a day for the cadences counted in days)."""
    for name, _months, tolerance in _MONTH_CADENCES:
        if name == cadence:
            return tolerance
    return 1


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _on_day(index: int, day: int) -> date:
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day, calendar.monthrange(year, month + 1)[1]))


def _spans_enough(seen: int, missed: int) -> bool:
    """Whether `seen` occurrences and `missed` empty slots make a run worth calling regular."""
    slots = seen + missed
    return slots >= MIN_SPAN_SLOTS and seen / slots >= MIN_PRESENT_SHARE


def _fit_days(days: Sequence[date], cadence: str, period: int) -> _Fit | None:
    missed = 0
    missing: list[date] = []
    for before, after in pairwise(days):
        gap = (after - before).days
        slots = round(gap / period)
        if slots < 1 or abs(gap - slots * period) > 1:
            return None
        missed += slots - 1
        missing += [before + timedelta(days=period * n) for n in range(1, slots)]
    if not _spans_enough(len(days), missed):
        return None
    weekdays = Counter(d.weekday() for d in days)
    weekday, on_it = weekdays.most_common(1)[0]
    if on_it / len(days) < WEEKDAY_SHARE:
        return None
    return _Fit(
        cadence,
        0,
        0,
        weekday,
        missed,
        days[-1] + timedelta(days=period),
        tuple(missing),
        period,
        0,
    )


def _fit_months(days: Sequence[date], cadence: str, period: int, tolerance: int) -> _Fit | None:
    counts = Counter(d.day for d in days)
    usual = min(counts, key=lambda day: (-counts[day], day))
    slots: list[int] = []
    for d in days:
        here = _month_index(d)
        nearest = min((here - 1, here, here + 1), key=lambda k: abs((d - _on_day(k, usual)).days))
        if abs((d - _on_day(nearest, usual)).days) > tolerance:
            return None
        slots.append(nearest)
    missed = 0
    missing: list[date] = []
    for before, after in pairwise(slots):
        step, left = divmod(after - before, period)
        if step < 1 or left:
            return None
        missed += step - 1
        missing += [_on_day(before + period * n, usual) for n in range(1, step)]
    if not _spans_enough(len(days), missed):
        return None
    month = (slots[0] % 12) + 1 if cadence == "yearly" else 0
    return _Fit(
        cadence,
        usual,
        month,
        None,
        missed,
        _on_day(slots[-1] + period, usual),
        tuple(missing),
        0,
        period,
    )


def _fit(days: Sequence[date]) -> _Fit | None:
    for cadence, period in _DAY_CADENCES:
        found = _fit_days(days, cadence, period)
        if found is not None:
            return found
    for cadence, months, tolerance in _MONTH_CADENCES:
        found = _fit_months(days, cadence, months, tolerance)
        if found is not None:
            return found
    return None


def _usual_amount(amounts: Sequence[int]) -> tuple[int, bool]:
    """The usual amount, and whether most amounts sit at it.

    Amounts within `CHANGE_PERCENT` of the first of a run of sorted amounts are one cluster; the
    largest cluster is the usual one, and of two as large the one seen first, because a price
    that held longest and then moved is the thing to report the move from.
    """
    order = sorted(range(len(amounts)), key=lambda i: amounts[i])
    clusters: list[list[int]] = []
    for index in order:
        if clusters and amounts[index] <= amounts[clusters[-1][0]] * (1 + CHANGE_PERCENT / 100):
            clusters[-1].append(index)
        else:
            clusters.append([index])
    biggest = max(clusters, key=lambda members: (len(members), -min(members)))
    usual = int(median_low([amounts[i] for i in biggest]))
    return usual, len(biggest) / len(amounts) >= STEADY_SHARE


#: What a transfer between two held accounts is called, followed by the account the money went to,
#: for the commitment confirmed from it to find it again.
TRANSFER_NAME = "transfer to "


@dataclass(frozen=True)
class _Leg:
    row: Transaction
    #: The account the money went to, where the row is the leaving leg of a proved pair.
    other: str
    #: The name `name_rows` gave the row, or `TRANSFER_NAME` and the other account for a transfer.
    name: str = ""
    #: Whether the row was named by an inference (`entities.LEARNED_RULE`) and not by anything its
    #: source states: a series resting on such rows says so.
    inferred: bool = False


#: How long before a slot the statement for its cycle may be dated: a card statement closes some
#: days before its payment is collected, and the next closes a month after.
_STATEMENT_LEAD_DAYS = 45

#: Per account, the closing balance each held statement states: (statement date, balance in the
#: store's sign, money owed negative). Read once for every account by the caller.
Closings = Mapping[str, Sequence[tuple[date, int]]]


def _nothing_due(card: Sequence[tuple[date, int]], slot: date) -> bool:
    """Whether the card's statement for the cycle `slot` collects was held and closed at nil.

    The cycle's statement is the latest one dated before the slot and no more than
    `_STATEMENT_LEAD_DAYS` earlier. NOT HELD IS NOT NIL: no such statement may mean none was
    issued (nothing owed, so none) or that it was never added, and the two cannot be told apart
    from what is held, so only a statement held at exactly nil explains a slot. A card in credit
    or with nothing claimed is left unexplained too, until a measurement says otherwise.
    """
    cycle = [
        (day, minor)
        for day, minor in card
        if slot - timedelta(days=_STATEMENT_LEAD_DAYS) <= day < slot
    ]
    if not cycle:
        return False
    return max(cycle)[1] == 0


def _kind_by_type(legs: Sequence[_Leg]) -> tuple[str, str] | None:
    """The kind the occurrences' stated types decide, and the type said, or None where none do.

    One statement on one occurrence is enough, since a feed and an aggregator do not each state
    a type for every row. Where occurrences state different types the commoner wins, and of two
    as common the one stated most recently.
    """
    stated: list[tuple[str, str]] = []
    for leg in legs:
        for pair in words_in(leg.row.source, leg.row.raw):
            if pair in _TYPE_WORDS:
                stated.append(_TYPE_WORDS[pair])
                break
    if not stated:
        return None
    counts = Counter(stated)
    best = max(counts.values())
    for found in reversed(stated):
        if counts[found] == best:
            return found
    return None  # pragma: no cover - the commonest is always among the stated


def _kind_of(
    legs: Sequence[_Leg], fit: _Fit, *, steady: bool, is_transfer: bool
) -> tuple[str, str]:
    """Who starts the payment, and which signal said so: the stated type, else the shape.

    By shape: a month-counted cadence is a payee collecting on its own day of the month, which a
    payment made by choice does not keep (a steady amount is a subscription, a varying one a
    bill or a card provider's collection), unless it is a transfer between the owner's own
    accounts, which he set running. A weekday rhythm is the owner's: a weekly series is a habit,
    and so is a fortnightly or four-weekly one that has gaps or varies; only an unbroken run of
    one amount every few weeks reads as pulled.
    """
    typed = _kind_by_type(legs)
    if typed is not None:
        return typed[0], f"by type: {typed[1]}"
    if fit.weekday is None:
        if is_transfer:
            return SCHEDULED, "by shape: transfer between own accounts, same day"
        if steady:
            return PULLED, "by shape: steady amount, same day"
        return PULLED, "by shape: same day each month, amount varies"
    if steady and fit.missed == 0 and fit.cadence != "weekly":
        return PULLED, "by shape: steady amount, same weekday every few weeks"
    return HABIT, "by shape: weekday rhythm, " + ("amounts vary" if not steady else "gaps allowed")


@dataclass(frozen=True)
class _Frame:
    """A group's legs in the order of one of their dates, and the cadence fitted on that date.

    `fit` is None where the dates do not keep a cadence. The two frames of a group are fitted
    before its kind is known, because the kind is read from the coded type words and, failing
    those, from the shape of whichever fit exists; the kind then says which frame is the series.
    """

    legs: Sequence[_Leg]
    fit: _Fit | None
    taken: bool


def _day_of(row: Transaction, frame: _Frame) -> date:
    """The row's date in the frame's terms: the posting date for a posting frame."""
    return row.booking_date if frame.taken else row.value_date


def _is_transfer(legs: Sequence[_Leg]) -> bool:
    transfers = sum(
        1 for leg in legs if leg.other or leg.row.is_internal_transfer or leg.row.transfer_confirmed
    )
    return transfers * 2 >= len(legs)


def _dating(chosen: _Frame, kind: str) -> str:
    """The phrase for the date `chosen` was fitted on, for a series of `kind`."""
    legs = chosen.legs
    if all(leg.row.booking_date == leg.row.value_date for leg in legs):
        posting_only = sum(not leg.row.states_transaction_date for leg in legs) * 2 > len(legs)
        return DATED_POSTED if posting_only else DATED_ONE
    if chosen.taken:
        return DATED_TAKEN
    posting_only = sum(not leg.row.states_transaction_date for leg in legs) * 2 > len(legs)
    return DATED_POSTED if posting_only and kind != PULLED else DATED_MADE


def _series_of(
    made: _Frame,
    taken: _Frame,
    *,
    shape: str,
    reach: dict[str, date],
    closings: Closings,
    today: date,
) -> Series:
    first_fit = made.fit or taken.fit
    if first_fit is None:  # pragma: no cover - the caller fits at least one frame
        raise ValueError("a series needs a cadence fitted on one of its dates")
    steady0 = _usual_amount([abs(leg.row.amount_minor) for leg in made.legs])[1]
    kind, basis = _kind_of(
        made.legs, first_fit, steady=steady0, is_transfer=_is_transfer(made.legs)
    )
    # The kind is read first (from the coded type words, else the shape of the first fit that
    # exists) and then says which date the rhythm is measured on; the other date is the fallback
    # where the preferred one keeps no cadence.
    frames = (taken, made) if kind == PULLED else (made, taken)
    chosen = frames[0] if frames[0].fit is not None else frames[1]
    fit = chosen.fit
    if fit is None:  # pragma: no cover - one frame fits, and the choice prefers one that does
        raise ValueError("a series needs a cadence fitted on one of its dates")
    legs = chosen.legs
    newest = legs[-1].row
    magnitudes = [abs(leg.row.amount_minor) for leg in legs]
    usual, steady = _usual_amount(magnitudes)
    latest = magnitudes[-1]
    drift = (latest - usual) / usual * 100 if usual else 0.0
    began = len(legs) - 1
    while began > 0 and abs(magnitudes[began - 1] - latest) <= latest * CHANGE_PERCENT / 100:
        began -= 1
    is_transfer = _is_transfer(legs)
    direction = "in" if newest.amount_minor > 0 else "out"
    paid_from = Counter(leg.row.account_id for leg in legs)
    # The account most often paid from; of two as common, the one paid from most recently.
    usual_account = max(
        paid_from,
        key=lambda ref: (
            paid_from[ref],
            max(leg.row.value_date for leg in legs if leg.row.account_id == ref),
        ),
    )
    # Where a series was paid from more than one account, none has stopped while any account it
    # uses still reaches a day past the expected one.
    horizon = min(today, max(reach.get(ref, today) for ref in paid_from))
    habit = kind == HABIT
    grace = timedelta(days=_GRACE_DAYS[fit.cadence])
    # A pulled series collected by a card account the store holds statements for: a slot the
    # payee did not take is explained where that card owed nothing for the cycle.
    explained_inside = 0
    explained_after = 0
    stopped = not habit and horizon > fit.next_expected + grace
    if kind == PULLED and legs[-1].other:
        card = closings.get(legs[-1].other, ())
        explained_inside = sum(_nothing_due(card, slot) for slot in fit.missing)
        trailing: list[date] = []
        slot = fit.next_expected
        while slot + grace < horizon:
            trailing.append(slot)
            slot = fit.after(slot)
        if trailing and all(_nothing_due(card, slot) for slot in trailing):
            stopped = False
            explained_after = len(trailing)
    return Series(
        account=usual_account,
        off_account=len(legs) - paid_from[usual_account],
        other_account=legs[-1].other,
        shape=shape,
        label=newest.description,
        currency=newest.currency,
        direction=direction,
        cadence=fit.cadence,
        usual_day=fit.usual_day,
        usual_month=fit.usual_month,
        weekday=fit.weekday,
        usual_minor=usual,
        min_minor=min(magnitudes),
        max_minor=max(magnitudes),
        latest_minor=latest,
        drift_percent=drift,
        steady=steady,
        count=len(legs),
        inferred=sum(leg.inferred for leg in legs),
        kind=kind,
        basis=basis,
        periods=len(legs) + fit.missed,        explained=explained_inside + explained_after,
        missed=0 if habit else fit.missed - explained_inside,
        first_seen=_day_of(legs[0].row, chosen),
        last_seen=_day_of(legs[-1].row, chosen),
        next_expected=fit.next_expected,
        is_transfer=is_transfer,
        is_income=direction == "in" and not is_transfer,
        stopped=stopped,
        changed=steady and abs(drift) > CHANGE_PERCENT,
        dated_on=_dating(chosen, kind),
        name_keys=frozenset(leg.name for leg in legs if leg.name),
        latest_from=_day_of(legs[began].row, chosen),
        seen_days=tuple(_day_of(leg.row, chosen) for leg in legs[-SEEN_DAYS_KEPT:]),
    )


def _series_in_group(
    legs: list[_Leg], shape: str, reach: dict[str, date], closings: Closings, today: date
) -> list[Series]:
    """The series one group holds: the whole group if it fits, otherwise each exact amount."""
    return _split(legs, shape, reach, closings, today, _SPLITS)


#: What a group that does not fit as a whole is divided by, in turn: account, then exact amount.
#: Only a group that already fits no cadence reaches here, so a payee paid from several accounts
#: on one rhythm is never divided (an occurrence from another account is marked, not lost). The
#: account comes first because gathering names into one payee (an entity, or a counterparty one
#: bank states for every account) put one employer's monthly varying pay beside its other
#: payments elsewhere; dividing by exact amount first left that pay in pieces of one or two,
#: and a series that was found before the gathering vanished (12 series to 11 on the large store).
#: A payee taking two payments a month into one account is then told apart by amount.
_SPLITS: tuple[Callable[[_Leg], object], ...] = (
    lambda leg: leg.row.account_id,
    lambda leg: leg.row.amount_minor,
)


def _split(
    legs: list[_Leg],
    shape: str,
    reach: dict[str, date],
    closings: Closings,
    today: date,
    splits: Sequence[Callable[[_Leg], object]],
) -> list[Series]:
    if len(legs) < MIN_OCCURRENCES:
        return []
    legs.sort(key=lambda leg: (leg.row.value_date, leg.row.entity_id))
    made = _Frame(legs, _fit([leg.row.value_date for leg in legs]), taken=False)
    taken = made
    if any(leg.row.booking_date != leg.row.value_date for leg in legs):
        posted = sorted(legs, key=lambda leg: (leg.row.booking_date, leg.row.entity_id))
        taken = _Frame(posted, _fit([leg.row.booking_date for leg in posted]), taken=True)
    if made.fit is not None or taken.fit is not None:
        return [_series_of(made, taken, shape=shape, reach=reach, closings=closings, today=today)]
    if not splits:
        return []
    parts: dict[object, list[_Leg]] = defaultdict(list)
    for leg in legs:
        parts[splits[0](leg)].append(leg)
    if len(parts) == 1:
        return _split(legs, shape, reach, closings, today, splits[1:])
    found: list[Series] = []
    for part in parts.values():
        found.extend(_split(part, shape, reach, closings, today, splits[1:]))
    return found


def counts_as_occurrence(row: Transaction) -> bool:
    """Whether the row is an occurrence of anything: not history, and not yet pending."""
    return not row.status.is_history and row.status is not TransactionStatus.PENDING


def find_recurring(
    transactions: Iterable[Transaction],
    pairs: Iterable[tuple[str, str]],
    today: date,
    closings: Closings | None = None,
    entities: Mapping[tuple[str, str], str] | None = None,
    links: Mapping[str, Alias] | None = None,
    external: Mapping[str, str] | None = None,
    entity_ids: Mapping[str, int] | None = None,
) -> list[Series]:
    """Every series the transactions hold, by account and then by what they are called.

    A row is called what `name_of` says: the account or id it states, else the counterparty it
    states, else the one learned for its description from rows seen by two sources (`links`, from
    `learned_links` over these rows when not given), else its description's shape. A payee seen
    through a feed in some months and statements in others is therefore one series. A series
    named by an identifier is shown by the readable name of its rows (`display_names`); one whose
    payee is a household account carries that account (`Series.held_account`) for the page to
    label.
    `pairs` is the pairing pass's (leaving entity, arriving entity) for each proved transfer, and
    `external` the identifier of each account the owner declared theirs that obdi holds no source
    for (`entities.held_counterparts`).
    `entities` maps an identifier (its kind and value, `entities.shape_entities`) to the name of
    the entity the owner gathered it under; a row finds its entity by the identifier its name
    and kind link through (`entities.entity_of`). The names of one entity are one payee here,
    so a subscription that changed the name it prints under, or alternates between two, is one
    series, named by the entity (`Series.shape`). A name under no entity is grouped as it is.
    `entity_ids` maps an entity's name to its id, which a series of that entity carries
    (`Series.party_entity`) for a confirmed commitment to find it by.
    Money in and money out stay apart, and a transfer is found by its legs, never by a name.
    `closings` is each account's held statement closings, which explain a pulled series' missed
    slot where the card it pays owed nothing.
    Pending and history rows (void, folded, reversed) are not occurrences: a pending row will be
    replaced by its settlement, and history is not money.
    """
    rows = [row for row in transactions if counts_as_occurrence(row)]
    pairs = list(pairs)
    fields, links, named = name_rows(rows, pairs, links, external=external)
    labels = display_names(fields, named)
    by_entity = {row.entity_id: row for row in rows}
    arriving: dict[str, Transaction] = {}
    for leaving_id, arriving_id in pairs:
        if leaving_id in by_entity and arriving_id in by_entity:
            arriving[leaving_id] = by_entity[arriving_id]
    arrivals = {row.entity_id for row in arriving.values()}

    reach: dict[str, date] = {}
    for row in rows:
        # The newest day the account holds on either date, since a series fitted on the posting
        # date is judged "stopped" against the newest posting as well as the newest purchase.
        newest = max(row.value_date, row.booking_date)
        if newest > reach.get(row.account_id, date.min):
            reach[row.account_id] = newest

    groups: dict[tuple[str, ...], list[_Leg]] = defaultdict(list)
    shapes: dict[tuple[str, ...], str] = {}
    held_of: dict[tuple[str, ...], str] = {}
    party_of: dict[tuple[str, ...], int] = {}
    for row, item in zip(rows, named, strict=True):
        if row.entity_id in arrivals:
            continue
        direction = "in" if row.amount_minor > 0 else "out"
        opposite = arriving.get(row.entity_id)
        if opposite is not None:
            between = ("transfer", row.account_id, opposite.account_id, row.currency, direction)
            shapes[between] = ""
            groups[between].append(
                _Leg(row, opposite.account_id, TRANSFER_NAME + opposite.account_id)
            )
            continue
        shape = item.name
        if not shape:
            continue
        linked = entity_of(entities or {}, item.kind, shape)
        gathered = None if linked is None else linked[1]
        if gathered is not None:
            payee = ("entity", gathered.casefold(), row.currency, direction)
            shapes[payee] = gathered
            if entity_ids and gathered in entity_ids:
                party_of[payee] = entity_ids[gathered]
        else:
            payee = ("payee", shape, row.currency, direction)
            # An identifier groups the rows and is never shown (`display_names`).
            shapes[payee] = labels.get(shape, shape)
            if shape.startswith(HELD_PREFIX):
                held_of[payee] = shape[len(HELD_PREFIX) :]
        # A leg to an account the household owns that no confirmed pair joined is still a
        # transfer to it: `other` is what makes the series one (`_is_transfer`), not income or a
        # payee, and a transfer to an account declared external (`held_counterparts`) has no
        # opposite leg to pair with at all.
        groups[payee].append(
            _Leg(row, held_of.get(payee, ""), shape, inferred=item.kind == LEARNED_RULE)
        )

    found: list[Series] = []
    for key, legs in groups.items():
        for series in _series_in_group(legs, shapes[key], reach, closings or {}, today):
            found.append(
                replace(
                    series,
                    held_account=held_of.get(key, series.held_account),
                    party_entity=party_of.get(key, 0),
                )
            )
    found.sort(key=lambda s: (s.account, s.label.casefold(), s.cadence, s.usual_minor))
    return found
