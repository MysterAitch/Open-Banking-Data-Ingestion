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

from obdi.alerts import Finding, consent_rung, disk_finding, process, refusal_trends


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


class TestLadderedAnnouncement:
    """Impending danger escalates: a finding's RUNG rising on the same key
    re-announces (14 days -> 7 days -> 3 days each deserve their own ping),
    a rung falling stays silent (improvement short of clearance is not
    news), and clearance still announces exactly once."""

    def _sender(self):
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        return sent, send

    def test_ARungIncrease_ReAnnounces_ARungDecreaseStaysSilent(self, tmp_path):
        state = tmp_path / "state.json"
        sent, send = self._sender()

        process([Finding("consent:halifax", "14-day notice", rung=1)], state, send)
        process([Finding("consent:halifax", "14-day notice", rung=1)], state, send)
        process([Finding("consent:halifax", "7-day warning", rung=2)], state, send)
        process([Finding("consent:halifax", "back to 14-day", rung=1)], state, send)
        process([Finding("consent:halifax", "3-day warning", rung=3)], state, send)
        process([], state, send)

        assert sent == [
            "14-day notice",
            "7-day warning",
            "3-day warning",
            "resolved: 3-day warning",
        ]

    def test_LegacyStateWithoutRungs_IsReadAsRungZero(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text('{"consent:halifax": "old message"}', encoding="utf-8")
        sent, send = self._sender()

        process([Finding("consent:halifax", "7-day warning", rung=2)], state, send)

        assert sent == ["7-day warning"]


class TestDegradedFeeds:
    """Total silence is the top rung of a ladder that starts at partial
    degradation: half the asks failing across days deserves a warning
    BEFORE the feed goes fully dark."""

    def test_HalfTheAsksRefusedOverDays_IsRungOne_WithDenominator(self):
        attempts = []
        for index in range(12):
            outcome = "refused" if index % 2 == 0 else "landed"
            attempts.append(
                _attempt(index * 6, outcome, status=429 if outcome == "refused" else None)
            )

        found = refusal_trends(attempts)

        assert len(found) == 1
        assert found[0].rung == 1
        assert "6 of 12 asks refused" in found[0].message

    def test_MostAsksRefused_IsRungTwo(self):
        # The landed ask sits right at the newest end, so the consecutive
        # streak is unambiguously broken and only the rate can speak.
        attempts = []
        for index in range(10):
            outcome = "refused" if index != 1 else "landed"
            attempts.append(
                _attempt(index * 6, outcome, status=400 if outcome == "refused" else None)
            )

        found = refusal_trends(attempts)

        assert len(found) == 1
        assert found[0].rung == 2

    def test_EveryAskRefused_StaysTheTopRung(self):
        attempts = [
            _attempt(hours, "refused", status=400) for hours in (0, 12, 24, 36)
        ]

        found = refusal_trends(attempts)

        assert len(found) == 1
        assert found[0].rung == 3
        assert "consecutive" in found[0].message

    def test_AFewScatteredRefusals_AreNotAFinding(self):
        attempts = []
        for index in range(12):
            outcome = "refused" if index in (0, 5) else "landed"
            attempts.append(_attempt(index * 6, outcome))

        assert refusal_trends(attempts) == []


class TestConsentLadder:
    def test_TheRungsMatchTheDeadline(self):
        assert consent_rung(20) is None
        assert consent_rung(14) == (1, "14-day notice")
        assert consent_rung(8) == (1, "14-day notice")
        assert consent_rung(7) == (2, "7-day warning")
        assert consent_rung(4) == (2, "7-day warning")
        assert consent_rung(3) == (3, "3-day warning")
        assert consent_rung(0) == (3, "3-day warning")
        assert consent_rung(None) is None


class TestDiskRungs:
    def test_TheRungsMatchTheFill(self, tmp_path, monkeypatch):
        import shutil as _shutil

        def fake_usage(path):
            return type("U", (), {"total": 100, "used": 92, "free": 8})()

        monkeypatch.setattr(_shutil, "disk_usage", fake_usage)

        finding = disk_finding(tmp_path)

        assert finding is not None
        assert finding.rung == 2
        assert "92%" in finding.message

    def test_AHealthyVolume_IsNoFinding(self, tmp_path, monkeypatch):
        import shutil as _shutil

        def fake_usage(path):
            return type("U", (), {"total": 100, "used": 50, "free": 50})()

        monkeypatch.setattr(_shutil, "disk_usage", fake_usage)

        assert disk_finding(tmp_path) is None
