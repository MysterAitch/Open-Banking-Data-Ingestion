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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from .models import MatchTier, SourceTier, Transaction, TransactionStatus

FUZZY_WINDOW_DAYS = 7

# Wider, because a hand-entered date is remembered rather than observed. YNAB
# uses ten days when matching an import against a user-entered transaction, and
# their reasoning applies here unchanged.
MANUAL_WINDOW_DAYS = 10

INTERNAL_TRANSFER_WINDOW_DAYS = 1


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
        return True

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


def resolve(incoming: Transaction, existing: Sequence[Transaction]) -> MatchResult:
    """Decide whether `incoming` is already represented in `existing`."""
    same_account = [t for t in existing if t.account_id == incoming.account_id]

    # Provider ids are only unique within a provider's own namespace, so tier 1
    # matches on (source, source_id) rather than the id alone. Two sources
    # reporting the same payment are SUPPOSED to disagree here.
    if incoming.source_id:
        for candidate in same_account:
            if (
                candidate.source_id
                and candidate.source == incoming.source
                and candidate.source_id == incoming.source_id
            ):
                return MatchResult(MatchTier.SOURCE_ID, candidate)

    if incoming.content_key:
        for candidate in same_account:
            if candidate.content_key == incoming.content_key and could_be_one_payment(
                incoming, candidate, same_content=True
            ):
                return MatchResult(MatchTier.CONTENT_KEY, candidate)

    # A hand-entered date is remembered rather than observed, so the window
    # widens when one side was typed by a person. YNAB allows ten days for the
    # same reason; between two machine-read sources seven is ample.
    def window_for(candidate: Transaction) -> timedelta:
        if SourceTier.MANUAL in (incoming.tier, candidate.tier):
            return timedelta(days=MANUAL_WINDOW_DAYS)
        return timedelta(days=FUZZY_WINDOW_DAYS)

    similar = [
        t
        for t in same_account
        if t.amount_minor == incoming.amount_minor
        and abs(t.value_date - incoming.value_date) <= window_for(t)
    ]

    # Applies whether or not the source numbers its rows: two rows of one file
    # are two payments either way, and an id-less format needs the brake most.
    near = [
        t
        for t in similar
        if could_be_one_payment(incoming, t, same_content=t.content_key == incoming.content_key)
    ]
    rejected = tuple(t for t in similar if t not in near)

    if not near:
        return MatchResult(
            MatchTier.UNRESOLVED,
            None,
            near_misses=rejected,
            recurring=belongs_to_established_series(incoming, same_account),
        )

    near.sort(key=lambda t: abs(t.value_date - incoming.value_date))
    return MatchResult(MatchTier.FUZZY, near[0])


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
    window = timedelta(days=window_days)
    paired: set[int] = set()
    result = list(items)

    for i, debit in enumerate(items):
        if i in paired or debit.amount_minor >= 0:
            continue
        for j, credit in enumerate(items):
            if j in paired or j == i or credit.amount_minor <= 0:
                continue
            if credit.account_id == debit.account_id:
                continue
            if credit.amount_minor != -debit.amount_minor:
                continue
            if abs(credit.value_date - debit.value_date) > window:
                continue
            paired.update({i, j})
            result[i] = replace(debit, is_internal_transfer=True)
            result[j] = replace(credit, is_internal_transfer=True)
            break

    return result


def settled(transaction: Transaction) -> bool:
    return transaction.status is TransactionStatus.BOOKED
