"""Where an account's transactions carry a stated other party, and where only the printed
description names it, as dates and counts.

This is the data behind the "Party stated" row of an account's coverage bars, the sentence that
says which months are named by the description only, and the export file Bring in asks for. It
holds no name and no amount: every rendering of it may be masked.

WHAT "STATED" MEANS, said here and nowhere else. A transaction carries a stated party when its
name is anything above the printed description in the ladder `analysis.entities` states: the
other party's account, a source's id for the party, the name a source states, a link learned from
rows two sources saw, or the stated name its description matches exactly. A transaction named by
its description's shape alone is DESCRIBED. The description stays in use for those rows (it is
the only field every source gives); this says only that no source stated the party, so the name
is the bank's print and not a fact the source gave. The decision is made where the names are
(`analysis.party_stated`), above this layer, which is why this module takes each transaction as a
day and a flag.

THE BAR is per day: a day is stated when every transaction of it is, described when any is, and
absent when it has none. Runs of the same state are joined across at most `MERGE_DAYS` days
without a transaction, so a quiet week does not become a dozen cells; the join draws over days
that hold nothing, at a scale where a day is a fraction of a pixel.

THE SENTENCE AND THE WANT are per MONTH, not per day, because the file that fixes it is asked for
by the month and a bank statement or export is a month's: a month is described when more of its
transactions are described than stated, and consecutive described months are one `Stretch`. A
feed account with a handful of unstated rows among hundreds of stated ones therefore draws its
hollow cells and says nothing, where an account whose statement-only months are named by
description alone is asked about. `Stretch.rows` counts every described transaction in the months
of the stretch, and its dates are the first and last of them.

AN EXPORT FILE IS ONLY WORTH ASKING FOR where the account has another way in than statements
(`askable`): an account whose every source is a statement reader is told "this source states no
party", since a file that the bank does not offer for it is not a fix.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from ..core.plural import plural

#: The most days without a transaction a run of one state is joined across.
MERGE_DAYS = 6


@dataclass(frozen=True)
class Stretch:
    """Consecutive months in which the description names more transactions than a source does."""

    #: The first and last described transaction in the months.
    first: date
    last: date
    #: How many transactions in the months are described.
    rows: int


@dataclass(frozen=True)
class PartyStated:
    """One account's stated and described days, and the stretches of months to say."""

    #: The runs of days on which every transaction states its party, and on which any does not.
    stated_runs: tuple[tuple[date, date], ...] = ()
    described_runs: tuple[tuple[date, date], ...] = ()
    stretches: tuple[Stretch, ...] = ()
    transactions: int = 0
    described: int = 0
    #: Whether an export file could be asked for (see the module docstring).
    askable: bool = True

    @property
    def drawn(self) -> bool:
        """Whether there is a row to draw: any transaction at all."""
        return self.transactions > 0

    @property
    def said(self) -> bool:
        """Whether the account has a stretch to say in words."""
        return bool(self.stretches)


def _next_month(month: tuple[int, int]) -> tuple[int, int]:
    year, number = month
    return (year + 1, 1) if number == 12 else (year, number + 1)


@dataclass
class _Month:
    stated: int = 0
    described: int = 0
    first: date = field(default=date.max)
    last: date = field(default=date.min)


def party_stated(rows: Iterable[tuple[date, bool]], *, askable: bool = True) -> PartyStated:
    """The coverage of one account's transactions, each given as its day and whether it states
    its party."""
    per_day: dict[date, list[int]] = {}
    per_month: dict[tuple[int, int], _Month] = {}
    for day, stated in rows:
        counts = per_day.setdefault(day, [0, 0])
        counts[0 if stated else 1] += 1
        month = per_month.setdefault((day.year, day.month), _Month())
        if stated:
            month.stated += 1
        else:
            month.described += 1
            month.first = min(month.first, day)
            month.last = max(month.last, day)
    # A run is joined only to the next transaction day when that day has the same state, so a
    # day of the other state between two of one state always ends the run.
    stated_runs: list[list[date]] = []
    described_runs: list[list[date]] = []
    previous: bool | None = None
    for day in sorted(per_day):
        state = per_day[day][1] == 0
        runs = stated_runs if state else described_runs
        joined = previous is state and (day - runs[-1][1]).days - 1 <= MERGE_DAYS
        if joined:
            runs[-1][1] = day
        else:
            runs.append([day, day])
        previous = state
    stretches: list[Stretch] = []
    current: list[tuple[int, int]] = []
    for key in sorted(per_month):
        if per_month[key].described <= per_month[key].stated:
            continue
        if current and _next_month(current[-1]) != key:
            stretches.append(_stretch(current, per_month))
            current = []
        current.append(key)
    if current:
        stretches.append(_stretch(current, per_month))
    return PartyStated(
        tuple((first, last) for first, last in stated_runs),
        tuple((first, last) for first, last in described_runs),
        tuple(stretches),
        transactions=sum(sum(counts) for counts in per_day.values()),
        described=sum(counts[1] for counts in per_day.values()),
        askable=askable,
    )


def _stretch(keys: list[tuple[int, int]], per_month: dict[tuple[int, int], _Month]) -> Stretch:
    months = [per_month[key] for key in keys]
    return Stretch(
        min(m.first for m in months), max(m.last for m in months), sum(m.described for m in months)
    )


def party_row_words(party: PartyStated) -> str:
    """The short form for a crowded row: the count over every stretch, without the dates, which
    the account's page gives."""
    rows = sum(stretch.rows for stretch in party.stretches)
    return f"{plural(rows, 'transaction')} named by the description only"


def want_words(rows: int) -> str:
    """What Bring in says beside an export wanted for `rows` described transactions; the account
    and the days are the row's own."""
    return (
        f"{plural(rows, 'transaction')} there {'is' if rows == 1 else 'are'} named by the "
        "description only"
    )


def stretch_words(stretch: Stretch, *, askable: bool) -> str:
    """The sentence for one stretch, the one place it is worded: the count and the dates, and
    what would fix it, or that the source cannot."""
    span = (
        f"on {stretch.first.isoformat()}"
        if stretch.first == stretch.last
        else f"from {stretch.first.isoformat()} to {stretch.last.isoformat()}"
    )
    verb = "is" if stretch.rows == 1 else "are"
    tail = (
        "an export file for those months would state the party"
        if askable
        else "this source states no party"
    )
    return (
        f"{plural(stretch.rows, 'transaction')} {span} {verb} named by the description only - "
        f"{tail}."
    )
