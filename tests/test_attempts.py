"""Every ask made of a provider leaves a row - the quota ledger.

Refusals used to exist only in container stderr, which vanishes with the
container. Yet the refused attempts are the valuable ones: what was asked,
when, and the provider's exact code are the raw material of the quota model
("how many calls hit this account in 24 hours?") and of the ceiling-probe
protocol ("which windows has this bank already said no to?").
"""

from __future__ import annotations

import pytest

from obdi.accounts import AccountMap
from obdi.connections import Connection, ConnectionStore
from obdi.providers.truelayer import TrueLayerError
from obdi.pull import pull_truelayer
from obdi.store import Store


def _connection():
    return Connection(
        connection_id="halifax",
        provider="halifax",
        access_token="a",
        refresh_token="r",
        access_expires_at="2099-01-01T00:00:00+00:00",
        consent_expires_at="2099-01-01T00:00:00+00:00",
        scopes="",
    )


@pytest.fixture
def provider(monkeypatch):
    def fake_accounts(_token, **_kwargs):
        return (
            [{"account_id": "acc-1", "display_name": "Current", "account_type": "T"}],
            b"{}",
        )

    def fake_balance(_token, _account_id, **_kwargs):
        return [], b"{}"

    monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", fake_accounts)
    monkeypatch.setattr("obdi.pull.truelayer.fetch_balance", fake_balance)
    return monkeypatch


class TestAttemptsAreRecorded:
    def test_Pull_WhenTheProviderRefuses_TheRefusalRowCarriesTheCode(
        self, tmp_path, provider
    ):
        def refuse(_token, _account_id, **_kwargs):
            raise TrueLayerError(
                "Transaction fetch failed (HTTP 403): sca_exceeded",
                status=403,
                code="sca_exceeded",
                description="SCA exemption has expired.",
            )

        provider.setattr("obdi.pull.truelayer.fetch_transactions", refuse)

        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(TrueLayerError):
                pull_truelayer(
                    store,
                    _connection(),
                    client_id="i",
                    client_secret="s",
                    connection_store=ConnectionStore(tmp_path / "c.json"),
                    account_map=AccountMap(),
                    trigger="web-extend",
                )
            rows = store.attempts()

        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "refused"
        assert row["http_status"] == 403
        assert row["error_code"] == "sca_exceeded"
        assert row["connection_id"] == "halifax"
        assert "web-extend" in str(row["request_meta"])

    def test_Pull_WhenItLands_TheLedgerSaysWhatWasActuallyAsked(
        self, tmp_path, provider
    ):
        def succeed(_token, _account_id, **_kwargs):
            return [], b'{"results": [], "status": "Succeeded"}', "from=2026-05-01&to=2026-08-02"

        provider.setattr("obdi.pull.truelayer.fetch_transactions", succeed)

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
                trigger="scheduled",
            )
            rows = store.attempts()

        # Booked and pending both asked on a routine pull: two ledger rows.
        assert {row["outcome"] for row in rows} == {"landed"}
        assert len(rows) == 2
        assert any(row["asked"] == "from=2026-05-01&to=2026-08-02" for row in rows)
        assert {row["source"] for row in rows} == {
            "truelayer-booked",
            "truelayer-pending",
        }


class TestAttemptsCommand:
    def test_Attempts_PrintsRefusalsWithTheirCodes(self, tmp_path, capsys):
        from obdi.cli import _attempts

        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            store.record_attempt(
                source="truelayer-booked",
                connection_id="halifax",
                account_ref="halifax-current",
                asked="since=2022-08-03 until=2024-08-03",
                request_meta='{"trigger": "web-extend"}',
                outcome="refused",
                http_status=403,
                error_code="sca_exceeded",
                detail="Transaction fetch failed",
            )

        assert _attempts(db) == 0
        out = capsys.readouterr().out
        assert "REFUSED 403 sca_exceeded" in out
        assert "[web-extend]" in out
        assert "halifax/halifax-current" in out

    def test_Attempts_WhenLedgerEmpty_SaysSo(self, tmp_path, capsys):
        from obdi.cli import _attempts

        assert _attempts(tmp_path / "s.sqlite3") == 0
        assert "no attempts recorded yet" in capsys.readouterr().out


class TestScaWindowLengthIsLearnt:
    def test_Refusal_NamingTheWindow_RecordsItAsAProviderFact(
        self, tmp_path, provider
    ):
        def refuse(_token, _account_id, **_kwargs):
            raise TrueLayerError(
                "Transaction fetch failed (HTTP 403): sca_exceeded",
                status=403,
                code="sca_exceeded",
                description="SCA exemption has expired.",
                provider_details=(
                    "403 access_denied: should be accessed within 5 minutes "
                    "of PSU Authentication"
                ),
            )

        provider.setattr("obdi.pull.truelayer.fetch_transactions", refuse)

        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(TrueLayerError):
                pull_truelayer(
                    store,
                    _connection(),
                    client_id="i",
                    client_secret="s",
                    connection_store=ConnectionStore(tmp_path / "c.json"),
                    account_map=AccountMap(),
                )

            assert store.provider_fact("truelayer", "halifax", "sca_window_minutes") == "5"
