"""The import pipeline: land raw, derive transactions, resolve identity.

Land first, always. Parsing can be retried from a stored artefact; a download
that was parsed and discarded cannot be recovered once the bank's export window
closes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .identity import artefact_digest
from .matching import MatchTier, pair_internal_transfers, resolve, supersede
from .models import RawArtefact, Transaction
from .parsers.uk_banks import detect
from .store import Store


@dataclass
class ImportSummary:
    artefact_new: bool
    parsed: int = 0
    inserted: int = 0
    matched: int = 0
    superseded: int = 0
    needs_review: int = 0

    def describe(self) -> str:
        return (
            f"parsed {self.parsed}, new {self.inserted}, matched {self.matched}, "
            f"superseded {self.superseded}, for review {self.needs_review}"
        )


def import_file(store: Store, path: Path, *, account_id: str) -> ImportSummary:
    payload = path.read_bytes()
    digest = artefact_digest(payload)

    artefact = RawArtefact(
        source=path.suffix.lstrip(".") or "unknown",
        account_ref=account_id,
        fetched_at=datetime.now().astimezone(),
        media_type="text/csv",
        digest=digest,
        payload=payload,
        origin=path.name,
    )
    is_new_artefact = store.land_artefact(artefact)

    parser = detect(payload)
    incoming = list(parser.parse(payload, account_id=account_id))

    summary = ImportSummary(artefact_new=is_new_artefact, parsed=len(incoming))
    existing = store.transactions_for_account(account_id)

    for transaction in incoming:
        stored = _reconcile(store, transaction, existing, digest, summary)
        existing.append(stored)

    store.connection.commit()
    return summary


def pair_transfers_across_store(store: Store) -> int:
    """Flag internal transfers across the WHOLE store, not just one import.

    This has to be a separate pass: a transfer's two sides live in different
    accounts and therefore arrive in different files, usually on different days.
    Pairing only within an import batch would never fire at all.

    Returns the number of transactions newly flagged.
    """
    before = {t.entity_id: t.is_internal_transfer for t in store.all_transactions()}
    paired = pair_internal_transfers(store.all_transactions())

    newly_flagged = 0
    for transaction in paired:
        if transaction.is_internal_transfer and not before.get(transaction.entity_id):
            store.mark_internal_transfer(transaction.entity_id)
            newly_flagged += 1

    store.connection.commit()
    return newly_flagged


def _reconcile(
    store: Store,
    transaction: Transaction,
    existing: list[Transaction],
    digest: str,
    summary: ImportSummary,
) -> Transaction:
    result = resolve(transaction, existing)

    if result.existing is not None:
        merged = supersede(result.existing, transaction)
        merged = replace(merged, artefact_digest=digest)
        store.upsert_transaction(
            merged, match_tier=result.tier.value, matched_entity_id=result.existing.entity_id
        )
        if merged.status != result.existing.status:
            summary.superseded += 1
        else:
            summary.matched += 1
        return merged

    fresh = replace(transaction, entity_id=str(uuid.uuid4()), artefact_digest=digest)
    store.upsert_transaction(fresh, match_tier=result.tier.value)
    summary.inserted += 1

    # Tier 4 means nothing matched. That is expected for genuinely new
    # transactions, so it is not queued for review on its own - only a
    # near-miss would be, once a heuristic for "suspiciously close" exists.
    if result.tier is MatchTier.UNRESOLVED and transaction.source_id is None:
        pass

    return fresh
