"""What a pull actually asks the provider, per invocation shape.

An explicit --since is an instruction to probe one window, and a probe must
cost what it measures: one call. Fetching balances and pending items for every
account on the connection alongside it turns a single measurement into nine
calls against a quota of four - so a window probe would exhaust the day's
allowance for accounts it was not even asking about.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.accounts import AccountMap
from obdi.connections import Connection
from obdi.pull import pull_truelayer
from obdi.store import Store


@pytest.fixture
def calls(monkeypatch):
    made: dict[str, list[str]] = {"accounts": [], "balance": [], "transactions": []}

    def fake_accounts(_token):
        made["accounts"].append("x")
        return (
            [{"account_id": "acc-1", "display_name": "Current", "account_type": "TRANSACTION"},
             {"account_id": "acc-2", "display_name": "Saver", "account_type": "SAVINGS"}],
            b"{}",
        )

    def fake_transactions(_token, account_id, **kwargs):
        made["transactions"].append(
            f"{account_id}:{'pending' if kwargs.get('pending') else 'booked'}"
        )
        return [], b'{"results": [], "status": "Succeeded"}', "from=x&to=y"

    def fake_balance(_token, account_id, **_kwargs):
        made["balance"].append(account_id)
        return [], b"{}"

    monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", fake_accounts)
    monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", fake_transactions)
    monkeypatch.setattr("obdi.pull.truelayer.fetch_balance", fake_balance)
    return made


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


def _run(store, since=None, only_account=None):
    from obdi.connections import ConnectionStore

    return pull_truelayer(
        store,
        _connection(),
        client_id="c",
        client_secret="s",
        connection_store=ConnectionStore(store.path.parent / "c.json"),
        account_map=AccountMap(),
        since=since,
        only_account=only_account,
    )


class TestARoutinePull:
    def test_FetchesBalancePendingAndBookedForEveryAccount(self, calls, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _run(store)

        assert sorted(calls["balance"]) == ["acc-1", "acc-2"]
        assert sorted(calls["transactions"]) == [
            "acc-1:booked", "acc-1:pending", "acc-2:booked", "acc-2:pending",
        ]


class TestAWindowProbe:
    def test_WithSince_FetchesBookedOnly_NoBalanceNoPending(self, calls, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _run(store, since=date(2020, 8, 5))

        assert calls["balance"] == [], "a probe must not spend quota on balances"
        assert all(c.endswith(":booked") for c in calls["transactions"])

    def test_WithOnlyAccount_TouchesNothingElse(self, calls, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _run(store, since=date(2020, 8, 5), only_account="acc-1")

        assert calls["transactions"] == ["acc-1:booked"], "one call, the one measured"
