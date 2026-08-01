"""What the store actually holds, and whether its sources agree with each other.

Pulling one account from several routes is deliberate: two independent sources
agreeing is real evidence the data is right, and where they disagree the
disagreement is the finding. But that only pays if something asks - otherwise
the second source is cost without benefit, and a silent divergence looks
identical to a quiet success.

Two questions, kept separate because they fail differently:

  coverage    what have we got, per account and per source - the range, the
              volume, and how much of it rests on a provider's own identifier
              rather than on content matching
  agreements  do two sources describing the same account and the same period
              describe the same money

The comparison is windowed to the OVERLAP, which is the difference between a
useful report and a noisy one. A three-month CSV and a two-year feed will never
agree on totals, and reporting that as a discrepancy would bury the real ones
under arithmetic that was never going to match.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations

from .models import Transaction


@dataclass(frozen=True)
class SourceCoverage:
    account_id: str
    source: str
    count: int
    earliest: date
    latest: date
    inflow_minor: int
    outflow_minor: int
    #: How many carry the provider's own durable identifier. The rest are
    #: matched on content, which is sound but more sensitive to a change in the
    #: matching rules - so this says how much of the store could move if those
    #: rules were revised.
    with_durable_id: int

    @property
    def net_minor(self) -> int:
        return self.inflow_minor - self.outflow_minor


@dataclass(frozen=True)
class Agreement:
    """Two sources, one account, compared over the period they share."""

    account_id: str
    left: str
    right: str
    overlap_from: date
    overlap_to: date
    left_count: int
    right_count: int
    left_net_minor: int
    right_net_minor: int

    @property
    def agrees(self) -> bool:
        return self.left_count == self.right_count and self.left_net_minor == self.right_net_minor

    def describe(self) -> str:
        verdict = "agree" if self.agrees else "DISAGREE"
        return (
            f"{self.account_id}: {self.left} vs {self.right} "
            f"[{self.overlap_from} .. {self.overlap_to}] {verdict} "
            f"({self.left_count} vs {self.right_count} transactions, "
            f"net {self.left_net_minor} vs {self.right_net_minor})"
        )


def coverage(transactions: Iterable[Transaction]) -> list[SourceCoverage]:
    """Per account and source: range, volume, direction split, id provenance."""
    grouped: dict[tuple[str, str], list[Transaction]] = {}
    for transaction in transactions:
        grouped.setdefault((transaction.account_id, transaction.source), []).append(transaction)

    rows = []
    for (account_id, source), items in sorted(grouped.items()):
        dates = [item.value_date for item in items]
        rows.append(
            SourceCoverage(
                account_id=account_id,
                source=source,
                count=len(items),
                earliest=min(dates),
                latest=max(dates),
                # Kept apart rather than netted: a sign-convention fault moves
                # both sides by the same amount and leaves the net untouched,
                # so a net figure is precisely the one that would hide it.
                inflow_minor=sum(i.amount_minor for i in items if i.amount_minor > 0),
                outflow_minor=-sum(i.amount_minor for i in items if i.amount_minor < 0),
                with_durable_id=sum(1 for i in items if i.source_id),
            )
        )
    return rows


def _within(items: Sequence[Transaction], start: date, end: date) -> list[Transaction]:
    """Bounds passed explicitly rather than captured from the enclosing loop.

    A closure over loop variables reads correctly and evaluates late, so it
    silently uses whichever pair the loop ended on the moment anything defers
    the call.
    """
    return [item for item in items if start <= item.value_date <= end]


def _window(items: Sequence[Transaction]) -> tuple[date, date]:
    dates = [item.value_date for item in items]
    return min(dates), max(dates)


def agreements(transactions: Iterable[Transaction]) -> list[Agreement]:
    """Compare every pair of sources that describes the same account.

    Pairs with no shared period are omitted entirely rather than reported as
    agreeing or disagreeing: having nothing to compare is a third outcome, and
    collapsing it into either of the other two would mislead.
    """
    by_account: dict[str, dict[str, list[Transaction]]] = {}
    for transaction in transactions:
        by_account.setdefault(transaction.account_id, {}).setdefault(
            transaction.source, []
        ).append(transaction)

    found = []
    for account_id, sources in sorted(by_account.items()):
        for left, right in combinations(sorted(sources), 2):
            left_from, left_to = _window(sources[left])
            right_from, right_to = _window(sources[right])
            start, end = max(left_from, right_from), min(left_to, right_to)
            if start > end:
                continue

            in_left = _within(sources[left], start, end)
            in_right = _within(sources[right], start, end)
            found.append(
                Agreement(
                    account_id=account_id,
                    left=left,
                    right=right,
                    overlap_from=start,
                    overlap_to=end,
                    left_count=len(in_left),
                    right_count=len(in_right),
                    left_net_minor=sum(i.amount_minor for i in in_left),
                    right_net_minor=sum(i.amount_minor for i in in_right),
                )
            )
    return found


@dataclass(frozen=True)
class Gap:
    """A month a source has nothing for, inside the period it otherwise covers."""

    account_id: str
    source: str
    month: str
    #: Other sources that DO have data for that month. This is the whole
    #: distinction: absence that another source contradicts is evidence of
    #: something missing, whereas absence every source agrees on is most
    #: likely the truth - the account was simply quiet.
    seen_in: tuple[str, ...] = ()

    @property
    def contradicted(self) -> bool:
        return bool(self.seen_in)


def gaps(transactions: Iterable[Transaction]) -> list[Gap]:
    """Months with nothing in them, bounded on BOTH sides by months that have.

    A hole in a file-import series almost always means a file that was never
    downloaded, and that is actionable: the bank will still let you fetch it.

    Only enclosed months qualify. An account that has fallen out of use has
    empty months at the end, and those are not missing data - they are the
    truth. Reporting them would produce a standing complaint about something
    correct, which is the quickest way to teach someone to skip the report.

    Months are the unit because that is how banks package statements; a
    day-level view of a quiet account would be almost entirely holes.
    """
    grouped: dict[tuple[str, str], set[str]] = {}
    for transaction in transactions:
        key = (transaction.account_id, transaction.source)
        grouped.setdefault(key, set()).add(transaction.value_date.strftime("%Y-%m"))

    # Which sources have anything at all for a given account-month. This is what
    # turns "nobody has June" into "the account was quiet in June" rather than
    # a fault report against every source at once.
    witnesses: dict[tuple[str, str], set[str]] = {}
    for (account_id, source), months in grouped.items():
        for month in months:
            witnesses.setdefault((account_id, month), set()).add(source)

    found = []
    for (account_id, source), present in sorted(grouped.items()):
        ordered = sorted(present)
        first, last = ordered[0], ordered[-1]
        year, month_number = int(first[:4]), int(first[5:])
        while (cursor := f"{year:04d}-{month_number:02d}") <= last:
            if cursor not in present:
                others = sorted(witnesses.get((account_id, cursor), set()) - {source})
                found.append(
                    Gap(
                        account_id=account_id,
                        source=source,
                        month=cursor,
                        seen_in=tuple(others),
                    )
                )
            month_number += 1
            if month_number > 12:
                year, month_number = year + 1, 1
    return found


def report(
    rows: Sequence[SourceCoverage],
    checks: Sequence[Agreement],
    holes: Sequence[Gap] = (),
) -> str:
    lines = ["What the store holds:", ""]
    for row in rows:
        lines.append(
            f"  {row.account_id:<22} {row.source:<18} {row.count:>6} txns  "
            f"{row.earliest} .. {row.latest}"
        )
        lines.append(
            f"  {'':<22} {'':<18} in {row.inflow_minor / 100:>12,.2f}  "
            f"out {row.outflow_minor / 100:>12,.2f}  net {row.net_minor / 100:>12,.2f}  "
            f"({row.with_durable_id}/{row.count} with a provider id)"
        )

    # Split by whether anything contradicts the absence. Lumping them together
    # would bury the handful worth acting on among months that are empty simply
    # because nothing happened.
    contradicted = [hole for hole in holes if hole.contradicted]
    unwitnessed = [hole for hole in holes if not hole.contradicted]

    if contradicted:
        lines += ["", "MISSING - another source has data for these months:", ""]
        for hole in contradicted:
            lines.append(
                f"  {hole.account_id} / {hole.source}: {hole.month} "
                f"(present in {', '.join(hole.seen_in)})"
            )
        # Actionable rather than alarming: the bank will still hand over a
        # statement you never downloaded, so the response is to fetch it.
        lines += [
            "",
            "  Another route saw activity here, so this is data not yet collected "
            "rather than data lost. Download those months and import them.",
        ]

    if unwitnessed:
        lines += ["", "Empty months, but NO source has data for them:", ""]
        by_source: dict[tuple[str, str], list[str]] = {}
        for hole in unwitnessed:
            by_source.setdefault((hole.account_id, hole.source), []).append(hole.month)
        for (account_id, source), months in sorted(by_source.items()):
            lines.append(f"  {account_id} / {source}: {', '.join(months)}")
        lines += [
            "",
            "  Every source agrees these months are empty, so most likely the account "
            "was simply quiet. Worth a glance only if you expected activity.",
        ]

    if not checks:
        # Said plainly. "No disagreements" would imply a comparison happened,
        # and a reader would take confidence from a check that never ran.
        lines += ["", "No two sources cover the same account and period, so nothing was compared."]
        return "\n".join(lines)

    lines += ["", "Where two sources describe the same period:", ""]
    lines += [f"  {check.describe()}" for check in checks]
    if any(not check.agrees for check in checks):
        lines += [
            "",
            "A disagreement is a finding, not necessarily a bug: an export may be "
            "partial, or one route may omit pending items. It is worth explaining "
            "rather than explaining away.",
        ]
    return "\n".join(lines)
