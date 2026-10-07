"""The same money, itemised differently by two sources, is counted once.

A card statement lists a recurring charge (a plan fee, a levy) on its closing
day, and the aggregator's feed carries the same money ONCE, dated the FIRST day
of the SAME period, a month before the statement's rows. Identity cannot merge
them: no date matches, and the statement may itemise the charge as several rows.
So the store holds both. Measured on a real card, every period after the first
was over by exactly that feed row, and the account's total by every one of them,
while the statements' own arithmetic was exact. The feed row dated the day AFTER
a closing is the NEXT statement's charge, not this one's.

The rule, per statement period, over the rows the pairing left unmatched
(`period_reconciliation`'s, which is the cross-source page's own pairing). The
period's DIFFERENCE is what the store counts in it less the statement's
movement. A period that agrees, or that holds no statement-only row, folds
nothing. Otherwise the candidates are the feed's unmatched rows dated WITHIN the
period, and a set of them is the same money as the statement's when ALL of:

  - its sum is EXACTLY the period's difference, so folding it makes the period
    agree and nothing else does (the difference is what makes the choice
    determinate where several rows share an amount);
  - that sum is EXACTLY the sum of some non-empty subset of the period's
    statement-only rows, in non-zero minor units: the folded money is money the
    statement itemises (the statement may also print rows the feed never saw,
    and those take no part);
  - after ALL folds every period they touch agrees exactly with its statement's
    movement. The difference makes this true of the period itself; the check
    stays because it is the statements' own arithmetic vouching for the pass, and
    a fold that cannot make a period agree has hidden something that is not a
    duplicate.

The statement side asks only "does the statement itemise this money?", and an
excuse the cross-source page gave a row (so that it is not reported as a
discrepancy there) answers that neither way. So an excused statement-only row
may be part of the subset, and the period's difference remains the real test.
The feed side is the opposite, deliberately: an excused feed row is one the
cross-source page has already explained as something else (a transfer leg), and
folding it would explain it twice. Measured on a real card, the statement row
that WAS the charge was excused, the rule searched only the others, and that one
period kept differing. The excuses a statement-only row can carry, each judged
by whether it says the row is not THIS account's money (that alone would bar it,
since the statement side is what the period's movement is made of):
  - a proven internal transfer: a leg printed in this account's statement, so
    it is this account's money. The opposite leg is another account's row, which
    this fold never touches.
  - matched to a row filed under a sibling account: an equal row of the other
    source exists under another account within days. That is a pairing
    heuristic about where the OTHER source filed a copy, not a finding that
    this statement's row is not its own; the sibling's row is not used here and
    its arithmetic is untouched.
Neither bars the row. Where an excused and an unexcused subset make the same
sum, the one with fewer excused rows is taken, so an excuse is leaned on only
when it has to be, and the page names which rows were used by date.

Among the sets that qualify the smallest is taken, and of equal size the one
dated latest. The reason is a period spanning a statement that is not held: it
holds that statement's charge too, dated earlier, and where the two share an
amount either makes the period agree. The held statement's is the later one, and
the earlier is the charge nothing else records. The choice is made once and is
not retried when the proof refuses it. A feed row dated after a closing belongs
to the next period's arithmetic, which judges it when that statement is held.

Then the FEED-side rows are folded (status FOLDED, no longer counted, withheld
from the push, sightings kept) and the statement's rows stay counted. They are
kept because they are what the statement's balance is made of, so every later
anchor check stays exact.

Never folded: a row that is a confirmed transfer leg, a row some statement lists
(it is the statement's own, however its dates fell in the pairing), a row dated
outside the period, or a row of another account.

A period that differs for a reason that is not a charge (a feed-only purchase no
statement lists) folds nothing: its difference is the charge AND the purchase,
which no subset of the statement's rows sums to, and the page says the period
differs. Folding the charge alone would leave the period differing by a figure
nobody has explained. That is the proof working, and it is the honest state.

Bounds, so the work per period is fixed whatever a store holds: each feed row is
tried alone, however many a period holds (a gap period spanning a missing
statement held 32 on a real card); sets of two to `MAX_SET_SIZE` are tried only
over the latest `MAX_SET_ROWS` rows; the statement side takes at most
`MAX_STATEMENT_ROWS` rows, so 2**12 sums. A real card's statement-only rows
numbered at most four a period and its difference was one row, so none of the
bounds reaches a real period; one past a bound folds less, never more, and the
page's record says which bound was reached.

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

REJECTED. Two earlier rules, each built from a guess about a real card's shape
and each folding nothing on it.
0.4.266 took every unclaimed feed row from the period's first day to two days
past its closing and required ALL the period's statement-only rows to equal ALL
of them. It looked in the right place and reached too far: the next statement's
charge sits two days past the closing, so every period's sum was spoiled by the
next period's row (8 of 10 real periods still differing).
0.4.268 took the feed rows within two days either side of the CLOSING and a
subset sum against the statement's rows. It looked in the wrong place: the
statement's charge is dated its period's first day, and the row near the closing
is the next statement's, which no subset of THIS statement's rows ever equalled
(nothing folded on the real card; its page showed every closing with one
unmatched row dated closing + 1).
A second source tried only where the in-period search finds nothing, for the
band after a closing: no real card has shown a charge dated there, and a row
outside the period cannot change the period's sum, so the proof could never
accept it as this period's. A rule nobody has observed a use for can only
misfire.
Folding the STATEMENT's side: the next statement's check would then need the
feed's date to fall in the right period, which is the fault being fixed.
Pairing rows one to one: three rows against one cannot pair. Folding on equal
sums alone: a coincidence waiting to happen. Folding the charge alone from a
period that also differs by a purchase: it leaves the period differing and
reads as success. Retrying the next subset when the proof refuses the first: a
stranger row of the charge's own amount would stand in for the charge and leave
the charge's own row double counted. Loosening the proof so a period that
differs for another reason does not stop its own fold: the proof is the only
thing separating a duplicate from a purchase.

NOT COVERED. A feed row dated beyond the span the pairing compared is treated
as unmatched, which it is by construction (nothing in the statements is dated
that late), but its statement-side partners are only the leftovers inside the
span. Coincidental equal sums that also make the statement's arithmetic exact
would still fold; nothing here tells that apart from the real thing. Among
equal-amount candidates that equally make the period agree (a fixed monthly fee
across a gap period) the later is taken on the reasoning above, which no data
has confirmed; the period's total is right either way and only the hidden row's
date is in doubt. A charge the feed dates in a period other than its
statement's (or a statement whose charge the feed never carries) is not found.
Statement rows dated after the feed's own last row lie outside the span the
pairing compared and are not leftovers, so the last statement's charge folds
only once the feed holds something later than that closing. A fixed charge, one
amount every month: the identity layer merges each statement's charge row with
the feed row of that amount dated a day after its closing (the next month's), so
the run's first feed row is left over with no statement-only row to be the same
money as, and is not folded.
A period whose first statement's opening is unknown is not tested, so its own
charge stays counted. Cost: the pass reads the whole store's sightings and
pairs the sources of each account that holds a statement, after every import.
Its steps record as sub-phases (`SAME_MONEY_PHASE`), so a slow rebuild names
which one. What a real store has that a synthetic one lacks was the documents
themselves: every pass used to extract the text of every held statement again,
which is slow only for a real PDF. A statement's reading is now kept
(`statement_terms.keep_statement_readings`) and the pass reads that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations

from ..core import instrumentation
from ..core.models import Transaction
from ..period_reconciliation import (
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
from ..same_money_outcome import AccountOutcome, ClosingOutcome, Verdict
from .accounts import AccountMap
from .statement_terms import keep_statement_readings
from .store import Store

MAX_STATEMENT_ROWS = 12
MAX_SET_ROWS = 12
MAX_SET_SIZE = 4


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
    #: The statement-only rows whose sum the feed rows equal.
    statement: tuple[Leftover, ...] = ()


@dataclass(frozen=True)
class _Attempt:
    """One period against one feed: what was searched and what was found."""

    window: _Window
    feed: str
    #: Whether the period's rows already sum to the statement's movement.
    agrees: bool
    #: Every unmatched feed row dated in the period, latest first.
    feed_rows: Sequence[Transaction]
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
    """The period's statement-only rows, excused or not, nearest the closing
    first."""
    return sorted(
        (
            leftover
            for leftover in leftovers
            if leftover.side == STATEMENT_SIDE
            and window.first_day <= leftover.row_date <= window.last_day
        ),
        key=lambda leftover: (window.last_day - leftover.row_date, leftover.amount_minor),
    )


def _statement_subsets(rows: Sequence[Leftover]) -> dict[int, tuple[Leftover, ...]]:
    """Every non-zero total some non-empty subset of the rows adds up to, with
    the subset that makes it, taking at most `MAX_STATEMENT_ROWS` of them (the
    nearest, as `_statement_rows` orders them). Of subsets with one total, the
    one with fewest excused rows, then fewest rows, then the earliest in
    `rows` order."""

    def cost(subset: tuple[tuple[int, Leftover], ...]) -> tuple[int, int, tuple[int, ...]]:
        return (
            sum(1 for _, row in subset if row.excuse),
            len(subset),
            tuple(index for index, _ in subset),
        )

    best: dict[int, tuple[tuple[int, Leftover], ...]] = {}
    for index, leftover in enumerate(rows[:MAX_STATEMENT_ROWS]):
        grown = [
            (total + leftover.amount_minor, (*subset, (index, leftover)))
            for total, subset in best.items()
        ]
        grown.append((leftover.amount_minor, ((index, leftover),)))
        for total, subset in grown:
            if total not in best or cost(subset) < cost(best[total]):
                best[total] = subset
    best.pop(0, None)
    return {total: tuple(row for _, row in subset) for total, subset in best.items()}


def _period_rows(feed_rows: Iterable[Transaction], window: _Window) -> list[Transaction]:
    """The feed rows dated within the period, the latest first."""
    inside = [row for row in feed_rows if window.first_day <= row.value_date <= window.last_day]
    inside.sort(key=lambda row: (row.value_date, row.entity_id), reverse=True)
    return inside


def _smallest_set(
    rows: Sequence[Transaction], difference: int, statement_subsets: Mapping[int, object]
) -> tuple[Transaction, ...] | None:
    """The smallest set of the rows summing to the period's difference, if that
    sum is also one the statement's rows make; the earliest in `rows` order
    among sets of one size. Each row is tried alone whatever their number; sets
    of more are tried over the first `MAX_SET_ROWS` only."""
    if difference == 0 or difference not in statement_subsets:
        return None
    for row in rows:
        if row.amount_minor == difference:
            return (row,)
    near = rows[:MAX_SET_ROWS]
    for size in range(2, min(MAX_SET_SIZE, len(near)) + 1):
        for subset in combinations(near, size):
            if sum(row.amount_minor for row in subset) == difference:
                return subset
    return None


def _attempts(evidence: AccountEvidence, chain: Sequence[_Window]) -> list[_Attempt]:
    """Every period against every feed paired with the statements, with the
    candidate it found, if any.

    A feed's rows are removed from the count before the next feed is judged, so
    two feeds cannot each fold the one difference.
    """
    found: list[_Attempt] = []
    taken: set[str] = set()
    for feed, paired in evidence.paired.items():
        if not feed or paired is None:
            continue
        leftovers, covered_from, covered_to = paired
        feed_rows = _feed_candidates(evidence, feed, leftovers, (covered_from, covered_to))
        counted = [row for row in evidence.counted if row.entity_id not in taken]
        for window in chain:
            held = held_in(window, counted, evidence.membership)
            difference = sum(row.amount_minor for row in held) - window.movement_minor
            statement_rows = _statement_rows(leftovers, window)
            in_period = _period_rows(feed_rows.values(), window)
            subsets = _statement_subsets(statement_rows)
            taking = (
                _smallest_set(in_period, difference, subsets)
                if difference and statement_rows and in_period
                else None
            )
            candidate = None
            if taking is not None:
                taken.update(row.entity_id for row in taking)
                candidate = _Candidate(window, feed, taking, subsets[difference])
            found.append(
                _Attempt(window, feed, difference == 0, in_period, statement_rows, candidate)
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
    if candidate is not None:
        verdict = Verdict.FOLDED if kept else Verdict.REFUSED
    elif attempt.agrees:
        verdict = Verdict.AGREES
    elif not attempt.feed_rows:
        verdict = Verdict.NO_FEED_ROWS
    elif not attempt.statement_rows:
        verdict = Verdict.NO_STATEMENT_ROWS
    else:
        verdict = Verdict.NO_MATCH
    blocking = (
        ()
        if candidate is None or kept
        else tuple(
            (window.first_day, window.last_day) for window in refused.get(id(candidate), ())
        )
    )
    statement = sorted(attempt.statement_rows, key=lambda row: (row.row_date, row.excuse))
    matched = (
        []
        if candidate is None
        else sorted(candidate.statement, key=lambda row: (row.row_date, row.excuse))
    )
    return ClosingOutcome(
        closing=attempt.window.last_day,
        feed=attempt.feed,
        verdict=verdict,
        period=(attempt.window.first_day, attempt.window.last_day),
        feed_dates=tuple(sorted(row.value_date for row in attempt.feed_rows)),
        feed_searched=MAX_SET_ROWS,
        statement_dates=tuple(row.row_date for row in statement),
        statement_searched=MAX_STATEMENT_ROWS,
        taken_dates=(
            ()
            if candidate is None
            else tuple(sorted(row.value_date for row in candidate.rows))
        ),
        blocking=blocking,
        statement_excuses=tuple(row.excuse for row in statement),
        matched_dates=tuple(row.row_date for row in matched),
        matched_excuses=tuple(row.excuse for row in matched),
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
        attempts = _attempts(item, chain)
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
