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
from .matching import pair_internal_transfers, resolve, supersede
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

    # Reconciliation is shared with API pulls rather than duplicated here, so
    # identity resolution cannot drift between the two routes - the same
    # payment arriving by file and by API must resolve identically.
    summary = ImportSummary(artefact_new=is_new_artefact)
    reconcile_batch(store, incoming, digest=digest, summary=summary)
    return summary


def pair_transfers_across_store(store: Store) -> int:
    """Confirm internal transfers across the WHOLE store, not just one import.

    A separate pass by necessity: a transfer's two sides live in different
    accounts and so arrive in different files, usually on different days.
    Pairing within a single import batch would never fire.

    Two distinct signals are at play and are deliberately not conflated:

      the provider's claim  some feeds mark a movement as internal themselves
      confirmation          the other side was actually found in the store

    A claim without confirmation means the opposite side is missing - the
    account it belongs to has not been ingested yet. The flag is kept either
    way, since excluding a genuine transfer from spending is right even
    unconfirmed, but only confirmations are counted, so the number means
    "pairs found" rather than "flags written".
    """
    stored = store.all_transactions()

    # Detect on a flag-stripped copy so the result reflects what pairing
    # actually found, rather than echoing the claims that arrived with it.
    stripped = [replace(t, is_internal_transfer=False) for t in stored]
    confirmed = {t.entity_id for t in pair_internal_transfers(stripped) if t.is_internal_transfer}

    for transaction in stored:
        if transaction.entity_id in confirmed and not transaction.is_internal_transfer:
            store.mark_internal_transfer(transaction.entity_id)

    store.connection.commit()
    return len(confirmed)


def unconfirmed_transfers(store: Store) -> list[Transaction]:
    """Transactions claimed internal by their provider but never paired.

    Each means the opposite side is absent - usually an account or a savings
    space that has not been ingested. Worth surfacing: an unpaired claim is
    excluded from spending on the provider's word alone.
    """
    stored = store.all_transactions()
    stripped = [replace(t, is_internal_transfer=False) for t in stored]
    confirmed = {t.entity_id for t in pair_internal_transfers(stripped) if t.is_internal_transfer}
    return [t for t in stored if t.is_internal_transfer and t.entity_id not in confirmed]


def reconcile_batch(
    store: Store,
    transactions: list[Transaction],
    *,
    digest: str,
    summary: ImportSummary | None = None,
) -> ImportSummary:
    """Resolve a batch against what is already stored, and persist the outcome.

    Shared by file import and API pulls deliberately: identity resolution must
    behave identically whichever route data arrives by, or the same payment
    seen twice through different doors would be stored twice.
    """
    result = summary or ImportSummary(artefact_new=True)
    result.parsed += len(transactions)

    # Number each repeat of the same content within this batch. Deterministic
    # across re-parses, because it depends only on the order the source
    # presents its rows - which is what lets a re-downloaded export merge while
    # two genuinely repeated payments stay apart.
    seen: dict[tuple[str, str], int] = {}
    numbered: list[Transaction] = []
    for transaction in transactions:
        key = (transaction.account_id, transaction.content_key)
        numbered.append(replace(transaction, occurrence=seen.get(key, 0)))
        seen[key] = seen.get(key, 0) + 1

    by_account: dict[str, list[Transaction]] = {}
    for transaction in numbered:
        existing = by_account.setdefault(
            transaction.account_id, store.transactions_for_account(transaction.account_id)
        )
        merged, matched_entity_id = _reconcile(store, transaction, existing, digest, result)

        if matched_entity_id is None:
            existing.append(merged)
            continue

        # REPLACE the candidate rather than appending alongside it. Appending
        # would leave the pre-merge row in the list, letting a later incoming
        # record claim the same stored transaction a second time - which
        # swallows repeated payments and reports them as matched.
        for index, candidate in enumerate(existing):
            if candidate.entity_id == matched_entity_id:
                existing[index] = merged
                break

    store.connection.commit()
    return result


def _reconcile(
    store: Store,
    transaction: Transaction,
    existing: list[Transaction],
    digest: str,
    summary: ImportSummary,
) -> tuple[Transaction, str | None]:
    """Resolve one transaction, returning it and the entity it merged into.

    The second element is what lets the caller replace the candidate it
    matched, rather than leaving the pre-merge row available to be claimed
    again by the next record.
    """
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
        return merged, result.existing.entity_id

    fresh = replace(transaction, entity_id=str(uuid.uuid4()), artefact_digest=digest)
    store.upsert_transaction(fresh, match_tier=result.tier.value)
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
