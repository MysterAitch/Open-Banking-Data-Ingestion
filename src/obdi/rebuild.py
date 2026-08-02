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

One honest caveat: entity ids are minted at first sighting, so a rebuild
RE-MINTS them. Everything observable about each payment - account, amount,
date, description, sightings - reproduces exactly; the opaque identity does
not. Downstream consumers therefore key on content, never on stored entity
ids - the Actual replay's imported_id is the content key plus occurrence
for exactly this reason, making rebuild-then-replay a no-op rather than a
duplication.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .accounts import AccountMap
from .errors import DataError
from .ingest import ImportSummary, pair_transfers_across_store, reconcile_batch
from .jsontypes import rows as json_rows
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
    #: Sum of stored record counts over transactional artefacts - the
    #: denominator for record-level progress. Uncounted artefacts (landed
    #: before counts existed) make it a floor, stated as such.
    records_total_known: int = 0
    artefacts_uncounted: int = 0

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


#: Artefact sources that carry no transactions - evidence kept, not replayed.
_NON_TRANSACTIONAL = {
    "truelayer-accounts",
    "truelayer-balance",
    "truelayer-standing_orders",
    "truelayer-direct_debits",
    "starling-accounts",
    "starling-spaces",
    "starling-balance",
    "truelayer-cards",
}


#: Artefact refs beginning with these are provider-qualified fallbacks
#: ("source:provider_ref") and get resolved through the account map at
#: replay time; anything else is already a canonical name.
_SOURCES = ("starling", "truelayer")


#: The provider-true identity of a feed fetch, recorded at landing time:
#: /api/v2/feed/account/{accountUid}/category/{categoryUid}?...
_FEED_ORIGIN = re.compile(r"/feed/account/([^/?]+)/category/([^/?]+)")


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

    store.connection.execute("DELETE FROM transactions")
    store.connection.execute("DELETE FROM transaction_sources")
    store.connection.execute("DELETE FROM review_queue")
    store.connection.commit()

    artefact_rows = store.connection.execute(
        "SELECT rowid, source, account_ref, digest, payload, origin, "
        "record_count FROM raw_artefacts ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()
    starling_defaults = _starling_defaults(artefact_rows)
    for row in artefact_rows:
        if str(row["source"]) in _NON_TRANSACTIONAL:
            continue
        if row["record_count"] is None:
            report.artefacts_uncounted += 1
        else:
            report.records_total_known += int(row["record_count"])

    total = len(artefact_rows)
    for index, row in enumerate(artefact_rows, start=1):
        if progress is not None:
            with contextlib.suppress(Exception):
                progress(index, total, report)
        source = str(row["source"])
        if source == "starling-feed":
            account_ref = _starling_feed_ref(
                str(row["origin"]),
                str(row["account_ref"]),
                starling_defaults,
                account_map,
            )
        else:
            account_ref = _resolve_ref(str(row["account_ref"]), account_map)
        digest = str(row["digest"])
        payload = row["payload"]

        if source in _NON_TRANSACTIONAL:
            report.artefacts_skipped += 1
            continue

        summary = ImportSummary(artefact_new=False)
        try:
            if source in ("truelayer-booked", "truelayer-pending"):
                decoded = json.loads(payload)
                records = json_rows(decoded, "results")
                transactions = [
                    replace(
                        truelayer.to_transaction(
                            record,
                            account_id=account_ref,
                            pending=source.endswith("pending"),
                        ),
                        artefact_digest=digest,
                    )
                    for record in records
                ]
            elif source == "truelayer-card-booked":
                decoded = json.loads(payload)
                transactions = [
                    replace(
                        truelayer.to_card_transaction(
                            record, account_id=account_ref
                        ),
                        artefact_digest=digest,
                    )
                    for record in json_rows(decoded, "results")
                ]
            elif source == "starling-feed":
                decoded = json.loads(payload)
                transactions = []
                for item in json_rows(decoded, "feedItems"):
                    transaction = starling.to_transaction(item, account_id=account_ref)
                    if transaction is not None:
                        transactions.append(
                            replace(transaction, artefact_digest=digest)
                        )
            else:
                # File imports: source is the suffix (csv, qif, ...). The
                # parser registry re-detects from the bytes, exactly as the
                # original import did.
                parser = detect(payload)
                transactions = list(parser.parse(payload, account_id=account_ref))
        except (
            DataError,
            ValueError,
            KeyError,
            starling.StarlingError,
            truelayer.TrueLayerError,
        ) as exc:
            # Provider errors subclass RuntimeError, and before they were
            # listed here ONE problem item aborted the whole rebuild
            # mid-loop - after the wipe, with everything later in arrival
            # order (all of Starling, live) silently absent. A poison
            # artefact is that artefact's problem, recorded and skipped.
            report.problems.append(f"{source} for {account_ref}: {exc}")
            report.artefacts_skipped += 1
            continue

        if row["record_count"] is None:
            # Backfill exactly once: the count becomes landed metadata and
            # is never recalculated again.
            store.connection.execute(
                "UPDATE raw_artefacts SET record_count = ? WHERE rowid = ?",
                (len(transactions), int(row["rowid"])),
            )
            report.records_total_known += len(transactions)
        report.artefacts_replayed += 1
        if transactions:
            reconcile_batch(store, transactions, digest=digest, summary=summary)
            report.transactions += len(transactions)
        if source == "truelayer-pending":
            # Complete-set semantics replayed in order: the same resolution
            # the live pull runs, but WITHOUT re-emitting events - the
            # outbox records what was announced at the time, and a rebuild
            # re-derives state, not history.
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
    if progress is not None:
        with contextlib.suppress(Exception):
            progress(total, total, report)
    return report
