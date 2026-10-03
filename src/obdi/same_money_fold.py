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

Within one statement's period, the statement-only rows and the feed-only rows
the pairing left unmatched (`period_reconciliation`'s, which is the
cross-source page's own pairing) are the same money when ALL of:

  - both sides are non-empty and sum to EXACTLY the same non-zero figure in
    minor units;
  - the feed rows are dated within the period or up to `BOUNDARY_DAYS` after
    its closing, which is where a feed posts a statement's last charges;
  - folding the feed rows makes every period they touch agree exactly with its
    statement's movement. The statement's own arithmetic is the proof: equal
    sums alone are a coincidence waiting to happen, and a fold that leaves the
    period differing has hidden something that is not a duplicate.

Then the FEED-side rows are folded (status FOLDED, no longer counted, withheld
from the push, sightings kept) and the statement's rows stay counted. They are
kept because they are what the statement's balance is made of, so every later
anchor check stays exact.

Never folded: a row that is a confirmed transfer leg, a row some statement lists
(it is the statement's own, however its dates fell in the pairing), a row
outside the boundary window, or a row of another account. A period with
leftovers on one side only is left alone. A feed row is taken for the EARLIEST
statement it could belong to, so a neighbouring month's row is not claimed
twice; if that statement's fold is refused, the next statement sees the row
among its own candidates, the sums stop being equal, and it is refused too.

The pass is a pure function of the stored rows, rewritten wholesale after every
import, pull, and assignment and once at the end of a rebuild, so the outcome
does not depend on which source arrived first; a fold whose evidence goes away
(a statement no longer held, a statement later listing the row) is released.
It is modelled on `space_attribution`, and the two never touch each other's
rows: a Space-folded row has a copied sighting on the Space row, and this
pass's folds have none, which is how the store tells them apart.

REJECTED. Folding the STATEMENT's side: the next statement's check would then
need the feed's date to fall in the right period, which is the fault being
fixed. Pairing rows one to one: three rows against one cannot pair. Folding on
equal sums alone: see above. A wider boundary than two days (a month was
tried on paper): it reaches the next statement's own rows, and a feed row of
the right size in the next period becomes a candidate for this one.

NOT COVERED. A feed row dated beyond the span the pairing compared is treated
as unmatched, which it is by construction (nothing in the statements is dated
that late), but its statement-side partners are only the leftovers inside the
span. Coincidental equal sums that also make the statement's arithmetic exact
would still fold; nothing here tells that apart from the real thing. Cost: the
pass runs the cross-source pairing over the whole store after each import where
an account holds two statements; it has not been timed on a store of real size.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .accounts import AccountMap
from .models import Transaction
from .period_reconciliation import (
    BOUNDARY_DAYS,
    FEED_SIDE,
    STATEMENT_SIDE,
    AccountEvidence,
    Leftover,
    PeriodKind,
    _Window,
    gather_evidence,
    held_in,
)
from .store import Store


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


@dataclass(frozen=True)
class _Candidate:
    window: _Window
    rows: tuple[Transaction, ...]


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


def _candidates(
    evidence: AccountEvidence, chain: Sequence[_Window], claimed: set[str]
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for feed, paired in evidence.paired.items():
        if not feed or paired is None:
            continue
        leftovers, covered_from, covered_to = paired
        feed_rows = _feed_candidates(evidence, feed, leftovers, (covered_from, covered_to))
        for window in chain:
            statement_only = [
                leftover
                for leftover in leftovers
                if leftover.side == STATEMENT_SIDE
                and not leftover.excuse
                and window.first_day <= leftover.row_date <= window.last_day
            ]
            reach = window.last_day + timedelta(days=BOUNDARY_DAYS)
            taking = [
                row
                for row in feed_rows.values()
                if row.entity_id not in claimed and window.first_day <= row.value_date <= reach
            ]
            if not statement_only or not taking:
                continue
            owed = sum(leftover.amount_minor for leftover in statement_only)
            if owed == 0 or owed != sum(row.amount_minor for row in taking):
                continue
            claimed.update(row.entity_id for row in taking)
            found.append(_Candidate(window, tuple(taking)))
    return found


def _proven(
    evidence: AccountEvidence, chain: Sequence[_Window], candidates: Sequence[_Candidate]
) -> list[_Candidate]:
    """The candidates whose folds the statements' own arithmetic vouches for.

    Every period a fold touches (its statement's, and any a folded row is dated
    in) must agree exactly once ALL the surviving folds are applied; a candidate
    that fails is dropped and the rest re-judged, until none changes.
    """
    active = list(candidates)
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
                if any(
                    window.first_day <= row.value_date <= window.last_day
                    for row in candidate.rows
                )
            ]
            if all(agrees[id(window)] for window in touched):
                kept.append(candidate)
        if len(kept) == len(active):
            return kept
        active = kept


def plan_same_money(evidence: Iterable[AccountEvidence]) -> SameMoneyPlan:
    """Which feed rows are the same money as a statement's rows. Pure."""
    folds: dict[str, date] = {}
    for item in evidence:
        if item.withheld:
            continue
        chain = sorted(
            (w for w in item.windows if w.kind is not PeriodKind.INSIDE),
            key=lambda w: w.last_day,
        )
        if not chain:
            continue
        for candidate in _proven(item, chain, _candidates(item, chain, set())):
            for row in candidate.rows:
                folds[row.entity_id] = candidate.window.last_day
    return SameMoneyPlan(folds)


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
    try:
        store.release_statement_folds()
        plan = plan_same_money(gather_evidence(store, sibling_accounts=sibling))
        store.replace_statement_folds(plan.folds)
    except BaseException:
        store.connection.rollback()
        raise
    after = store.statement_folded_ids()
    return SameMoneyReport(
        folded=len(after), newly_folded=len(after - before), released=len(before - after)
    )
