"""A rebuild of the faithful large store reproduces it; a rebuild of the default one does not.

Every figure the read-model design quotes for a rebuild at this size rests on the rebuild
replaying the store it is given. The default large store lands its feed, aggregator, and export
rows with no raw artefact, so a rebuild of it loses most of them and measures a fraction of the
work (`large_store_corpus`, "two forms"). The faithful form lands each as an artefact, and this
file is the proof: the rows counted before and after a rebuild from raw are the same, account by
account and status by status, and so are the sightings.

KNOWN ANSWER, decided before the first run: nothing differs. A rebuild that lost or invented a row
disagrees with the store it was built from. The control is the default form, whose rebuild must
lose rows, so a test that passed over both forms would be proving nothing.

MEASURED, the faithful store rebuilt on a machine with other builds running (2026-10-06): 1,083
artefacts replayed, 34,707 transactions resolved (one per sighting; the rows that result are about
a fifth of that), every account's row count unchanged. Seconds per phase, from `OBDI_TIMINGS`:

    reconcile              25.2   (1,083 calls; includes plan-partners 1.2 and resolve 1.1)
    write-flush             4.7   (1,083)
    parse                   3.3   (1,083)
    same-money-fold         2.7   (of which reading-statements 2.0)
    space-fold              0.6
    transfer-pairing        0.2
    declined-items          0.04
    review-settlement, flag-answers, protection, load-candidates    under 0.01 each

The earlier lower bound on the default store, which replays 55 artefacts and loses four rows in
five, was 7.0 s with `parse`, `same-money-fold`, and `review-settlement` the three largest; at
full size `reconcile` is five times the next phase and `review-settlement` is nothing; why the
latter differs was not investigated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from large_store_corpus import MAIN, LargeStore, cached_large_store
from large_store_pages import copy_of
from obdi.core import instrumentation
from obdi.ingest.rebuild import RebuildReport, rebuild_from_raw
from obdi.ingest.store import Store


@pytest.fixture(scope="module")
def faithful() -> LargeStore:
    return cached_large_store(faithful=True)


def census(store: Store) -> dict[str, object]:
    """What a rebuild must reproduce: rows by account and status, sightings, and pairs."""
    connection = store.connection
    return {
        "rows": {
            (str(a), str(s)): int(n)
            for a, s, n in connection.execute(
                "SELECT account_id, status, COUNT(*) FROM transactions GROUP BY 1, 2"
            )
        },
        "sightings": int(
            connection.execute("SELECT COUNT(*) FROM transaction_sources").fetchone()[0]
        ),
        "pairs": int(connection.execute("SELECT COUNT(*) FROM transfer_pairs").fetchone()[0]),
    }


def rebuilt(
    large: LargeStore, directory: Path
) -> tuple[dict[str, object], dict[str, object], RebuildReport]:
    copy = copy_of(large, directory)
    with Store(copy.path) as store:
        before = census(store)
        instrumentation.configure(True)
        try:
            report = rebuild_from_raw(store, account_map=large.account_map)
        finally:
            instrumentation.configure(None)
        return before, census(store), report


class TestRebuildingTheLargeStore:
    def test_FaithfulStore_RebuiltFromRaw_HoldsEveryRowSightingAndPairItHeld(
        self, faithful, tmp_path
    ):
        before, after, report = rebuilt(faithful, tmp_path / "faithful")

        assert after == before
        assert report.problems == []
        assert all(was == now for was, now in report.account_changes.values())
        assert sum(was for was, _ in report.account_changes.values()) > 6000

    def test_FaithfulStore_RebuiltFromRaw_ReadsEveryRecordOfEveryPayload(self, faithful, tmp_path):
        _, after, report = rebuilt(faithful, tmp_path / "faithful")

        assert report.records_total > 0
        assert report.records_done == report.records_total
        # A row is sighted by every pull whose window held it, so the transactions the replay
        # resolved are the sightings it made, many more than the rows that result.
        rows = sum(after["rows"].values())  # type: ignore[union-attr]
        assert report.transactions > 4 * rows
        assert report.artefacts_skipped < report.artefacts_replayed

    def test_FaithfulStore_RebuiltFromRaw_ReportsItsPhasesInSeconds(self, faithful, tmp_path):
        _, _, report = rebuilt(faithful, tmp_path / "faithful")

        for phase in ("parse", "reconcile", "transfer-pairing", "space-fold"):
            assert report.timings[phase]["seconds"] >= 0, phase
        print(
            "rebuild phases: "
            + ", ".join(
                f"{name} {entry['seconds']}s x{entry['calls']}"
                for name, entry in report.timings.items()
            )
        )

    def test_DefaultStore_RebuiltFromRaw_LosesTheRowsItHasNoArtefactFor(self, tmp_path):
        default = cached_large_store()

        before, after, report = rebuilt(default, tmp_path / "default")

        assert after != before
        was, now = report.account_changes[MAIN]
        assert now < was
