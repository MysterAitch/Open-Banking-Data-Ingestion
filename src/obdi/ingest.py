"""The import pipeline: land raw, derive transactions, resolve identity.

Land first, always. Parsing can be retried from a stored artefact; a download
that was parsed and discarded cannot be recovered once the bank's export window
closes.
"""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from . import instrumentation
from .accounts import AccountMap
from .identity import artefact_digest, entity_id_for
from .matching import CandidateIndex, pair_transfer_entities, resolve, supersede
from .models import RawArtefact, SourceTier, Transaction, TransactionStatus
from .parsers.uk_banks import detect
from .review_settlement import settle_review_flags
from .space_attribution import fold_space_copies
from .store import Store


@dataclass
class ImportSummary:
    artefact_new: bool
    parsed: int = 0
    #: How many rows the file held, where the format presents rows. The
    #: denominator for `parsed`: without it, a parser that filtered every
    #: row reports a successful import of nothing.
    rows_offered: int | None = None
    inserted: int = 0
    matched: int = 0
    superseded: int = 0
    needs_review: int = 0
    #: Main-account rows newly folded into the Space rows they copy, by the
    #: pass the caller ran after the batch; see `space_attribution`.
    folded: int = 0

    def describe(self) -> str:
        offered = ""
        if self.rows_offered is not None and self.rows_offered != self.parsed:
            skipped = self.rows_offered - self.parsed
            offered = (
                f" of {self.rows_offered} row(s) in the file - {skipped} "
                "skipped, which is a fault unless you know why"
            )
        folded = (
            f", folded {self.folded} main-account row(s) into their Space rows"
            if self.folded
            else ""
        )
        return (
            f"parsed {self.parsed}{offered}, new {self.inserted}, "
            f"matched {self.matched}, superseded {self.superseded}, "
            f"for review {self.needs_review}{folded}"
        )


_CLAIM_DAY_FIRST = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_CLAIM_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def claimed_window(
    filename: str,
    *,
    rows_from: date | None = None,
    rows_to: date | None = None,
) -> tuple[date, date] | None:
    """The date range the FILENAME claims - the file import's "asked".

    Banks name their exports with the requested range, which is evidence
    the rows alone cannot carry: a claim wider than the rows proves the
    quiet days were genuinely quiet. Day-first is preferred (the UK
    reality); when both tokens are ambiguous the reading that CONTAINS the
    supplied row span wins, because a claim that cannot hold its own
    contents is the wrong reading. Nothing parseable claims nothing -
    absence of a claim is not a fault.
    """
    iso = _CLAIM_ISO.findall(filename)
    if len(iso) >= 2:
        try:
            first = date(int(iso[0][0]), int(iso[0][1]), int(iso[0][2]))
            second = date(int(iso[1][0]), int(iso[1][1]), int(iso[1][2]))
        except ValueError:
            return None
        return (first, second) if first <= second else None

    tokens = _CLAIM_DAY_FIRST.findall(filename)
    if len(tokens) < 2:
        return None

    candidates: list[tuple[date, date]] = []
    for day_index, month_index in ((0, 1), (1, 0)):
        try:
            first = date(
                int(tokens[0][2]), int(tokens[0][month_index]), int(tokens[0][day_index])
            )
            second = date(
                int(tokens[1][2]), int(tokens[1][month_index]), int(tokens[1][day_index])
            )
        except ValueError:
            continue
        if first <= second and (first, second) not in candidates:
            candidates.append((first, second))
    if not candidates:
        return None
    if len(candidates) > 1 and rows_from is not None and rows_to is not None:
        containing = [
            candidate
            for candidate in candidates
            if candidate[0] <= rows_from and candidate[1] >= rows_to
        ]
        if len(containing) == 1:
            return containing[0]
    return candidates[0]


def claimed_window_note(
    filename: str,
    *,
    earliest: date | None,
    latest: date | None,
) -> str | None:
    """What the filename's claim adds to what the rows already say.

    A quiet head or tail is AFFIRMED by the document - the origin question
    ("opened on the 17th, first transaction on the 20th") answered
    mechanically. Rows outside the claim are the opposite finding: the
    filename and the content disagree, which is the export equivalent of
    an ask-vs-artefact mismatch.
    """
    window = claimed_window(filename, rows_from=earliest, rows_to=latest)
    if window is None:
        return None
    claim_from, claim_to = window
    parts = [f"the filename claims {claim_from.isoformat()} .. {claim_to.isoformat()}"]
    if earliest is not None and latest is not None:
        if earliest < claim_from or latest > claim_to:
            parts.append(
                "rows fall OUTSIDE the claimed window - the filename and the "
                "content disagree"
            )
        else:
            quiet = []
            head = (earliest - claim_from).days
            tail = (claim_to - latest).days
            if head > 0:
                quiet.append(f"the first {head} day(s)")
            if tail > 0:
                quiet.append(f"the last {tail} day(s)")
            if quiet:
                parts.append(
                    f"{' and '.join(quiet)} of the claim hold no rows - "
                    "affirmed quiet by the document"
                )
    return "; ".join(parts)


def dates_cannot_confirm_format(dates: Sequence[date]) -> bool:
    """True when nothing in the data rules out the transposed reading.

    Parsers pin their date format rather than guessing, and that is what
    normally catches a file in the wrong convention: under %d/%m/%Y a day of 13
    or more is an invalid month and fails immediately. One such date anywhere in
    the file proves the format for all of it.

    With no such date, both readings parse cleanly and the file is silently
    wrong under one of them. That is not a reason to refuse the import - the
    pinned format is still the best available guess - but it is a reason to say
    so, because the alternative is a batch of transactions in the wrong month
    that nothing will ever question.
    """
    return bool(dates) and all(value.day <= 12 for value in dates)


def media_type_of(payload: bytes, path: Path) -> str:
    """What the artefact actually holds.

    Content first, filename second: the raw layer's promise is that it
    keeps the evidence as it arrived, and a stamp that says CSV over a PDF
    is the one kind of lie that layer must never tell.
    """
    from .parsers.uk_banks import PDF_MAGIC

    if payload.startswith(PDF_MAGIC):
        return "application/pdf"
    if path.suffix.lower() == ".qif":
        return "application/qif"
    if path.suffix.lower() == ".json":
        return "application/json"
    return "text/csv"


def import_file(
    store: Store,
    path: Path,
    *,
    account_id: str,
    account_map: AccountMap | None = None,
) -> ImportSummary:
    """Land a file, resolve its rows, and - given the account map - fold any
    main-account row that copies a Space payment."""
    # The account becomes a query key across every layer, so it is checked
    # at the door rather than trusted from whoever posted it. The rule
    # existed and had no live call site: every writer invented its own or
    # none, and a canonical name that could pose as a provider reference
    # merges two real accounts into one - after which the agreement report
    # cheerfully "corroborates" one bank's rows with another's.
    from .namespaces import validate_canonical_name

    validate_canonical_name(account_id)
    payload = path.read_bytes()
    digest = artefact_digest(payload)

    artefact = RawArtefact(
        source=path.suffix.lstrip(".") or "unknown",
        account_ref=account_id,
        fetched_at=datetime.now().astimezone(),
        media_type=media_type_of(payload, path),
        digest=digest,
        payload=payload,
        origin=path.name,
    )
    # Landed BEFORE parsing, so evidence survives a file nothing can read
    # yet - a statement whose parser is written next week replays from here
    # rather than needing to be fetched from the bank again.
    is_new_artefact = store.land_artefact(artefact).payload_stored

    parser = detect(payload)
    incoming = list(parser.parse(payload, account_id=account_id))
    offered = getattr(parser, "rows_offered", None)

    if dates_cannot_confirm_format([item.value_date for item in incoming]):
        print(
            f"WARNING: every date in {path.name} falls on the 12th or earlier, so "
            f"nothing in the file rules out the opposite day/month reading. It was "
            f"parsed as {parser.date_format}. If that is wrong, every date here is "
            "in the wrong month - cross-check against another source before relying "
            "on it.",
            file=sys.stderr,
        )

    # Reconciliation is shared with API pulls rather than duplicated here, so
    # identity resolution cannot drift between the two routes - the same
    # payment arriving by file and by API must resolve identically.
    summary = ImportSummary(artefact_new=is_new_artefact, rows_offered=offered)
    reconcile_batch(store, incoming, digest=digest, summary=summary)
    if account_map is not None:
        summary.folded += fold_space_copies(store, account_map).newly_folded
    settle_review_flags(store)
    return summary


def pair_transfers_across_store(store: Store) -> int:
    """Confirm internal transfers across the WHOLE store, not just one import.

    A separate pass by necessity: a transfer's two sides live in different
    accounts and so arrive in different files, usually on different days.
    Pairing within a single import batch would never fire.

    Two distinct signals are at play and are kept as separate facts:

      the provider's claim  some feeds mark a movement as internal themselves;
                            that stays on the transaction row, untouched here
      confirmation          the other side was actually found in the store;
                            recorded in the pairing table, owned by this pass

    A claim without confirmation means the opposite side is missing - the
    account it belongs to has not been ingested yet. Both kinds of evidence
    exclude a movement from spending; only this pass's findings are counted
    here, so the number means "pairs found" rather than "flags written".
    """
    # A folded row is a second report of a payment, not a movement, so it must
    # not be offered as the leg of a transfer.
    pairs = pair_transfer_entities(
        t for t in store.all_transactions() if t.status is not TransactionStatus.FOLDED
    )
    store.replace_transfer_pairs(pairs)
    store.connection.commit()
    return len(pairs)


def unconfirmed_transfers(store: Store) -> list[Transaction]:
    """Transactions claimed internal by their provider but never paired.

    Each means the opposite side is absent - usually an account or a savings
    space that has not been ingested. Worth surfacing: an unpaired claim is
    excluded from spending on the provider's word alone.
    """
    return [
        t
        for t in store.all_transactions()
        if t.is_internal_transfer and not t.transfer_confirmed
    ]


def _numbered(transactions: list[Transaction]) -> list[Transaction]:
    """Number each repeat of the same content within a batch.

    Deterministic across re-parses, because it depends only on the order the
    source presents its rows - which is what lets a re-downloaded export merge
    while two genuinely repeated payments stay apart.
    """
    seen: dict[tuple[str, str], int] = {}
    numbered: list[Transaction] = []
    for transaction in transactions:
        key = (transaction.account_id, transaction.content_key)
        numbered.append(replace(transaction, occurrence=seen.get(key, 0)))
        seen[key] = seen.get(key, 0) + 1
    return numbered


@dataclass(frozen=True)
class MatcherPreview:
    """What `reconcile_batch` would do with a batch, counted and not done."""

    merged: int
    new: int

    @property
    def total(self) -> int:
        return self.merged + self.new

    @property
    def share_merged(self) -> float:
        return self.merged / self.total if self.total else 0.0

    @property
    def clause(self) -> str:
        return (
            f"the matcher would merge {self.merged} of {self.total} rows onto "
            f"rows this account already holds and add {self.new} new"
        )

    def describe(self) -> str:
        return f"{self.clause[0].upper()}{self.clause[1:]}."


def preview_reconcile(store: Store, transactions: list[Transaction]) -> MatcherPreview:
    """Count how a batch would resolve against what is stored, writing nothing.

    The same loop `_reconcile_all` runs - the same numbering, the same
    `resolve`, a row claimed once it has answered, a merged row replacing its
    candidate and a new row joining the candidates so a later row of the batch
    can merge onto it - over indexes built from the store's rows and
    sightings. The only stores touched are reads: no upsert, no sighting, no
    review flag, and no batch is opened, so a caller can ask before deciding
    whether to read the batch in. A new row is given a stand-in identity,
    which never leaves the index.
    """
    by_account: dict[str, CandidateIndex] = {}
    merged = 0
    new = 0
    for position, transaction in enumerate(_numbered(transactions)):
        existing = by_account.get(transaction.account_id)
        if existing is None:
            existing = CandidateIndex(
                store.transactions_for_account(transaction.account_id),
                sightings=store.sighted_ids_for_account(transaction.account_id),
            )
            existing.begin_batch()
            by_account[transaction.account_id] = existing
        result = resolve(transaction, existing)
        if result.existing is None:
            occurrence = existing.free_occurrence(
                transaction.account_id,
                transaction.content_key,
                wanted=transaction.occurrence,
            )
            stand_in = replace(
                transaction,
                occurrence=occurrence,
                entity_id=f"preview-{position}",
            )
            existing.claim(stand_in.entity_id, transaction.source, transaction.source_id)
            existing.append(stand_in)
            new += 1
            continue
        held = result.existing
        merged += 1
        existing.claim(held.entity_id, transaction.source, transaction.source_id)
        if held.status in (
            TransactionStatus.BOOKED,
            TransactionStatus.FOLDED,
        ) and transaction.status is TransactionStatus.PENDING:
            existing.note_sighting(
                held.entity_id,
                transaction.account_id,
                transaction.source,
                transaction.source_id,
            )
            continue
        superseded = supersede(held, transaction)
        existing.replace(
            replace(
                superseded,
                occurrence=_occurrence_once_merged(held, superseded, existing),
            )
        )
    return MatcherPreview(merged=merged, new=new)


def reconcile_batch(
    store: Store,
    transactions: list[Transaction],
    *,
    digest: str,
    summary: ImportSummary | None = None,
    on_record: Callable[[int], None] | None = None,
    candidate_cache: dict[str, CandidateIndex] | None = None,
) -> ImportSummary:
    """Resolve a batch against what is already stored, and persist the outcome.

    Shared by file import and API pulls deliberately: identity resolution must
    behave identically whichever route data arrives by, or the same payment
    seen twice through different doors would be stored twice.

    candidate_cache, when given, carries each account's CandidateIndex
    ACROSS calls - the rebuild passes one for its whole run, because
    reloading the merged account's history once per artefact batch was
    a fifth of the rebuild and the one cost that grew with corpus times
    account size. Sound only while the caller is the sole writer (the
    rebuild holds its lease) and while the caller drops an account's
    entry whenever it mutates that account's rows outside the fold
    (vanished-pending resolution does exactly that). Live pulls pass
    nothing and keep the load-per-batch behaviour.

    on_record is called with the number resolved so far, once per record.
    A batch of several thousand is minutes of work with nothing to show
    for it from outside, and this is the only place that knows the loop
    is still turning. What it reports is NOT yet committed - the commit
    happens once, below - so a caller must present it as position within
    the batch rather than as progress banked.
    """
    result = summary or ImportSummary(artefact_new=True)
    result.parsed += len(transactions)

    numbered = _numbered(transactions)

    # Each account's history is read ONCE and then kept up to date in
    # memory as the batch resolves against it. dict.setdefault cannot be
    # used here: it evaluates its default eagerly, so every record ran a
    # full query and rebuilt every stored row of the account into a
    # Transaction before discarding the lot because the key was already
    # present. The work was invisible - correctness was unaffected - and
    # it scaled with the batch AND the account, so a merged account
    # holding two pipes' history paid it twice over.
    by_account = candidate_cache if candidate_cache is not None else {}
    store.begin_batch()
    try:
        _reconcile_all(
            store, numbered, by_account, digest, result, on_record
        )
    except BaseException:
        # Discard the failed batch's buffers: nothing of it may reach
        # disk, and a stale buffer would silently swallow the NEXT
        # caller's direct-mode writes.
        store.abort_batch()
        raise
    with instrumentation.phase("write-flush"):
        store.flush_batch()
    store.connection.commit()
    return result


def _reconcile_all(
    store: Store,
    numbered: list[Transaction],
    by_account: dict[str, CandidateIndex],
    digest: str,
    result: ImportSummary,
    on_record: Callable[[int], None] | None,
) -> None:
    # One call is one response; a cached index may have seen earlier ones.
    # Read from the artefact, as the stored sightings are, so a live pull and a
    # rebuild agree on which ids were provisional.
    pending_snapshot = store.is_pending_snapshot(digest)
    for index in by_account.values():
        index.begin_batch(pending_snapshot=pending_snapshot)
    for position, transaction in enumerate(numbered, start=1):
        existing = by_account.get(transaction.account_id)
        if existing is None:
            with instrumentation.phase("load-candidates"):
                existing = CandidateIndex(
                    store.transactions_for_account(transaction.account_id),
                    sightings=store.sighted_ids_for_account(transaction.account_id),
                )
            existing.begin_batch(pending_snapshot=pending_snapshot)
            by_account[transaction.account_id] = existing
        merged, matched_entity_id = _reconcile(store, transaction, existing, digest, result)
        # Whichever row took this record has now answered to its id.
        existing.claim(
            matched_entity_id or merged.entity_id, transaction.source, transaction.source_id
        )

        if on_record is not None:
            # Never let reporting break the work it reports on.
            with contextlib.suppress(Exception):
                on_record(position)

        if matched_entity_id is None:
            existing.append(merged)
            continue

        # REPLACE the candidate rather than appending alongside it. Appending
        # would leave the pre-merge row live, letting a later incoming record
        # claim the same stored transaction a second time - which swallows
        # repeated payments and reports them as matched.
        existing.replace(merged)


def _reconcile(
    store: Store,
    transaction: Transaction,
    existing: CandidateIndex,
    digest: str,
    summary: ImportSummary,
) -> tuple[Transaction, str | None]:
    """Resolve one transaction, returning it and the entity it merged into.

    The second element is what lets the caller replace the candidate it
    matched, rather than leaving the pre-merge row available to be claimed
    again by the next record.
    """
    with instrumentation.phase("resolve"):
        result = resolve(transaction, existing)

    if (
        result.existing is not None
        and result.existing.status in (TransactionStatus.BOOKED, TransactionStatus.FOLDED)
        and transaction.status is TransactionStatus.PENDING
    ):
        # Settlement runs one way.
        # A pending record that matches a settled row is the stale tail of a
        # pending list, or a second source that has not caught up; either way
        # it is a sighting of that row and says nothing new about it.
        # Superseding with it put a settled payment back to pending, moved its
        # date, and - when the pending record was really a different payment -
        # replaced the settled one altogether.
        sighting = replace(
            transaction, entity_id=result.existing.entity_id, artefact_digest=digest
        )
        store.record_source(sighting)
        existing.note_sighting(
            sighting.entity_id, sighting.account_id, sighting.source, sighting.source_id
        )
        summary.matched += 1
        return result.existing, result.existing.entity_id

    if (
        result.existing is not None
        and transaction.tier is SourceTier.MANUAL
        and result.existing.tier is not SourceTier.MANUAL
    ):
        # A typed row that matches a row a source reported is a sighting of it
        # and says nothing new: the precise record absorbs the imprecise one.
        # Superseding would replace a bank's description, date, and identity
        # with what a person remembered, and move the row's imported id.
        sighting = replace(
            transaction, entity_id=result.existing.entity_id, artefact_digest=digest
        )
        store.record_source(sighting)
        existing.note_sighting(
            sighting.entity_id, sighting.account_id, sighting.source, sighting.source_id
        )
        summary.matched += 1
        return result.existing, result.existing.entity_id

    if result.existing is not None:
        merged = supersede(result.existing, transaction)
        merged = replace(
            merged,
            artefact_digest=digest,
            occurrence=_occurrence_once_merged(result.existing, merged, existing),
        )
        store.upsert_transaction(
            merged, match_tier=result.tier.value, matched_entity_id=result.existing.entity_id
        )
        # Record the INCOMING source, not the merged row's. The merged row can
        # only carry one, so the sighting that just arrived is exactly the fact
        # that would otherwise be lost - and it is the one that makes this a
        # corroboration rather than a repeat.
        store.record_source(
            replace(transaction, entity_id=result.existing.entity_id, artefact_digest=digest)
        )
        if merged.status != result.existing.status:
            summary.superseded += 1
        else:
            summary.matched += 1
        return merged, result.existing.entity_id

    occurrence = existing.free_occurrence(
        transaction.account_id, transaction.content_key, wanted=transaction.occurrence
    )
    fresh = replace(
        transaction,
        occurrence=occurrence,
        entity_id=entity_id_for(
            account_id=transaction.account_id,
            source=transaction.source,
            source_id=transaction.source_id,
            content_key_value=transaction.content_key,
            occurrence=occurrence,
            first_artefact_digest=digest,
        ),
        artefact_digest=digest,
    )
    store.upsert_transaction(fresh, match_tier=result.tier.value)
    store.record_source(fresh)
    summary.inserted += 1

    # Only the genuinely ambiguous cases: something matched on amount and date
    # and was kept apart solely by the same-source rule. Flagging every new
    # transaction would bury these under thousands that need no thought.
    if result.is_ambiguous:
        store.queue_for_review(
            fresh.entity_id,
            f"stored as new, but {len(result.near_misses)} transaction(s) in this account "
            f"match on amount and date and were kept apart only by the same source rule - "
            f"confirm this is a repeated payment and not a duplicate report",
        )
        summary.needs_review += 1

    return fresh, None


def _occurrence_once_merged(
    previous: Transaction, merged: Transaction, existing: CandidateIndex
) -> int:
    """The occurrence a held row carries after a later sighting of it.

    `merged` arrives numbered by the batch that re-reported it, which says
    where it sat in that response and nothing about the row's identity.
    While the content is unchanged the number already held stands - the
    upsert keeps it too, and the in-memory candidates must agree with what
    is stored, or the next allocation is made against numbers nobody holds.
    When the sighting changes the content the row has a new content key,
    and needs a number no other row under that key is using.
    """
    if merged.content_key == previous.content_key:
        return previous.occurrence
    return existing.free_occurrence(
        merged.account_id,
        merged.content_key,
        wanted=merged.occurrence,
        excluding=previous.entity_id,
    )
