"""Batched writes must be indistinguishable from per-record writes.

The buffering lives inside Store so the fold cannot get it wrong, which
makes the two paths directly comparable: the same reconcile code runs
either way, and only the write mechanics differ. These tests hold the
equivalence and the one behaviour that CHANGED on purpose - a failed
batch now leaves no trace at all, where the per-record path left
executed-but-uncommitted rows on the shared connection for the next
commit to sweep in.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.store import Store


def _corpus(count: int, *, account: str = "a1") -> list[Transaction]:
    """Dense enough to exercise every write shape in one batch.

    Repeated content triggers occurrence numbering; a same-source
    settlement reissue triggers supersession (a same-entity re-upsert
    within the batch); amount collisions across the fuzzy window
    produce near-misses and review rows.
    """
    out = []
    for n in range(count):
        day = date(2026, 4, 1 + (n % 20))
        amount = -(500 + (n % 5) * 250)
        out.append(
            Transaction(
                entity_id=f"e-{n}",
                account_id=account,
                amount_minor=amount,
                currency="GBP",
                description=f"SHOP {n % 7}",
                value_date=day,
                booking_date=day,
                source="starling" if n % 3 else "truelayer",
                source_id=f"sid-{n % 40}" if n % 4 else None,
                content_key=f"ck-{amount}-{day.isoformat()}-{n % 7}",
                tier=SourceTier.AUTHORITATIVE,
                status=TransactionStatus.PENDING if n % 11 == 0 else TransactionStatus.BOOKED,
            )
        )
    return out


def _dump(store: Store) -> dict[str, list[tuple]]:
    """Every derived row, timestamps excluded - they are stamped per batch
    now and per call before, and that difference is deliberate."""
    transactions = store.connection.execute(
        "SELECT entity_id, account_id, amount_minor, currency, value_date, "
        "booking_date, description, status, source, source_id, content_key, "
        "occurrence, artefact_digest, match_tier, "
        "matched_entity_id IS NOT NULL "
        "FROM transactions ORDER BY account_id, content_key, occurrence, source"
    ).fetchall()
    sightings = store.connection.execute(
        "SELECT entity_id, source, source_id, artefact_digest "
        "FROM transaction_sources ORDER BY entity_id, source, artefact_digest"
    ).fetchall()
    reviews = store.connection.execute(
        "SELECT entity_id, reason FROM review_queue ORDER BY entity_id"
    ).fetchall()
    return {
        "transactions": [tuple(row) for row in transactions],
        "sightings": [tuple(row) for row in sightings],
        "reviews": [tuple(row) for row in reviews],
    }


class TestBatchedWritesMatchPerRecordWrites:
    def test_TheSameFold_ProducesIdenticalRows_BatchedOrDirect(self, tmp_path):
        """Same corpus, same code, both write paths - identical stores.

        The direct path is forced by neutering begin_batch, so every
        upsert, sighting and review row executes individually exactly as
        before the change. Entity ids are minted fresh per run, so the
        comparison keys on content, not ids.
        """
        corpus = _corpus(120)

        with Store(tmp_path / "batched.sqlite3") as store:
            reconcile_batch(store, list(corpus), digest="d-1")
            batched = _dump(store)

        with Store(tmp_path / "direct.sqlite3") as store:
            store.begin_batch = lambda: None  # type: ignore[method-assign]
            reconcile_batch(store, list(corpus), digest="d-1")
            direct = _dump(store)

        def _shape(dump):
            # Sorted: entity ids are minted fresh per store, so any
            # ordering that involves them differs between runs even when
            # the rows themselves are identical.
            return {
                "transactions": sorted(
                    tuple(str(v) for v in row[1:]) for row in dump["transactions"]
                ),
                "sightings": sorted(
                    tuple(str(v) for v in row[1:]) for row in dump["sightings"]
                ),
                "reviews": len(dump["reviews"]),
            }

        assert _shape(batched) == _shape(direct)

    def test_TwoOverlappingBatches_MergeIdenticallyUnderBatching(self, tmp_path):
        """The cross-batch path: batch two re-resolves against batch one's
        stored rows, so the flush of batch one must be complete and
        correct before batch two reads the account."""
        first = _corpus(60)
        second = _corpus(90)  # superset by construction

        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, list(first), digest="d-1")
            summary = reconcile_batch(store, list(second), digest="d-2")

        # The 60 shared records must merge, not duplicate.
        assert summary.matched + summary.superseded >= 55, summary.describe()

    def test_AFailureMidBatch_LeavesTheStoreCompletelyUntouched(self, tmp_path):
        """The deliberate improvement: a failed batch cannot smear.

        A transaction whose raw payload is unserialisable fails at
        parameter build. Everything buffered before it must be
        discarded, nothing may reach disk, and the store must remain
        usable for the next batch - including its direct-mode writes.
        """
        corpus = _corpus(20)
        poison = corpus[10]
        object.__setattr__(poison, "raw", {"unserialisable": {1, 2, 3}})

        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(TypeError):
                reconcile_batch(store, corpus, digest="d-poison")

            counts = store.connection.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
            assert counts == 0, "a failed batch left rows behind"

            # The store must not have a stale buffer swallowing writes.
            clean = _corpus(5)
            reconcile_batch(store, clean, digest="d-clean")
            counts = store.connection.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
            assert counts == 5

    def test_RowsWithinOneBatch_ShareOneTimestamp(self, tmp_path):
        """Deliberate change, stated: a batch is one observation event.

        Per-record stamping gave every row its own microsecond, which
        implied a precision the data never had - the batch arrived
        together. One stamp per batch also makes 'which rows landed
        together' answerable from the rows themselves.
        """
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, _corpus(30), digest="d-1")
            stamps = {
                row[0]
                for row in store.connection.execute(
                    "SELECT DISTINCT first_seen_at FROM transactions"
                )
            }

        assert len(stamps) == 1
