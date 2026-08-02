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
        import json as _json

        from obdi.store import Store

        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            store.connection.execute(
                "INSERT INTO fetch_attempts (attempted_at, source, "
                "connection_id, account_ref, asked, request_meta, outcome) "
                "VALUES (?, 'truelayer', 'halifax', 'acc', 'window', ?, "
                "'landed')",
                (attempted_at, _json.dumps({"trigger": "scheduled"})),
            )
            store.connection.commit()
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
            store.connection.execute(
                "INSERT INTO fetch_attempts (attempted_at, source, "
                "connection_id, account_ref, asked, request_meta, outcome) "
                "VALUES (?, 'truelayer', 'halifax', 'acc', 'window', ?, "
                "'landed')",
                (
                    "2026-08-02T11:59:00+00:00",
                    _json.dumps({"trigger": "attended"}),
                ),
            )
            store.connection.commit()

        now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
        assert scheduled_pull_skip_reason(db, now=now) is None

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
