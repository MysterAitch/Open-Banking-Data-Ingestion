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
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from . import instrumentation
from .accounts import AccountMap
from .identity import artefact_digest, entity_id_for
from .matching import (
    EXACT_RULE_DOUBT,
    INTERNAL_TRANSFER_WINDOW_DAYS,
    CandidateIndex,
    MatchResult,
    pair_transfer_entities,
    plan_partners,
    resolve,
    second_row_named_by_exact_rules,
    supersede,
)
from .models import (
    BASIS_FOUNDED,
    MatchTier,
    RawArtefact,
    SourceTier,
    Transaction,
    TransactionStatus,
)
from .parsers.uk_banks import detect
from .payment_links import stated_link_of
from .plural import plural
from .review_settlement import settle_review_flags
from .same_money_fold import fold_same_money
from .space_attribution import category_resolver, fold_space_copies
from .store import Store

#: Whether a source cannot see Spaces in an account, as (source, account).
#: `family_anchors.Families.blind_in` is the one answer; the matcher is handed it
#: and never derives one (`matching.is_internal_leg_meeting_space_blind_source`).
SpaceBlind = Callable[[str, str], bool]


def _blind_in(space_blind: SpaceBlind | None, account: str) -> Callable[[str], bool] | None:
    if space_blind is None:
        return None
    return lambda source: space_blind(source, account)


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
    #: Stored rows joined to another that an exact rule proved the same payment
    #: (`_absorb_second_row`).
    absorbed: int = 0
    #: Main-account rows newly folded into the Space rows they copy, by the
    #: pass the caller ran after the batch; see `space_attribution`.
    folded: int = 0
    #: Feed rows newly folded as the same money as a statement's rows, by the
    #: pass the caller ran after the batch; see `same_money_fold`.
    same_money_folded: int = 0

    def describe(self) -> str:
        offered = ""
        if self.rows_offered is not None and self.rows_offered != self.parsed:
            skipped = self.rows_offered - self.parsed
            offered = (
                f" of {plural(self.rows_offered, 'row')} in the file - {skipped} "
                "skipped, which is a fault unless you know why"
            )
        folded = (
            f", folded {plural(self.folded, 'main-account row')} into their Space rows"
            if self.folded
            else ""
        )
        same_money = (
            f", folded {plural(self.same_money_folded, 'feed row')} as the same money a "
            "statement itemises (withheld from the push)"
            if self.same_money_folded
            else ""
        )
        absorbed = (
            f", joined {plural(self.absorbed, 'stored row')} held twice" if self.absorbed else ""
        )
        return (
            f"parsed {self.parsed}{offered}, new {self.inserted}, "
            f"matched {self.matched}, superseded {self.superseded}, "
            f"for review {self.needs_review}{absorbed}{folded}{same_money}"
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
    blind = None
    if account_map is not None:
        # Imported here: `family_anchors` reaches the store's readers, which reach this module.
        from .family_anchors import families_of

        blind = families_of(store, account_map).blind_in
    reconcile_batch(store, incoming, digest=digest, summary=summary, space_blind=blind)
    # Imported here: `declined_items` reaches the feed readers, which reach this module.
    from .declined_items import void_declined_items

    void_declined_items(store)
    if account_map is not None:
        summary.folded += fold_space_copies(store, account_map).newly_folded
    summary.same_money_folded += fold_same_money(store, account_map).newly_folded
    settle_review_flags(store)
    return summary


def pair_transfers_across_store(store: Store, account_map: AccountMap | None = None) -> int:
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

    With the account map, a leg's own statement of the category it moved to or
    from is resolved to an account, and the leg pairs only there
    (`matching.pair_transfer_entities`). Without one, every leg is paired by
    amount, sign, and date, as a store with no declared accounts always was.
    """
    resolver = category_resolver(store, account_map) if account_map is not None else None

    def counterpart(row: Transaction) -> str | None:
        if resolver is None:
            return None
        named = row.raw.get("counterPartyUid")
        return resolver(named) if isinstance(named, str) and named else None

    # Imported here: `declined_items` reaches the feed readers, which reach this module.
    from .declined_items import declined_void_entities

    # A folded row is a second report of a payment, not a movement, so it must
    # not be offered as the leg of a transfer, and nor can a reversed one or one the bank
    # declined: the money never moved, so the other side has nothing to pair with.
    never_moved = declined_void_entities(store)
    # Made first, because the pass reads rows that are already history: a withdrawal the bank
    # declined or a reversal loses its leg here, live and in a rebuild alike. A cash withdrawal
    # and its leg are paired to each other by that pass (`cash_transfers`) and offered to no one.
    from .cash_transfers import reconcile_cash_legs

    cash = reconcile_cash_legs(store)
    pairs = pair_transfer_entities(
        (
            t
            for t in store.all_transactions()
            if t.status not in (TransactionStatus.FOLDED, TransactionStatus.REVERSED)
            and t.entity_id not in never_moved
            and t.entity_id not in cash.entities
        ),
        counterpart=counterpart,
    )
    pairs = [*pairs, *cash.pairs]
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


class RowOutcome(StrEnum):
    """What the matcher would do with one row of a batch."""

    #: It merges onto a row the account already holds.
    HELD = "held"
    #: It is added.
    NEW = "new"
    #: It is held or added, and a flag would be raised for a person to decide.
    DECISION = "decision"


@dataclass(frozen=True)
class RowPreview:
    """One row of a batch, and what the matcher would do with it."""

    day: date
    amount_minor: int
    description: str
    outcome: RowOutcome
    #: The source of the stored row it merges onto, "" where it does not.
    held_source: str = ""
    #: The reason the flag would carry, "" where none would be raised.
    reason: str = ""
    #: The account it is a row of.
    account: str = ""
    #: Another account of the household that moved the same amount the other way within a day
    #: of it, where one did (`with_transfer_legs`); "" where none did or none was looked for.
    transfer_with: str = ""

    @property
    def direction(self) -> str:
        return "in" if self.amount_minor > 0 else "out"


@dataclass(frozen=True)
class MatcherPreview:
    """What `reconcile_batch` would do with a batch, counted and not done.

    `rows` says it row by row, in the batch's order: where each merges and onto what, which are
    new, and which would raise a flag and why.
    """

    merged: int
    new: int
    rows: tuple[RowPreview, ...] = ()

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


def preview_reconcile(
    store: Store,
    transactions: list[Transaction],
    *,
    space_blind: SpaceBlind | None = None,
    transfers: bool = False,
) -> MatcherPreview:
    """Count how a batch would resolve against what is stored, writing nothing.

    The same loop `_reconcile_all` runs - the same numbering, the same
    `resolve`, a row claimed once it has answered, a merged row replacing its
    candidate and a new row joining the candidates so a later row of the batch
    can merge onto it - over indexes built from the store's rows and
    sightings. The only stores touched are reads: no upsert, no sighting, no
    review flag, and no batch is opened, so a caller can ask before deciding
    whether to read the batch in. A new row is given a stand-in identity,
    which never leaves the index.

    The preview says each row too (`MatcherPreview.rows`); with `transfers` it also names the
    account whose row it could be the other leg of (`with_transfer_legs`).
    """
    by_account: dict[str, CandidateIndex] = {}
    merged = 0
    new = 0
    rows: list[RowPreview] = []
    numbered = _numbered(transactions)
    for transaction in numbered:
        if transaction.account_id not in by_account:
            loaded = CandidateIndex(
                store.transactions_for_account(transaction.account_id),
                sightings=store.sighted_ids_for_account(transaction.account_id),
                space_blind=_blind_in(space_blind, transaction.account_id),
                settlements=store.settlement_days_for_account(transaction.account_id),
                links=store.linked_ids_for_account(transaction.account_id),
                listings=store.listed_sightings_for_account(transaction.account_id),
            )
            loaded.begin_batch()
            by_account[transaction.account_id] = loaded
    plans = _partner_plans(numbered, by_account)
    for position, transaction in enumerate(numbered):
        existing = by_account[transaction.account_id]
        partner, reserved = plans[position]
        result = resolve(transaction, existing, partner=partner, reserved=reserved)
        reason = doubt_reason(result.review) or ambiguity_reason(result)
        rows.append(
            RowPreview(
                transaction.value_date,
                transaction.amount_minor,
                transaction.description,
                RowOutcome.DECISION
                if reason
                else RowOutcome.NEW
                if result.existing is None
                else RowOutcome.HELD,
                held_source="" if result.existing is None else result.existing.source,
                reason=reason,
                account=transaction.account_id,
            )
        )
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
    return MatcherPreview(
        merged=merged,
        new=new,
        rows=with_transfer_legs(store, rows) if transfers else tuple(rows),
    )


def with_transfer_legs(store: Store, rows: Sequence[RowPreview]) -> tuple[RowPreview, ...]:
    """The rows, each naming the other account that moved the same amount the other way within
    `INTERNAL_TRANSFER_WINDOW_DAYS`, where one did: the leg a transfer pairing would take it
    with (`matching.pair_transfer_entities` states the rule).

    Each stored row is the partner of one row at most, as in the pairing. This is that rule's
    candidate test over the rows the store holds now, not the store-wide greedy pass, which can
    choose differently where several rows fit; so a row named here is one that COULD be a leg.
    """
    window = timedelta(days=INTERNAL_TRANSFER_WINDOW_DAYS)
    legs: dict[str, list[tuple[str, date, int]]] = {}
    for account in {row.account for row in rows}:
        mine = [row for row in rows if row.account == account]
        legs[account] = store.counter_legs(
            {-row.amount_minor for row in mine},
            min(row.day for row in mine) - window,
            max(row.day for row in mine) + window,
            excluding=account,
        )
    taken: dict[str, set[int]] = {account: set() for account in legs}
    found: list[RowPreview] = []
    for row in rows:
        partner = ""
        for at, (other, day, amount) in enumerate(legs[row.account]):
            if at in taken[row.account] or amount != -row.amount_minor:
                continue
            if abs(day - row.day) <= window:
                taken[row.account].add(at)
                partner = other
                break
        found.append(replace(row, transfer_with=partner) if partner else row)
    return tuple(found)


def reconcile_batch(
    store: Store,
    transactions: list[Transaction],
    *,
    digest: str,
    summary: ImportSummary | None = None,
    on_record: Callable[[int], None] | None = None,
    candidate_cache: dict[str, CandidateIndex] | None = None,
    space_blind: SpaceBlind | None = None,
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

    space_blind says which sources cannot see an account's Spaces
    (`Families.blind_in`), for the rule that keeps an internal leg apart from
    their rows. A caller that has no account map passes nothing, and then the
    rule is not applied: a rebuild with the map re-resolves every row under it.
    A cached index keeps the answer it was built with, so the cache and the
    argument must come from the same map.

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
            store, numbered, by_account, digest, result, on_record, space_blind
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
    space_blind: SpaceBlind | None = None,
) -> None:
    # One call is one response; a cached index may have seen earlier ones.
    # Read from the artefact, as the stored sightings are, so a live pull and a
    # rebuild agree on which ids were provisional.
    pending_snapshot = store.is_pending_snapshot(digest)
    for index in by_account.values():
        index.begin_batch(pending_snapshot=pending_snapshot)
    for transaction in numbered:
        if transaction.account_id not in by_account:
            with instrumentation.phase("load-candidates"):
                loaded = CandidateIndex(
                    store.transactions_for_account(transaction.account_id),
                    sightings=store.sighted_ids_for_account(transaction.account_id),
                    space_blind=_blind_in(space_blind, transaction.account_id),
                    settlements=store.settlement_days_for_account(transaction.account_id),
                    links=store.linked_ids_for_account(transaction.account_id),
                    listings=store.listed_sightings_for_account(transaction.account_id),
                )
            loaded.begin_batch(pending_snapshot=pending_snapshot)
            by_account[transaction.account_id] = loaded
    with instrumentation.phase("plan-partners"):
        plans = _partner_plans(numbered, by_account)
    for position, transaction in enumerate(numbered, start=1):
        existing = by_account[transaction.account_id]
        merged, matched_entity_id = _reconcile(
            store, transaction, existing, digest, result, plans[position - 1]
        )
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


def _partner_plans(
    numbered: list[Transaction], by_account: dict[str, CandidateIndex]
) -> list[tuple[str | None, frozenset[str]]]:
    """Each record's planned partner and the rows reserved, in the batch's order.

    Planned per account against the rows held before the batch starts, so the
    outcome is the same whichever order the batch lists its records in
    (`matching.plan_partners` states the rule).
    """
    positions_by_account: dict[str, list[int]] = {}
    for position, transaction in enumerate(numbered):
        positions_by_account.setdefault(transaction.account_id, []).append(position)
    plans: list[tuple[str | None, frozenset[str]]] = [(None, frozenset())] * len(numbered)
    for account, positions in positions_by_account.items():
        plan = plan_partners([numbered[p] for p in positions], by_account[account])
        for local, position in enumerate(positions):
            plans[position] = plan.for_position(local)
    return plans


def _reconcile(
    store: Store,
    transaction: Transaction,
    existing: CandidateIndex,
    digest: str,
    summary: ImportSummary,
    plan: tuple[str | None, frozenset[str]] = (None, frozenset()),
) -> tuple[Transaction, str | None]:
    """Resolve one transaction, returning it and the entity it merged into.

    The second element is what lets the caller replace the candidate it
    matched, rather than leaving the pre-merge row available to be claimed
    again by the next record.
    """
    with instrumentation.phase("resolve"):
        result = resolve(transaction, existing, partner=plan[0], reserved=plan[1])
    if result.existing is not None and result.tier in (MatchTier.LINKED_ID, MatchTier.SOURCE_ID):
        result = _absorb_second_row(store, transaction, existing, result, summary)

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
        store.record_source(sighting, basis=result.basis)
        existing.note_sighting(
            sighting.entity_id, sighting.account_id, sighting.source, sighting.source_id
        )
        existing.note_link(sighting.entity_id, sighting.account_id, stated_link_of(sighting))
        _flag(store, summary, result.existing.entity_id, result.review)
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
        store.record_source(sighting, basis=result.basis)
        existing.note_sighting(
            sighting.entity_id, sighting.account_id, sighting.source, sighting.source_id
        )
        _flag(store, summary, result.existing.entity_id, result.review)
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
            replace(transaction, entity_id=result.existing.entity_id, artefact_digest=digest),
            basis=result.basis,
        )
        _flag(store, summary, result.existing.entity_id, result.review)
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
    store.record_source(fresh, basis=BASIS_FOUNDED)
    summary.inserted += 1
    _flag(store, summary, fresh.entity_id, result.review)

    # Only the genuinely ambiguous cases: something matched on amount and date
    # and was kept apart solely by the same-source rule. Flagging every new
    # transaction would bury these under thousands that need no thought.
    if reason := ambiguity_reason(result):
        store.queue_for_review(fresh.entity_id, reason)
        summary.needs_review += 1

    return fresh, None


def ambiguity_reason(result: MatchResult) -> str:
    """What a new row is queued with where something like it was kept apart only by the
    same-source rule, or "" where nothing is. The one wording of that flag: the import queues it
    and a dry run says it beforehand."""
    if not result.is_ambiguous:
        return ""
    return (
        f"stored as new, but {len(result.near_misses)} transaction(s) in this account "
        f"match on amount and date and were kept apart only by the same source rule - "
        f"confirm this is a repeated payment and not a duplicate report"
    )


def doubt_reason(review: str) -> str:
    """What a row is queued with for a doubt the exact rules raised (`MatchResult.review`), or
    "" where they raised none. The one wording of that flag, as `ambiguity_reason` is of the
    other."""
    return f"{review} {EXACT_RULE_DOUBT}" if review else ""


def _absorb_second_row(
    store: Store,
    transaction: Transaction,
    index: CandidateIndex,
    result: MatchResult,
    summary: ImportSummary,
) -> MatchResult:
    """Join the second stored row an exact rule names for this record to the first.

    `matching.second_row_named_by_exact_rules` says when two rows are one payment.
    The row KEPT is the one whose artefact arrived first, with the lower entity id on a tie:
    it has been annotated, protected, and sent to the budgeting application for longest, and
    its entity id is what an annotation or a review decision is keyed to.
    What the push then sees: the record merges into the kept row as any later sighting does,
    so the row carries the record's own content key from then on (`matching.supersede`), and
    the content key and occurrence of the absorbed row, which no row produces any longer, is
    the orphan the existing alignment step removes. Making the kept row's own key survive was
    rejected: both rows' keys come from the sightings that would not join them, and the
    record's is the one a later fetch of the same source reproduces.
    """
    first = result.existing
    if first is None:
        return result
    other = second_row_named_by_exact_rules(transaction, index, first)
    if other is None:
        return result
    furthest = (datetime.max.replace(tzinfo=UTC), 0)

    def arrived(row: Transaction) -> tuple[tuple[datetime, int], str]:
        return (store.arrival_of(row.entity_id) or furthest, row.entity_id)

    kept, gone = sorted((first, other), key=arrived)
    store.note_absorption(kept.entity_id, gone.entity_id)
    index.absorb(kept.entity_id, gone.entity_id)
    summary.absorbed += 1
    return replace(result, existing=kept)


def _flag(store: Store, summary: ImportSummary, entity_id: str, reason: str) -> None:
    """Queue a doubt the exact rules raised, where there is one."""
    if not reason:
        return
    store.queue_for_review(entity_id, doubt_reason(reason))
    summary.needs_review += 1


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
