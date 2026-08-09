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

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations

from .models import Transaction
from .money import format_amount

#: Two sources describing the SAME account may date one movement a day or two
#: apart (statement value date versus feed settlement). Row matching within the
#: account tolerates that before anything is called missing.
WITHIN_ACCOUNT_WINDOW_DAYS = 2

#: A row the other source filed under a SIBLING account with the same sign is
#: the same movement seen through a door that disagrees about where it belongs
#: - a bill paid directly from a savings space appears on the statement as the
#: main account's spending while the feed holds it in the space.
SIBLING_SAME_SIGN_WINDOW_DAYS = 2

#: The statement's main leg of a space top-up pairs with the space's OPPOSITE
#: leg. Internal moves land same-day, so the window mirrors transfer pairing:
#: a distant opposite-signed row is coincidence, not evidence.
SIBLING_OPPOSITE_SIGN_WINDOW_DAYS = 1

#: How many unexplained rows the flat description prints before summarising.
#: The residue is the finding, so some of it must always be visible - but a
#: line is not a dump.
_UNEXPLAINED_SHOWN = 3

#: The structured outline can afford more before summarising - a nested list
#: reads row by row where a prose line cannot.
_UNEXPLAINED_SHOWN_OUTLINE = 10

#: Confirmed legs are repetitive by nature (the same standing top-up, month
#: after month), so a shorter sample suffices to show what the count means.
_LEGS_SHOWN_OUTLINE = 5


def _row_items(rows: Sequence[UnexplainedRow], cap: int) -> list[str]:
    """Row lines for a single side's bucket - no source suffix, the bucket's
    label already names the side."""
    ordered = sorted(rows, key=lambda r: (r.row_date, r.amount_minor, r.description))
    items = [
        f"{row.row_date} {format_amount(row.amount_minor)} '{row.description}'"
        for row in ordered[:cap]
    ]
    if len(ordered) > len(items):
        items.append(f"+{len(ordered) - len(items)} more not shown")
    return items


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
class SiblingAttribution:
    """One source's row in this account, matched to a row the other source
    filed under a SIBLING account.

    Evidence, not inference: the sibling and the matched date are carried so
    the claim is checkable on sight - a nonsense match (a Starling row
    "explained" by a different bank's account) announces itself instead of
    hiding inside a count.
    """

    source: str
    row_date: date
    amount_minor: int
    description: str
    matched_source: str
    sibling_account: str
    sibling_date: date
    #: True when the sibling row is the opposite leg of an internal move
    #: (the statement's main leg of a space top-up); False when the sibling
    #: holds the SAME-signed row (a payment made directly from a space).
    opposite_sign: bool


@dataclass(frozen=True)
class UnexplainedRow:
    """A row one source holds that neither the other source's rows here nor
    its sibling accounts can account for - the residue that IS the finding."""

    source: str
    row_date: date
    amount_minor: int
    description: str


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
    #: Rows one source holds only here that matched the other source's rows
    #: in sibling accounts. Populated only when a sibling scope was supplied
    #: and the aggregate figures disagreed.
    attributed: tuple[SiblingAttribution, ...] = ()
    #: Leftover rows the pairing pass has already PROVEN internal - their
    #: opposite leg is held in another account - so the other source not
    #: carrying them is expected, not a fault: a consolidated statement
    #: cannot show a movement that is internal to what it consolidates.
    #: A provider's bare claim does not qualify; only the store's own proof.
    confirmed_transfer_legs: tuple[UnexplainedRow, ...] = ()
    #: What remains after within-account matching, transfer proof, and
    #: sibling attribution.
    unexplained: tuple[UnexplainedRow, ...] = ()
    #: Whether sibling reconciliation ran at all. An empty `attributed` from
    #: a run that never looked must not read as "nothing to attribute".
    reconciled: bool = False
    #: Rows matched between the two sources within this account during
    #: reconciliation - the shared base every per-side ledger starts from.
    #: Meaningful only when `reconciled` is true and the figures disagreed.
    matched_count: int = 0

    @property
    def agrees(self) -> bool:
        return self.left_count == self.right_count and self.left_net_minor == self.right_net_minor

    def describe(self) -> str:
        heading = (
            f"{self.account_id}: {self.left} vs {self.right} "
            f"[{self.overlap_from} .. {self.overlap_to}]"
        )
        figures = (
            f"({self.left_count} vs {self.right_count} transactions, "
            f"net {format_amount(self.left_net_minor)} vs "
            f"{format_amount(self.right_net_minor)})"
        )
        if self.agrees:
            return f"{heading} agree {figures}"
        if not self.reconciled:
            return f"{heading} DISAGREE {figures}"

        clauses = []
        if self.attributed:
            clauses.append(_attribution_clause(self.attributed))
        if self.confirmed_transfer_legs:
            clauses.append(_transfer_legs_clause(self.confirmed_transfer_legs))
        if self.unexplained:
            clauses.append(_unexplained_clause(self.unexplained))
        detail = f" - {'; '.join(clauses)}" if clauses else ""
        return f"{heading} {self._reconciled_verdict()} {figures}{detail}"

    def _reconciled_verdict(self) -> str:
        explained = bool(self.attributed or self.confirmed_transfer_legs)
        if explained and not self.unexplained:
            # Not plain "agree": the aggregate figures still differ, and the
            # verdict says exactly on what grounds the difference is excused.
            return "agree once sibling attribution is counted"
        return "DISAGREE"

    def outline(self) -> dict[str, object]:
        """The same verdict as `describe`, shaped as a per-source ledger.

        One prose line carrying four kinds of fact proved unreadable in live
        use - and a first structured cut still demanded forensic
        reconstruction: counts with no denominator, "unexplained" with no
        direction. So each side gets a ledger in which every row lands in
        exactly one bucket and the buckets sum to that side's own total -
        the arithmetic is checkable on sight, and every line names WHICH
        source holds the rows it counts. `describe` stays the flat form for
        logs and terminals. Plain data on purpose - it crosses the web
        boundary without types.
        """
        if self.agrees:
            verdict, warn = "agree", False
        elif not self.reconciled:
            verdict, warn = "DISAGREE", True
        else:
            verdict = self._reconciled_verdict()
            warn = verdict == "DISAGREE"

        sides: list[dict[str, object]] = []
        if not self.agrees and self.reconciled:
            for name, other, total in (
                (self.left, self.right, self.left_count),
                (self.right, self.left, self.right_count),
            ):
                buckets: list[dict[str, object]] = [
                    {"label": f"{self.matched_count} matched with {other}", "items": []}
                ]
                attributed = [m for m in self.attributed if m.source == name]
                if attributed:
                    counts: dict[str, int] = {}
                    for match in attributed:
                        counts[match.sibling_account] = (
                            counts.get(match.sibling_account, 0) + 1
                        )
                    buckets.append(
                        {
                            "label": (
                                f"{len(attributed)} matched to rows {other} filed "
                                "under sibling accounts"
                            ),
                            "items": [
                                f"{sibling}: {count}"
                                for sibling, count in sorted(counts.items())
                            ],
                        }
                    )
                legs = [
                    leg for leg in self.confirmed_transfer_legs if leg.source == name
                ]
                if legs:
                    count = len(legs)
                    buckets.append(
                        {
                            "label": (
                                f"{count} confirmed internal-transfer "
                                f"{'leg' if count == 1 else 'legs'} "
                                "(opposite leg held in another account)"
                            ),
                            "items": _row_items(legs, _LEGS_SHOWN_OUTLINE),
                        }
                    )
                unexplained = [
                    row for row in self.unexplained if row.source == name
                ]
                if unexplained:
                    count = len(unexplained)
                    buckets.append(
                        {
                            "label": (
                                f"{count} in {name} ONLY - no counterpart in "
                                f"{other}, its sibling accounts, or the "
                                "pairing table"
                            ),
                            "items": _row_items(unexplained, _UNEXPLAINED_SHOWN_OUTLINE),
                        }
                    )
                sides.append(
                    {"heading": f"{name}: {total} rows in the window", "buckets": buckets}
                )

        return {
            "sources": f"{self.left} vs {self.right}",
            "window": f"{self.overlap_from} .. {self.overlap_to}",
            "verdict": verdict,
            "warn": warn,
            "figures": (
                f"{self.left_count} vs {self.right_count} transactions; "
                f"net {format_amount(self.left_net_minor)} vs "
                f"{format_amount(self.right_net_minor)}"
            ),
            "sides": sides,
        }


def _attribution_clause(attributed: Sequence[SiblingAttribution]) -> str:
    counts: dict[tuple[str, str], int] = {}
    for match in attributed:
        key = (match.sibling_account, match.matched_source)
        counts[key] = counts.get(key, 0) + 1
    parts = [
        f"{sibling} ({count} via {source})"
        for (sibling, source), count in sorted(counts.items())
    ]
    total = len(attributed)
    rows = "row" if total == 1 else "rows"
    return f"{total} {rows} matched to sibling-account rows: {', '.join(parts)}"


def _transfer_legs_clause(legs: Sequence[UnexplainedRow]) -> str:
    counts: dict[str, int] = {}
    for leg in legs:
        counts[leg.source] = counts.get(leg.source, 0) + 1
    parts = [f"{source} ({count})" for source, count in sorted(counts.items())]
    total = len(legs)
    plural = "leg" if total == 1 else "legs"
    return (
        f"{total} confirmed internal-transfer {plural} the other source "
        f"does not carry: {', '.join(parts)}"
    )


def _unexplained_clause(unexplained: Sequence[UnexplainedRow]) -> str:
    ordered = sorted(unexplained, key=lambda r: (r.row_date, r.amount_minor, r.description))
    shown = [
        f"{row.row_date} {format_amount(row.amount_minor)} '{row.description}' ({row.source})"
        for row in ordered[:_UNEXPLAINED_SHOWN]
    ]
    more = len(ordered) - len(shown)
    suffix = f" (+{more} more)" if more else ""
    return f"unexplained: {'; '.join(shown)}{suffix}"


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


def _leftovers(
    in_left: Sequence[Transaction],
    in_right: Sequence[Transaction],
    window_days: int,
) -> tuple[list[Transaction], list[Transaction]]:
    """Each side's rows with no same-amount counterpart on the other.

    Greedy nearest-date within the window, each row consumed once - the same
    discipline as transfer pairing, so a repeated standing order of one value
    does not chain-match.
    """
    by_amount: dict[int, list[int]] = {}
    for j, item in enumerate(in_right):
        by_amount.setdefault(item.amount_minor, []).append(j)

    window = timedelta(days=window_days)
    used: set[int] = set()
    left_over: list[Transaction] = []
    for item in sorted(in_left, key=lambda t: (t.value_date, t.amount_minor, t.description)):
        candidates = [
            j
            for j in by_amount.get(item.amount_minor, ())
            if j not in used and abs(in_right[j].value_date - item.value_date) <= window
        ]
        if candidates:
            used.add(
                min(candidates, key=lambda j: (abs(in_right[j].value_date - item.value_date), j))
            )
        else:
            left_over.append(item)
    right_over = [item for j, item in enumerate(in_right) if j not in used]
    return left_over, right_over


def _attribute(
    rows: Sequence[Transaction],
    other_source: str,
    pool: Sequence[Transaction],
) -> tuple[list[SiblingAttribution], list[UnexplainedRow]]:
    """Match each row to an unconsumed row of `other_source` in a sibling
    account: equal amount within the same-sign window, or the exact opposite
    within the tighter internal-move window. Same-sign wins a tie because it
    is the stronger claim (the same movement, not a counterpart leg)."""
    by_amount: dict[int, list[int]] = {}
    for j, item in enumerate(pool):
        by_amount.setdefault(item.amount_minor, []).append(j)

    used: set[int] = set()
    matched: list[SiblingAttribution] = []
    residue: list[UnexplainedRow] = []
    for item in sorted(rows, key=lambda t: (t.value_date, t.amount_minor, t.description)):
        candidates = []
        for sign, window_days in (
            (1, SIBLING_SAME_SIGN_WINDOW_DAYS),
            (-1, SIBLING_OPPOSITE_SIGN_WINDOW_DAYS),
        ):
            window = timedelta(days=window_days)
            for j in by_amount.get(sign * item.amount_minor, ()):
                if j in used:
                    continue
                distance = abs(pool[j].value_date - item.value_date)
                if distance <= window:
                    candidates.append((distance, 0 if sign == 1 else 1, j))
        if candidates:
            _, flipped, j = min(candidates)
            used.add(j)
            matched.append(
                SiblingAttribution(
                    source=item.source,
                    row_date=item.value_date,
                    amount_minor=item.amount_minor,
                    description=item.description,
                    matched_source=other_source,
                    sibling_account=pool[j].account_id,
                    sibling_date=pool[j].value_date,
                    opposite_sign=bool(flipped),
                )
            )
        else:
            residue.append(
                UnexplainedRow(
                    source=item.source,
                    row_date=item.value_date,
                    amount_minor=item.amount_minor,
                    description=item.description,
                )
            )
    return matched, residue


def _sibling_pool(
    by_source_account: Mapping[str, Mapping[str, Sequence[Transaction]]],
    scope: Mapping[str, Collection[str]],
    source: str,
    account_id: str,
) -> list[Transaction]:
    """The rows `source` filed under its OTHER accounts - the places a
    movement seen here by a different witness might actually live."""
    rows: list[Transaction] = []
    for sibling in scope.get(source, ()):
        if sibling == account_id:
            continue
        rows.extend(by_source_account.get(source, {}).get(sibling, ()))
    return rows


def agreements(
    transactions: Iterable[Transaction],
    *,
    sibling_accounts: Mapping[str, Collection[str]] | None = None,
) -> list[Agreement]:
    """Compare every pair of sources that describes the same account.

    Pairs with no shared period are omitted entirely rather than reported as
    agreeing or disagreeing: having nothing to compare is a third outcome, and
    collapsing it into either of the other two would mislead.

    `sibling_accounts` maps each source to every canonical account it feeds.
    When supplied, a disagreeing pair is reconciled row by row and the rows
    only one source holds are searched for in the OTHER source's sibling
    accounts - because a statement shows the main account's view of movements
    the feed files under a space. Attributions carry their evidence and the
    residue is reported, never swallowed.
    """
    by_account: dict[str, dict[str, list[Transaction]]] = {}
    for transaction in transactions:
        by_account.setdefault(transaction.account_id, {}).setdefault(
            transaction.source, []
        ).append(transaction)

    by_source_account: dict[str, dict[str, list[Transaction]]] = {}
    if sibling_accounts is not None:
        for transaction in transactions:
            by_source_account.setdefault(transaction.source, {}).setdefault(
                transaction.account_id, []
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
            left_count, right_count = len(in_left), len(in_right)
            left_net = sum(i.amount_minor for i in in_left)
            right_net = sum(i.amount_minor for i in in_right)

            attributed: tuple[SiblingAttribution, ...] = ()
            confirmed_legs: tuple[UnexplainedRow, ...] = ()
            unexplained: tuple[UnexplainedRow, ...] = ()
            reconciled = sibling_accounts is not None
            matched_count = 0
            if sibling_accounts is not None and (
                left_count != right_count or left_net != right_net
            ):
                left_over, right_over = _leftovers(
                    in_left, in_right, WITHIN_ACCOUNT_WINDOW_DAYS
                )
                matched_count = left_count - len(left_over)
                # Proven-internal legs come out first: the pairing pass has
                # already matched them to their opposite side in another
                # account, which is stronger and cheaper evidence than any
                # fresh search here could produce.
                proven = [t for t in left_over + right_over if t.transfer_confirmed]
                left_over = [t for t in left_over if not t.transfer_confirmed]
                right_over = [t for t in right_over if not t.transfer_confirmed]
                confirmed_legs = tuple(
                    UnexplainedRow(
                        source=t.source,
                        row_date=t.value_date,
                        amount_minor=t.amount_minor,
                        description=t.description,
                    )
                    for t in sorted(
                        proven, key=lambda t: (t.value_date, t.amount_minor, t.description)
                    )
                )
                left_found, left_residue = _attribute(
                    left_over,
                    right,
                    _sibling_pool(by_source_account, sibling_accounts, right, account_id),
                )
                right_found, right_residue = _attribute(
                    right_over,
                    left,
                    _sibling_pool(by_source_account, sibling_accounts, left, account_id),
                )
                attributed = tuple(left_found + right_found)
                unexplained = tuple(left_residue + right_residue)

            found.append(
                Agreement(
                    account_id=account_id,
                    left=left,
                    right=right,
                    overlap_from=start,
                    overlap_to=end,
                    left_count=left_count,
                    right_count=right_count,
                    left_net_minor=left_net,
                    right_net_minor=right_net,
                    attributed=attributed,
                    confirmed_transfer_legs=confirmed_legs,
                    unexplained=unexplained,
                    reconciled=reconciled,
                    matched_count=matched_count,
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


@dataclass(frozen=True)
class DateTransposition:
    """One payment, two sources, dates that are each other's day/month swap."""

    account_id: str
    amount_minor: int
    description: str
    left: str
    left_date: date
    right: str
    right_date: date

    def describe(self) -> str:
        return (
            f"{self.account_id}: {self.amount_minor / 100:,.2f} \"{self.description}\" "
            f"dated {self.left_date} by {self.left} but {self.right_date} by {self.right}"
        )


def transpositions(transactions: Iterable[Transaction]) -> list[DateTransposition]:
    """Find payments two sources date as each other's day/month swap.

    The quietest corruption available: the amount is right, the payee is right,
    and the date is a perfectly real date. Count-and-total checks are blind to
    it, because moving a transaction between months changes neither the count
    nor the sum - so a wholesale transposition passes every other check here
    while the data is systematically wrong.

    Only days 1-12 can transpose at all; 13 upwards is unambiguous and parses
    identically either way. That produces the characteristic signature of an
    auto-detecting parser: a file where the ambiguous rows moved and the rest
    did not, which is far harder to spot by eye than a file that is wholly
    wrong.

    Requires the same amount AND description across DIFFERENT sources, so a
    coincidence would have to be two equal payments to the same payee whose
    dates happen to be each other's mirror. Within one source it is not
    reported at all: two such payments are ordinary, and one payment cannot be
    in two places.
    """
    grouped: dict[tuple[str, int, str], list[Transaction]] = {}
    for transaction in transactions:
        key = (transaction.account_id, transaction.amount_minor, transaction.description)
        grouped.setdefault(key, []).append(transaction)

    found = []
    for (account_id, amount_minor, description), items in sorted(grouped.items()):
        for left, right in combinations(items, 2):
            if left.source == right.source:
                continue
            a, b = left.value_date, right.value_date
            if a == b:
                continue
            if a.day == b.month and a.month == b.day and a.year == b.year:
                found.append(
                    DateTransposition(
                        account_id=account_id,
                        amount_minor=amount_minor,
                        description=description,
                        left=left.source,
                        left_date=a,
                        right=right.source,
                        right_date=b,
                    )
                )
    return found


def report(
    rows: Sequence[SourceCoverage],
    checks: Sequence[Agreement],
    holes: Sequence[Gap] = (),
    swapped: Sequence[DateTransposition] = (),
) -> str:
    lines = []
    if swapped:
        # First, above everything. It is the only finding here that every other
        # check passes while it is true: transposing a date changes neither the
        # count nor the total, so agreement figures look perfect.
        lines += ["DATES DISAGREE - possible day/month transposition:", ""]
        lines += [f"  {item.describe()}" for item in swapped]
        lines += [
            "",
            "  Two sources date the same payment as each other's day/month swap. "
            "Only days 1-12 can do this, so a parser reading dates the wrong way "
            "round moves the ambiguous rows and leaves the rest correct. Check "
            "which source is right before importing more from the wrong one.",
            "",
        ]
    lines += ["What the store holds:", ""]
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
