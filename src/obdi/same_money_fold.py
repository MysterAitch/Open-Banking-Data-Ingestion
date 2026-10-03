"""The same money, itemised differently by two sources, is counted once.

A card statement prints three small charges on the last day of a period (an
interest charge, a fee, a levy) where the aggregator's feed carries ONE row for
their total, posted on the statement date, which by date is the first days of
the next period. Identity cannot merge them: no amount or description matches.
So the store holds all four rows. Measured on a real card, almost every period
reported "3 rows only in the statements, 1 row only in the feed, and the two
sum to the same figure", the periods after the first were over by the feed's
row, and the account's total was over by every one of them, while the
statements' own arithmetic was exact.

The rule, per statement closing, over the rows the pairing left unmatched
(`period_reconciliation`'s, which is the cross-source page's own pairing). The
feed rows that may be the statement's charges are the unmatched ones dated
within `BOUNDARY_DAYS` either side of ITS CLOSING DAY, not anywhere in its
period. A set of them is the same money as the statement's when ALL of:

  - its sum is EXACTLY the sum of some non-empty subset of that statement's
    statement-only rows, in non-zero minor units (the statement may also print
    rows the feed never saw, and those take no part);
  - folding it makes every period it touches agree exactly with its
    statement's movement. The statement's own arithmetic is the proof: equal
    sums alone are a coincidence waiting to happen, and a fold that leaves the
    period differing has hidden something that is not a duplicate.

Among the subsets of the band's rows the smallest is taken, and of equal size
the one dated latest, because a feed posts a statement's charges on or after
its closing. The choice is made once and is not retried when the proof refuses
it: a stranger row of the charge's own amount on the closing day would
otherwise stand in for the charge and leave the charge's own row double counted
in the next period. Each statement's two searches are bounded by
`MAX_STATEMENT_ROWS` and `MAX_BAND_ROWS`.

Then the FEED-side rows are folded (status FOLDED, no longer counted, withheld
from the push, sightings kept) and the statement's rows stay counted. They are
kept because they are what the statement's balance is made of, so every later
anchor check stays exact.

Never folded: a row that is a confirmed transfer leg, a row some statement lists
(it is the statement's own, however its dates fell in the pairing), a row
outside the band, or a row of another account. A statement with leftovers on
one side only is left alone. A feed row is claimed by the EARLIEST statement
whose band holds it, so two closings within the boundary of each other do not
claim one row twice.

A period that differs for a reason that is not a charge (a feed-only purchase no
statement lists) refuses the folds that touch it, and so, because the row it
refused is still counted in the next period, the folds after it until that
period is reconciled. That is the proof working: those periods do differ.

Alongside the folds the pass records what it concluded at every closing of every
account (`same_money_outcome`), built where the decision is made, so the
statement-periods page can say why a closing folded nothing without a second
copy of this rule.

The pass is a pure function of the stored rows, rewritten wholesale after every
import, pull, and assignment and once at the end of a rebuild, so the outcome
does not depend on which source arrived first; a fold whose evidence goes away
(a statement no longer held, a statement later listing the row) is released.
It is modelled on `space_attribution`, and the two never touch each other's
rows: a Space-folded row has a copied sighting on the Space row, and this
pass's folds have none, which is how the store tells them apart.

REJECTED. Taking every unclaimed feed row from the period's first day to two
days past its closing, and requiring ALL the period's statement-only rows to
equal ALL of them: on a real card the previous statement's charge sits on the
first day of each period, so every period saw two feed rows, the sums were
unequal, and one first period with an extra row disabled the rule for a whole
real account (nothing folded, 8 of 10 periods still differing). Folding the
STATEMENT's side: the next statement's check would then need the feed's date
to fall in the right period, which is the fault being fixed. Pairing rows one
to one: three rows against one cannot pair. Folding on equal sums alone: see
above. A band wider than two days: it reaches the next closing's own charge,
and a feed row of the right size there becomes a candidate for this statement.
Retrying the next subset when the proof refuses the first: see above.
Loosening the proof so a period that differs for another reason does not stop
the folds after it: the proof is the only thing separating a duplicate from a
purchase, and a fold that cannot make its own period agree proves nothing.

NOT COVERED. A feed row dated beyond the span the pairing compared is treated
as unmatched, which it is by construction (nothing in the statements is dated
that late), but its statement-side partners are only the leftovers inside the
span. Coincidental equal sums that also make the statement's arithmetic exact
would still fold; nothing here tells that apart from the real thing. A band
holding more than `MAX_BAND_ROWS` unmatched feed rows searches only the nearest
ones, so it folds less, never more. Cost: the pass reads the whole store's
sightings and pairs the sources of each account that holds a statement, after
every import. Its steps record as sub-phases (`SAME_MONEY_PHASE`), so a slow
rebuild names which one. What a real store has that a synthetic one lacks was
the documents themselves: every pass used to extract the text of every held
statement again, which is slow only for a real PDF. A statement's reading is now
kept (`statement_terms.keep_statement_readings`) and the pass reads that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations

from . import instrumentation
from .accounts import AccountMap
from .models import Transaction
from .period_reconciliation import (
    BOUNDARY_DAYS,
    FEED_SIDE,
    SAME_MONEY_PHASE,
    STATEMENT_SIDE,
    AccountEvidence,
    Leftover,
    PeriodKind,
    _Window,
    gather_evidence,
    held_in,
)
from .same_money_outcome import AccountOutcome, ClosingOutcome, Verdict
from .statement_terms import keep_statement_readings
from .store import Store

#: Bounds on the subset searches in `_candidates`, so the work per statement is
#: fixed whatever a store holds: at most 2**12 statement-side sums and 2**8 - 1
#: feed-side subsets. A real card's statements carried at most four statement-only
#: rows and one feed-only row near each closing, so neither bound reaches a real
#: period; a period past one folds less, never more.
MAX_STATEMENT_ROWS = 12
MAX_BAND_ROWS = 8


@dataclass(frozen=True)
class SameMoneyReport:
    #: Feed rows folded after this pass: the state, not the change.
    folded: int = 0
    newly_folded: int = 0
    #: Rows folded before this pass and counted again after it, because their
    #: evidence changed.
    released: int = 0


@dataclass(frozen=True)
class SameMoneyPlan:
    #: Folded row's entity id -> the closing day of the statement it is the same
    #: money as.
    folds: Mapping[str, date]
    #: What the rule did for each account it looked at, in account order.
    outcomes: tuple[AccountOutcome, ...] = ()


@dataclass(frozen=True)
class _Candidate:
    window: _Window
    feed: str
    rows: tuple[Transaction, ...]


@dataclass(frozen=True)
class _Attempt:
    """One closing against one feed: what was searched and what was found."""

    window: _Window
    feed: str
    #: Every unclaimed unmatched feed row in the band, latest first; the search
    #: takes at most `MAX_BAND_ROWS` of them.
    band: Sequence[Transaction]
    #: Band rows an earlier closing had claimed.
    claimed: Sequence[Transaction]
    #: Every statement-only row in the period, nearest the closing first; the
    #: search takes at most `MAX_STATEMENT_ROWS` of them.
    statement_rows: Sequence[Leftover]
    candidate: _Candidate | None


def _feed_candidates(
    evidence: AccountEvidence, feed: str, leftovers: Sequence[Leftover], covered: tuple[date, date]
) -> dict[str, Transaction]:
    """The feed's rows that could be the other half of a statement's leftovers.

    The feed-only leftovers of the pairing, and the feed's rows dated outside
    the span the pairing compared, which are unmatched by construction. Never a
    row a statement lists, a confirmed transfer leg, or one the cross-source
    page already excuses.
    """
    by_id = {row.entity_id: row for row in evidence.counted}
    listed = evidence.membership.placed
    found: dict[str, Transaction] = {}
    for leftover in leftovers:
        if (
            leftover.side == FEED_SIDE
            and not leftover.excuse
            and leftover.entity_id in by_id
            and leftover.entity_id not in listed
        ):
            found[leftover.entity_id] = by_id[leftover.entity_id]
    start, end = covered
    for sighting in evidence.sightings:
        if (
            sighting.source == feed
            and sighting.entity_id in by_id
            and sighting.entity_id not in listed
            and not sighting.transfer_confirmed
            and not (start <= sighting.value_date <= end)
        ):
            found[sighting.entity_id] = by_id[sighting.entity_id]
    return found


def _statement_rows(leftovers: Sequence[Leftover], window: _Window) -> list[Leftover]:
    """The period's statement-only rows, nearest the closing first."""
    return sorted(
        (
            leftover
            for leftover in leftovers
            if leftover.side == STATEMENT_SIDE
            and not leftover.excuse
            and window.first_day <= leftover.row_date <= window.last_day
        ),
        key=lambda leftover: (window.last_day - leftover.row_date, leftover.amount_minor),
    )


def _statement_sums(rows: Sequence[Leftover]) -> set[int]:
    """Every non-zero total some non-empty subset of the rows adds up to, taking
    at most `MAX_STATEMENT_ROWS` of them (the nearest, as `_statement_rows`
    orders them)."""
    sums: set[int] = set()
    for leftover in rows[:MAX_STATEMENT_ROWS]:
        sums |= {total + leftover.amount_minor for total in sums} | {leftover.amount_minor}
    sums.discard(0)
    return sums


def _band_rows(
    feed_rows: Iterable[Transaction], claimed: set[str], window: _Window
) -> tuple[list[Transaction], list[Transaction]]:
    """The feed rows dated within `BOUNDARY_DAYS` of the closing: the unclaimed
    ones, the latest first (a feed posts a statement's charges on or after its
    closing), and the ones an earlier closing had claimed. The search takes at
    most `MAX_BAND_ROWS` of the first."""
    near = [
        row
        for row in feed_rows
        if abs((row.value_date - window.last_day).days) <= BOUNDARY_DAYS
    ]
    free = [row for row in near if row.entity_id not in claimed]
    free.sort(key=lambda row: (row.value_date, row.entity_id), reverse=True)
    taken = sorted(
        (row for row in near if row.entity_id in claimed),
        key=lambda row: (row.value_date, row.entity_id),
    )
    return free, taken


def _attempts(
    evidence: AccountEvidence, chain: Sequence[_Window], claimed: set[str]
) -> list[_Attempt]:
    """Every closing against every feed paired with the statements, with the
    candidate it found, if any."""
    found: list[_Attempt] = []
    for feed, paired in evidence.paired.items():
        if not feed or paired is None:
            continue
        leftovers, covered_from, covered_to = paired
        feed_rows = _feed_candidates(evidence, feed, leftovers, (covered_from, covered_to))
        for window in chain:
            statement_rows = _statement_rows(leftovers, window)
            sums = _statement_sums(statement_rows)
            free, already = _band_rows(feed_rows.values(), claimed, window)
            band = free[:MAX_BAND_ROWS]
            taking = (
                next(
                    (
                        subset
                        for size in range(1, len(band) + 1)
                        for subset in combinations(band, size)
                        if sum(row.amount_minor for row in subset) in sums
                    ),
                    None,
                )
                if sums and band
                else None
            )
            candidate = None
            if taking is not None:
                claimed.update(row.entity_id for row in taking)
                candidate = _Candidate(window, feed, taking)
            found.append(
                _Attempt(
                    window,
                    feed,
                    free,
                    already,
                    statement_rows,
                    candidate,
                )
            )
    return found


def _proven(
    evidence: AccountEvidence, chain: Sequence[_Window], candidates: Sequence[_Candidate]
) -> tuple[list[_Candidate], dict[int, tuple[_Window, ...]]]:
    """The candidates whose folds the statements' own arithmetic vouches for, and
    for each one refused (by `id`) the periods that still differed when it was.

    Every period a fold touches (its statement's, and any a folded row is dated
    in) must agree exactly once ALL the surviving folds are applied; a candidate
    that fails is dropped and the rest re-judged, until none changes.
    """
    active = list(candidates)
    refused: dict[int, tuple[_Window, ...]] = {}
    while True:
        gone = {row.entity_id for c in active for row in c.rows}
        remaining = [row for row in evidence.counted if row.entity_id not in gone]
        agrees = {
            id(window): sum(r.amount_minor for r in held_in(window, remaining, evidence.membership))
            == window.movement_minor
            for window in chain
        }
        kept = []
        for candidate in active:
            touched = [candidate.window] + [
                window
                for window in chain
                if window is not candidate.window
                and any(
                    window.first_day <= row.value_date <= window.last_day
                    for row in candidate.rows
                )
            ]
            if all(agrees[id(window)] for window in touched):
                kept.append(candidate)
            else:
                refused[id(candidate)] = tuple(w for w in touched if not agrees[id(w)])
        if len(kept) == len(active):
            return kept, refused
        active = kept


def _outcome_of(
    attempt: _Attempt, refused: Mapping[int, tuple[_Window, ...]], kept: bool
) -> ClosingOutcome:
    candidate = attempt.candidate
    if candidate is None:
        verdict = Verdict.NO_BAND_ROWS if not attempt.band else Verdict.NO_MATCH
    else:
        verdict = Verdict.FOLDED if kept else Verdict.REFUSED
    blocking = (
        ()
        if candidate is None or kept
        else tuple(
            (window.first_day, window.last_day) for window in refused.get(id(candidate), ())
        )
    )
    return ClosingOutcome(
        closing=attempt.window.last_day,
        feed=attempt.feed,
        verdict=verdict,
        band=(
            attempt.window.last_day - timedelta(days=BOUNDARY_DAYS),
            attempt.window.last_day + timedelta(days=BOUNDARY_DAYS),
        ),
        band_dates=tuple(sorted(row.value_date for row in attempt.band)),
        band_searched=MAX_BAND_ROWS,
        claimed_dates=tuple(row.value_date for row in attempt.claimed),
        statement_dates=tuple(sorted(row.row_date for row in attempt.statement_rows)),
        statement_searched=MAX_STATEMENT_ROWS,
        taken_dates=(
            ()
            if candidate is None
            else tuple(sorted(row.value_date for row in candidate.rows))
        ),
        blocking=blocking,
    )


def plan_same_money(evidence: Iterable[AccountEvidence]) -> SameMoneyPlan:
    """Which feed rows are the same money as a statement's rows, and what the
    rule did at every closing it looked at. Pure."""
    folds: dict[str, date] = {}
    outcomes: list[AccountOutcome] = []
    for item in evidence:
        if item.withheld:
            outcomes.append(
                AccountOutcome(item.account, item.feeds, withheld=item.withheld)
            )
            continue
        chain = sorted(
            (w for w in item.windows if w.kind is not PeriodKind.INSIDE),
            key=lambda w: w.last_day,
        )
        if not chain:
            outcomes.append(AccountOutcome(item.account, item.feeds))
            continue
        attempts = _attempts(item, chain, set())
        kept, refused = _proven(item, chain, [a.candidate for a in attempts if a.candidate])
        kept_ids = {id(candidate) for candidate in kept}
        for candidate in kept:
            for row in candidate.rows:
                folds[row.entity_id] = candidate.window.last_day
        outcomes.append(
            AccountOutcome(
                item.account,
                item.feeds,
                unpaired=tuple(
                    feed for feed, paired in item.paired.items() if feed and paired is None
                ),
                closings=tuple(
                    sorted(
                        (
                            _outcome_of(
                                a, refused, a.candidate is not None and id(a.candidate) in kept_ids
                            )
                            for a in attempts
                        ),
                        key=lambda o: (o.feed, o.closing),
                    )
                ),
            )
        )
    return SameMoneyPlan(folds, tuple(outcomes))


def fold_same_money(store: Store, account_map: AccountMap | None = None) -> SameMoneyReport:
    """Fold the feed rows that are the same money as a statement's, and commit.

    Call after every batch that can add or change rows and after
    `fold_space_copies`, and once at the end of a rebuild after transfer pairing,
    so that a transfer leg is known before a row is judged. `account_map` gives
    the pairing the sibling accounts the cross-source page uses; without one the
    pairing is told there are none.
    """
    sibling = None if account_map is None else account_map.accounts_by_source()
    before = store.statement_folded_ids()
    # Before the release below, because it commits: a reading kept is never
    # part of a pass that can still be rolled back.
    with instrumentation.phase(f"{SAME_MONEY_PHASE}/reading-statements"):
        keep_statement_readings(store)
    try:
        store.release_statement_folds()
        evidence = gather_evidence(store, sibling_accounts=sibling)
        with instrumentation.phase(f"{SAME_MONEY_PHASE}/search"):
            plan = plan_same_money(evidence)
        with instrumentation.phase(f"{SAME_MONEY_PHASE}/writing"):
            store.replace_same_money_outcomes(
                {outcome.account: outcome.to_text() for outcome in plan.outcomes}
            )
            store.replace_statement_folds(plan.folds)
    except BaseException:
        store.connection.rollback()
        raise
    after = store.statement_folded_ids()
    return SameMoneyReport(
        folded=len(after), newly_folded=len(after - before), released=len(before - after)
    )
