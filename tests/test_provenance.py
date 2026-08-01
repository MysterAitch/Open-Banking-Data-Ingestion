"""Which sources have seen a given payment, after they have been merged.

Merging is the point: one payment observed by an aggregator and by a CSV export
should end up as one transaction, not two. But the merged row can only carry
one source, so by default the act of merging destroys the very evidence that
made it trustworthy - namely that two independent routes agreed.

Without that record, "present in the API but missing from the export" is
unanswerable, because a payment both saw is indistinguishable from one only the
last writer saw.
"""

from __future__ import annotations

from datetime import date

from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(source, *, source_id=None, day=5, amount=-2500, account="current",
        tier=SourceTier.SYNTHETIC):
    return Transaction(
        account_id=account,
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, 3, day),
        booking_date=date(2026, 3, day),
        description="RENT",
        source=source,
        source_id=source_id,
        tier=tier,
        content_key=f"key-{day}-{amount}",
    )


class TestWhoHasSeenThisPayment:
    def test_Provenance_WhenTwoSourcesSeeOnePayment_BothAreRecorded(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")
            reconcile_batch(store, [txn("halifax-qif")], digest="d2")

            held = store.all_transactions()
            assert len(held) == 1, "one payment, seen twice, is one transaction"
            assert store.sources_for(held[0].entity_id) == ["halifax-qif", "truelayer"]

    def test_Provenance_WhenOnlyOneSourceSeesIt_OnlyThatOneIsRecorded(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")

            held = store.all_transactions()
            assert store.sources_for(held[0].entity_id) == ["truelayer"]

    def test_Provenance_WhenTheSameSourceSeesItTwice_IsNotDoubleCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d2")

            held = store.all_transactions()
            # Re-pulling the same feed is routine and says nothing new about
            # corroboration - two sightings by one source are not two sources.
            assert store.sources_for(held[0].entity_id) == ["truelayer"]


class TestTraversalBackToTheRawBytes:
    """Every derived record must be traceable to the exact artefacts behind it.

    Recording the source name alone says who saw it but not WHERE - and the
    transaction row's own digest is last-writer-wins, so the artefact behind an
    earlier sighting was unreachable. Confidence in a merged record comes from
    being able to open the raw bytes of every observation that formed it.
    """

    def test_Provenance_EachSightingRecordsTheArtefactItCameFrom(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="digest-api")
            reconcile_batch(store, [txn("halifax-qif")], digest="digest-csv")

            held = store.all_transactions()
            sightings = store.sightings_for(held[0].entity_id)
            assert ("truelayer", "digest-api") in sightings
            assert ("halifax-qif", "digest-csv") in sightings

    def test_Provenance_ARepullRecordsItsOwnArtefactWithoutInventingASecondSource(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="digest-1")
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="digest-2")

            held = store.all_transactions()
            # Both artefacts are reachable - that is the traversal guarantee -
            # while the source list still shows one source, not two.
            assert len(store.sightings_for(held[0].entity_id)) == 2
            assert store.sources_for(held[0].entity_id) == ["truelayer"]


class TestEmptyResultsAreEvidence:
    """An account that returns nothing was still asked, and that fact must land.

    Dormant accounts are routine. Without landing the empty payload and the
    range that produced it, "asked and empty" and "never asked" are the same
    absence, and no rebuild can ever tell them apart.
    """

    def test_Landing_WhenTwoAccountsReturnIdenticalEmptyBodies_BothLand(self, tmp_path):
        from datetime import UTC, datetime

        from obdi.models import RawArtefact

        empty = b'{"results": [], "status": "Succeeded"}'
        with Store(tmp_path / "s.sqlite3") as store:
            first = RawArtefact(
                source="truelayer-booked",
                account_ref="account-a",
                fetched_at=datetime.now(UTC),
                media_type="application/json",
                digest="same-digest",
                payload=empty,
                origin="https://api/transactions?from=2016-01-01",
            )
            second = RawArtefact(
                source="truelayer-booked",
                account_ref="account-b",
                fetched_at=datetime.now(UTC),
                media_type="application/json",
                digest="same-digest",
                payload=empty,
                origin="https://api/transactions?from=2016-01-01",
            )

            assert store.land_artefact(first)
            # Identical bytes, different account: different evidence, not a
            # duplicate. A digest-only key would silently swallow this.
            assert store.land_artefact(second)

    def test_Landing_WhenTheSameRequestIsReimported_IsDeduplicated(self, tmp_path):
        from datetime import UTC, datetime

        from obdi.models import RawArtefact

        artefact = RawArtefact(
            source="truelayer-booked",
            account_ref="account-a",
            fetched_at=datetime.now(UTC),
            media_type="application/json",
            digest="same-digest",
            payload=b"{}",
            origin="https://api/transactions?from=2016-01-01",
        )
        with Store(tmp_path / "s.sqlite3") as store:
            assert store.land_artefact(artefact)
            assert not store.land_artefact(artefact), "same bytes, same request: a re-download"

    def test_Migration_WhenAStoreHasTheOldDigestOnlyKey_IsUpgradedWithDataIntact(
        self, tmp_path
    ):
        import sqlite3

        db = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(db)
        legacy.execute(
            """CREATE TABLE raw_artefacts (
                digest TEXT PRIMARY KEY, source TEXT NOT NULL,
                account_ref TEXT NOT NULL, media_type TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL,
                payload BLOB NOT NULL)"""
        )
        legacy.execute(
            "INSERT INTO raw_artefacts VALUES "
            "('d1','s','acc','application/json','o','2026-01-01',x'7b7d')"
        )
        legacy.commit()
        legacy.close()

        with Store(db) as store:
            kept = store.connection.execute("SELECT digest FROM raw_artefacts").fetchall()
            assert [row[0] for row in kept] == ["d1"], "migration must not lose rows"
            pk = [
                row["name"]
                for row in sorted(
                    store.connection.execute("PRAGMA table_info(raw_artefacts)").fetchall(),
                    key=lambda r: r["pk"],
                )
                if row["pk"]
            ]
            assert pk == ["digest", "account_ref", "origin"]


class TestLearnedProviderFacts:
    """What a pull learns about a provider is worth keeping.

    The first backfill spent three quota calls discovering the accepted
    window; without a record, every reconnection re-spends them rediscovering
    the same refusal. Facts are per-connection because banks differ, and
    re-recording overwrites - the latest observation wins.
    """

    def test_Facts_RoundTrip_AndOverwrite(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            assert store.provider_fact("truelayer", "halifax", "accepted_backfill_days") is None

            store.record_provider_fact("truelayer", "halifax", "accepted_backfill_days", "730")
            assert (
                store.provider_fact("truelayer", "halifax", "accepted_backfill_days") == "730"
            )

            store.record_provider_fact("truelayer", "halifax", "accepted_backfill_days", "3650")
            assert (
                store.provider_fact("truelayer", "halifax", "accepted_backfill_days") == "3650"
            )


class TestRebindingIsAnOperationNotAFate:
    """Which canonical account a payment belongs to is revisable, cheaply.

    Content keys no longer contain the account, so re-binding is a column
    update: entity ids survive, sightings survive, raw is untouched, and
    nothing is refetched. The alternative was discarding derived data and
    re-authorising at the bank - spending quota to change a label.
    """

    def test_Rebind_MovesRowsAndReportsHowMany(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [txn("truelayer", source_id="tl-1"), txn("truelayer", source_id="tl-2", day=6)],
                digest="d1",
            )
            before = {t.entity_id for t in store.all_transactions()}

            moved = store.rebind_account("current", "halifax-current")

            held = store.all_transactions()
            assert moved == 2
            assert {t.account_id for t in held} == {"halifax-current"}
            assert {t.entity_id for t in held} == before, "identity survives the rename"

    def test_Rebind_TouchesOnlyTheNamedAccount(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")
            reconcile_batch(
                store, [txn("truelayer", source_id="tl-9", account="other", day=9)], digest="d2"
            )

            store.rebind_account("current", "halifax-current")

            accounts = {t.account_id for t in store.all_transactions()}
            assert accounts == {"halifax-current", "other"}
