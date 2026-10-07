"""Resolving whether an incoming transaction is one already seen.

Tiered, highest fidelity first, and it never guesses:

  1. exact source_id, scoped to (account, source)
  2. exact content_key
  3. fuzzy - same account, exact amount, value date within a window
  4. unresolved - stored as new

Every tier below the first also asks `could_be_one_payment`, which is where the
source tier decides what a resemblance is worth. Without it the rules answer
only "is this the same payment seen through a different door?" and never the
commoner opposite, "are these two different payments that merely look alike?".

Tier 3's window is +/- 7 days between machine-read sources, widening to 10 when
one side was typed by a person, since a remembered date is approximate. Actual
Budget uses 7 and beancount-import 5; YNAB uses 10 for hand-entered records.

Note what tier 4 does NOT mean: unresolved is the normal state of every
genuinely new transaction, so it is not by itself cause for review. Only a
near-miss - something that matched on amount and date and was held apart by the
source rules - is worth a human decision, and only that is queued.

Separately, `pair_internal_transfers` handles a different problem: a movement
between two of your own accounts arrives twice, once as a debit and once as a
credit. Unpaired, it inflates both spending and income.
"""

from __future__ import annotations

import contextlib
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import TypeVar

from ..core.models import (
    BASIS_ID,
    BASIS_MANUAL,
    BASIS_OWN_ID,
    BASIS_SETTLEMENT,
    BASIS_WINDOW,
    MatchTier,
    SourceTier,
    Transaction,
    TransactionStatus,
)
from .payment_links import FIRST_PARTY_FEEDS, feed_uid_of, stated_link_of
from .stated_times import lists_on_settlement_day, settlement_days_of

_K = TypeVar("_K")

FUZZY_WINDOW_DAYS = 7

# Wider, because a hand-entered date is remembered rather than observed. YNAB
# uses ten days when matching an import against a user-entered transaction, and
# their reasoning applies here unchanged.
MANUAL_WINDOW_DAYS = 10

INTERNAL_TRANSFER_WINDOW_DAYS = 1

#: The most records or rows in one group that `assign_as_a_set` solves exactly.
#: `plan_partners` states the rule and what happens past this.
ASSIGNMENT_SET_LIMIT = 12

#: Sources that name a payment by ONE id for its whole life, pending and
#: settled alike.
#: For these a different id is always a different payment.
#: A source is listed only once that is known of it: an aggregator that
#: reissues a payment under a new id when it settles must not be here, or
#: every settlement would read as a second payment.
SETTLEMENT_KEEPS_ID = frozenset({"starling"})

#: Ends the reason of a review flag raised because exact rules disagree (an id against a size,
#: or two ids against each other), which `review_report.assess_flags` never settles for it:
#: the duplicate-report passes close a flag when no neighbour is live, and here no neighbour
#: is the question.
EXACT_RULE_DOUBT = "(exact-rule doubt)"


def could_be_one_payment(
    incoming: Transaction, candidate: Transaction, *, same_content: bool
) -> bool:
    """Whether these two records could be one payment observed twice.

    Two questions have to be answered, and they pull in opposite directions:
    "is this the same payment seen through a different door?" and "are these two
    different payments that merely look alike?". A statement is mostly full of
    the second, so a rule that only answers the first destroys data.

    Within ONE source, two records are two payments. A bank does not report the
    same payment twice in one export, so an id-less file listing three weekly
    standing orders of equal value is three payments, not one seen thrice.

    The single exception is settlement: a pending record and a settled one from
    the same source CAN be one payment, and that pairing must survive, which is
    why the test is an exclusive-or rather than "neither is pending".
    """
    # A person meant to record two things. Never collapse that, whatever the
    # figures look like - it is the one input carrying intent rather than
    # observation.
    if incoming.tier is SourceTier.MANUAL and candidate.tier is SourceTier.MANUAL:
        return False

    # An import may CLAIM a hand-entered record: you note a payment, the feed
    # reports it days later, and they are one payment. Deliberately permissive,
    # because a remembered date is approximate - YNAB allows ten days for the
    # same reason. The precise record absorbs the imprecise one.
    if SourceTier.MANUAL in (incoming.tier, candidate.tier):
        return True

    if candidate.source != incoming.source:
        return True

    # Settlement next, because it is the one case where a source deliberately
    # reissues a payment under a NEW identifier. Testing ids before this would
    # read that reissue as proof of two separate payments and duplicate every
    # transaction as it settled.
    incoming_pending = incoming.status is TransactionStatus.PENDING
    candidate_pending = candidate.status is TransactionStatus.PENDING
    if incoming_pending != candidate_pending:
        # Settlement runs one way.
        # A pending record dated after a settled row cannot be that row's
        # precursor, so it is a different payment; a pending record dated on
        # or before it may be the stale tail of a pending list that still
        # names a payment already settled.
        return not (incoming_pending and incoming.value_date > candidate.value_date)

    both_authoritative = (
        incoming.tier is SourceTier.AUTHORITATIVE and candidate.tier is SourceTier.AUTHORITATIVE
    )
    if both_authoritative and incoming.source_id and candidate.source_id:
        # Two durable identifiers from one source settle it outright, in both
        # directions. The source itself has told us, and nothing below can know
        # better than that.
        return incoming.source_id == candidate.source_id

    if not same_content:
        # Different content within one source is two different payments, with
        # no exception. A source never reports one payment twice under two
        # different descriptions or dates in the same breath, so this is what
        # keeps a weekly standing order from collapsing into a single row.
        return False

    # Identical content within one source is genuinely ambiguous: it is either
    # two matching payments, or one payment appearing in two overlapping
    # downloads. Occurrence separates them. A file holding two matching rows
    # numbers them 0 and 1, and re-importing it, or fetching an overlapping
    # range, reproduces that numbering - so first matches first and second
    # matches second, merging a repeat without ever merging two payments.
    return incoming.occurrence == candidate.occurrence


def could_be_reissue(incoming: Transaction, candidate: Transaction) -> bool:
    """Whether `incoming` could be a source's reissue of a row it already reported.

    For a candidate that the incoming record's own source has ALREADY called by
    a different id, though a later source now holds the row's `source` column.
    `could_be_one_payment` reads that column, so it takes such a row for a
    cross-source candidate and merges on amount and date alone: an aggregator's
    second payment of the same price, a few days on, was folded into the first
    after an export had merely sighted the first, with nothing flagged and one
    payment missing from every sum.

    Called only when the source has called the row by another id IN A SETTLED
    RESPONSE (`CandidateIndex.sighted_settled_under_another_id`). An earlier id
    seen only in pending snapshots is provisional, and the new id may be its
    settlement, so that case never reaches here and merges as any cross-source
    pair does. Holding it apart instead counted one card payment twice, on the
    most ordinary path there is: the aggregator pending, the bank's own feed
    settled, the aggregator settled under a new id.

    A settled id is not replaced, so a different one is evidence of a second
    payment, unless the incoming record is a stale pending tail of the row. So
    the only merge left is that shape: pending incoming, not dated after the
    row. Anything else is held apart, and the near-miss is queued for review:
    a wrongly separate row is visible, and a wrongly merged one is silent.
    """
    return (
        incoming.status is TransactionStatus.PENDING
        and candidate.status is not TransactionStatus.PENDING
        and incoming.value_date <= candidate.value_date
    )


def is_internal_leg_meeting_space_blind_source(
    incoming: Transaction, candidate: Transaction, blind: Callable[[str], bool]
) -> bool:
    """Whether one of these is an internal leg and the other's source cannot see Spaces.

    THE RULE. A row that is an internal leg is never a candidate for a row from a
    source that cannot see Spaces, in either direction.
    An internal leg is the feed's item whose counterparty is one of the account's
    own categories, or a derived round-up leg: `Transaction.is_internal_transfer`,
    which the feed's provider sets for exactly those.
    A source cannot see Spaces when the account map binds it to none of the
    account's Spaces (`Families.blind`), and `blind` answers that for this account.

    Why: the export and the aggregator list a payment made from a Space under the
    one account and never list a movement between the account and its Space.
    Merged by amount and nearby date into a transfer leg or a round-up leg, the
    payment's row stood in for a movement it could not have reported:
    "out row dated starling 2021-11-10, starling-csv 2021-11-11, truelayer
    2021-11-11; seen by starling, starling-csv, truelayer; booked, a transfer leg,
    confirmed paired with the Space starling-space-bills", while the Space's own
    payment row was counted with no export sighting.
    Whether the pair merged also depended on which source arrived first.

    The test reads each row's own `source`, the last to observe it, because that
    is the only source a candidate carries into resolution.
    """
    return (incoming.is_internal_transfer and blind(candidate.source)) or (
        candidate.is_internal_transfer and blind(incoming.source)
    )


def _name_of(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def same_payee(first: Transaction, second: Transaction) -> bool:
    """Whether the two name the same payee, by their description or counterparty alone.

    Spelling, case, and spacing are ignored; a name that is empty names nobody.
    """
    names = {n for n in (_name_of(first.description), _name_of(first.counterparty)) if n}
    return any(
        n in names for n in (_name_of(second.description), _name_of(second.counterparty)) if n
    )


def settles_together(
    incoming: Transaction,
    incoming_days: frozenset[date],
    candidate: Transaction,
    candidate_days: frozenset[date],
) -> bool:
    """Whether the date one of these carries is the settlement day the other states.

    THE RULE. A bank's export dates a card payment by its SETTLEMENT day, so an export
    row and a feed row of one size and direction are the same payment when the export's
    day is the day the feed states it settled, however far that is from the day it was made
    (`stated_times.settlement_days` says which days a settlement can be listed under).
    Measured on the deployed store: two card payments of 2021-12-06 settled, and were
    listed by the export, on 2022-04-21; the export's rows lay outside the matcher's
    window of the payments, found no partner, and the store counted both payments twice
    for want of this.
    Where two rows of one size both lie inside an export row's window, the one whose
    settlement day is the export's day is its partner, and the window decides only when
    no settlement day does (`plan_partners` and `resolve` both read this).
    A wider window was rejected: it would pair unrelated payments of one size for every
    payment it rescued.
    Beyond the window the two must also name the same payee (`same_payee`), because the
    day and the size alone are then the only evidence; within it the matcher has never
    asked that of two sources, and does not here.
    """
    return incoming.value_date in candidate_days or candidate.value_date in incoming_days


def belongs_to_established_series(
    incoming: Transaction, candidates: Sequence[Transaction]
) -> bool:
    """Whether this looks like the next instalment of a regular commitment.

    A weekly standing order sits inside the fuzzy window, so every instalment
    after the first resembles the ambiguous case. Technically true, but roughly
    fifty flags a year for one commitment, and a queue that cries wolf weekly
    stops being read - which defeats it exactly when it matters.

    Regularity is what separates the two. Two prior instalments at a consistent
    interval make a rhythm, and a third arriving on schedule is expected. A
    repeat with NO established rhythm is still flagged, because that is the
    shape a duplicate report takes.

    Deliberately conservative: it takes two priors to establish a series, so
    the second instalment is still flagged once. Confirming a commitment once
    is a reasonable price for silence thereafter.

    THAT "ROUGHLY FIFTY" WAS AN ESTIMATE AND IS NOW MEASURED AT 46. Disabling
    this function against the generated corpus - one weekly commitment over six
    months - takes the review queue from 2 flags to 25, of which 24 are that
    single standing order. The corpus also holds one payment genuinely reported
    twice, and it stays flagged in both runs, so the silence bought here is not
    bought by going quiet in general. Reproduce with test_synthetic_corpus.py.
    """
    same_shape = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.amount_minor == incoming.amount_minor
            and candidate.description == incoming.description
            and candidate.value_date < incoming.value_date
        ),
        key=lambda candidate: candidate.value_date,
    )
    if len(same_shape) < 2:
        return False

    latest, previous = same_shape[-1], same_shape[-2]
    established = (latest.value_date - previous.value_date).days
    arriving = (incoming.value_date - latest.value_date).days
    if established <= 0:
        return False

    # A day either side, because a payment falling on a weekend or holiday
    # moves without ceasing to be the same commitment.
    return abs(arriving - established) <= 1


@dataclass(frozen=True)
class MatchResult:
    tier: MatchTier
    existing: Transaction | None
    # Candidates that looked alike on amount and date but were kept apart by
    # the same-source rule. Recorded because that is the one genuinely
    # ambiguous case: a repeated payment and a duplicate report have the same
    # shape, and being wrong is expensive in both directions.
    near_misses: tuple[Transaction, ...] = ()
    #: How the record came to be on `existing` (`models.BASIS_*`), empty when it is new.
    basis: str = ""
    #: A doubt the exact rules raised, for the row the record ends on to be queued with:
    #: empty when they raised none.
    review: str = ""

    @property
    def is_new(self) -> bool:
        return self.existing is None

    #: Set when the near-misses turned out to be an established rhythm rather
    #: than a genuine puzzle - a standing order arriving on schedule.
    recurring: bool = False

    @property
    def is_ambiguous(self) -> bool:
        """Stored as new, but something similar was deliberately not matched.

        Not the same as `is_new`: every genuinely new transaction is
        unresolved, and flagging all of them would bury the few worth looking
        at under thousands that are not. Nor does a near-miss alone qualify - a
        recognised recurring series resembles its own past instalments by
        definition, and saying so weekly teaches the reader to ignore it.
        """
        return self.existing is None and bool(self.near_misses) and not self.recurring


class CandidateIndex:
    """The account's stored candidates, arrival-ordered, with hash lookups.

    resolve() asks three shapes of question and each has a natural index:
    tier 1 is an exact (account, source, source_id) probe, tier 2 an exact
    (account, content_key) probe, and everything fuzzy - tier 3, the
    near-miss ledger, the recurring-series check - only ever considers
    candidates of one exact amount. Scanning the whole account for each
    was what made replay quadratic.

    Buckets hold entity ids and every lookup returns candidates sorted by
    ARRIVAL position. That ordering is load-bearing twice over: tier 2
    returns the first match in arrival order, and the near-miss tuple
    preserves it. A superseded entity KEEPS its original position - the
    stored row it replaces was mutated, not re-appended - which is why
    replacement is by entity id into the same slot.

    Supersession can change an entity's source_id and content_key (a
    pending settling under a new identity does exactly that), so replace()
    removes the entity from every old bucket before filing the new keys.
    A stale key here would let a record match a rendering the store no
    longer holds - invisible in output until it merges the wrong payment.
    """

    def __init__(
        self,
        transactions: Iterable[Transaction] = (),
        sightings: Iterable[tuple[str, str, str, bool, bool]] = (),
        space_blind: Callable[[str], bool] | None = None,
        settlements: Mapping[str, Iterable[date]] | None = None,
        links: Iterable[tuple[str, str]] = (),
        listings: Iterable[tuple[str, str, str]] = (),
    ) -> None:
        # The days each row has been listed under by a source that lists a payment on its
        # settlement day, by (account, source, day): which only grows, and is what tells a
        # file listing the same row again from one listing another. See `listed_rows`.
        self._listed: dict[tuple[str, str, str], list[str]] = {}
        # Every first-party id any sighting of a row stated, which only grows like the ids above.
        self._links_of: dict[str, set[str]] = {}
        self._by_link: dict[tuple[str, str], list[str]] = {}
        # The days each row's sightings state it settled under, which only grow:
        # a row's stored record is its last writer's, and the sightings say the rest.
        self._settle: dict[str, set[date]] = {
            entity_id: set(days) for entity_id, days in (settlements or {}).items()
        }
        # Whether a source cannot see this account's Spaces, which only the
        # account map knows. None means nothing is known, so the rule that
        # keeps internal legs apart (`is_internal_leg_meeting_space_blind_source`)
        # has nothing to ask and every pair is judged as it was before it.
        self.space_blind: Callable[[str], bool] = space_blind or (lambda source: False)
        self._order: list[Transaction] = []
        self._position: dict[str, int] = {}
        self._by_source_id: dict[tuple[str, str, str], list[str]] = {}
        self._by_content: dict[tuple[str, str], list[str]] = {}
        self._by_amount: dict[tuple[str, int], list[str]] = {}
        # Every provider id a row has EVER been sighted under, which outlives
        # the row's own source and id.
        # A row carries one source at a time, the last to observe it; this is
        # what remembers the others.
        self._sighted: dict[tuple[str, str, str], list[str]] = {}
        self._ids_of: dict[str, dict[str, set[str]]] = {}
        # The id each row has answered to in the CURRENT batch, by source.
        # See `claim`.
        self._claimed: dict[str, dict[str, str]] = {}
        # Which of those ids were seen in a pending snapshot and which in a
        # settled response, per (row, source, id). See `sighted_settled_under_another_id`.
        self._seen_pending: set[tuple[str, str, str]] = set()
        self._seen_settled: set[tuple[str, str, str]] = set()
        # Whether the response now being resolved is a pending snapshot.
        self._batch_pending = False
        # Stored rows are filed with no evidence of their own: the row holds
        # only its last writer's digest, and the store's sightings say the rest.
        self._recording_evidence = False
        for transaction in transactions:
            self.append(transaction)
        for entity_id, source, source_id, was_pending, was_settled in sightings:
            position = self._position.get(entity_id)
            if position is not None:
                account_id = self._order[position].account_id
                self.note_sighting(
                    entity_id, account_id, source, source_id, record_evidence=False
                )
                key = (entity_id, source, source_id)
                if was_pending:
                    self._seen_pending.add(key)
                if was_settled:
                    self._seen_settled.add(key)
        for entity_id, link in links:
            position = self._position.get(entity_id)
            if position is not None:
                self.note_link(entity_id, self._order[position].account_id, link)
        for entity_id, source, day in listings:
            position = self._position.get(entity_id)
            if position is not None:
                self._note_listing(entity_id, self._order[position].account_id, source, day)
        self._recording_evidence = True

    def copy(self) -> CandidateIndex:
        """An index that can be changed without changing this one.

        The rows are shared, since a row is never altered in place (`replace` swaps one in), and
        every container is copied, so a batch can be tried against it and thrown away.
        """
        other = CandidateIndex.__new__(CandidateIndex)
        other.space_blind = self.space_blind
        other._order = list(self._order)
        other._position = dict(self._position)
        other._batch_pending = self._batch_pending
        other._recording_evidence = self._recording_evidence
        other._seen_pending = set(self._seen_pending)
        other._seen_settled = set(self._seen_settled)
        other._links_of = {k: set(v) for k, v in self._links_of.items()}
        other._settle = {k: set(v) for k, v in self._settle.items()}
        other._ids_of = {k: {s: set(v) for s, v in d.items()} for k, d in self._ids_of.items()}
        other._claimed = {k: dict(v) for k, v in self._claimed.items()}
        for name in (
            "_by_link",
            "_by_source_id",
            "_by_content",
            "_by_amount",
            "_sighted",
            "_listed",
        ):
            setattr(other, name, {k: list(v) for k, v in getattr(self, name).items()})
        return other

    def _note_listing(self, entity_id: str, account_id: str, source: str, day: str) -> None:
        held = self._listed.setdefault((account_id, source, day), [])
        if entity_id not in held:
            held.append(entity_id)

    def listed_rows(self, incoming: Transaction) -> list[Transaction]:
        """The rows of the record's size that its own source has already listed on its day.

        A row is listed by a source on the day that source dated it, whatever date the row
        carries now (a later sighting by another source may have moved it).
        Read by `plan_settlement`, which leaves a record alone when its source has already
        placed a row of its size on its day: a file listed again must find the row the first
        listing joined, and not the one its settlement day now names.
        """
        held = self._listed.get(
            (incoming.account_id, incoming.source, incoming.value_date.isoformat()), []
        )
        return [
            row
            for row in self._in_arrival_order(held)
            if row.amount_minor == incoming.amount_minor
        ]

    def note_link(self, entity_id: str, account_id: str, link: str) -> None:
        """Record that a sighting of this row stated this first-party id. Never undone."""
        if not link:
            return
        self._links_of.setdefault(entity_id, set()).add(link)
        held = self._by_link.setdefault((account_id, link), [])
        if entity_id not in held:
            held.append(entity_id)

    def links_of(self, entity_id: str) -> frozenset[str]:
        """The first-party ids the row's aggregator sightings stated."""
        return frozenset(self._links_of.get(entity_id, ()))

    def feed_uids_of(self, entity_id: str) -> frozenset[str]:
        """The feed uids the row's first-party sightings carry."""
        sighted = self._ids_of.get(entity_id, {})
        return frozenset(uid for source in FIRST_PARTY_FEEDS for uid in sighted.get(source, ()))

    def rows_stating(self, account_id: str, link: str) -> list[Transaction]:
        """Rows an aggregator sighting stated this first-party id for."""
        return self._in_arrival_order(self._by_link.get((account_id, link), []))

    def rows_with_feed_uid(self, account_id: str, uid: str) -> list[Transaction]:
        """Rows a first-party feed sighted under this uid."""
        found: list[Transaction] = []
        for source in sorted(FIRST_PARTY_FEEDS):
            found.extend(self.by_source_id(account_id, source, uid))
        return found

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self) -> Iterator[Transaction]:
        return iter(self._order)

    def note_sighting(
        self,
        entity_id: str,
        account_id: str,
        source: str,
        source_id: str | None,
        *,
        record_evidence: bool = True,
    ) -> None:
        """Record that `source` has called this row by `source_id`.

        Never undone. A row's own source and id change every time another
        source observes it, and a row that forgot what it had been called is
        how one payment came to be held twice: the first source re-reported
        it with a changed amount, found no row under its id, and made another.

        Also records whether the response now being resolved is a pending
        snapshot or a settled one, unless `record_evidence` is False (a stored
        row being loaded, whose evidence comes from the store instead).
        """
        if not source_id:
            return
        if record_evidence:
            (self._seen_pending if self._batch_pending else self._seen_settled).add(
                (entity_id, source, source_id)
            )
        held = self._sighted.setdefault((account_id, source, source_id), [])
        if entity_id not in held:
            held.append(entity_id)
        self._ids_of.setdefault(entity_id, {}).setdefault(source, set()).add(source_id)

    def sighted_under_another_id(
        self, entity_id: str, source: str, source_id: str | None
    ) -> bool:
        """Whether `source` has called this row by an id other than this one."""
        if not source_id:
            return False
        return bool(self._ids_of.get(entity_id, {}).get(source, set()) - {source_id})

    def sighted_settled_under_another_id(
        self, entity_id: str, source: str, source_id: str | None
    ) -> bool:
        """Whether `source` has called this row, in a settled response, by another id.

        An id seen ONLY in pending snapshots is provisional: the payment's
        settled id will replace it, so a different settled id may be that
        settlement. An id seen in a settled response is not provisional, and a
        different settled id after it is a different payment. An id with no
        evidence either way is taken as settled, because holding a payment
        apart is visible and merging two is not.
        """
        if not source_id:
            return False
        for other in self._ids_of.get(entity_id, {}).get(source, set()) - {source_id}:
            key = (entity_id, source, other)
            if key in self._seen_settled or key not in self._seen_pending:
                return True
        return False

    def begin_batch(self, *, pending_snapshot: bool = False) -> None:
        """Forget which row answered to which id; a new response starts clean.

        `pending_snapshot` says the response is a pending list, so ids it
        reports are noted as provisional.
        """
        self._claimed.clear()
        self._batch_pending = pending_snapshot

    @property
    def batch_is_pending_snapshot(self) -> bool:
        return self._batch_pending

    def claim(self, entity_id: str, source: str, source_id: str | None) -> None:
        """Record that, in this batch, this row answered to `source_id`.

        One response naming two ids is the provider saying two payments, so a
        row that has answered to one of a source's ids in a batch may not
        answer to another of them in the same batch.
        Without this the memory of every id a row was ever called made one
        early wrong merge permanent: both ids found the same row for ever,
        and one payment had no row of its own (one such on the deployed
        store, the day the memory was introduced).
        The first id to claim a row keeps it.
        """
        if source_id:
            self._claimed.setdefault(entity_id, {}).setdefault(source, source_id)

    def claimed_under_another_id(
        self, entity_id: str, source: str, source_id: str | None
    ) -> bool:
        """Whether this row has already answered, in this batch, to a
        different id from the same source."""
        if not source_id:
            return False
        held = self._claimed.get(entity_id, {}).get(source)
        return held is not None and held != source_id

    def settlement_days(self, entity_id: str) -> frozenset[date]:
        """The days any sighting of this row states it settled under."""
        return frozenset(self._settle.get(entity_id, ()))

    def _file(self, transaction: Transaction) -> None:
        own = settlement_days_of(transaction)
        if own:
            self._settle.setdefault(transaction.entity_id, set()).update(own)
        self.note_sighting(
            transaction.entity_id,
            transaction.account_id,
            transaction.source,
            transaction.source_id,
            record_evidence=self._recording_evidence,
        )
        self.note_link(
            transaction.entity_id, transaction.account_id, stated_link_of(transaction)
        )
        if lists_on_settlement_day(transaction.source):
            self._note_listing(
                transaction.entity_id,
                transaction.account_id,
                transaction.source,
                transaction.value_date.isoformat(),
            )
        if transaction.source_id:
            key = (transaction.account_id, transaction.source, transaction.source_id)
            self._by_source_id.setdefault(key, []).append(transaction.entity_id)
        if transaction.content_key:
            ckey = (transaction.account_id, transaction.content_key)
            self._by_content.setdefault(ckey, []).append(transaction.entity_id)
        akey = (transaction.account_id, transaction.amount_minor)
        self._by_amount.setdefault(akey, []).append(transaction.entity_id)

    def _unfile(self, transaction: Transaction) -> None:
        def drop(bucket: dict[_K, list[str]], key: _K) -> None:
            ids = bucket.get(key)
            if ids is not None:
                with contextlib.suppress(ValueError):
                    ids.remove(transaction.entity_id)
                if not ids:
                    del bucket[key]

        if transaction.source_id:
            drop(
                self._by_source_id,
                (transaction.account_id, transaction.source, transaction.source_id),
            )
        if transaction.content_key:
            drop(self._by_content, (transaction.account_id, transaction.content_key))
        drop(self._by_amount, (transaction.account_id, transaction.amount_minor))

    def append(self, transaction: Transaction) -> None:
        self._position[transaction.entity_id] = len(self._order)
        self._order.append(transaction)
        self._file(transaction)

    def replace(self, merged: Transaction) -> None:
        """Supersede in place: same entity, same arrival slot, fresh keys."""
        position = self._position[merged.entity_id]
        self._unfile(self._order[position])
        self._order[position] = merged
        self._file(merged)

    def row(self, entity_id: str) -> Transaction | None:
        """The stored row with this entity id, as the index holds it now."""
        position = self._position.get(entity_id)
        return None if position is None else self._order[position]

    def sighting_sources(self, entity_id: str) -> frozenset[str]:
        """Every source that has stated an id for this row, and the source that wrote it last."""
        found = set(self._ids_of.get(entity_id, {}))
        found.add(self._order[self._position[entity_id]].source)
        return frozenset(found)

    def could_be_one_row(self, first: str, second: str) -> bool:
        """Whether no source has called these two rows by different ids.

        A source that has called two rows by ids of its own, different from each other, has
        said they are two payments (`could_be_one_payment`), and no other evidence joins them.
        """
        ids = self._ids_of
        return not any(
            known and other and known != other
            for source, known in ids.get(first, {}).items()
            for other in [ids.get(second, {}).get(source, set())]
        )

    def absorb(self, kept: str, gone: str) -> None:
        """Make row `gone` part of row `kept`: every id, link, and day it was known by is kept's.

        The stored side is `Store.absorb_entity`; this keeps the in-memory index agreeing with it,
        so a later record of the same batch finds one row where two were. Positions are renumbered,
        which is linear in the account and so only done where two rows are proven one payment.
        """
        index = self._position[gone]
        self._unfile(self._order[index])
        del self._order[index]
        self._position = {t.entity_id: position for position, t in enumerate(self._order)}
        for held in self._sighted.values():
            if gone in held:
                held.remove(gone)
                if kept not in held:
                    held.append(kept)
        for source, source_ids in self._ids_of.pop(gone, {}).items():
            self._ids_of.setdefault(kept, {}).setdefault(source, set()).update(source_ids)
        for evidence in (self._seen_pending, self._seen_settled):
            for _entity, source, source_id in [k for k in evidence if k[0] == gone]:
                evidence.discard((gone, source, source_id))
                evidence.add((kept, source, source_id))
        self._links_of.setdefault(kept, set()).update(self._links_of.pop(gone, set()))
        for held in self._by_link.values():
            if gone in held:
                held.remove(gone)
                if kept not in held:
                    held.append(kept)
        self._settle.setdefault(kept, set()).update(self._settle.pop(gone, set()))
        for held in self._listed.values():
            if gone in held:
                held.remove(gone)
                if kept not in held:
                    held.append(kept)
        for source, source_id in self._claimed.pop(gone, {}).items():
            self._claimed.setdefault(kept, {}).setdefault(source, source_id)

    def _in_arrival_order(self, entity_ids: list[str]) -> list[Transaction]:
        return [
            self._order[self._position[entity_id]]
            for entity_id in sorted(
                entity_ids, key=lambda entity_id: self._position[entity_id]
            )
        ]

    def by_source_id(
        self, account_id: str, source: str, source_id: str
    ) -> list[Transaction]:
        """Rows this source has called by this id, whatever they carry now.

        A row that carries the id NOW comes before one that only used to.
        Once two payments wrongly merged have been separated, each must find
        its own row and not the older one that remembers them both, or the
        two rows trade payments on every later response.
        """
        key = (account_id, source, source_id)
        current = self._by_source_id.get(key, [])
        remembered = [e for e in self._sighted.get(key, []) if e not in current]
        return [*self._in_arrival_order(current), *self._in_arrival_order(remembered)]

    def by_content_key(self, account_id: str, content_key: str) -> list[Transaction]:
        return self._in_arrival_order(
            self._by_content.get((account_id, content_key), [])
        )

    def by_amount(self, account_id: str, amount_minor: int) -> list[Transaction]:
        return self._in_arrival_order(
            self._by_amount.get((account_id, amount_minor), [])
        )

    def free_occurrence(
        self, account_id: str, content_key: str, *, wanted: int, excluding: str = ""
    ) -> int:
        """The occurrence a row may take without sharing another row's identity.

        Content key plus occurrence is the identity everything downstream is
        keyed on, so within an account the pair must name exactly one row.
        A batch numbers its own repeats from zero and knows nothing of rows
        already held: two identical payments reported in separate responses
        were both numbered zero, stayed two rows because the provider's ids
        differed, and so reached the push to Actual sharing one imported id -
        which no rebuild could repair, because a rebuild replays the same
        batches.

        `wanted` is honoured whenever it is free.
        That keeps an id-less export merging occurrence for occurrence on
        re-import, and leaves every identity already handed out untouched -
        only a newcomer whose number is taken moves, to the lowest number
        nobody holds.

        `excluding` is the row being renumbered, when it is already held:
        a row does not collide with itself.
        """
        taken = {
            candidate.occurrence
            for candidate in self.by_content_key(account_id, content_key)
            if candidate.entity_id != excluding
        }
        if wanted not in taken:
            return wanted
        free = 0
        while free in taken:
            free += 1
        return free


class _Judgement:
    """What the stored rows are to one incoming record, by each tier's own question."""

    def __init__(self, incoming: Transaction, index: CandidateIndex) -> None:
        self.incoming = incoming
        self.index = index
        self.account = incoming.account_id
        self.incoming_days = settlement_days_of(incoming)
        self.link = stated_link_of(incoming)
        self.feed_uid = feed_uid_of(incoming)
        #: Set when an id names a row of another size, for `resolve` to flag.
        self.size_conflict = ""

    def named_by_id(self) -> Transaction | None:
        """THE ID TIER. The row an id names for this record, or None.

        An aggregator item states the uid of the feed item it reports, and the feed item's
        uid is what that statement names: so the item IS that payment, and no date,
        description, or window is consulted.
        Measured on one real month: 58 of 58 aggregator items carried a feed item's own
        uid, all agreeing with it on size and direction, and the matcher read none of them.
        Where the id names a row of another size the two cannot be one payment, so
        nothing merges and the doubt is flagged (`size_conflict`): a source's id that
        disagrees with its own figures is worth a person's eye, never a quiet pairing.
        """
        index = self.index
        if self.link:
            candidates = index.rows_with_feed_uid(self.account, self.link)
        elif self.feed_uid:
            candidates = index.rows_stating(self.account, self.feed_uid)
        else:
            return None
        for candidate in candidates:
            if self.taken(candidate):
                continue
            if candidate.amount_minor != self.incoming.amount_minor:
                self.size_conflict = (
                    "its id names a payment of another size, so the two were not merged: "
                    "an id that disagrees with its own figures needs a person's eye"
                )
                continue
            return candidate
        return None

    def contradicted(self, candidate: Transaction) -> bool:
        """Whether an exact rule says this row is a different payment from the record.

        The row is already known, by an id a sighting of it stated or carries, to be another
        payment: a row sighted under feed uid U is not the payment an aggregator item names L,
        and a row an aggregator sighting names L is not the feed item U.
        A heuristic pairing never overrides that, which is what stopped two equal payments
        minutes apart from swapping their sightings.
        """
        index, entity = self.index, candidate.entity_id
        if self.link:
            uids = index.feed_uids_of(entity)
            if uids and self.link not in uids:
                return True
            links = index.links_of(entity)
            if links and self.link not in links:
                return True
        if self.feed_uid:
            links = index.links_of(entity)
            if links and self.feed_uid not in links:
                return True
        return False

    def heuristic_basis(self, candidate: Transaction) -> str:
        """The basis of a pairing the exact rules did not decide."""
        if SourceTier.MANUAL in (self.incoming.tier, candidate.tier):
            return BASIS_MANUAL
        if self.settled_together(candidate):
            return BASIS_SETTLEMENT
        return BASIS_WINDOW

    # Provider ids are only unique within a provider's own namespace, so tier 1
    # matches on (source, source_id) rather than the id alone. Two sources
    # reporting the same payment are SUPPOSED to disagree here.
    def taken(self, candidate: Transaction) -> bool:
        # Already answered, in this batch, to another of this source's ids.
        return self.index.claimed_under_another_id(
            candidate.entity_id, self.incoming.source, self.incoming.source_id
        )

    def apart_by_kind(self, candidate: Transaction) -> bool:
        return is_internal_leg_meeting_space_blind_source(
            self.incoming, candidate, self.index.space_blind
        )

    def one_payment(self, candidate: Transaction, *, same_content: bool) -> bool:
        incoming, index = self.incoming, self.index
        if self.taken(candidate) or self.apart_by_kind(candidate) or self.contradicted(candidate):
            return False
        # Where a source keeps a payment's id through settlement, a row it
        # has already called by a different id is a different payment, and
        # no resemblance of amount, date, or status can make it the same.
        # Without this a new pending payment was merged into any settled row
        # of the same amount within a week, and replaced it.
        if incoming.source in SETTLEMENT_KEEPS_ID and index.sighted_under_another_id(
            candidate.entity_id, incoming.source, incoming.source_id
        ):
            return False
        # Before every other rule, hand-entered rows included: the row's
        # `source` is only the last writer, and this source's own earlier id on
        # it outranks any resemblance.
        if candidate.source != incoming.source and index.sighted_settled_under_another_id(
            candidate.entity_id, incoming.source, incoming.source_id
        ):
            return could_be_reissue(incoming, candidate)
        return could_be_one_payment(incoming, candidate, same_content=same_content)

    def exact(self, *, settled: str | None = None) -> MatchResult | None:
        """The row this record names by an id, by its settlement day, or by the content key.

        The source's own id and the id-link are each exact.
        Where they name DIFFERENT rows the evidence disagrees with itself, so the join already
        made stands (the sighting is on that row, and moving it would rewrite what a source
        said) and nothing new is merged on the other id: the row is flagged for a person.
        `settled` is the row `plan_settlement` named for a record listed on its settlement day,
        which comes before the content key: that key finds the payment MADE on the record's
        date, and this finds the one SETTLED on it.
        """
        incoming, index = self.incoming, self.index
        own: Transaction | None = None
        if incoming.source_id:
            for candidate in index.by_source_id(
                self.account, incoming.source, incoming.source_id
            ):
                if not self.taken(candidate):
                    own = candidate
                    break
        named = self.named_by_id()
        if own is not None:
            review = ""
            if named is not None and named.entity_id != own.entity_id:
                review = (
                    "its own id and the id it states for the bank's payment name different "
                    "rows, so nothing further was merged: one payment may be held twice, or "
                    "two joined that are not one"
                )
            return MatchResult(MatchTier.SOURCE_ID, own, basis=BASIS_OWN_ID, review=review)
        if named is not None:
            return MatchResult(MatchTier.LINKED_ID, named, basis=BASIS_ID)
        if settled is not None:
            planned = [*settlement_candidates(incoming, index), *index.listed_rows(incoming)]
            for candidate in planned:
                if candidate.entity_id == settled:
                    return MatchResult(MatchTier.FUZZY, candidate, basis=BASIS_SETTLEMENT)
        if incoming.content_key:
            for candidate in index.by_content_key(self.account, incoming.content_key):
                if self.one_payment(candidate, same_content=True):
                    return MatchResult(
                        MatchTier.CONTENT_KEY,
                        candidate,
                        basis=self.heuristic_basis(candidate),
                        review=self.size_conflict,
                    )
        return None

    # A hand-entered date is remembered rather than observed, so the window
    # widens when one side was typed by a person. YNAB allows ten days for the
    # same reason; between two machine-read sources seven is ample.
    def window_for(self, candidate: Transaction) -> timedelta:
        if SourceTier.MANUAL in (self.incoming.tier, candidate.tier):
            return timedelta(days=MANUAL_WINDOW_DAYS)
        return timedelta(days=FUZZY_WINDOW_DAYS)

    def same_amount(self) -> list[Transaction]:
        return self.index.by_amount(self.account, self.incoming.amount_minor)

    def settled_together(self, candidate: Transaction) -> bool:
        """Whether the day one carries is the settlement day the other states."""
        return settles_together(
            self.incoming,
            self.incoming_days,
            candidate,
            self.index.settlement_days(candidate.entity_id),
        )

    def reaches(self, candidate: Transaction) -> bool:
        """Whether the candidate lies inside the window, or beyond it by settlement and payee."""
        if abs(candidate.value_date - self.incoming.value_date) <= self.window_for(candidate):
            return True
        return self.settled_together(candidate) and same_payee(self.incoming, candidate)

    def similar(self) -> list[Transaction]:
        # A pair kept apart by kind is certain, not a near-miss, so it is never
        # put to the reviewer as a puzzle.
        return [
            t
            for t in self.same_amount()
            if self.reaches(t) and not self.apart_by_kind(t) and not self.contradicted(t)
        ]

    def near(self, similar: Sequence[Transaction]) -> list[Transaction]:
        # Applies whether or not the source numbers its rows: two rows of one
        # file are two payments either way, and an id-less format needs the
        # brake most.
        return [
            t
            for t in similar
            if self.one_payment(t, same_content=t.content_key == self.incoming.content_key)
        ]


def _distance_days(incoming: Transaction, candidate: Transaction) -> int:
    return abs((candidate.value_date - incoming.value_date).days)


def resolve(
    incoming: Transaction,
    existing: Sequence[Transaction] | CandidateIndex,
    *,
    partner: str | None = None,
    reserved: frozenset[str] = frozenset(),
) -> MatchResult:
    """Decide whether `incoming` is already represented in `existing`.

    `partner` and `reserved` come from `plan_partners`, which decides a batch's
    fuzzy matches as a set.
    The planned `partner` is taken when it is still a candidate, and a row
    reserved for another record of the batch is never taken by this one.
    With neither given, the nearest candidate wins, the earlier arrival on a tie.
    """
    index = (
        existing if isinstance(existing, CandidateIndex) else CandidateIndex(existing)
    )
    judge = _Judgement(incoming, index)

    found = judge.exact(settled=partner)
    if found is not None:
        return found
    conflict = judge.size_conflict

    same_amount = judge.same_amount()
    similar = judge.similar()
    near = judge.near(similar)
    # Two typed rows kept apart are not a puzzle to put to the reviewer: the
    # person typed both on purpose, and `could_be_one_payment` refuses to merge
    # them for exactly that reason.
    rejected = tuple(
        t
        for t in similar
        if t not in near
        and not (incoming.tier is SourceTier.MANUAL and t.tier is SourceTier.MANUAL)
    )
    # Reserved for another record, which is not the same as kept apart from this one.
    near = [t for t in near if t.entity_id == partner or t.entity_id not in reserved]

    if not near:
        # The series check filters on exact amount itself, so the amount
        # bucket is a complete candidate set for it.
        return MatchResult(
            MatchTier.UNRESOLVED,
            None,
            near_misses=rejected,
            recurring=belongs_to_established_series(incoming, same_amount),
            review=conflict,
        )

    for candidate in near:
        if candidate.entity_id == partner:
            return MatchResult(
                MatchTier.FUZZY, candidate, basis=judge.heuristic_basis(candidate), review=conflict
            )
    near.sort(key=lambda t: (not judge.settled_together(t), _distance_days(incoming, t)))
    return MatchResult(
        MatchTier.FUZZY, near[0], basis=judge.heuristic_basis(near[0]), review=conflict
    )


def second_row_named_by_exact_rules(
    incoming: Transaction, index: CandidateIndex, kept: Transaction
) -> Transaction | None:
    """The one other stored row the exact rules also name for `incoming`, or None.

    THE RULE. A record whose id names the stored row `kept` (its own id, or the feed uid an
    aggregator item states) may also have the settlement rule name another row: the other
    row lies on a day the record states it settled (or states one the record's own day is),
    of one size, direction, and payee (`settles_together`, `same_payee`).
    Two rows an exact rule each names for one record are one payment held twice, which arose
    where the records that would have joined them arrived in the one order that left each
    alone: an aggregator and an export before the feed that links them, the aggregator's row
    with no settlement day to reach the export's, and the export's row beyond the window of
    the aggregator's.
    Only ONE other row may be named: where the settlement day names several (two equal payments
    to one payee settled on one day, two export rows) it cannot say which belongs, and nothing
    is joined. The other row is never one the record's own source has already sighted (two feed
    rows of one size are two payments), never one an id contradicts (`contradicted`), and never
    one a source has called by an id of its own that differs from the kept row's
    (`CandidateIndex.could_be_one_row`).
    `second_row_verdicts` says, of every row the settlement rule names, which guard refuses it.
    """
    allowed = [v for v in second_row_verdicts(incoming, index, kept) if not v.refused]
    return allowed[0].row if len(allowed) == 1 else None


REFUSED_KIND = "a typed or history row"
REFUSED_CONTRADICTED = "a row an id contradicts"
REFUSED_OTHER_ID = "a row a source calls by another id"
REFUSED_SAME_SOURCE = "a row the record's source already sighted"
REFUSED_SEVERAL = "two or more settlement candidates"
REFUSALS = (
    REFUSED_SEVERAL,
    REFUSED_SAME_SOURCE,
    REFUSED_CONTRADICTED,
    REFUSED_OTHER_ID,
    REFUSED_KIND,
)


@dataclass(frozen=True)
class SecondRowVerdict:
    """A row the settlement rule names for a record, and the guard that refuses it ("" if none).

    Where several guards would refuse a row the first of `REFUSED_KIND`, `REFUSED_CONTRADICTED`,
    `REFUSED_SAME_SOURCE`, `REFUSED_OTHER_ID` is named: a row that is not a payment's own at all
    outranks the id evidence, which outranks the sighting evidence.
    """

    row: Transaction
    refused: str


def second_row_verdicts(
    incoming: Transaction, index: CandidateIndex, kept: Transaction
) -> list[SecondRowVerdict]:
    """Every row other than `kept` that the settlement rule names for `incoming`, judged.

    The rule and its guards are stated at `second_row_named_by_exact_rules`.
    Several rows that pass every row guard are all refused (`REFUSED_SEVERAL`): the settlement
    day cannot say which belongs.
    """
    judge = _Judgement(incoming, index)
    kept_unfit = kept.status.is_history or SourceTier.MANUAL in (incoming.tier, kept.tier)
    verdicts: list[SecondRowVerdict] = []
    for row in index.by_amount(incoming.account_id, incoming.amount_minor):
        if row.entity_id == kept.entity_id:
            continue
        if not (judge.settled_together(row) and same_payee(incoming, row)):
            continue
        refused = ""
        if kept_unfit or row.status.is_history or SourceTier.MANUAL in (incoming.tier, row.tier):
            refused = REFUSED_KIND
        elif judge.contradicted(row):
            refused = REFUSED_CONTRADICTED
        elif incoming.source in index.sighting_sources(row.entity_id):
            refused = REFUSED_SAME_SOURCE
        elif not index.could_be_one_row(kept.entity_id, row.entity_id) or not judge.one_payment(
            row, same_content=False
        ):
            refused = REFUSED_OTHER_ID
        verdicts.append(SecondRowVerdict(row, refused))
    passing = [v for v in verdicts if not v.refused]
    if len(passing) > 1:
        verdicts = [
            SecondRowVerdict(v.row, REFUSED_SEVERAL) if not v.refused else v for v in verdicts
        ]
    return verdicts


def settlement_candidates(incoming: Transaction, index: CandidateIndex) -> list[Transaction]:
    """The stored rows a settlement day names for a row listed on its settlement day.

    DETECTION ONLY, shared by the rule (`plan_settlement`) and the measurement that says what
    the rule would do (`exact_rule_measure`), so the two cannot name different rows.
    A source lists a payment on its settlement day only where its parser says so
    (`stated_times.lists_on_settlement_day`); for any other source there are no candidates.
    A candidate is a stored row of the record's size and direction whose feed sightings state
    that the payment settled on the record's date (`settles_together`, in either zone),
    inside the matcher's window of it or, beyond it, of the same payee (`reaches`).
    Never a row that is history, that a person typed, that is an internal leg meeting a source
    that cannot see Spaces, or that an id contradicts: the guards every pairing is judged by.
    """
    if not lists_on_settlement_day(incoming.source):
        return []
    judge = _Judgement(incoming, index)
    return [
        row
        for row in index.by_amount(incoming.account_id, incoming.amount_minor)
        if incoming.value_date in index.settlement_days(row.entity_id)
        and not row.status.is_history
        and SourceTier.MANUAL not in (incoming.tier, row.tier)
        and judge.reaches(row)
        and not judge.apart_by_kind(row)
        and not judge.contradicted(row)
    ]


def plan_settlement(
    batch: Sequence[Transaction], index: CandidateIndex, *, first_listing: bool = False
) -> dict[int, str]:
    """The stored row each record of a batch is, where its settlement day names one, by position.

    THE RULE, applied through `guarded_settlement_plan` (which says which of these moves it
    keeps), `plan_partners` and `_Judgement.exact`, and read by
    `exact_rule_measure.settlement_figures`, which says what it changes before it is trusted.
    A record listed on its settlement day (`settlement_candidates`) is the payment
    its day names, and is that row before its own content key is tried against rows made on
    that date.
    Measured on the deployed store: 4611 export rows had exactly one such row, and the store
    held 33 of them on another row, because the content key found the payment MADE on the
    row's date before the payment SETTLED on it; with seven equal payments to one payee on
    consecutive days (`consecutive_days_corpus`) every row sat one payment off.
    Records of one size and date are a SET: where as many records as rows are named, and every
    record is of the payee of every row, they are assigned in order (`assign_as_a_set`, the
    earliest record to the earliest row), because rows of one size and payee settled on one day
    cannot be told apart by anything but order.
    Nothing is planned where the day names no row, where more records than rows (or more rows
    than records) share a day, where a payee differs inside a set, or where two groups name
    the same row: those records keep the existing order of tiers (`resolve`).
    A record that is a file LISTING A ROW AGAIN (`CandidateIndex.listed_rows`) is that row,
    wherever the first listing found it, and is not planned by the day: a second export that
    went to the row its day now names stored every payment the first had placed elsewhere a
    second time (a second export of an overlapping span after the feed arrived last: seven
    surplus rows in the week of `consecutive_days_corpus`).
    That holds only for a record the settlement day names a row for; any other record keeps
    the existing order, as it always did.
    `first_listing` plans as if no file had listed anything yet, for the measurement of
    where each distinct row would go the first time it is listed.
    """
    named: dict[int, list[Transaction]] = {}
    groups: dict[tuple[int, date], list[int]] = {}
    planned: dict[int, str] = {}
    for position, incoming in enumerate(batch):
        found = settlement_candidates(incoming, index)
        if not found:
            continue
        listed = [] if first_listing else index.listed_rows(incoming)
        if incoming.occurrence < len(listed):
            again = listed[incoming.occurrence]
            if not again.status.is_history:
                planned[position] = again.entity_id
            continue
        named[position] = found
        groups.setdefault((incoming.amount_minor, incoming.value_date), []).append(position)

    for positions in groups.values():
        rows = named[positions[0]]
        ids = {row.entity_id for row in rows}
        if len(rows) != len(positions) or any(
            {row.entity_id for row in named[p]} != ids for p in positions
        ):
            continue
        if len(positions) > 1 and not all(
            same_payee(batch[p], row) for p in positions for row in rows
        ):
            continue
        planned.update(
            assign_as_a_set(
                [(p, batch[p].value_date) for p in positions],
                [(row.entity_id, row.value_date) for row in rows],
                lambda _position, _entity: True,
                preferred=lambda _position, _entity: True,
            )
        )
    claims = Counter(planned.values())
    return {position: row for position, row in planned.items() if claims[row] == 1}


#: One row's part in a group of moves: its size, the day it is counted on now, and the day it
#: would be counted on after (None for a row that does not exist on that side).
Effect = tuple[int, date | None, date | None]


@dataclass(frozen=True)
class SettlementGroup:
    """Moves of the plan that chain together, and what making them all would do to the days.

    A group is the records whose moves share a stored row: record r1 leaves T1 for T2, so the
    record on T2 has to go somewhere, and so on. Moves between rows of one size are always
    between equal sizes, so a group changes no day's total only if the rows end on the same
    days they started on, in some other order.
    """

    #: The positions of the records whose moves make up the group, in batch order.
    positions: tuple[int, ...]
    rows: frozenset[str]
    #: The days whose total of counted transactions would differ, by the change in it.
    day_changes: Mapping[date, int]
    #: How many existing rows would be counted on another day.
    redated: int

    @property
    def safe(self) -> bool:
        return not self.day_changes


def settlement_groups(
    planned: Mapping[int, str],
    hosts: Callable[[int], frozenset[str]],
    effect: Callable[[Sequence[int]], Sequence[Effect]],
) -> list[SettlementGroup]:
    """The plan's moves, grouped by the rows they chain, each with what it does to the days.

    `hosts(position)` is the rows the record sits on now (the existing order's answer), a move
    being a record whose planned row is not among them.
    `effect(positions)` is what making just those moves does to each row they touch, as
    (size, day counted on now, day counted on after): the caller owns how a row's day is read,
    which differs between a store that has already placed the rows and a batch not yet placed.
    SAFE is a property of a whole group (`SettlementGroup.safe`), so it is decided here, over
    everything the group touches, before any record is resolved.
    """
    moved = [p for p, target in sorted(planned.items()) if target not in hosts(p)]
    parent: dict[str, str] = {}

    def find(row: str) -> str:
        while parent.setdefault(row, row) != row:
            parent[row] = parent[parent[row]]
            row = parent[row]
        return row

    for position in moved:
        for row in hosts(position):
            parent[find(row)] = find(planned[position])
    by_root: dict[str, list[int]] = {}
    for position in moved:
        by_root.setdefault(find(planned[position]), []).append(position)

    groups: list[SettlementGroup] = []
    for positions in by_root.values():
        totals: dict[date, int] = {}
        redated = 0
        for size, was, now in effect(positions):
            if was is not None:
                totals[was] = totals.get(was, 0) - size
            if now is not None:
                totals[now] = totals.get(now, 0) + size
            if was is not None and now is not None and was != now:
                redated += 1
        rows = frozenset(
            {planned[p] for p in positions} | {row for p in positions for row in hosts(p)}
        )
        groups.append(
            SettlementGroup(
                tuple(positions),
                rows,
                {day: change for day, change in sorted(totals.items()) if change},
                redated,
            )
        )
    return groups


def guarded_settlement_plan(batch: Sequence[Transaction], index: CandidateIndex) -> dict[int, str]:
    """`plan_settlement`, keeping only the moves of groups that change no day's total.

    THE GUARD. Measured on the deployed account: the plan would move 36 rows, re-date 38
    transactions, and leave four days holding a different total, on an account whose rows
    reproduce all 1,906 of its known balances as they are dated today. What is wrong there is
    only which of several equal payments carries which export row, so a group of moves that
    would change a day's total (a chain broken because an export row is missing, or one whose
    last link the plan refuses) is left exactly as the existing order of tiers leaves it.
    SAFE is a property of a whole group (`settlement_groups`), and a group is decided here, over
    the incoming batch and the rows held before it, before any record is resolved. A batch is
    all or none of a group: a group that spans two files is two groups, each judged on the rows
    held when its file arrives, and a half that is safe alone is safe because it changes no
    day's total by itself, never because the other half will arrive.
    Where the existing order sends a record is read with the exact tiers (`_Judgement.exact`),
    and a row is counted on the day of the record it holds, the batch being the latest sighting.
    A record that lists a row again keeps the row its first listing joined, whatever the groups say.
    """
    planned = plan_settlement(batch, index)
    again = {p: row for p, row in planned.items() if _lists_again(batch[p], index)}
    fresh = {p: row for p, row in planned.items() if p not in again}
    if not fresh:
        return again
    tried = _existing_order(batch, index)

    def hosts(position: int) -> frozenset[str]:
        held = tried.hosts[position]
        return frozenset() if held is None else frozenset({held})

    def effect(positions: Sequence[int]) -> list[Effect]:
        now: dict[str, date] = {}
        phantoms: list[Effect] = []
        for position in positions:
            record = batch[position]
            now[fresh[position]] = record.value_date
            if tried.hosts[position] is None:
                phantoms.append((record.amount_minor, record.value_date, None))
        found: list[Effect] = []
        for entity in sorted({*now, *(h for p in positions for h in hosts(p))}):
            row = index.row(entity)
            if row is None or row.status.is_history:
                continue
            found.append(
                (row.amount_minor, tried.dates[entity], now.get(entity, row.value_date))
            )
        return [*found, *phantoms]

    unsafe = {
        position
        for group in settlement_groups(fresh, hosts, effect)
        if not group.safe
        for position in group.positions
    }
    return {
        **{position: row for position, row in fresh.items() if position not in unsafe},
        **again,
    }


def _lists_again(record: Transaction, index: CandidateIndex) -> bool:
    """Whether the record's own source has listed this row before (`CandidateIndex.listed_rows`)."""
    return record.occurrence < len(index.listed_rows(record))


@dataclass(frozen=True)
class _ExistingOrder:
    """Where a batch goes when no record is planned by its settlement day."""

    #: Each record's stored row, or None where the existing order makes it a row of its own.
    hosts: dict[int, str | None]
    #: The day every stored row is counted on once the batch is resolved that way.
    dates: dict[str, date]


def _existing_order(batch: Sequence[Transaction], index: CandidateIndex) -> _ExistingOrder:
    """Resolve the batch against a copy of the index, as `ingest._reconcile_all` would, unplanned.

    The same loop `ingest.preview_reconcile` runs, kept here because `matching` cannot import
    `ingest`: the batch's own partner plan without the settlement rule, `resolve`, a row claimed
    once it has answered, a merged row replacing its candidate, a new row joining the candidates.
    Nothing is written, and the index passed in is untouched.
    """
    sim = index.copy()
    plan = plan_partners(batch, sim, settlement=False)
    hosts: dict[int, str | None] = {}
    for position, record in enumerate(batch):
        partner, reserved = plan.for_position(position)
        found = resolve(record, sim, partner=partner, reserved=reserved)
        if found.existing is None:
            occurrence = sim.free_occurrence(
                record.account_id, record.content_key, wanted=record.occurrence
            )
            stand_in = replace(record, occurrence=occurrence, entity_id=f"simulated-{position}")
            sim.claim(stand_in.entity_id, record.source, record.source_id)
            sim.append(stand_in)
            hosts[position] = None
            continue
        held = found.existing
        sim.claim(held.entity_id, record.source, record.source_id)
        merged = supersede(held, record)
        if merged.content_key != held.content_key:
            merged = replace(
                merged,
                occurrence=sim.free_occurrence(
                    merged.account_id,
                    merged.content_key,
                    wanted=merged.occurrence,
                    excluding=held.entity_id,
                ),
            )
        else:
            merged = replace(merged, occurrence=held.occurrence)
        sim.replace(merged)
        hosts[position] = held.entity_id
    dates = {row.entity_id: row.value_date for row in sim}
    return _ExistingOrder(hosts, dates)


@dataclass(frozen=True)
class PartnerPlan:
    """What `plan_partners` decided for one batch.

    The stored row each record is to take, by the record's position in the
    batch, and every row so taken.
    """

    partner: dict[int, str]
    reserved: frozenset[str]

    def for_position(self, position: int) -> tuple[str | None, frozenset[str]]:
        return self.partner.get(position), self.reserved


def plan_partners(
    batch: Sequence[Transaction], index: CandidateIndex, *, settlement: bool = True
) -> PartnerPlan:
    """Decide which stored row each record of a batch takes, as a SET.

    THE RULE. When several records of one batch have no exact match and several
    stored rows of the same size, direction, and account lie within the window
    of them, they are assigned together and not in the order they arrive:
    a pair of the same date is taken first, and the rest so that no record is
    left without a partner while one within the window exists, at the least
    total distance in days.
    Ties go to the earlier date, then to the earlier arrival among rows of one
    date, so the outcome does not depend on the order the batch lists its rows.
    A record that shares no candidate with another record of the batch is not
    planned at all, and takes its nearest candidate, the earlier arrival on a
    tie, as it always did.

    Measured on the deployed page: "out row dated starling 2021-03-22,
    truelayer 2021-03-22; seen by starling, truelayer; booked" with no export
    sighting, beside a payment dated three days on that carried the export's
    row of a day before it.
    The feed and the aggregator held payments of one size on the 22nd and the
    25th and the export rows on the 24th and the 25th: arriving first, the 24th
    took the 25th's payment (one day away) and the 25th's row was left with the
    22nd's (three days away).

    BOUND. A group is solved exactly only while it has at most
    `ASSIGNMENT_SET_LIMIT` records and as many rows of the same size, after
    same-date pairs are set aside.
    A larger group keeps its same-date pairs and every other record is left to
    be matched one at a time, as it was before this rule.
    Exact solving is exponential in the rows, which is why it is bounded and not
    measured: a month of one standing amount stays well inside it.
    """
    settled = guarded_settlement_plan(batch, index) if settlement else {}
    pool: dict[int, list[Transaction]] = {}
    consumed: set[str] = set(settled.values())
    for position, incoming in enumerate(batch):
        if position in settled:
            continue
        judge = _Judgement(incoming, index)
        found = judge.exact()
        if found is not None and found.existing is not None:
            consumed.add(found.existing.entity_id)
            continue
        near = judge.near(judge.similar())
        if near:
            pool[position] = near

    options: dict[int, list[Transaction]] = {
        position: [t for t in near if t.entity_id not in consumed]
        for position, near in pool.items()
    }
    options = {position: near for position, near in options.items() if near}

    partner: dict[int, str] = {}
    for group in _groups_sharing_candidates(options):
        if len(group) < 2:
            continue
        partner.update(_assign_group(batch, options, group, index))
    partner.update(settled)
    return PartnerPlan(partner, frozenset(partner.values()))


def _assign_group(
    batch: Sequence[Transaction],
    options: dict[int, list[Transaction]],
    group: list[int],
    index: CandidateIndex,
) -> dict[int, str]:
    reach = {position: {t.entity_id for t in options[position]} for position in group}
    rows = {t.entity_id: t for position in group for t in options[position]}
    settled = {
        position: {
            t.entity_id
            for t in options[position]
            if _Judgement(batch[position], index).settled_together(t)
        }
        for position in group
    }
    return assign_as_a_set(
        [(position, batch[position].value_date) for position in group],
        [(entity_id, row.value_date) for entity_id, row in rows.items()],
        lambda position, entity_id: entity_id in reach[position],
        preferred=lambda position, entity_id: entity_id in settled[position],
    )


def _groups_sharing_candidates(options: dict[int, list[Transaction]]) -> list[list[int]]:
    """Records joined by a stored row both could take, each group in batch order."""
    parent: dict[int, int] = {position: position for position in options}

    def find(position: int) -> int:
        while parent[position] != position:
            parent[position] = parent[parent[position]]
            position = parent[position]
        return position

    first_for: dict[str, int] = {}
    for position, near in options.items():
        for candidate in near:
            owner = first_for.setdefault(candidate.entity_id, position)
            parent[find(position)] = find(owner)
    groups: dict[int, list[int]] = {}
    for position in sorted(options):
        groups.setdefault(find(position), []).append(position)
    return list(groups.values())


def assign_as_a_set(
    wanting: Sequence[tuple[int, date]],
    offered: Sequence[tuple[str, date]],
    allowed: Callable[[int, str], bool],
    preferred: Callable[[int, str], bool] | None = None,
) -> dict[int, str]:
    """Pair records with rows by the rule `plan_partners` states.

    `wanting` is each record's key and date; `offered` each row's key and date;
    `allowed` says whether a record could take a row at all.
    `preferred` says whether a record and a row are paired by settlement
    (`settles_together`), and those pairs come before every other, in the same order.
    Same-date pairs come next, taken in date order and then in the order given.
    The rest are solved exactly for the fewest records left unpaired, then the
    least total distance, when no more than `ASSIGNMENT_SET_LIMIT` records and
    rows remain in a connected group; a larger group is left unpaired here.
    """
    records = sorted(enumerate(wanting), key=lambda item: (item[1][1], item[0]))
    rows = sorted(enumerate(offered), key=lambda item: (item[1][1], item[0]))
    result: dict[int, str] = {}
    used: set[int] = set()

    if preferred is not None:
        for _, (key, _day) in records:
            for row_index, (entity_id, _row_day) in rows:
                if row_index in used:
                    continue
                if not (allowed(key, entity_id) and preferred(key, entity_id)):
                    continue
                used.add(row_index)
                result[key] = entity_id
                break

    for _, (key, day) in records:
        if key in result:
            continue
        for row_index, (entity_id, row_day) in rows:
            if row_index in used or row_day != day or not allowed(key, entity_id):
                continue
            used.add(row_index)
            result[key] = entity_id
            break

    left_records = [(key, day) for _, (key, day) in records if key not in result]
    left_rows = [(entity_id, day) for row_index, (entity_id, day) in rows if row_index not in used]
    for component_records, component_rows in _components(left_records, left_rows, allowed):
        if max(len(component_records), len(component_rows)) > ASSIGNMENT_SET_LIMIT:
            continue
        result.update(_solve_exactly(component_records, component_rows, allowed))
    return result


def _components(
    records: list[tuple[int, date]],
    rows: list[tuple[str, date]],
    allowed: Callable[[int, str], bool],
) -> list[tuple[list[tuple[int, date]], list[tuple[str, date]]]]:
    parent = list(range(len(records) + len(rows)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for i, (key, _) in enumerate(records):
        for j, (entity_id, _) in enumerate(rows):
            if allowed(key, entity_id):
                parent[find(i)] = find(len(records) + j)
    grouped: dict[int, tuple[list[tuple[int, date]], list[tuple[str, date]]]] = {}
    for i, record in enumerate(records):
        grouped.setdefault(find(i), ([], []))[0].append(record)
    for j, row in enumerate(rows):
        grouped.setdefault(find(len(records) + j), ([], []))[1].append(row)
    return [group for group in grouped.values() if group[0] and group[1]]


def _solve_exactly(
    records: list[tuple[int, date]],
    rows: list[tuple[str, date]],
    allowed: Callable[[int, str], bool],
) -> dict[int, str]:
    count = len(rows)
    best: dict[tuple[int, int], tuple[int, int]] = {}
    choice: dict[tuple[int, int], int | None] = {}

    def solve(i: int, taken: int) -> tuple[int, int]:
        if i == len(records):
            return (0, 0)
        state = (i, taken)
        if state in best:
            return best[state]
        key, day = records[i]
        leave = solve(i + 1, taken)
        winner = (leave[0] + 1, leave[1])
        picked: int | None = None
        for j in range(count):
            if taken & (1 << j) or not allowed(key, rows[j][0]):
                continue
            rest = solve(i + 1, taken | (1 << j))
            score = (rest[0], rest[1] + abs((rows[j][1] - day).days))
            if score < winner:
                winner, picked = score, j
        best[state] = winner
        choice[state] = picked
        return winner

    solve(0, 0)
    result: dict[int, str] = {}
    taken = 0
    for i, (key, _) in enumerate(records):
        picked = choice[(i, taken)]
        if picked is not None:
            result[key] = rows[picked][0]
            taken |= 1 << picked
    return result


def supersede(previous: Transaction, observation: Transaction) -> Transaction:
    """Apply a later sighting of a transaction already held.

    A pending transaction that settles often arrives with a NEW provider id and
    a shifted date. That is a supersession, not an update: the entity keeps its
    identity, the newer observation supplies the current facts, and both raw
    payloads remain in the raw layer. Modelling it this way is what makes a
    rebuild from raw reproducible.

    THE ROW'S DATE IS THE LATEST SIGHTING'S, including where that is an export
    listing a payment on its settlement day (`settles_together`).
    Keeping the day the payment was made was tried for such a sighting and withdrawn
    within the hour: a row's date is part of the key by which a file's own row finds it
    again, so a second export of an overlapping span no longer found the row the first
    had joined and stored every card payment settled on a later day a second time
    (245 surplus rows on the deployed store's first rebuild), and the same key is the
    identity a push uses, so every such row would have gone to Actual as a new one.
    Each source's own day is kept on its sighting, which is what a balance is tested
    against (`sighting_placement`).
    """
    return replace(
        observation,
        entity_id=previous.entity_id,
        # `booking_date` is the day the bank posted it. A pending sighting states no posting day,
        # only the day it was seen, so the settled sighting's replaces it; this had kept the
        # earlier day, which put a Direct Debit's posting before its own settlement and hid the
        # day it was taken from a rhythm measured on it. Between two sightings that both state
        # a posting day the earlier is kept.
        booking_date=(
            observation.booking_date
            if previous.status is TransactionStatus.PENDING
            else min(previous.booking_date, observation.booking_date)
        ),
        status=observation.status or previous.status,
        # Sticky. Confirming a transfer is expensive - it needs both sides
        # present in different accounts - and a later sighting arriving from a
        # feed that does not mark transfers would otherwise silently reclassify
        # it as spending on every pull.
        is_internal_transfer=previous.is_internal_transfer or observation.is_internal_transfer,
        # A later observation may not carry a counterparty the earlier one did.
        # Losing it would degrade the payee on every replay.
        counterparty=observation.counterparty or previous.counterparty,
        # Kept first for the same reason, and each on its own: a statement sighting states
        # neither, and the feed's stay.
        party_account=observation.party_account or previous.party_account,
        party_source_id=observation.party_source_id or previous.party_source_id,
    )


def pair_internal_transfers(
    transactions: Iterable[Transaction],
    *,
    window_days: int = INTERNAL_TRANSFER_WINDOW_DAYS,
) -> list[Transaction]:
    """Flag matched debit/credit pairs across your own accounts.

    Matches on equal absolute amount, opposite sign, different account, and
    value dates within `window_days`. Each side is consumed once, so a repeated
    standing order of the same value does not chain-match.
    """
    items = sorted(transactions, key=lambda t: (t.value_date, t.account_id))
    result = list(items)
    for i, j in _pair_indices(items, timedelta(days=window_days)):
        result[i] = replace(items[i], is_internal_transfer=True)
        result[j] = replace(items[j], is_internal_transfer=True)
    return result


def pair_transfer_entities(
    transactions: Iterable[Transaction],
    *,
    window_days: int = INTERNAL_TRANSFER_WINDOW_DAYS,
    counterpart: Callable[[Transaction], str | None] | None = None,
) -> list[tuple[str, str]]:
    """The pairs themselves, as (debit entity, credit entity).

    Same detection as `pair_internal_transfers`, but reporting WHICH rows
    paired with which rather than flagging them - the shape a store needs
    to record a confirmation as its own fact instead of overwriting the
    provider's claim.

    THE ORDER OF CHOICE. An internal leg is offered another internal leg first,
    and only what is left is paired as before.
    Measured on the deployed store: "in row ... a transfer leg, confirmed paired
    with the Space starling-space-bills" beside "out row ... a transfer leg with
    no pair" and "out row ... booked; in the Space starling-space-bills", an
    ordinary payment of the same size, so the main account's IN leg had taken the
    payment and left the real leg without a partner.

    `counterpart` names the account a leg says it moved to or from, or None where
    that is not known (the provider's own category, resolved to an account). A leg
    whose counterpart account is known pairs only with an internal leg held in
    that account, and never with an ordinary payment: the feed told us which
    account the other side is in. A leg with no known counterpart, and every row
    that is not an internal leg, is paired by amount, sign, and date alone.
    """
    items = sorted(transactions, key=lambda t: (t.value_date, t.account_id))
    window = timedelta(days=window_days)
    named = counterpart or (lambda _row: None)
    paired: set[int] = set()

    def is_leg(row: Transaction) -> bool:
        return row.is_internal_transfer

    def agree(debit: Transaction, credit: Transaction) -> bool:
        return all(
            (account := named(leg)) is None or account == other.account_id
            for leg, other in ((debit, credit), (credit, debit))
        )

    def ordinary_or_unplaced(row: Transaction) -> bool:
        return not (row.is_internal_transfer and named(row) is not None)

    found = _pair_indices(items, window, paired=paired, eligible=is_leg, agree=agree)
    found += _pair_indices(items, window, paired=paired, eligible=ordinary_or_unplaced)
    return [(items[i].entity_id, items[j].entity_id) for i, j in found]


def _pair_indices(
    items: list[Transaction],
    window: timedelta,
    *,
    paired: set[int] | None = None,
    eligible: Callable[[Transaction], bool] | None = None,
    agree: Callable[[Transaction, Transaction], bool] | None = None,
) -> list[tuple[int, int]]:
    """Greedy debit-to-credit pairing over the canonically sorted list.

    The original scanned every credit for every debit - a full-store
    O(n^2) pass costing ~2.6s of the measured rebuild. Only credits of
    the exact opposite amount can ever pair, so credits are bucketed by
    amount, each bucket in the SAME global (value_date, account_id)
    order the scan used - which is what preserves the greedy choice
    exactly: for each debit, the first eligible credit in that order
    wins, each side consumed once.

    `paired` carries the rows already taken by an earlier pass, and is added
    to; `eligible` limits a pass to some rows; `agree` is a further test on a
    debit and a credit of one amount that could pair.
    """
    paired = set() if paired is None else paired
    pairs: list[tuple[int, int]] = []

    credits_by_amount: dict[int, list[int]] = {}
    for j, item in enumerate(items):
        if item.amount_minor > 0 and j not in paired and (eligible is None or eligible(item)):
            credits_by_amount.setdefault(item.amount_minor, []).append(j)
    # Debits are visited in non-decreasing date order, so a credit that
    # has fallen behind one debit's window can never re-enter a later
    # one's - the bucket start index only ever advances.
    bucket_start: dict[int, int] = {}

    for i, debit in enumerate(items):
        if i in paired or debit.amount_minor >= 0:
            continue
        if eligible is not None and not eligible(debit):
            continue
        bucket = credits_by_amount.get(-debit.amount_minor)
        if not bucket:
            continue
        start = bucket_start.get(-debit.amount_minor, 0)
        while start < len(bucket) and (
            debit.value_date - items[bucket[start]].value_date > window
        ):
            start += 1
        bucket_start[-debit.amount_minor] = start
        for j in bucket[start:]:
            credit = items[j]
            if credit.value_date - debit.value_date > window:
                break
            if j in paired or credit.account_id == debit.account_id:
                continue
            if agree is not None and not agree(debit, credit):
                continue
            paired.update({i, j})
            pairs.append((i, j))
            break

    return pairs


def settled(transaction: Transaction) -> bool:
    return transaction.status is TransactionStatus.BOOKED
