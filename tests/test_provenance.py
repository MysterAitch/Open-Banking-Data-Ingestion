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

            assert store.land_artefact(first).payload_stored
            # Identical bytes, different account: different evidence, not a
            # duplicate. A digest-only key would silently swallow this.
            assert store.land_artefact(second).payload_stored

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
            assert store.land_artefact(artefact).payload_stored
            landing = store.land_artefact(artefact)

        # Same bytes, same request: a re-download. Nothing is stored and
        # nothing is learnt - the name was already on record too.
        assert landing.payload_stored is False
        assert landing.origin_recorded is False

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
            assert pk == ["digest", "account_ref", "source"]
            # The name the row carried is not lost by leaving the key: it
            # moves to the set of names the artefact has been seen under.
            assert store.origins_for_artefact("d1", "acc", "s") == ["o"]


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

    Content keys exclude the account, so re-binding stays a column update:
    sightings survive, raw is untouched, nothing is refetched. The
    alternative was discarding derived data and re-authorising at the bank
    - spending quota to change a label.

    Entity ids do NOT survive, and this file used to claim they did. They
    fold the account into their material, so a rename re-mints them; the
    old assertion held only until the next rebuild, which mints from the
    current account map and would have left every annotation, event and
    pair keyed to an id no row carried. The stronger thing must be true
    instead: the ids move here, deterministically to the values the next
    rebuild will mint, and everything keyed by them moves with them.
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
            assert {t.entity_id for t in held} != before, (
                "ids fold the account in, so a rename re-mints them - doing "
                "it here rather than leaving it to the next rebuild is what "
                "lets everything keyed by them come along"
            )
            assert len({t.entity_id for t in held}) == 2, "still distinct"

    def test_Rebind_TouchesOnlyTheNamedAccount(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")
            reconcile_batch(
                store, [txn("truelayer", source_id="tl-9", account="other", day=9)], digest="d2"
            )

            store.rebind_account("current", "halifax-current")

            accounts = {t.account_id for t in store.all_transactions()}
            assert accounts == {"halifax-current", "other"}


class TestOldFormatKeysAreMigrated:
    """Rows stored under the old account-in-the-hash keys are re-keyed on open.

    Every input to the new key lives in stored columns, so the migration is a
    deterministic recompute - no raw replay, no refetch. Without it, a stored
    row and a fresh sighting of the same payment would carry different keys and
    tier-two matching would silently stop working for pre-change data.
    """

    def test_OpeningAStoreFromBeforeTheChange_RecomputesMismatchedKeys(self, tmp_path):
        """The migration runs when the store's recorded schema version is
        behind, which is the real upgrade path: an old store, then the
        first open on new code. It deliberately does NOT re-run on every
        open - opening must stay read-only so a page can render while a
        fetch holds the write lock."""
        from obdi.identity import content_key as compute

        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")
            entity = store.all_transactions()[0].entity_id
            store.connection.execute(
                "UPDATE transactions SET content_key = 'old-format-key' WHERE entity_id = ?",
                (entity,),
            )
            # A store written by a version before this mechanism carries no
            # version stamp, so the next open does the work.
            store.connection.execute("DELETE FROM obdi_meta")
            store.connection.commit()

        with Store(db) as store:
            row = store.all_transactions()[0]
            expected = compute(
                amount_minor=row.amount_minor,
                value_date=row.value_date,
                description=row.description,
            )
            assert row.content_key == expected


class TestAccountSourceBreakdown:
    """Which feeders an account is made of, and how much each gave.

    The question the multi-pipe plan exists to answer: a payment seen by
    the Starling API and by TrueLayer is ONE payment corroborated twice,
    and a page that counts it twice turns agreement into apparent growth.
    """

    def _seen_by(self, store, entity_id, source, digest):
        store.connection.execute(
            "INSERT OR IGNORE INTO transaction_sources (entity_id, source, "
            "artefact_digest, first_seen_at) VALUES (?, ?, ?, ?)",
            (entity_id, source, digest, "2026-08-04T00:00:00"),
        )

    def test_APaymentSeenByTwoPipes_CountsOnce_AndIsMarkedCorroborated(
        self, tmp_path
    ):
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            reconcile_batch(store, [txn("starling", source_id="s-1")], digest="d-star")
            entity = store.all_transactions()[0].entity_id
            self._seen_by(store, entity, "truelayer", "d-tl")
            store.connection.commit()

            breakdown = store.source_breakdown(
                store.all_transactions()[0].account_id
            )

        assert breakdown["transactions"] == 1
        assert breakdown["sightings"] == 2
        assert breakdown["corroborated"] == 1
        assert breakdown["single_source"] == 0
        assert breakdown["sources"] == ["starling", "truelayer"]

    def test_ASingleSourcedAccount_ReportsNothingCorroborated(self, tmp_path):
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            reconcile_batch(store, [txn("starling", source_id="s-1")], digest="d-star")
            breakdown = store.source_breakdown(
                store.all_transactions()[0].account_id
            )

        assert breakdown["transactions"] == breakdown["sightings"] == 1
        assert breakdown["corroborated"] == 0
        assert breakdown["single_source"] == 1

    def test_TheFeederIsRecoveredFromTheArtefactThatDeliveredTheRow(self, tmp_path):
        """Not the account's current name - the reference that actually
        delivered it, so a Starling SPACE is named as the feeder it is."""
        from datetime import UTC, datetime

        from obdi.models import RawArtefact

        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            store.land_artefact(
                RawArtefact(
                    source="starling-feed",
                    account_ref="starling:space-bills-uid",
                    fetched_at=datetime(2026, 8, 4, tzinfo=UTC),
                    media_type="application/json",
                    digest="d-star",
                    payload=b"{}",
                )
            )
            reconcile_batch(store, [txn("starling", source_id="s-1")], digest="d-star")
            breakdown = store.source_breakdown(
                store.all_transactions()[0].account_id
            )

        assert breakdown["by_feeder"] == [
            {
                "source": "starling",
                "feeder": "starling:space-bills-uid",
                "transactions": 1,
                # No connection recorded in this fixture's artefacts -
                # honestly empty, exactly like pre-attribution history.
                "connections": [],
            }
        ]

    def test_SourceCountsForEveryAccount_ComeFromOneQuery(self, tmp_path):
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            reconcile_batch(store, [txn("starling", source_id="s-1")], digest="d-1")
            entity = store.all_transactions()[0].entity_id
            account = store.all_transactions()[0].account_id
            self._seen_by(store, entity, "truelayer", "d-2")
            store.connection.commit()

            counts = store.source_counts_by_account()

        assert counts[account] == 2


class TestRefilingAMislandedArtefact:
    """An import sent to the wrong destination had no remedy: layer 0 is
    append-only for EVIDENCE, but account_ref is our FILING of it - the
    rebind doctrine, applied per artefact. Observed live: three statement
    chunks landed under a Space by a phone mis-tap; the person noticed and
    re-imported into the right account a minute later - and every rebuild
    thereafter faithfully re-derived 1,571 rows into the wrong container,
    because the misfiled artefacts stayed filed where they landed.
    """

    @staticmethod
    def _land(store, ref, payload=b"a,b\n1,2\n", origin="chunk.csv") -> int:
        from datetime import datetime

        from obdi.identity import artefact_digest
        from obdi.models import RawArtefact

        store.land_artefact(
            RawArtefact(
                source="csv",
                account_ref=ref,
                fetched_at=datetime.now().astimezone(),
                media_type="text/csv",
                digest=artefact_digest(payload),
                payload=payload,
                origin=origin,
                request_meta="trigger=test",
            )
        )
        return store.connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE account_ref=? AND origin=?",
            (ref, origin),
        ).fetchone()[0]

    def test_Refile_MovesTheFiling_AndRecordsTheCorrection(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            misfiled = self._land(store, "starling-space-money")

            old = store.refile_artefact(misfiled, "starling-personal")

            assert old == "starling-space-money"
            ref, meta = store.connection.execute(
                "SELECT account_ref, request_meta FROM raw_artefacts WHERE rowid=?",
                (misfiled,),
            ).fetchone()
            assert ref == "starling-personal"
            # The correction is part of the artefact's own history: the
            # payload is evidence, the filing is ours, and a changed filing
            # says so rather than pretending it always was.
            assert "refiled from starling-space-money" in meta

    def test_Refile_WhenTheBytesAlreadyLandedCorrectly_CollapsesTheDuplicate(
        self, tmp_path
    ):
        # The recovery-by-reimport case, exactly as observed live: the same
        # file was imported again into the RIGHT account, so the bytes
        # already exist under the correct filing. Refiling the misfiled
        # copy must not create a duplicate - the misfiled row collapses
        # into the survivor, which records what it absorbed.
        with Store(tmp_path / "s.sqlite3") as store:
            misfiled = self._land(store, "starling-space-money")
            survivor = self._land(store, "starling-personal")

            old = store.refile_artefact(misfiled, "starling-personal")

            assert old == "starling-space-money"
            gone = store.connection.execute(
                "SELECT COUNT(*) FROM raw_artefacts WHERE rowid=?", (misfiled,)
            ).fetchone()[0]
            assert gone == 0
            meta = store.connection.execute(
                "SELECT request_meta FROM raw_artefacts WHERE rowid=?", (survivor,)
            ).fetchone()[0]
            assert "starling-space-money" in meta

    def test_Refile_UnknownArtefact_ReturnsNone(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            assert store.refile_artefact(9999, "anywhere") is None
