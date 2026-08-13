"""`obdi rebuild-status`: one question, one exit code, for a deploy to gate on.

Written after a gate went in wrong and blocked production deploys. The converge
was made to fail when `obdi doctor` exited non-zero, on the reasoning that
doctor already carries the rebuild's outcome. It does - but it also carries
every other check, and the disposable instance legitimately has no credentials
at all. So doctor exited 1 for two missing secrets that are missing BY DESIGN,
the canary instance failed, and because it converges first the live instance
never updated.

A false positive that blocks a deploy is worse than the noise the gate was
guarding against, and the cause is a gate reading a broader signal than the
question it was asking. This command answers exactly one question.

EXIT CODES, which are the interface:
    0  the last rebuild succeeded, or none has run yet
    1  the last rebuild FAILED
    2  a rebuild is in flight, so the answer is not yet known

Two and one are distinguished because they call for different things - retry
versus stop - even where a caller currently retries on both.
"""

from __future__ import annotations

import pytest

from obdi.store import Store

FAILED = {
    "kind": "rebuild",
    "started_at": "2026-08-13T12:14:23Z",
    "finished_at": "2026-08-13T12:14:24Z",
    "ok": False,
    "summary": "table transaction_sources has no column named observed_date",
    "build": "0.4.224+3f2140f75a15",
}
SUCCEEDED = {
    "kind": "rebuild",
    "started_at": "2026-08-13T13:33:18Z",
    "finished_at": "2026-08-13T13:33:26Z",
    "ok": True,
    "summary": "",
    "records_total": 43015,
    "transactions": 46640,
    "artefacts_replayed": 419,
    "build": "0.4.227+677b63ab39e4",
}


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "c.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "a.json"))
    return tmp_path / "store.sqlite3"


def _record(path, run) -> None:
    with Store(path) as store:
        store.record_rebuild_run(**run)


class TestTheExitCode:
    def test_ASucceededRebuild_ExitsZero(self, store_path, capsys):
        from obdi.cli import main

        _record(store_path, SUCCEEDED)

        assert main(["--db", str(store_path), "rebuild-status"]) == 0
        assert "succeeded" in capsys.readouterr().out

    def test_AFailedRebuild_ExitsOne(self, store_path, capsys):
        from obdi.cli import main

        _record(store_path, FAILED)

        assert main(["--db", str(store_path), "rebuild-status"]) == 1
        assert "observed_date" in capsys.readouterr().out

    def test_ARebuildInFlight_ExitsTwo_NotZero(self, store_path, capsys):
        """The stale-evidence trap: the newest record is the run BEFORE this
        deploy, and after a healthy history that is a success."""
        from obdi import cli

        _record(store_path, SUCCEEDED)
        cli_note = "a rebuild is replaying the store"
        monkey = pytest.MonkeyPatch()
        monkey.setattr(cli, "rebuild_in_progress_note", lambda _p: cli_note)
        try:
            assert cli.main(["--db", str(store_path), "rebuild-status"]) == 2
        finally:
            monkey.undo()
        assert "not yet known" in capsys.readouterr().out

    def test_AStoreThatHasNeverRebuilt_ExitsZero(self, store_path, capsys):
        """A fresh instance must not block its own first deploy."""
        from obdi.cli import main

        with Store(store_path):
            pass

        assert main(["--db", str(store_path), "rebuild-status"]) == 0

    def test_MissingCredentials_DoNotAffectIt(self, store_path, capsys, monkeypatch):
        """The regression that caused this command to exist.

        The disposable instance has no credentials by design. `doctor` fails on
        that, correctly, and it has nothing to do with whether the rebuild
        worked - so gating a deploy on doctor's exit code stopped every
        converge. This command must answer the rebuild question alone.
        """
        from obdi.cli import main

        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET_FILE", "/secrets/nothing-here")
        monkeypatch.setenv("STARLING_PERSONAL_ACCESS_TOKEN_FILE", "/secrets/nor-here")
        _record(store_path, SUCCEEDED)

        assert main(["--db", str(store_path), "rebuild-status"]) == 0, (
            "missing secrets made the rebuild gate fail - which is the exact "
            "false positive that blocked production deploys"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
