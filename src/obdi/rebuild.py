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
from .ingest import ImportSummary, pair_transfers_across_store, reconcile_batch
from .jsontypes import rows as json_rows
from .matching import CandidateIndex
from .models import Transaction
from .namespaces import API_SOURCES
from .parsers.uk_banks import detect
from .pending_lifecycle import resolve_vanished_pending
from .providers import starling, truelayer
from .store import Store


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
        for problem in self.problems:
            lines.append(f"  problem: {problem}")
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


#: Artefact refs beginning with these are provider-qualified fallbacks
#: ("source:provider_ref") and get resolved through the account map at
#: replay time; anything else is already a canonical name.
_SOURCES = ("starling", "truelayer")


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
            transaction = starling.to_transaction(item, account_id=account_ref)
            if transaction is not None:
                transactions.append(replace(transaction, artefact_digest=digest))
        return transactions
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
    store.connection.execute("DELETE FROM review_queue")
    store.connection.commit()

    artefact_rows = store.connection.execute(
        "SELECT rowid, source, account_ref, digest, payload, origin, "
        "record_count FROM raw_artefacts ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()
    starling_defaults = _starling_defaults(artefact_rows)

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
        if str(row["source"]) in _NON_TRANSACTIONAL:
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

        if source in _NON_TRANSACTIONAL:
            report.artefacts_skipped += 1
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

    with instrumentation.phase("transfer-pairing"):
        report.transfers_paired = pair_transfers_across_store(store)
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
