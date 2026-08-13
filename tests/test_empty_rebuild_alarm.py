"""A rebuild that leaves the store empty says so, instead of waiting to be read.

On 2026-08-13 the live instance rebuilt twice into nothing. Both runs were
recorded correctly and both were rendered on the home page. Nobody was told. The
derived layer stayed empty for two and a quarter hours and was found by somebody
reading that page for an unrelated reason.

The evidence was rendered, which is the standard this project already holds
itself to - and a rendered fact only reaches somebody already looking at the
page. A rebuild is precisely the operation nobody watches.

WHY THIS THRESHOLD AND NOT ANOTHER. A rebuild wipes the derived layer before
replaying, so "failed having replayed nothing" is the state where the instance
serves NOTHING, and it can be read off the run record with no judgement at all.
It cannot false-positive.

Its blind spot is deliberate and is asserted below: a rebuild that fails partway
leaves most of the data and is not announced here. That case needs a comparison
against what the store held before, which can legitimately fall (dedupe,
refiling), so it needs a threshold somebody has chosen rather than one inferred
here. Until that decision is made, this covers the case that cannot be argued
about rather than covering nothing.
"""

from __future__ import annotations

import pytest

from obdi.alerts import empty_rebuild_finding

FAILED_EMPTY = {
    "ok": 0,
    "finished_at": "2026-08-13T12:14:24Z",
    "artefacts_replayed": 0,
    "records_total": None,
    "transactions": None,
    "summary": "table transaction_sources has no column named observed_date",
    "build": "0.4.224+3f2140f75a15",
}
SUCCEEDED = {
    "ok": 1,
    "finished_at": "2026-08-13T13:33:26Z",
    "artefacts_replayed": 419,
    "records_total": 43015,
    "transactions": 46640,
    "summary": "",
    "build": "0.4.227+677b63ab39e4",
}
FAILED_PARTWAY = {
    "ok": 0,
    "finished_at": "2026-08-13T12:14:24Z",
    "artefacts_replayed": 380,
    "records_total": 43015,
    "transactions": 41002,
    "summary": "interrupted",
    "build": "0.4.224+3f2140f75a15",
}


class TestTheEmptyRebuildAlarm:
    def test_ARebuildThatWipedAndThenFailed_IsAnnounced(self):
        """The incident, as the alarm would have met it."""
        finding = empty_rebuild_finding([FAILED_EMPTY])

        assert finding is not None
        assert "empty" in finding.message.lower()

    def test_TheAnnouncement_CarriesTheReasonTheRebuildGave(self):
        """A person woken by this needs to know whether to redeploy or restore,
        and the run already recorded which. An alarm that says only "the
        rebuild failed" sends them to the page they were not reading."""
        finding = empty_rebuild_finding([FAILED_EMPTY])

        assert "observed_date" in finding.message, (
            "the recorded reason is the difference between a fix and a guess"
        )
        assert "0.4.224" in finding.message, "which build did it"

    def test_ASucceededRebuild_RaisesNothing_SoTheFindingResolves(self):
        """Absence is how `process` announces a resolution, so the successful
        case must produce no finding rather than a cleared one."""
        assert empty_rebuild_finding([SUCCEEDED]) is None

    def test_OnlyTheMostRecentRunDecides(self):
        """A failure already recovered from is history, not an alarm. Runs
        arrive newest first."""
        assert empty_rebuild_finding([SUCCEEDED, FAILED_EMPTY]) is None

    def test_AStoreThatHasNeverRebuilt_RaisesNothing(self):
        """A fresh instance has no runs at all, and silence is the right
        answer - not "the last rebuild left nothing"."""
        assert empty_rebuild_finding([]) is None

    def test_ARebuildThatFailedPartway_IsNotAnnouncedHere(self):
        """The deliberate blind spot, pinned so it stays deliberate.

        This case leaves most of the data and looks healthy, which arguably
        makes it worse. It is excluded because deciding it needs a threshold
        against what the store held before, and a rebuild may legitimately
        reduce counts. Whoever adds that should delete this test rather than
        find it failing and weaken the one above.
        """
        assert empty_rebuild_finding([FAILED_PARTWAY]) is None

    def test_ARunMissingItsCounts_IsTreatedAsEmpty(self):
        """The real rows recorded None, not 0: the run died before it could
        count anything. Reading None as "not empty" would have missed the very
        incident this exists for."""
        sparse = {**FAILED_EMPTY, "artefacts_replayed": None}

        assert empty_rebuild_finding([sparse]) is not None


class TestTheAlarmThroughTheRealCommand:
    """A finding nothing calls is decoration.

    Written because that exact fault was found earlier the same day: obdi's
    arithmetic gate had been refusing bad statements for weeks while a test
    docstring claimed it did not, because every test called the parser directly
    and none called the door. So this drives `obdi alert`, the command the
    scheduler runs.

    No channel is configured, which is deliberate on two counts: the delivery
    path then prints instead of sending, so the test cannot reach anybody's
    phone, and the edge protocol still runs - which is the behaviour a store
    with no ntfy URL actually gets.
    """

    @pytest.fixture
    def store_whose_last_rebuild_emptied_it(self, tmp_path, monkeypatch):
        from obdi.store import Store

        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "c.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "a.json"))
        monkeypatch.setenv("OBDI_ALERT_STATE", str(tmp_path / "alert-state.json"))
        monkeypatch.delenv("OBDI_NTFY_URL", raising=False)
        path = tmp_path / "store.sqlite3"
        with Store(path) as store:
            store.record_rebuild_run(
                kind="rebuild",
                started_at="2026-08-13T12:14:23Z",
                finished_at="2026-08-13T12:14:24Z",
                ok=False,
                summary="table transaction_sources has no column named observed_date",
                build="0.4.224+3f2140f75a15",
            )
        return path

    def test_TheCommand_ReportsTheEmptyLayer(
        self, store_whose_last_rebuild_emptied_it, capsys
    ):
        from obdi.cli import main

        exit_code = main(["--db", str(store_whose_last_rebuild_emptied_it), "alert"])
        printed = capsys.readouterr().out

        assert exit_code == 0, "an alarm must not take the scheduler cycle down"
        assert "EMPTY" in printed
        assert "observed_date" in printed

    def test_ARecoveredStore_ReportsNothingAboutRebuilds(
        self, store_whose_last_rebuild_emptied_it, capsys
    ):
        """The other half: once a rebuild succeeds the alarm clears itself,
        because the finding stops being produced and the edge protocol turns
        that absence into a resolution."""
        from obdi.cli import main
        from obdi.store import Store

        with Store(store_whose_last_rebuild_emptied_it) as store:
            store.record_rebuild_run(
                kind="rebuild",
                started_at="2026-08-13T13:33:18Z",
                finished_at="2026-08-13T13:33:26Z",
                ok=True,
                summary="",
                records_total=43015,
                transactions=46640,
                artefacts_replayed=419,
                build="0.4.227+677b63ab39e4",
            )

        main(["--db", str(store_whose_last_rebuild_emptied_it), "alert"])
        printed = capsys.readouterr().out

        assert "EMPTY" not in printed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
