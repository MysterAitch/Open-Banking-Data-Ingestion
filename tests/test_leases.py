"""Leases: the traffic rules between workers and the updater.

The scenarios mirror the real actors: the scheduler must not be updated
mid-cycle, nothing should start critical work while an update runs, and
a crashed holder must never wedge the system - expiry reads as absence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from obdi.leases import STACK_UPDATE, acquire, active, held, lease, release


class TestLeases:
    def test_TakenLease_IsVisibleToOtherActors(self, tmp_path):
        acquire(tmp_path, "pull-cycle", "obdi-pull", ttl_seconds=1800)

        assert held(tmp_path, "pull-cycle")
        entries = active(tmp_path)
        assert entries[0]["holder"] == "obdi-pull"

    def test_ReleasedLease_ReadsAsAbsent(self, tmp_path):
        acquire(tmp_path, "pull-cycle", "obdi-pull", ttl_seconds=1800)
        release(tmp_path, "pull-cycle")

        assert not held(tmp_path, "pull-cycle")

    def test_CrashedHolder_ExpiresInsteadOfWedging(self, tmp_path):
        """The updater checks leases before acting; a worker that died
        holding one must not block updates forever. Past its TTL the
        lease reads as absent - crash safety over strictness."""
        acquire(tmp_path, "actual-apply", "obdi-applier", ttl_seconds=900)

        later = datetime.now(UTC) + timedelta(seconds=901)
        assert not held(tmp_path, "actual-apply", now=later)
        assert active(tmp_path, now=later) == []

    def test_ContextManager_ReleasesOnTheWayOut_EvenOnFailure(self, tmp_path):
        try:
            with lease(tmp_path, STACK_UPDATE, "operator", ttl_seconds=600):
                assert held(tmp_path, STACK_UPDATE)
                raise RuntimeError("update step failed")
        except RuntimeError:
            pass

        assert not held(tmp_path, STACK_UPDATE)

    def test_UnreadableLeaseFile_ReadsAsAbsent(self, tmp_path):
        (tmp_path / "junk.json").write_text("not json", encoding="utf-8")

        assert active(tmp_path) == []

    def test_MissingDirectory_MeansNoLeases(self, tmp_path):
        assert active(tmp_path / "never-created") == []


class TestScheduledPullGate:
    """Deploys and restarts must never spend bank quota: the compose loop
    pulls on container start, so without spacing every deploy costs an
    unattended fetch against a roughly four-per-day cap."""

    def _store_with_scheduled_attempt(self, tmp_path, attempted_at):
        """One scheduled attempt, placed in time, through the write door.

        The door stamps the clock by default and takes an injected `now` for
        exactly this case: the rule under test is about WHEN the last scheduled
        cycle ran, so the attempt has to be placed rather than merely made. That
        absence is why this used to write the row itself - the bypass existed
        because the door offered no way to say when, which is a gap in the door
        rather than a reason to go round it.
        """
        import json as _json
        from datetime import datetime

        from obdi.store import Store

        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            store.record_attempt(
                source="truelayer",
                connection_id="halifax",
                account_ref="acc",
                asked="window",
                request_meta=_json.dumps({"trigger": "scheduled"}),
                outcome="landed",
                now=datetime.fromisoformat(attempted_at),
            )
        return db

    def test_RecentScheduledCycle_SkipsInsteadOfSpendingQuota(
        self, tmp_path, monkeypatch
    ):
        from datetime import UTC, datetime

        from obdi.cli import scheduled_pull_skip_reason

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        monkeypatch.setenv("OBDI_PULL_INTERVAL_SECONDS", "21600")
        now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
        db = self._store_with_scheduled_attempt(
            tmp_path, "2026-08-02T11:30:00+00:00"
        )

        reason = scheduled_pull_skip_reason(db, now=now)

        assert reason is not None
        assert "quota" in reason

    def test_CycleDue_RunsNormally(self, tmp_path, monkeypatch):
        from datetime import UTC, datetime

        from obdi.cli import scheduled_pull_skip_reason

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        monkeypatch.setenv("OBDI_PULL_INTERVAL_SECONDS", "21600")
        now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
        db = self._store_with_scheduled_attempt(
            tmp_path, "2026-08-02T04:00:00+00:00"
        )

        assert scheduled_pull_skip_reason(db, now=now) is None

    def test_FirstEverCycle_RunsNormally(self, tmp_path, monkeypatch):
        from obdi.cli import scheduled_pull_skip_reason
        from obdi.store import Store

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass

        assert scheduled_pull_skip_reason(db) is None

    def test_AttendedAttempts_DoNotCountTowardsSpacing(
        self, tmp_path, monkeypatch
    ):
        """An attended fetch minutes ago is the person actively asking -
        it must not delay the scheduled cycle, which banks account
        separately."""
        import json as _json
        from datetime import UTC, datetime

        from obdi.cli import scheduled_pull_skip_reason
        from obdi.store import Store

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            # An ATTENDED pull a minute ago: recent, and irrelevant to the
            # scheduler's spacing rule, which is the distinction under test.
            store.record_attempt(
                source="truelayer",
                connection_id="halifax",
                account_ref="acc",
                asked="window",
                request_meta=_json.dumps({"trigger": "attended"}),
                outcome="landed",
                now=datetime.fromisoformat("2026-08-02T11:59:00+00:00"),
            )

        now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
        assert scheduled_pull_skip_reason(db, now=now) is None

    def test_RunningRebuild_HoldsTheCycleBack(self, tmp_path, monkeypatch):
        """A rebuild mid-replay and a pull cycle write the same store;
        their collision aborted a live rebuild after the wipe. The pull
        loses nothing by waiting one interval."""
        from obdi.cli import scheduled_pull_skip_reason
        from obdi.leases import acquire
        from obdi.store import Store

        locks = tmp_path / "locks"
        monkeypatch.setenv("OBDI_LOCKS_DIR", str(locks))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass
        acquire(locks, "rebuild-derived", "obdi-web", ttl_seconds=3600)

        reason = scheduled_pull_skip_reason(db)

        assert reason is not None
        assert "rebuild" in reason

    def test_MidCyclePull_HoldsTheRebuildBack(self, tmp_path, monkeypatch):
        from obdi.cli import start_background_rebuild
        from obdi.leases import acquire
        from obdi.store import Store

        locks = tmp_path / "locks"
        monkeypatch.setenv("OBDI_LOCKS_DIR", str(locks))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass
        acquire(locks, "pull-cycle", "obdi-pull", ttl_seconds=1800)

        message = start_background_rebuild(db)

        assert "mid-cycle" in message
        assert not (tmp_path / "rebuild-status.json").exists()

    def test_StackUpdateLease_HoldsTheCycleBack(self, tmp_path, monkeypatch):
        from obdi.cli import scheduled_pull_skip_reason
        from obdi.leases import STACK_UPDATE, acquire
        from obdi.store import Store

        locks = tmp_path / "locks"
        monkeypatch.setenv("OBDI_LOCKS_DIR", str(locks))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass
        acquire(locks, STACK_UPDATE, "ansible", ttl_seconds=600)

        reason = scheduled_pull_skip_reason(db)

        assert reason is not None
        assert "update" in reason


class TestRebuildGuards:
    """While a rebuild replays the store, actions that read or move rows
    must wait: a mid-rebuild bind leaves a split state, and a push or
    audit reads a half-populated store."""

    def _db_with_rebuild_running(self, tmp_path, monkeypatch):
        from obdi.leases import acquire
        from obdi.store import Store

        locks = tmp_path / "locks"
        monkeypatch.setenv("OBDI_LOCKS_DIR", str(locks))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass
        acquire(locks, "rebuild-derived", "obdi-web", ttl_seconds=3600)
        return db

    def test_PushAndAudit_WaitPolitely(self, tmp_path, monkeypatch):
        from obdi.cli import queue_actual_audit, queue_actual_push

        db = self._db_with_rebuild_running(tmp_path, monkeypatch)
        monkeypatch.setenv("ACTUAL_SYNC_ID", "sync-1")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))

        assert "rebuild is replaying" in queue_actual_push(db)
        assert "rebuild is replaying" in queue_actual_audit(db)

    def test_NoRebuild_NoNote(self, tmp_path, monkeypatch):
        from obdi.cli import rebuild_in_progress_note
        from obdi.store import Store

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass

        assert rebuild_in_progress_note(db) is None


class TestTransientBlocksAreWaitedOut:
    """A deploy or rebuild holds its lease for minutes; the old behaviour
    cost the whole six-hour cycle. Spacing skips keep the long sleep."""

    def test_TransientLease_ClearsMidWait_AndThePullProceeds(
        self, tmp_path, monkeypatch
    ):
        from obdi.cli import _await_scheduled_clearance
        from obdi.leases import STACK_UPDATE, acquire, release
        from obdi.store import Store

        locks = tmp_path / "locks"
        monkeypatch.setenv("OBDI_LOCKS_DIR", str(locks))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass
        acquire(locks, STACK_UPDATE, "ansible", ttl_seconds=600)

        calls = []

        def fake_sleep(seconds):
            calls.append(seconds)
            if len(calls) == 2:
                release(locks, STACK_UPDATE)

        outcome = _await_scheduled_clearance(db, sleep=fake_sleep)

        assert outcome is None
        assert len(calls) == 2

    def test_SpacingSkip_ReturnsImmediately_NoWaiting(
        self, tmp_path, monkeypatch
    ):
        import json as _json

        from obdi.cli import _await_scheduled_clearance
        from obdi.store import Store

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        monkeypatch.setenv("OBDI_PULL_INTERVAL_SECONDS", "21600")
        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            # Just now, so the spacing rule is inside its window. The default
            # clock is exactly right here, which is why no time is injected.
            store.record_attempt(
                source="truelayer",
                connection_id="halifax",
                account_ref="acc",
                asked="window",
                request_meta=_json.dumps({"trigger": "scheduled"}),
                outcome="landed",
            )

        def explode(_seconds):
            raise AssertionError("a spacing skip must not wait")

        outcome = _await_scheduled_clearance(db, sleep=explode)

        assert outcome is not None
        assert "quota" in outcome

    def test_TransientThatNeverClears_GivesUpAfterTheBudget(
        self, tmp_path, monkeypatch
    ):
        from obdi.cli import _await_scheduled_clearance
        from obdi.leases import acquire
        from obdi.store import Store

        locks = tmp_path / "locks"
        monkeypatch.setenv("OBDI_LOCKS_DIR", str(locks))
        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass
        acquire(locks, "rebuild-derived", "obdi-web", ttl_seconds=3600)

        sleeps = []
        outcome = _await_scheduled_clearance(
            db, wait_seconds=60, poll_seconds=15, sleep=sleeps.append
        )

        assert outcome is not None
        assert "rebuild" in outcome
        assert len(sleeps) == 4


class TestExclusiveAcquisition:
    def test_SecondActor_CannotTakeALiveLease(self, tmp_path):
        from obdi import leases

        assert leases.acquire_exclusive(tmp_path, "rebuild-derived", "web", 3600)
        assert not leases.acquire_exclusive(tmp_path, "rebuild-derived", "web", 3600)

    def test_ExpiredLease_IsContestedAndRetaken(self, tmp_path):
        from obdi import leases

        assert leases.acquire_exclusive(tmp_path, "rebuild-derived", "web", -1)
        assert leases.acquire_exclusive(tmp_path, "rebuild-derived", "web", 3600)

    def test_ReleasedLease_CanBeRetaken(self, tmp_path):
        from obdi import leases

        assert leases.acquire_exclusive(tmp_path, "rebuild-derived", "web", 3600)
        leases.release(tmp_path, "rebuild-derived")
        assert leases.acquire_exclusive(tmp_path, "rebuild-derived", "web", 3600)


class TestAbortedRebuildMarker:
    def test_RunningStatusWithNoLease_BlocksStoreActions(self, tmp_path, monkeypatch):
        import json as _json

        from obdi.cli import rebuild_in_progress_note

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        db = tmp_path / "store.sqlite3"
        (tmp_path / "rebuild-status.json").write_text(
            _json.dumps({"state": "running", "started_at": "2026-08-03T10:00:00Z"}),
            encoding="utf-8",
        )

        note = rebuild_in_progress_note(db)

        assert note is not None
        assert "did not finish" in note
        assert "2026-08-03T10:00:00Z" in note

    def test_CompletedStatus_DoesNotBlock(self, tmp_path, monkeypatch):
        import json as _json

        from obdi.cli import rebuild_in_progress_note

        monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
        db = tmp_path / "store.sqlite3"
        (tmp_path / "rebuild-status.json").write_text(
            _json.dumps({"state": "done", "ok": True}), encoding="utf-8"
        )

        assert rebuild_in_progress_note(db) is None


class TestStoreExitDiscipline:
    def test_ExceptionInsideStoreBlock_RollsBackUncommittedWork(self, tmp_path):
        import pytest

        from obdi.store import Store

        db = tmp_path / "s.sqlite3"
        with pytest.raises(RuntimeError, match="mid-block failure"), Store(db) as store:
            # Through the door, which deliberately does NOT commit - it leaves
            # the write in flight for the block to settle, and work in flight is
            # exactly the state this scenario is about.
            store.queue_for_review("e-1", "test")
            raise RuntimeError("mid-block failure")

        with Store(db) as store:
            rows = store.connection.execute(
                "SELECT COUNT(*) FROM review_queue"
            ).fetchone()
        assert rows[0] == 0

    def test_CleanExit_StillCommits(self, tmp_path):
        from obdi.store import Store

        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            store.queue_for_review("e-1", "test")

        with Store(db) as store:
            rows = store.connection.execute(
                "SELECT COUNT(*) FROM review_queue"
            ).fetchone()
        assert rows[0] == 1
