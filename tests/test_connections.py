from datetime import UTC, datetime, timedelta

import pytest

from obdi.connections import (
    Connection,
    ConnectionStore,
    apply_refresh,
    build_connection,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

TOKEN_RESPONSE = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 3600,
}


def connection(**overrides) -> Connection:
    base = build_connection(
        connection_id="nationwide",
        provider="uk-ob-nationwide",
        token_response=TOKEN_RESPONSE,
        now=NOW,
    )
    return base if not overrides else Connection(**{**base.__dict__, **overrides})


class TestBuildingAConnection:
    def test_Connection_WhenBuiltFromTokenResponse_RefreshTokenRetained(self):
        assert connection().refresh_token == "refresh-1"

    def test_Connection_WhenBuiltFromTokenResponse_AccessExpiryTakenFromExpiresIn(self):
        assert connection().access_expires_at == (NOW + timedelta(seconds=3600)).isoformat()

    def test_Connection_WhenBuiltFromTokenResponse_ConsentClockStartedIndependently(self):
        # No token response reports consent expiry, which is exactly why it
        # gets forgotten. It is derived here instead.
        assert connection().consent_expires_at == (NOW + timedelta(days=90)).isoformat()

    def test_Connection_WhenNoRefreshTokenReturned_RefusedLoudly(self):
        # Without offline_access there is no refresh token, and every sync
        # would silently need manual re-authorisation.
        with pytest.raises(ValueError, match="offline_access"):
            build_connection(
                connection_id="x",
                provider="y",
                token_response={"access_token": "a", "expires_in": 3600},
                now=NOW,
            )


class TestAccessTokenLifetime:
    def test_AccessToken_WhenFreshlyIssued_TreatedAsValid(self):
        assert connection().access_token_valid(now=NOW)

    def test_AccessToken_WhenExpired_TreatedAsInvalid(self):
        assert not connection().access_token_valid(now=NOW + timedelta(hours=2))

    def test_AccessToken_WhenCloseToExpiry_RefreshedEarlyRatherThanRisked(self):
        # A request that starts valid and arrives expired is a confusing
        # intermittent failure, so the margin refreshes ahead of the deadline.
        just_inside = NOW + timedelta(seconds=3600) - timedelta(minutes=2)
        assert not connection().access_token_valid(now=just_inside)

    def test_AccessToken_WhenAbsent_TreatedAsInvalid(self):
        assert not connection(access_token="").access_token_valid(now=NOW)


class TestRefreshing:
    def test_Refresh_WhenNewTokensIssued_AccessTokenAndExpiryUpdated(self):
        later = NOW + timedelta(minutes=59)
        refreshed = apply_refresh(
            connection(), {"access_token": "access-2", "expires_in": 3600}, now=later
        )
        assert refreshed.access_token == "access-2"
        assert refreshed.access_expires_at == (later + timedelta(seconds=3600)).isoformat()

    def test_Refresh_WhenProviderRotatesRefreshToken_NewOneStored(self):
        refreshed = apply_refresh(
            connection(),
            {"access_token": "a", "refresh_token": "refresh-2", "expires_in": 3600},
            now=NOW,
        )
        assert refreshed.refresh_token == "refresh-2"

    def test_Refresh_WhenProviderDoesNotRotate_PreviousRefreshTokenKept(self):
        refreshed = apply_refresh(connection(), {"access_token": "a", "expires_in": 3600}, now=NOW)
        assert refreshed.refresh_token == "refresh-1"

    def test_Consent_WhenAccessTokenRefreshed_ConsentExpiryUnchanged(self):
        # The whole trap: refreshing indefinitely does NOT renew consent, and
        # pretending otherwise hides the wall until it is hit.
        original = connection()
        refreshed = apply_refresh(
            original, {"access_token": "a", "expires_in": 3600}, now=NOW + timedelta(days=80)
        )
        assert refreshed.consent_expires_at == original.consent_expires_at


class TestConsentLifetime:
    def test_Consent_WhenNewlyGranted_NotFlaggedForAttention(self):
        assert not connection().consent_needs_attention(now=NOW)

    def test_Consent_WhenNearingExpiry_FlaggedBeforeItFires(self):
        assert connection().consent_needs_attention(now=NOW + timedelta(days=80))

    def test_Consent_WhenPastExpiry_ReportedAsExpired(self):
        assert connection().consent_expired(now=NOW + timedelta(days=91))

    def test_Consent_WhenStillValid_NotReportedAsExpired(self):
        assert not connection().consent_expired(now=NOW + timedelta(days=89))

    def test_Consent_WhenCounted_ReportsDaysRemaining(self):
        assert connection().consent_days_remaining(now=NOW + timedelta(days=60)) == 30


class TestPersistence:
    def test_Connection_WhenSavedAndReloaded_SurvivesRoundTrip(self, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        store.put(connection())

        reloaded = store.load()["nationwide"]

        assert reloaded.refresh_token == "refresh-1"
        assert reloaded.consent_expires_at == connection().consent_expires_at

    def test_Store_WhenFileAbsent_ReturnsNoConnectionsRatherThanFailing(self, tmp_path):
        assert ConnectionStore(tmp_path / "missing.json").load() == {}

    def test_Store_WhenSecondConnectionAdded_FirstPreserved(self, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        store.put(connection())
        store.put(connection(connection_id="halifax", provider="uk-ob-halifax"))

        assert set(store.load()) == {"nationwide", "halifax"}

    def test_Store_WhenConnectionReauthorised_ReplacedNotDuplicated(self, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        store.put(connection())
        store.put(connection(refresh_token="refresh-new"))

        connections = store.load()
        assert len(connections) == 1
        assert connections["nationwide"].refresh_token == "refresh-new"
