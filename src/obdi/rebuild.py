"""Rebuild every derived layer from the raw artefacts alone.

The founding promise of the layer model, made executable: layer 0 is the
canonical copy and everything above it is transient and regenerable. A
matching bug is therefore recoverable rather than permanent - fix the rule,
rebuild, and every payment re-resolves under the fixed rule, in the same
order the evidence originally arrived.

What is wiped: transactions, sightings, the review queue. What is kept:
the artefacts themselves, the provider facts (learnt at quota cost), the
attempt ledger (history of asks), and the events outbox (already-emitted
facts about the past do not un-happen).

Account bindings survive by construction: a bind moves the label on the
artefacts too, so replayed rows land under the bound name.

Entity ids are minted deterministically from the first sighting (account,
source identity, artefact digest), so a rebuild REPRODUCES them - two
replays of the same layer 0 agree row for row, ids included, and live
ingest agrees with a later rebuild. The determinism is conditional on
(stream, rules): a rule change can move which sighting is first and with
it the id, which is why downstream consumers still key on content - the
Actual replay's imported_id is the content key plus occurrence - rather
than on ids, keeping rebuild-then-replay a no-op under rule changes too.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from . import instrumentation
from .accounts import AccountMap
from .errors import DataError
from .family_anchors import families_of
from .ingest import ImportSummary, SpaceBlind, pair_transfers_across_store, reconcile_batch
from .jsontypes import rows as json_rows
from .matching import CandidateIndex
from .models import Transaction
from .namespaces import (
    API_SOURCES,
    MANUAL_SOURCE,
    MANUAL_WITHDRAWAL_SOURCE,
    UNASSIGNED_ACCOUNT,
)
from .parsers.uk_banks import detect
from .pending_lifecycle import resolve_vanished_pending
from .period_reconciliation import SAME_MONEY_PHASE
from .protection import recheck as recheck_protections
from .providers import starling, truelayer
from .review_report import FlagClass
from .review_settlement import SettleReport, settle_review_flags
from .same_money_fold import fold_same_money
from .space_attribution import FoldRefusal, fold_space_copies
from .statement_sections import SectionBatches, replay_batches
from .store import Store
from .typed_transactions import transaction_from_entry, withdrawn_entry_ids


@dataclass
class RebuildReport:
    artefacts_replayed: int = 0
    artefacts_skipped: int = 0
    transactions: int = 0
    transfers_paired: int = 0
    problems: list[str] = field(default_factory=list)
    #: Per-account row counts before the wipe and after the replay - the
    #: reconciliation that turns "rebuild finished" into "and here is
    #: what changed". An aborted or lossy rebuild announces itself here
    #: as "account: N -> 0 (VANISHED)" instead of hiding behind a page
    #: that quietly shows fewer rows.
    account_changes: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Records the replay must process, counted up front from the payloads
    #: themselves - exact, rather than a floor that grows as it discovers.
    records_total: int = 0
    #: Records processed by artefacts already FINISHED. Deliberately not
    #: including the one in flight, so the pair reads honestly: work done,
    #: then the size of what is currently holding things up.
    records_done: int = 0
    #: How many records the artefact being processed right now contains.
    current_records: int = 0
    #: Which artefact that is, for the same reason.
    current_index: int = 0
    #: Phase timings when OBDI_TIMINGS is set; empty otherwise. Opt-in
    #: because per-record clocks are only worth paying for while a
    #: performance question is actually open.
    timings: dict[str, dict[str, float | int]] = field(default_factory=dict)
    #: How far INTO the current artefact the replay has resolved. Kept
    #: apart from records_done because this work is not committed yet -
    #: the batch commits once, at its end - so adding it to the banked
    #: figure would report progress a crash could take back.
    records_in_flight: int = 0
    #: Statements kept before anyone decided whose they are.
    #: They are evidence, not a problem, and are never read into rows: the
    #: rows would be filed under an account that does not exist.
    kept_unassigned: int = 0
    #: Of those, how many a parser recognises and could read once assigned.
    kept_readable: int = 0
    #: Accounts of "all accounts" statements: those a person assigned, read
    #: back into their accounts, and those still waiting for one.
    kept_sections_replayed: int = 0
    kept_sections_unassigned: int = 0
    #: Main-account rows folded into the Space rows they copy, and the
    #: space-blind rows the fold left counted - see `space_attribution`.
    space_folded: int = 0
    space_ambiguous: int = 0
    space_unmatched: int = 0
    #: Which refusal each of the `space_ambiguous` rows was.
    space_refusals: tuple[FoldRefusal, ...] = ()
    #: Feed rows folded as the same money a statement itemises - see
    #: `same_money_fold`.
    same_money_folded: int = 0
    #: Review flags the evidence already answered and the pass closed, by the
    #: class of proof, and the open flags that remain - see `review_settlement`.
    review_settled: dict[FlagClass, int] = field(default_factory=dict)
    review_still_open: int = 0

    def describe(self) -> str:
        lines = [
            f"replayed {self.artefacts_replayed} artefact(s) "
            f"({self.artefacts_skipped} non-transactional skipped), "
            f"{self.transactions} transaction(s) resolved, "
            f"{self.transfers_paired} transfer pair(s) confirmed"
        ]
        changed = {
            account: (before, after)
            for account, (before, after) in sorted(self.account_changes.items())
            if before != after
        }
        if self.account_changes and not changed:
            lines.append(
                "  account totals unchanged - the replay reproduced the store"
            )
        for account, (before, after) in changed.items():
            marker = ""
            if after == 0 and before > 0:
                marker = " (VANISHED - check problems and layer 0)"
            elif before == 0 and after > 0:
                marker = " (new)"
            lines.append(f"  {account}: {before} -> {after}{marker}")
        # Identical lines collapse to one with a count: a store holding thirty
        # undecodable statements printed thirty copies and buried the account
        # lines that say whether anything was lost.
        grouped: dict[str, int] = {}
        for problem in self.problems:
            grouped[problem] = grouped.get(problem, 0) + 1
        for problem, count in grouped.items():
            noun = "artefact" if count == 1 else "artefacts"
            lines.append(f"  problem: {problem} ({count} {noun})")
        if self.problems:
            lines.append(
                "  Each artefact listed as a problem was skipped: the replay "
                "could not read it, so it produced no rows. Layer 0 still "
                "holds it."
            )
            if self.account_changes and not changed:
                lines.append(
                    "  The account totals are unchanged, so these problems "
                    "lost nothing the store held."
                )
            elif changed:
                lines.append(
                    "  Where an account's total changed above, check whether "
                    "a skipped artefact fed it."
                )
        if self.space_folded or self.space_ambiguous:
            lines.append(
                f"  {self.space_folded} main-account row(s) folded into their Space "
                f"rows - the same payment, which the aggregator and the export "
                f"report under the main account and the bank's feed files under "
                f"the Space. {self.space_ambiguous} more could not be paired "
                f"one to one and stay counted in the main account."
            )
            for refusal in self.space_refusals[:_REFUSALS_NAMED]:
                lines.append(
                    f"  not folded: the {refusal.account} row dated {refusal.day.isoformat()} - "
                    f"{refusal.describe()}."
                )
            hidden = len(self.space_refusals) - _REFUSALS_NAMED
            if hidden > 0:
                lines.append(f"  and {hidden} more not folded.")
        if self.same_money_folded:
            lines.append(
                f"  {self.same_money_folded} feed row(s) folded as the same money a "
                "statement itemises differently (the statement's own rows stay "
                "counted). A folded row no longer counts and is withheld from the "
                "push; one already in Actual becomes an orphan that the removal "
                "pass takes out."
            )
        if self.review_settled:
            lines.append(
                "  "
                + SettleReport(self.review_settled, self.review_still_open).describe()
            )
        if self.kept_unassigned:
            noun = "statement" if self.kept_unassigned == 1 else "statements"
            unread = self.kept_unassigned - self.kept_readable
            lines.append(
                f"  {self.kept_unassigned} kept {noun} with no account yet, so "
                f"none was read into rows: {self.kept_readable} a parser "
                f"recognises, {unread} with no parser yet for its layout. "
                "The Kept statements page says which, and whether each "
                "recognised one can actually be read."
            )
        if self.kept_sections_replayed or self.kept_sections_unassigned:
            lines.append(
                f"  {self.kept_sections_replayed} account(s) of all-accounts "
                f"statements read back into the accounts they were assigned to; "
                f"{self.kept_sections_unassigned} more still have no account, so "
                "contributed no rows."
            )
        return "\n".join(lines)


#: API sources whose payloads parse into transactions. Everything else the
#: API namespace declares is evidence kept but not replayed - DERIVED from
#: the registry rather than listed twice, because the two lists drifted
#: within a week of each other's creation: starling-identifiers was
#: declared in the namespace, missed here, and every rebuild fed it to the
#: CSV detector and reported a spurious problem. A source can be a member
#: of exactly one of these sets, and only one is written by hand.
_TRANSACTIONAL = frozenset(
    {
        "truelayer-booked",
        "truelayer-pending",
        "truelayer-card-booked",
        "starling-feed",
    }
)

#: Artefact sources that carry no transactions - evidence kept, not replayed.
_NON_TRANSACTIONAL = frozenset(API_SOURCES - _TRANSACTIONAL)

#: Artefacts that read into no row of their own: the provider facts above, and a
#: withdrawal, which acts on the entry it names (`typed_transactions`).
_READS_NO_ROWS = _NON_TRANSACTIONAL | {MANUAL_WITHDRAWAL_SOURCE}


#: Artefact refs beginning with these are provider-qualified fallbacks
#: ("source:provider_ref") and get resolved through the account map at
#: replay time; anything else is already a canonical name.
_SOURCES = ("starling", "truelayer")

#: How many unfolded copies the summary names before it only counts the rest.
_REFUSALS_NAMED = 5


#: The provider-true identity of a feed fetch, recorded at landing time:
#: /api/v2/feed/account/{accountUid}/category/{categoryUid}?...
_FEED_ORIGIN = re.compile(r"/feed/account/([^/?]+)/category/([^/?]+)")


def _record_count(payload: object) -> int:
    """How many records a landed payload carries.

    RECORDS, not transactions: this counts what goes in, so it stays
    comparable with progress through the file. What comes out is a
    different and equally interesting number, and conflating them makes
    a total that is wrong while looking reasonable.
    """
    if not isinstance(payload, str | bytes | bytearray):
        return 0
    try:
        decoded = json.loads(payload)
    except ValueError:
        return 0
    if not isinstance(decoded, dict):
        return 0
    for key in ("results", "feedItems", "booked", "pending", "accounts"):
        rows = decoded.get(key)
        if isinstance(rows, list):
            return len(rows)
    return 0


def _starling_defaults(artefact_rows: Sequence[Any]) -> dict[str, str]:
    """defaultCategory -> accountUid, from the starling-accounts artefacts.

    A main account's feed is fetched via its default category, but its
    identity key is the ACCOUNT uid (that is what binds it); a Space's
    identity key is its category uid. This mapping is what tells the two
    apart when reading an origin."""
    defaults: dict[str, str] = {}
    for row in artefact_rows:
        if str(row["source"]) != "starling-accounts":
            continue
        with contextlib.suppress(ValueError, KeyError, TypeError, AttributeError):
            decoded = json.loads(row["payload"])
            for account in decoded.get("accounts", []) or []:
                uid = str(account.get("accountUid", ""))
                default = str(account.get("defaultCategory", ""))
                if uid and default:
                    defaults[default] = uid
    return defaults


def _starling_feed_ref(
    origin: str,
    stored_ref: str,
    defaults: dict[str, str],
    account_map: AccountMap | None,
) -> str:
    """Identity from the origin, never the stored label.

    Labels froze whatever the map said at landing time - and during the
    mis-bind era that meant three accounts' history landed under one
    name. The origin records the request that actually happened; the
    stored label is used only when the origin is unreadable (imports
    from before origins were recorded)."""
    match = _FEED_ORIGIN.search(origin)
    if not match:
        return _resolve_ref(stored_ref, account_map)
    account_uid, category_uid = match.group(1), match.group(2)
    key = account_uid if defaults.get(category_uid) == account_uid else category_uid
    if account_map is not None:
        return account_map.resolve("starling", key)
    return f"starling:{key}"


def _resolve_ref(account_ref: str, account_map: AccountMap | None) -> str:
    if account_map is None or ":" not in account_ref:
        return account_ref
    source, _, provider_ref = account_ref.partition(":")
    if source not in _SOURCES:
        return account_ref
    return account_map.resolve(source, provider_ref)


def parse_artefact_transactions(
    source: str, payload: bytes, account_ref: str, digest: str
) -> list[Transaction]:
    """Parse one artefact's payload into transactions - the shared
    reading path for full rebuilds and single-artefact replays. Raises
    provider and data errors for the caller to record or surface."""
    if source in ("truelayer-booked", "truelayer-pending"):
        decoded = json.loads(payload)
        return [
            replace(
                truelayer.to_transaction(
                    record,
                    account_id=account_ref,
                    pending=source.endswith("pending"),
                ),
                artefact_digest=digest,
            )
            for record in json_rows(decoded, "results")
        ]
    if source == "truelayer-card-booked":
        decoded = json.loads(payload)
        return [
            replace(
                truelayer.to_card_transaction(record, account_id=account_ref),
                artefact_digest=digest,
            )
            for record in json_rows(decoded, "results")
        ]
    if source == "starling-feed":
        decoded = json.loads(payload)
        transactions = []
        for item in json_rows(decoded, "feedItems"):
            for transaction in starling.to_transactions(item, account_id=account_ref):
                transactions.append(replace(transaction, artefact_digest=digest))
        return transactions
    if source == MANUAL_SOURCE:
        return [transaction_from_entry(payload, account_ref, digest)]
    # File imports: source is the suffix (csv, qif, ...). The parser
    # registry re-detects from the bytes, exactly as the original import.
    parser = detect(payload)
    return list(parser.parse(payload, account_id=account_ref))


def resolve_artefact_ref(
    row: Any, account_map: AccountMap | None, starling_defaults: dict[str, str]
) -> str:
    """The identity half of the shared reading path."""
    source = str(row["source"])
    if source == "starling-feed":
        return _starling_feed_ref(
            str(row["origin"]), str(row["account_ref"]), starling_defaults, account_map
        )
    return _resolve_ref(str(row["account_ref"]), account_map)


def _replay_sections(
    store: Store,
    report: RebuildReport,
    digest: str,
    payload: bytes,
    account_map: AccountMap | None,
    candidate_cache: dict[str, CandidateIndex],
    space_blind: SpaceBlind | None = None,
) -> SectionBatches:
    """Read each assigned section of one kept statement back into its account.

    Each section is its own batch, numbered on its own, exactly as it was when
    assigned live: that is what lets a section overlapping an annual statement
    merge into it occurrence for occurrence rather than being renumbered by
    whatever else the rebuild has seen.
    """
    replay = replay_batches(
        store, digest, payload, lambda ref: _resolve_ref(ref, account_map)
    )
    report.kept_sections_unassigned += replay.unassigned
    report.problems.extend(replay.problems)
    for _account, transactions in replay.batches:
        if transactions:
            reconcile_batch(
                store,
                transactions,
                digest=digest,
                summary=ImportSummary(artefact_new=False),
                candidate_cache=candidate_cache,
                space_blind=space_blind,
            )
            report.transactions += len(transactions)
        report.kept_sections_replayed += 1
    return replay


def rebuild_from_raw(
    store: Store,
    progress: Callable[[int, int, RebuildReport], None] | None = None,
    account_map: AccountMap | None = None,
) -> RebuildReport:
    """Wipe the derived layers and replay layer 0 in arrival order.

    Arrival order matters: occurrence counting and supersession depend on
    which sighting came first, and replaying in fetched_at order reproduces
    the history the store actually lived through.

    Every source-qualified artefact ref is resolved through the CURRENT
    account map - the promise the button makes. Without this, artefacts
    landed before a bind replayed under the raw ref while coverage marks
    sat under the canonical, and one real account rendered as two rows:
    a nameless one holding the rows and a named ghost holding nothing.

    `progress` is called as (reaching, total, report) before each artefact
    and once more at the end - a rebuild takes minutes, and "running" with
    no number reads as "hung" to anyone watching a page. A failing
    progress callback is ignored: reporting must never break the work.
    """
    report = RebuildReport()

    before_counts = {
        str(row[0]): int(row[1])
        for row in store.connection.execute(
            "SELECT account_id, COUNT(*) FROM transactions GROUP BY account_id"
        )
    }

    # The claim of currency is withdrawn BEFORE the wipe. Anything that
    # kills this process from here until the re-stamp leaves a partial
    # derived layer, and a store that says so rather than one that
    # certifies itself current while holding half the corpus.
    from .fingerprint import invalidate_fingerprint

    invalidate_fingerprint(store)
    store.connection.execute("DELETE FROM transactions")
    store.connection.execute("DELETE FROM transaction_sources")
    store.connection.execute("DELETE FROM sighting_times")
    # A new parser takes effect here, so what the old one read is forgotten.
    store.clear_statement_readings()
    # The same-money pass rewrites these at the end of the rebuild.
    store.clear_same_money_outcomes()
    # UNRESOLVED only. An unjudged flag is a claim the current rules make about
    # the current evidence, so re-deriving it is right: keeping it would
    # preserve doubts the rules have since learned to settle, and the queue
    # could only ever grow. A RESOLVED one is the opposite - it is the single
    # thing here that replaying raw evidence cannot reproduce, because the
    # evidence is exactly what was ambiguous. Wiping the table discarded those
    # silently, after an operation the page encourages following every refile
    # and which also runs by itself after every deploy. Re-adjudication is no
    # substitute: it is not idempotent, so a second pass can reach a different
    # answer from the one a person gave.
    #
    # Safe because entity ids are deterministic - the rebuild re-mints exactly
    # the ids it wiped, so a kept row still names the transaction it judged.
    # That is the property annotations already rely on.
    store.connection.execute("DELETE FROM review_queue WHERE resolved_at IS NULL")
    store.connection.commit()

    artefact_rows = store.connection.execute(
        "SELECT rowid, source, account_ref, digest, payload, origin, "
        "record_count FROM raw_artefacts ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()
    starling_defaults = _starling_defaults(artefact_rows)
    retracted = withdrawn_entry_ids(
        ((str(row["source"]), row["payload"]) for row in artefact_rows),
        report.problems,
    )

    if instrumentation.enabled():
        # Each rebuild reports its own numbers, not the residue of the
        # last one - or of a scheduled pull that ran in between.
        instrumentation.reset()

    # Count the whole job before starting it. Parsing every payload costs
    # under a second across the entire store, which is cheaper than the
    # bookkeeping needed to avoid doing so - and it makes the total exact
    # instead of a floor that rises as the replay learns.
    sizes: dict[int, int] = {}
    for row in artefact_rows:
        if str(row["source"]) in _READS_NO_ROWS:
            continue
        sizes[int(row["rowid"])] = _record_count(row["payload"])
    report.records_total = sum(sizes.values())

    total = len(artefact_rows)
    # One CandidateIndex per account for the WHOLE replay: the fold is
    # per-account and this run is the store's only writer (the lease
    # guarantees it), so the in-memory index and the committed rows
    # cannot diverge - except where this loop itself mutates rows
    # outside reconcile_batch, which is handled at that site below.
    candidate_cache: dict[str, CandidateIndex] = {}
    # Read from the raw artefacts and the map alone, so it is the same answer
    # at every point of the replay, whatever has been resolved so far.
    space_blind = None if account_map is None else families_of(store, account_map).blind_in
    for index, row in enumerate(artefact_rows, start=1):
        report.current_index = index
        report.current_records = sizes.get(int(row["rowid"]), 0)
        report.records_in_flight = 0
        if progress is not None:
            with contextlib.suppress(Exception):
                progress(index, total, report)
        source = str(row["source"])
        account_ref = resolve_artefact_ref(row, account_map, starling_defaults)
        digest = str(row["digest"])
        payload = row["payload"]

        if source in _READS_NO_ROWS:
            report.artefacts_skipped += 1
            continue

        if account_ref == UNASSIGNED_ACCOUNT:
            # Kept before anyone decided whose it is, so it is not replayed.
            report.artefacts_skipped += 1
            # The statement as a whole has no account, but an "all accounts"
            # document may have had some of its sections assigned: those are
            # declared state, which the artefact alone cannot reproduce, so
            # they are read back here or the rebuild would silently drop them.
            # A document every one of whose sections is assigned is waiting
            # for nothing, so it is not counted as waiting for an account.
            waiting = True
            if source == "statement":
                with instrumentation.phase("reconcile"):
                    replay = _replay_sections(
                        store,
                        report,
                        digest,
                        bytes(payload),
                        account_map,
                        candidate_cache,
                        space_blind,
                    )
                waiting = not replay.every_section_assigned
            if waiting:
                # Asking which parser would take it costs one read and tells
                # the person how many are waiting only on an account.
                report.kept_unassigned += 1
                with contextlib.suppress(DataError, ValueError):
                    detect(payload)
                    report.kept_readable += 1
            continue

        summary = ImportSummary(artefact_new=False)
        try:
            with instrumentation.phase("parse"):
                transactions = parse_artefact_transactions(
                    source, payload, account_ref, digest
                )
        except (
            DataError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            starling.StarlingError,
            truelayer.TrueLayerError,
        ) as exc:
            # TypeError/AttributeError cover shape poison: a body that IS
            # valid JSON but the wrong shape (a string where an object
            # should be) fails on .get/.items, and must be recorded and
            # skipped like every other poison - the store is already
            # wiped by the time this loop runs.
            # Provider errors subclass RuntimeError, and before they were
            # listed here ONE problem item aborted the whole rebuild
            # mid-loop - after the wipe, with everything later in arrival
            # order (all of Starling, live) silently absent. A poison
            # artefact is that artefact's problem, recorded and skipped.
            report.problems.append(f"{source} for {account_ref}: {exc}")
            report.artefacts_skipped += 1
            continue
        if source == MANUAL_SOURCE:
            # Retracted by a withdrawal that may have arrived at any time: the
            # entry stays in layer 0 and reads into no row.
            transactions = [t for t in transactions if t.source_id not in retracted]

        if row["record_count"] is None:
            # Landed metadata, recorded once: how many transactions this
            # artefact yielded. Deliberately NOT the progress denominator -
            # that counts records going in, and the two are different
            # numbers whose blending produced a total that chased its own
            # numerator.
            store.connection.execute(
                "UPDATE raw_artefacts SET record_count = ? WHERE rowid = ?",
                (len(transactions), int(row["rowid"])),
            )
        report.artefacts_replayed += 1
        if transactions:
            # Both the report and the artefact number are bound here
            # rather than closed over: the callback only ever runs during
            # this iteration, but a late-bound loop variable is the kind
            # of thing that becomes wrong the moment anything defers.
            def tick(
                position: int,
                _report: RebuildReport = report,
                _index: int = index,
            ) -> None:
                _report.records_in_flight = position
                if progress is not None:
                    progress(_index, total, _report)

            with instrumentation.phase("reconcile"):
                reconcile_batch(
                    store,
                    transactions,
                    digest=digest,
                    summary=summary,
                    on_record=tick if progress is not None else None,
                    candidate_cache=candidate_cache,
                    space_blind=space_blind,
                )
            report.transactions += len(transactions)
        # Banked only once the batch has committed. Counting the artefact
        # as done before resolving it would have the total include work
        # still in progress, which is precisely the overstatement the
        # in-flight figure exists to avoid.
        report.records_done += sizes.get(int(row["rowid"]), 0)
        report.records_in_flight = 0
        if source == "truelayer-pending":
            # Complete-set semantics replayed in order: the same resolution
            # the live pull runs, but WITHOUT re-emitting events - the
            # outbox records what was announced at the time, and a rebuild
            # re-derives state, not history.
            with instrumentation.phase("pending-lifecycle"):
                # This mutates stored rows (voiding vanished pendings)
                # OUTSIDE the fold, so the account's cached index is now
                # stale - a voided row cached as pending could wrongly
                # claim a settlement pair. Drop it; the next batch that
                # touches the account reloads the committed truth.
                candidate_cache.pop(account_ref, None)
                resolve_vanished_pending(
                    store,
                    account_ref,
                    present_source_ids={
                        t.source_id for t in transactions if t.source_id
                    },
                    present_amount_dates={
                        (t.amount_minor, t.value_date.isoformat())
                        for t in transactions
                    },
                    emit_events=False,
                )

    # Before pairing, so a main-account copy of a Space payment is not
    # offered as one leg of a transfer. Without an account map there is no way
    # to know which accounts are siblings, so nothing is folded.
    if account_map is not None:
        with instrumentation.phase("space-fold"):
            folds = fold_space_copies(store, account_map)
        report.space_folded = folds.folded
        report.space_ambiguous = folds.ambiguous
        report.space_unmatched = folds.unmatched
        report.space_refusals = folds.refusals
    # After the fold, because a row folded into a Space row is history and its
    # flag is one of the questions this closes. It runs with or without an
    # account map: most of what it settles has nothing to do with Spaces.
    with instrumentation.phase("review-settlement"):
        settled = settle_review_flags(store)
    report.review_settled = settled.settled
    report.review_still_open = settled.still_open
    with instrumentation.phase("transfer-pairing"):
        report.transfers_paired = pair_transfers_across_store(store, account_map)
    # After pairing, because a confirmed transfer leg is never folded and the
    # pairing table is how the pass knows one.
    with instrumentation.phase(SAME_MONEY_PHASE):
        report.same_money_folded = fold_same_money(store, account_map).folded
    # Last, so a protected span is compared with the finished derivation. The check only
    # records; `protection` says why a rebuild is never refused or altered by one.
    with instrumentation.phase("protection"):
        recheck_protections(store)
    after_counts = {
        str(row[0]): int(row[1])
        for row in store.connection.execute(
            "SELECT account_id, COUNT(*) FROM transactions GROUP BY account_id"
        )
    }
    report.account_changes = {
        account: (before_counts.get(account, 0), after_counts.get(account, 0))
        for account in sorted(set(before_counts) | set(after_counts))
    }
    if instrumentation.enabled():
        report.timings = instrumentation.snapshot()

    if progress is not None:
        # Nothing is in flight any more. Leaving the last artefact's size
        # in place would have the finished report still naming something
        # as being worked on - exactly the claim a reader checks when
        # deciding whether the rebuild has actually stopped.
        report.current_records = 0
        report.records_in_flight = 0
        with contextlib.suppress(Exception):
            progress(total, total, report)
    return report
