"""The walk-back loop and the freshness note, tested without a bank.

One explicit press may fan out into several calls - the attended part is the
person present and requesting; the mechanics are here, and the mechanics
must stop the moment the provider says stop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from obdi.probing import StepRefused, sca_note, walk_history


class TestWalkingHistoryBack:
    def test_Walk_StepsDownOnWindowRefusals_AndFindsTheBoundaryAtOneDay(self):
        granted = []

        def step(days):
            if days > 90:
                raise StepRefused("invalid_date_range", "beyond the boundary")
            if len(granted) < 2 and days == 90:
                granted.append(days)
                return "landed"
            raise StepRefused("invalid_date_range", "beyond the boundary")

        transcript, outcome = walk_history(step)

        assert outcome == "boundary"
        assert granted == [90, 90]
        # The transcript shows the descent: refusals at 730 and 365, grants
        # at 90, then the walk down to the 1-day wall.
        assert any("+730d: refused" in line for line in transcript)
        assert any("+90d: landed" in line for line in transcript)
        assert transcript[-1] == "+1d: refused (invalid_date_range)"

    def test_Walk_StopsImmediately_WhenTheAuthenticationWindowCloses(self):
        def step(days):
            raise StepRefused("sca_exceeded", "expired")

        transcript, outcome = walk_history(step)

        assert outcome == "sca_expired"
        assert len(transcript) == 1  # one refusal, no retries against it

    def test_Walk_StopsOnRateLimiting_RatherThanDiggingDeeper(self):
        def step(days):
            raise StepRefused("provider_request_limit_exceeded", "429")

        _transcript, outcome = walk_history(step)

        assert outcome == "rate_limited"

    def test_Walk_IsCallCapped_SoOnePressCannotRunUnbounded(self):
        def step(days):
            return "landed"

        transcript, outcome = walk_history(step, call_cap=5)

        assert outcome == "cap"
        assert len(transcript) == 5


class TestFreshnessNote:
    def test_Note_InsideTheKnownWindow_SaysOpenWithMinutesLeft(self):
        note = sca_note(
            authorised_at=datetime.now(UTC) - timedelta(minutes=2),
            window_minutes=5,
            refusal_seen=False,
        )

        assert "OPEN" in note and "3 min" in note

    def test_Note_PastTheKnownWindow_SaysClosedAndHowToReopen(self):
        note = sca_note(
            authorised_at=datetime.now(UTC) - timedelta(hours=3),
            window_minutes=5,
            refusal_seen=False,
        )

        assert "closed" in note and "re-authorise" in note.lower()

    def test_Note_AfterARefusal_TrustsTheProviderOverTheClock(self):
        note = sca_note(
            authorised_at=datetime.now(UTC),
            window_minutes=5,
            refusal_seen=True,
        )

        assert "closed" in note

    def test_Note_UnknownWindowLength_SaysSoInsteadOfInventingFive(self):
        note = sca_note(
            authorised_at=datetime.now(UTC) - timedelta(minutes=10),
            window_minutes=None,
            refusal_seen=False,
        )

        assert "not yet known" in note
