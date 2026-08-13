"""A failed rebuild fails `doctor`, so a deploy can refuse to finish on it.

On 2026-08-13 the instance rebuilt into an empty derived layer twice. The
converge that preceded each one reported complete success: it asserts the
container is healthy, runs the expected image, and reports its version - and
says nothing about the rebuild the deploy itself triggers, which runs in the
background afterwards.

So the deploy passed while the thing it caused was failing, and the failure was
found two and a quarter hours later by eye. An alarm sent to a phone would have
been the WEAKEST place to catch this. The strongest is the deploy, where
somebody is already watching and the run can be refused.

`doctor` already exits non-zero when any check fails, and its own comment says a
deploy gates on that exit code. This puts the rebuild's outcome into that
report, so the gate exists with no new interface to build.

DELIBERATELY STRICT: any failed rebuild fails the check, not only one that
emptied the store. This is the aggressive setting on purpose - a check earns its
relaxation by evidence that the strictness costs more than it catches, and the
opposite order pays for the calibration with an incident. The detail
distinguishes an emptied store from a partial one, because severity is what the
reader needs even when the verdict is the same.
"""

from __future__ import annotations

import pytest

from obdi.doctor import rebuild_check

EMPTY_FAILURE = {
    "ok": 0,
    "finished_at": "2026-08-13T12:14:24Z",
    "artefacts_replayed": 0,
    "transactions": None,
    "summary": "table transaction_sources has no column named observed_date",
    "build": "0.4.224+3f2140f75a15",
}
PARTIAL_FAILURE = {
    "ok": 0,
    "finished_at": "2026-08-13T12:14:24Z",
    "artefacts_replayed": 380,
    "transactions": 41002,
    "summary": "interrupted",
    "build": "0.4.224+3f2140f75a15",
}
SUCCESS = {
    "ok": 1,
    "finished_at": "2026-08-13T13:33:26Z",
    "artefacts_replayed": 419,
    "transactions": 46640,
    "summary": "",
    "build": "0.4.227+677b63ab39e4",
}


class TestTheRebuildCheck:
    def test_ARebuildThatEmptiedTheStore_FailsTheCheck(self):
        result = rebuild_check([EMPTY_FAILURE])

        assert not result.ok
        assert "EMPTY" in result.detail
        assert "observed_date" in result.detail, "the recorded reason travels"

    def test_ARebuildThatFailedPartway_AlsoFailsTheCheck(self):
        """The strict setting, asserted rather than assumed.

        This case leaves most of the data and looks healthy. Under a
        fail-early policy it still stops the deploy; the detail says it was
        partial so a reader can judge urgency without opening the page.
        """
        result = rebuild_check([PARTIAL_FAILURE])

        assert not result.ok
        assert "380" in result.detail, "how far it got is the severity"
        assert "EMPTY" not in result.detail

    def test_ASuccessfulRebuild_PassesAndSaysWhatItDid(self):
        result = rebuild_check([SUCCESS])

        assert result.ok
        assert "419" in result.detail

    def test_OnlyTheLatestRunDecides(self):
        """A failure already recovered from must not fail every later deploy -
        that is how a gate gets routed around."""
        assert rebuild_check([SUCCESS, EMPTY_FAILURE]).ok

    def test_AStoreThatHasNeverRebuilt_Passes_AndSaysSo(self):
        """A fresh instance has no runs. Passing is right; passing SILENTLY is
        not, because "no rebuild recorded" and "rebuild fine" are different
        states and only one of them is evidence."""
        result = rebuild_check([])

        assert result.ok
        assert "no rebuild" in result.detail.lower()


class TestTheCheckReachesTheCommand:
    """The gate is the exit code, so the exit code is what gets asserted.

    Testing the builder alone would repeat the fault this exists to fix: on the
    same day, a parser gate was found to have been working for weeks while a
    test docstring said otherwise, because nothing called the door.
    """

    @pytest.fixture
    def store_whose_last_rebuild_failed(self, tmp_path, monkeypatch):
        from obdi.store import Store

        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "c.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "a.json"))
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

    def test_DoctorExitsNonZero_SoTheDeployCanRefuse(
        self, store_whose_last_rebuild_failed, capsys
    ):
        from obdi.cli import main

        exit_code = main(["--db", str(store_whose_last_rebuild_failed), "doctor"])
        printed = capsys.readouterr().out

        assert exit_code == 1, (
            "the converge gates on this exit code - a zero here is a deploy "
            "reporting success over a store serving nothing"
        )
        assert "EMPTY" in printed

    def test_ARebuildStillRunning_DoesNotPassOnThePreviousRunsRecord(
        self, tmp_path, monkeypatch, capsys
    ):
        """The subtle one, and the reason the converge retries.

        A rebuild in flight has not written its row yet, so the newest record
        is the run BEFORE this deploy - which after a healthy history is a
        SUCCESS. Reporting that would pass the deploy on evidence about the
        wrong rebuild, and it would do so most confidently in exactly the
        situation the gate exists for: a code change that is about to break the
        replay.
        """
        from obdi import cli
        from obdi.store import Store

        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "c.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "a.json"))
        path = tmp_path / "store.sqlite3"
        with Store(path) as store:
            store.record_rebuild_run(
                kind="rebuild",
                started_at="2026-08-12T21:34:33Z",
                finished_at="2026-08-12T21:34:42Z",
                ok=True,
                summary="",
                records_total=43010,
                transactions=46635,
                artefacts_replayed=419,
                build="0.4.202+68d27d3e8e0a",
            )
        monkeypatch.setattr(
            cli, "rebuild_in_progress_note", lambda _p: "a rebuild is replaying the store"
        )

        exit_code = cli.main(["--db", str(path), "doctor"])
        printed = capsys.readouterr().out

        assert exit_code == 1, (
            "doctor passed while a rebuild was still running, using the "
            "previous run's success as evidence for this one"
        )
        assert "not yet known" in printed

    def test_AStoreThatCannotBeRead_FailsRatherThanVanishing(
        self, store_whose_last_rebuild_failed, monkeypatch, capsys
    ):
        """A gate that disappears when it cannot run is worse than no gate.

        The block this check joins is wrapped in `contextlib.suppress`, so
        anything raising inside it removes every check it contributes and
        `doctor` exits 0 - a deploy would read that as a clean bill of health
        from a command that never looked. Silence and success must not be the
        same answer.
        """
        from obdi import cli
        from obdi.store import Store

        def refuse(self, limit: int = 10) -> list[dict[str, object]]:
            raise RuntimeError("the rebuild history could not be read")

        monkeypatch.setattr(Store, "recent_rebuild_runs", refuse)

        exit_code = cli.main(["--db", str(store_whose_last_rebuild_failed), "doctor"])
        printed = capsys.readouterr().out

        assert exit_code == 1, (
            "doctor passed while unable to check the rebuild - the gate "
            "vanished instead of failing"
        )
        assert "could not be read" in printed, "and it must say what stopped it"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
