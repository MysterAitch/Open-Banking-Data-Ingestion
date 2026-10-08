"""The record of the rows the learned rule named: kept once, settled once, across the rebuild.

KNOWN ANSWERS, decided before the first run. A row is recorded the first time only; settling is
once and the first outcome stands; a store stamped 30 (goals, no `inferred_links`) opens, gains
the table, and stamps 32; a rebuild from raw leaves the table as it was; and the landing
finishers the command line composes write the record after a landing.
"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import UTC, date, datetime

from landing import rebuild_from_raw
from obdi.cli import landing_finishers
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.inferred_link_records import AGREED, DISAGREED, PENDING
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.store import SCHEMA_VERSION, Store

HISTORY = pathlib.Path(__file__).resolve().parent.parent / "schema_history"
SNAPSHOT = HISTORY / "29-inferred-links.sql"
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


class TestRecording:
    def test_RecordingTheSameRowTwice_KeepsTheFirstRecord(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            made = [("e1", "starling:uid-a", "marlow bakery")]
            first = store.record_inferred_links(made, now=NOW)
            again = store.record_inferred_links([("e1", "starling:uid-b", "other")])

            (link,) = store.inferred_links()

        assert (first, again) == (1, 0)
        assert (link.party, link.outcome) == ("starling:uid-a", PENDING)
        assert link.opening == "marlow bakery"

    def test_Settling_IsOnceAndTheFirstOutcomeStands(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.record_inferred_links([("e1", "starling:uid-a", "marlow bakery")])
            store.settle_inferred_link("e1", AGREED, now=NOW)
            store.settle_inferred_link("e1", DISAGREED)

            (link,) = store.inferred_links()

        assert (link.outcome, link.settled_at) == (AGREED, NOW.isoformat())


class TestTheSchema:
    def test_Store_WhenStampedSchema33_OpensAndGainsTheTable(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(SNAPSHOT.read_text(encoding="utf-8"))
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            assert store.inferred_links() == []
            store.record_inferred_links([("e1", "starling:uid-a", "marlow bakery")])
            stamped = store.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            goals = store.goals()

        assert stamped == str(SCHEMA_VERSION) == "34"
        assert goals == [], "the goals table the snapshot carries is read without a fault"

    def test_RebuildFromRaw_LeavesTheRecordsAsTheyWere(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.record_inferred_links([("e1", "starling:uid-a", "marlow bakery")])
            store.settle_inferred_link("e1", AGREED)

            rebuild_from_raw(store)

            (link,) = store.inferred_links()

        assert (link.entity_id, link.outcome) == ("e1", AGREED)


class TestTheFinishersTheCommandLineComposes:
    def test_SettlingAfterALanding_WritesDownTheInferencesTheRuleMade(self, tmp_path):
        def row(day: date, minor: int, description: str, uid: str = "") -> Transaction:
            return Transaction(
                account_id="current-main",
                amount_minor=minor,
                value_date=day,
                booking_date=day,
                description=description,
                party_source_id=uid,
                source="starling" if uid else "statement",
                source_id=f"{description}-{day}" if uid else None,
                tier=SourceTier.AUTHORITATIVE if uid else SourceTier.SYNTHETIC,
            )

        rows = [
            row(date(2026, 3, 1), -450, "MARLOW BAKERY HIGH STREET 123", "starling:uid-marlow"),
            row(date(2026, 3, 8), -451, "MARLOW BAKERY HIGH STREET 456", "starling:uid-marlow"),
            *(
                row(date(2026, 2, 1 + n % 27), -1000 - n, f"ZEPHYR BOARD {n}", f"starling:uid-z{n}")
                for n in range(40)
            ),
            row(date(2026, 4, 1), -452, "MARLOW BAKERY HIGH STREET LONDON GB 789"),
        ]
        with Store(tmp_path / "s.sqlite3") as store:
            from obdi.analysis.learned_rules import set_settings

            set_settings(store, 2, 40)
            reconcile_batch(store, rows, digest="landing")

            landing_finishers().settle(store)

            assert [link.party for link in store.inferred_links()] == ["starling:uid-marlow"]
