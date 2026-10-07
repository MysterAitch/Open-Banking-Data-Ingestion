"""What recurs in the transactions held: payments, transfers, and income that come round again.

A MEASUREMENT, not a declaration. This reads the transactions and reports what looks like a
series; nothing is stored, and nothing here says a person has agreed that it is one. The owner
reads the findings against his own knowledge to judge whether finding them is a good enough way
in before anything is declared (`docs/design/2026-10-commitments/notes.md` says what is decided
and what cannot be told).

A SERIES is the transactions of one account that share a payee shape and a direction, coming
round on a regular cadence at least `MIN_OCCURRENCES` times. The payee shape is
`identity.normalise_description` (the normaliser the matcher's content key already uses) with
every word that holds a digit taken out, since a reference, a mandate number, or a card tail is
what changes between two sightings of one payee. Two payees that merely share words have
different shapes and are never joined.

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

WHEN NO WHOLE GROUP FITS the transactions of the group are tried again by exact amount, so that
a fixed monthly charge among a payee's random purchases, or two subscriptions to one payee at
different prices, are found by the one thing that tells them apart.

A TRANSFER is a movement the provider calls internal or the pairing pass proved. A pair of legs
is one series, reported under the account the money left, with the account it went to beside it;
the opposite leg is not an occurrence of its own.

STOPPED is judged against the newest day the account holds, capped at today, because a feed
that stopped arriving says nothing about what stopped being paid.

AN AMOUNT'S DRIFT is a percentage, which is not a figure: it says how far the latest amount sits
from the usual, never what either is.
"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from statistics import median_low

from .identity import normalise_description
from .models import Transaction, TransactionStatus

#: Fewest occurrences that make a series. Two is a coincidence of a payee and a gap.
MIN_OCCURRENCES = 3

#: Of the slots a series should have filled between its first and last occurrence, the share that
#: must have been filled. Below it the gaps are the finding and "regular" is not.
MIN_PRESENT_SHARE = 2 / 3

#: How far the latest amount may sit from the usual before the series is said to have changed,
#: and how near two amounts must be to count as one usual amount.
CHANGE_PERCENT = 3.0

#: Of all the amounts, the share the usual amount's cluster must hold for the series to be
#: steady. A bill that is different every time is "varies", and has no change to report.
STEADY_SHARE = 0.5

#: Of the occurrences of a weekly or four-weekly series, the share on its commonest weekday.
WEEKDAY_SHARE = 0.75

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

    account: str
    #: Where a transfer's money went; empty for anything else, and for a transfer seen as one leg.
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
    #: Expected occurrences between the first and the last that were not seen.
    missed: int
    first_seen: date
    last_seen: date
    next_expected: date
    is_transfer: bool
    is_income: bool
    stopped: bool
    changed: bool


@dataclass(frozen=True)
class _Fit:
    cadence: str
    usual_day: int
    usual_month: int
    weekday: int | None
    missed: int
    next_expected: date


def _words_of(text: str) -> str:
    """The payee shape: the normalised description without any word that holds a digit."""
    return " ".join(
        w for w in normalise_description(text).split() if not any(c.isdigit() for c in w)
    )


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _on_day(index: int, day: int) -> date:
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day, calendar.monthrange(year, month + 1)[1]))


def _fit_days(days: Sequence[date], cadence: str, period: int) -> _Fit | None:
    steps = [(b - a).days for a, b in pairwise(days)]
    missed = 0
    for gap in steps:
        slots = round(gap / period)
        if slots < 1 or abs(gap - slots * period) > 1:
            return None
        missed += slots - 1
    if len(days) / (len(days) + missed) < MIN_PRESENT_SHARE:
        return None
    weekdays = Counter(d.weekday() for d in days)
    weekday, on_it = weekdays.most_common(1)[0]
    if on_it / len(days) < WEEKDAY_SHARE:
        return None
    return _Fit(cadence, 0, 0, weekday, missed, days[-1] + timedelta(days=period))


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
    for before, after in pairwise(slots):
        step, left = divmod(after - before, period)
        if step < 1 or left:
            return None
        missed += step - 1
    if len(days) / (len(days) + missed) < MIN_PRESENT_SHARE:
        return None
    month = (slots[0] % 12) + 1 if cadence == "yearly" else 0
    return _Fit(cadence, usual, month, None, missed, _on_day(slots[-1] + period, usual))


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


@dataclass(frozen=True)
class _Leg:
    row: Transaction
    #: The account the money went to, where the row is the leaving leg of a proved pair.
    other: str


def _series_of(
    legs: Sequence[_Leg],
    fit: _Fit,
    *,
    shape: str,
    reach: dict[str, date],
    today: date,
) -> Series:
    newest = legs[-1].row
    magnitudes = [abs(leg.row.amount_minor) for leg in legs]
    usual, steady = _usual_amount(magnitudes)
    latest = magnitudes[-1]
    drift = (latest - usual) / usual * 100 if usual else 0.0
    transfers = sum(
        1 for leg in legs if leg.other or leg.row.is_internal_transfer or leg.row.transfer_confirmed
    )
    is_transfer = transfers * 2 >= len(legs)
    direction = "in" if newest.amount_minor > 0 else "out"
    horizon = min(today, reach.get(newest.account_id, today))
    stopped = horizon > fit.next_expected + timedelta(days=_GRACE_DAYS[fit.cadence])
    return Series(
        account=newest.account_id,
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
        missed=fit.missed,
        first_seen=legs[0].row.value_date,
        last_seen=newest.value_date,
        next_expected=fit.next_expected,
        is_transfer=is_transfer,
        is_income=direction == "in" and not is_transfer,
        stopped=stopped,
        changed=steady and abs(drift) > CHANGE_PERCENT,
    )


def _series_in_group(
    legs: list[_Leg], shape: str, reach: dict[str, date], today: date
) -> list[Series]:
    """The series one group holds: the whole group if it fits, otherwise each exact amount."""
    legs.sort(key=lambda leg: (leg.row.value_date, leg.row.entity_id))
    if len(legs) < MIN_OCCURRENCES:
        return []
    fit = _fit([leg.row.value_date for leg in legs])
    if fit is not None:
        return [_series_of(legs, fit, shape=shape, reach=reach, today=today)]
    by_amount: dict[int, list[_Leg]] = defaultdict(list)
    for leg in legs:
        by_amount[leg.row.amount_minor].append(leg)
    found: list[Series] = []
    for same in by_amount.values():
        if len(same) < MIN_OCCURRENCES:
            continue
        fit = _fit([leg.row.value_date for leg in same])
        if fit is not None:
            found.append(_series_of(same, fit, shape=shape, reach=reach, today=today))
    return found


def _counts(row: Transaction) -> bool:
    return not row.status.is_history and row.status is not TransactionStatus.PENDING


def find_recurring(
    transactions: Iterable[Transaction],
    pairs: Iterable[tuple[str, str]],
    today: date,
) -> list[Series]:
    """Every series the transactions hold, by account and then by what they are called.

    `pairs` is the pairing pass's (leaving entity, arriving entity) for each proved transfer.
    Pending and history rows (void, folded, reversed) are not occurrences: a pending row will be
    replaced by its settlement, and history is not money.
    """
    rows = [row for row in transactions if _counts(row)]
    by_entity = {row.entity_id: row for row in rows}
    arriving: dict[str, Transaction] = {}
    for leaving_id, arriving_id in pairs:
        if leaving_id in by_entity and arriving_id in by_entity:
            arriving[leaving_id] = by_entity[arriving_id]
    arrivals = {row.entity_id for row in arriving.values()}

    reach: dict[str, date] = {}
    for row in rows:
        if row.value_date > reach.get(row.account_id, date.min):
            reach[row.account_id] = row.value_date

    groups: dict[tuple[str, ...], list[_Leg]] = defaultdict(list)
    shapes: dict[tuple[str, ...], str] = {}
    for row in rows:
        if row.entity_id in arrivals:
            continue
        direction = "in" if row.amount_minor > 0 else "out"
        opposite = arriving.get(row.entity_id)
        if opposite is not None:
            between = ("transfer", row.account_id, opposite.account_id, row.currency, direction)
            shapes[between] = ""
            groups[between].append(_Leg(row, opposite.account_id))
            continue
        shape = _words_of(row.description)
        if not shape:
            continue
        payee = ("payee", row.account_id, shape, row.currency, direction)
        shapes[payee] = shape
        groups[payee].append(_Leg(row, ""))

    found: list[Series] = []
    for key, legs in groups.items():
        found.extend(_series_in_group(legs, shapes[key], reach, today))
    found.sort(key=lambda s: (s.account, s.label.casefold(), s.cadence, s.usual_minor))
    return found
