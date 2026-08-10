"""The account page's cost must not scale with sightings.

The live page took 44 seconds, and the phase timings it prints named the
culprit outright: 36.93s in the source breakdown. The cause was a lookup
per SIGHTING against the artefact table - and artefact rows carry whole
statement payloads, so an account corroborated by many overlapping imports
paid a payload-sized read tens of thousands of times over. Artefacts number
in the hundreds; sightings in the tens of thousands; the lookup belongs on
the small side.

Pinned by counting reads rather than seconds. A timing threshold on a fast
machine is a wolf-cry waiting to happen (this project has one on file); the
invariant that actually matters is that adding sightings adds no artefact
reads at all.
"""

from __future__ import annotations

from datetime import date

from obdi.ingest import reconcile_batch
from obdi.models import RawArtefact, SourceTier, Transaction
from obdi.store import Store


def _artefact(digest: str, *, connection: str = "conn-1") -> RawArtefact:
    from datetime import datetime

    return RawArtefact(
        source="starling",
        account_ref="starling:acc-1",
        fetched_at=datetime.now().astimezone(),
        media_type="text/csv",
        digest=digest,
        payload=b"date,amount\n2026-01-01,-1.00\n",
        origin=f"{digest}.csv",
        connection_id=connection,
    )


def _seed(store: Store, *, transactions: int, sightings: int) -> None:
    """One account, `transactions` rows, each seen by `sightings` sources."""
    for index in range(sightings):
        digest = f"digest-{index}"
        store.land_artefact(_artefact(digest))
        reconcile_batch(
            store,
            [
                Transaction(
                    account_id="starling-personal",
                    amount_minor=-100 - number,
                    currency="GBP",
                    value_date=date(2026, 1, 1),
                    booking_date=date(2026, 1, 1),
                    description=f"SHOP {number}",
                    source=f"source-{index}",
                    source_id=f"s-{index}-{number}",
                    tier=SourceTier.AUTHORITATIVE,
                )
                for number in range(transactions)
            ],
            digest=digest,
        )


class _ArtefactReadCounter:
    """Counts statements that touch the payload-bearing table."""

    def __init__(self, store: Store) -> None:
        self.count = 0
        store.connection.set_trace_callback(self._saw)

    def _saw(self, statement: str) -> None:
        if "raw_artefacts" in statement and statement.strip().upper().startswith(
            "SELECT"
        ):
            self.count += 1


class TestTheBreakdownReadsArtefactsOnce:
    def test_MoreSightings_AddNoArtefactReads(self, tmp_path):
        with Store(tmp_path / "few.sqlite3") as few:
            _seed(few, transactions=20, sightings=2)
            counter = _ArtefactReadCounter(few)
            few.source_breakdown("starling-personal")
            with_two = counter.count

        with Store(tmp_path / "many.sqlite3") as many:
            _seed(many, transactions=20, sightings=8)
            counter = _ArtefactReadCounter(many)
            many.source_breakdown("starling-personal")
            with_eight = counter.count

        assert with_two == with_eight, (
            "artefact reads must not grow with sightings - that growth cost "
            "37 seconds on the live account page"
        )
        assert with_eight <= 2, "one pass over the artefacts, not a lookup per row"

    def test_TheAnswerIsUnchanged_ByHowItIsComputed(self, tmp_path):
        # The optimisation must not quietly alter what the page reports:
        # distinct transactions, corroboration, and the feeder each source
        # arrived through.
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store, transactions=10, sightings=3)

            breakdown = store.source_breakdown("starling-personal")

            assert breakdown["transactions"] == 10, "distinct, never sightings"
            assert breakdown["corroborated"] == 10, "every row seen by all three"
            feeders = breakdown["by_feeder"]
            assert isinstance(feeders, list) and feeders
            assert all(entry["feeder"] == "starling:acc-1" for entry in feeders)
            assert all(entry["connections"] == ["conn-1"] for entry in feeders)

    def test_AnArtefactWithNoConnection_LeavesTheFeederConnectionEmpty(self, tmp_path):
        # File imports have no connection; the page must not invent one.
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(_artefact("file-1", connection=""))
            reconcile_batch(
                store,
                [
                    Transaction(
                        account_id="starling-personal",
                        amount_minor=-450,
                        currency="GBP",
                        value_date=date(2026, 1, 1),
                        booking_date=date(2026, 1, 1),
                        description="NETFLIX",
                        source="csv",
                        source_id="c-1",
                        tier=SourceTier.AUTHORITATIVE,
                    )
                ],
                digest="file-1",
            )

            breakdown = store.source_breakdown("starling-personal")

            feeders = breakdown["by_feeder"]
            assert isinstance(feeders, list)
            assert feeders[0]["connections"] == []
            assert feeders[0]["feeder"] == "starling:acc-1"
