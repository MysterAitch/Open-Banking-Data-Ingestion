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

import json
from dataclasses import dataclass, field, replace

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

    def describe(self) -> str:
        lines = [
            f"replayed {self.artefacts_replayed} artefact(s) "
            f"({self.artefacts_skipped} non-transactional skipped), "
            f"{self.transactions} transaction(s) resolved, "
            f"{self.transfers_paired} transfer pair(s) confirmed"
        ]
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
    # Card transactions land but are deliberately NOT replayed yet: their
    # sign conventions are unverified, and parsing unverified money is the
    # one mistake this project is built to never make.
    "truelayer-card-booked",
}


def rebuild_from_raw(store: Store) -> RebuildReport:
    """Wipe the derived layers and replay layer 0 in arrival order.

    Arrival order matters: occurrence counting and supersession depend on
    which sighting came first, and replaying in fetched_at order reproduces
    the history the store actually lived through.
    """
    report = RebuildReport()

    store.connection.execute("DELETE FROM transactions")
    store.connection.execute("DELETE FROM transaction_sources")
    store.connection.execute("DELETE FROM review_queue")
    store.connection.commit()

    artefact_rows = store.connection.execute(
        "SELECT source, account_ref, digest, payload FROM raw_artefacts "
        "ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()

    for row in artefact_rows:
        source = str(row["source"])
        account_ref = str(row["account_ref"])
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
    return report
