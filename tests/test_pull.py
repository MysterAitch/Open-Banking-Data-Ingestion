from datetime import UTC, datetime, timedelta

import pytest

from obdi.connections import ConnectionStore, build_connection
from obdi.pull import PullResult, ensure_access_token

TOKENS = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}


def connection(*, age_days: int = 0):
    return build_connection(
        connection_id="halifax",
        provider="uk-ob-halifax",
        token_response=TOKENS,
        now=datetime.now(UTC) - timedelta(days=age_days),
    )


class TestConsentGuard:
    def test_Pull_WhenConsentExpired_RefusedBeforeAnyNetworkCall(self, tmp_path):
        # Attempting a refresh here would fail with a less obvious error. No
        # token operation can recover expired consent - only a human
        # re-authorising at the bank.
        store = ConnectionStore(tmp_path / "c.json")
        with pytest.raises(RuntimeError, match="expired"):
            ensure_access_token(
                connection(age_days=95), client_id="x", client_secret="y", store=store
            )

    def test_Pull_WhenConsentExpired_ErrorPointsAtTheRunbook(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        with pytest.raises(RuntimeError, match="REAUTHORISE"):
            ensure_access_token(
                connection(age_days=95), client_id="x", client_secret="y", store=store
            )

    def test_Pull_WhenAccessTokenStillValid_NoRefreshAttempted(self, tmp_path):
        # A network call here would be pure waste, and would burn rate limit
        # that some banks meter as low as a few calls a day.
        store = ConnectionStore(tmp_path / "c.json")
        unchanged = ensure_access_token(
            connection(), client_id="x", client_secret="y", store=store
        )
        assert unchanged.access_token == "a"


class TestPullReporting:
    def test_Pull_WhenAccountUnbound_ReportedSoCrossCheckingCanBeSetUp(self):
        # An unbound account still ingests, but silently forgoes cross-source
        # matching, so the omission has to be visible.
        result = PullResult(provider="starling", accounts=1)
        result.notes.append("account abc is unbound")
        assert "unbound" in result.describe()

    def test_Pull_WhenNothingNoteworthy_DescriptionStaysTerse(self):
        assert PullResult(provider="starling", accounts=2).describe() == "starling: 2 account(s)"
