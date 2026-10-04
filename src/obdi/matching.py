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
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import TypeVar

from .models import MatchTier, SourceTier, Transaction, TransactionStatus

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
    ) -> None:
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
        self._recording_evidence = True

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

    def _file(self, transaction: Transaction) -> None:
        self.note_sighting(
            transaction.entity_id,
            transaction.account_id,
            transaction.source,
            transaction.source_id,
            record_evidence=self._recording_evidence,
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
        if self.taken(candidate) or self.apart_by_kind(candidate):
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

    def exact(self) -> MatchResult | None:
        """Tiers 1 and 2: the row this record names by id or by content."""
        incoming, index = self.incoming, self.index
        if incoming.source_id:
            for candidate in index.by_source_id(
                self.account, incoming.source, incoming.source_id
            ):
                if not self.taken(candidate):
                    return MatchResult(MatchTier.SOURCE_ID, candidate)
        if incoming.content_key:
            for candidate in index.by_content_key(self.account, incoming.content_key):
                if self.one_payment(candidate, same_content=True):
                    return MatchResult(MatchTier.CONTENT_KEY, candidate)
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

    def similar(self) -> list[Transaction]:
        # A pair kept apart by kind is certain, not a near-miss, so it is never
        # put to the reviewer as a puzzle.
        incoming = self.incoming
        return [
            t
            for t in self.same_amount()
            if abs(t.value_date - incoming.value_date) <= self.window_for(t)
            and not self.apart_by_kind(t)
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

    found = judge.exact()
    if found is not None:
        return found

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
        )

    for candidate in near:
        if candidate.entity_id == partner:
            return MatchResult(MatchTier.FUZZY, candidate)
    near.sort(key=lambda t: _distance_days(incoming, t))
    return MatchResult(MatchTier.FUZZY, near[0])


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


def plan_partners(batch: Sequence[Transaction], index: CandidateIndex) -> PartnerPlan:
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
    pool: dict[int, list[Transaction]] = {}
    consumed: set[str] = set()
    for position, incoming in enumerate(batch):
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
        partner.update(_assign_group(batch, options, group))
    return PartnerPlan(partner, frozenset(partner.values()))


def _assign_group(
    batch: Sequence[Transaction], options: dict[int, list[Transaction]], group: list[int]
) -> dict[int, str]:
    reach = {position: {t.entity_id for t in options[position]} for position in group}
    rows = {t.entity_id: t for position in group for t in options[position]}
    return assign_as_a_set(
        [(position, batch[position].value_date) for position in group],
        [(entity_id, row.value_date) for entity_id, row in rows.items()],
        lambda position, entity_id: entity_id in reach[position],
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
) -> dict[int, str]:
    """Pair records with rows by the rule `plan_partners` states.

    `wanting` is each record's key and date; `offered` each row's key and date;
    `allowed` says whether a record could take a row at all.
    Same-date pairs come first, taken in date order and then in the order given.
    The rest are solved exactly for the fewest records left unpaired, then the
    least total distance, when no more than `ASSIGNMENT_SET_LIMIT` records and
    rows remain in a connected group; a larger group is left unpaired here.
    """
    records = sorted(enumerate(wanting), key=lambda item: (item[1][1], item[0]))
    rows = sorted(enumerate(offered), key=lambda item: (item[1][1], item[0]))
    result: dict[int, str] = {}
    used: set[int] = set()

    for _, (key, day) in records:
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
    """
    return replace(
        observation,
        entity_id=previous.entity_id,
        # Retain the earliest booking date so "when did this first appear" is
        # answerable after settlement moves the dates.
        booking_date=min(previous.booking_date, observation.booking_date),
        status=observation.status or previous.status,
        # Sticky. Confirming a transfer is expensive - it needs both sides
        # present in different accounts - and a later sighting arriving from a
        # feed that does not mark transfers would otherwise silently reclassify
        # it as spending on every pull.
        is_internal_transfer=previous.is_internal_transfer or observation.is_internal_transfer,
        # A later observation may not carry a counterparty the earlier one did.
        # Losing it would degrade the payee on every replay.
        counterparty=observation.counterparty or previous.counterparty,
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
