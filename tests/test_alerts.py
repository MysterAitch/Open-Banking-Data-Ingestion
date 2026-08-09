"""Edge-triggered alerts: trends announced once, resolutions announced once,
silence in between.

The quiet-API incident is the design's origin and its acceptance case: the
attempts ledger held four days of consecutive refusals and nothing read it.
A trend detector over that ledger plus an edge-triggered announcer is what
turns "found by accident" into "reported proactively" - without nagging on
every cycle while a known condition persists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from obdi.alerts import Finding, process, refusal_trends


def _attempt(hours_ago: float, outcome: str, *, status: int | None = None,
             connection: str = "starling-api", ref: str = "starling:acc-1") -> dict:
    stamp = datetime.now(UTC) - timedelta(hours=hours_ago)
    return {
        "attempted_at": stamp.isoformat(),
        "connection_id": connection,
        "account_ref": ref,
        "asked": "routine-full",
        "outcome": outcome,
        "http_status": status,
    }


class TestRefusalTrends:
    def test_APersistedRefusalStreak_IsATrend_WithCountSpanAndStatus(self):
        attempts = [
            _attempt(hours, "refused", status=400)
            for hours in (0, 6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66)
        ]

        found = refusal_trends(attempts)

        assert len(found) == 1
        message = found[0].message
        assert "12 consecutive" in message
        assert "HTTP 400" in message
        assert "starling-api" in message and "starling:acc-1" in message

    def test_AShortBurst_IsWeatherNotATrend(self):
        # Plenty of refusals, all inside two hours: one bad patch, not a
        # pattern - the next cycle answers it.
        attempts = [
            _attempt(hours, "refused", status=429)
            for hours in (0.0, 0.5, 1.0, 1.5, 2.0)
        ]

        assert refusal_trends(attempts) == []

    def test_TooFewRefusals_NeverFire(self):
        attempts = [
            _attempt(0, "refused", status=400),
            _attempt(24, "refused", status=400),
        ]

        assert refusal_trends(attempts) == []

    def test_OneLandedAsk_ResetsTheStreak(self):
        attempts = [
            _attempt(0, "landed"),
            _attempt(6, "refused", status=400),
            _attempt(12, "refused", status=400),
            _attempt(18, "refused", status=400),
            _attempt(24, "refused", status=400),
        ]

        assert refusal_trends(attempts) == []

    def test_Streaks_AreScopedPerConnectionAndAccount(self):
        stuck = [
            _attempt(hours, "refused", status=400, ref="starling:acc-1")
            for hours in (0, 12, 24)
        ]
        healthy = [
            _attempt(hours, "landed", ref="starling:acc-2") for hours in (0, 12, 24)
        ]

        found = refusal_trends(stuck + healthy)

        assert [finding.key for finding in found] == [
            "refusals:starling-api:starling:acc-1"
        ]


class TestEdgeTriggeredAnnouncement:
    def _sender(self):
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        return sent, send

    def test_ANewFinding_IsAnnouncedOnce_ThenSilenceWhileItPersists(self, tmp_path):
        state = tmp_path / "state.json"
        finding = Finding("refusals:starling-api:acc-1", "every ask refused")
        sent, send = self._sender()

        process([finding], state, send)
        process([finding], state, send)
        process([finding], state, send)

        assert sent == ["every ask refused"]

    def test_AClearedFinding_AnnouncesItsResolution_Once(self, tmp_path):
        state = tmp_path / "state.json"
        finding = Finding("refusals:starling-api:acc-1", "every ask refused")
        sent, send = self._sender()

        process([finding], state, send)
        process([], state, send)
        process([], state, send)

        assert sent == ["every ask refused", "resolved: every ask refused"]

    def test_AFailedDelivery_RetriesNextCycle_InsteadOfDroppingTheAlert(
        self, tmp_path
    ):
        state = tmp_path / "state.json"
        finding = Finding("consent:halifax", "consent expiring")
        attempts: list[str] = []
        health = {"up": False}

        def flaky(message: str) -> bool:
            attempts.append(message)
            return health["up"]

        process([finding], state, flaky)
        health["up"] = True
        process([finding], state, flaky)
        process([finding], state, flaky)

        # Announced on the second cycle when delivery recovered, then quiet.
        assert attempts == ["consent expiring", "consent expiring"]

    def test_TheStateFile_SurvivesGarbage(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text("not json", encoding="utf-8")
        sent, send = self._sender()

        process([Finding("k", "message")], state, send)

        assert sent == ["message"]
        assert Path(state).exists()
