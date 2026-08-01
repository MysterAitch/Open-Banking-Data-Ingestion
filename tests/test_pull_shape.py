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

    def fake_accounts(_token, **_kwargs):
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


def _run(store, since=None, until=None, only_account=None, psu_ip=None):
    from obdi.connections import ConnectionStore

    return pull_truelayer(
        store,
        _connection(),
        client_id="c",
        client_secret="s",
        connection_store=ConnectionStore(store.path.parent / "c.json"),
        account_map=AccountMap(),
        since=since,
        until=until,
        only_account=only_account,
        psu_ip=psu_ip,
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


class TestAnOffsetWindowProbe:
    """--until lets a probe place the window anywhere in history.

    The discriminating experiment: every probe so far pinned `to` at today, so
    "from may not be older than X" and "the window may not span more than X"
    predict identical outcomes. A window of the known-accepted span placed
    entirely in the past separates them - and if span is the real constraint,
    the whole history is walkable in accepted-size pages.
    """

    def test_WithSinceAndUntil_TheExactWindowIsForwarded(self, calls, tmp_path, monkeypatch):
        seen = {}

        def fake_transactions(_token, account_id, **kwargs):
            seen["since"] = kwargs.get("since")
            seen["until"] = kwargs.get("until")
            return [], b'{"results": [], "status": "Succeeded"}', "from=x&to=y"

        monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", fake_transactions)

        with Store(tmp_path / "s.sqlite3") as store:
            _run(store, since=date(2022, 8, 3), until=date(2024, 8, 2), only_account="acc-1")

        assert seen["since"] == date(2022, 8, 3)
        assert seen["until"] == date(2024, 8, 2)


class TestAttendedAccessIsDeclaredHonestly:
    """The PSU-IP header is a statement of fact, sent only when it is one.

    The regulation's axis is attended versus unattended: four unattended
    accesses per day, unlimited when the customer actively requests. The header
    is the designed mechanism for declaring the latter - so it is attached
    exactly when a human drove the request and their address is known, and
    NEVER by the scheduler, which is genuinely unattended and must not dress
    itself up as anything else.
    """

    def test_Fetch_WithAPsuIp_SendsTheHeader(self):
        import httpx

        from obdi.providers.truelayer import fetch_transactions

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["psu"] = request.headers.get("X-PSU-IP")
            return httpx.Response(200, json={"status": "Succeeded", "results": []})

        fetch_transactions(
            "token", "acc", psu_ip="100.96.178.101",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        assert seen["psu"] == "100.96.178.101"

    def test_Fetch_Unattended_SendsNoHeaderAtAll(self):
        import httpx

        from obdi.providers.truelayer import fetch_transactions

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["psu"] = request.headers.get("X-PSU-IP")
            return httpx.Response(200, json={"status": "Succeeded", "results": []})

        fetch_transactions(
            "token", "acc",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        assert seen["psu"] is None, "unattended must not claim otherwise"

    def test_Pull_ForwardsThePsuIpToEveryFetch(self, calls, tmp_path, monkeypatch):
        seen = {}

        def fake_transactions(_token, account_id, **kwargs):
            seen.setdefault("txn", kwargs.get("psu_ip"))
            return [], b'{"results": [], "status": "Succeeded"}', "from=x&to=y"

        def fake_balance(_token, account_id, **kwargs):
            seen.setdefault("bal", kwargs.get("psu_ip"))
            return [], b"{}"

        monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", fake_transactions)
        monkeypatch.setattr("obdi.pull.truelayer.fetch_balance", fake_balance)

        with Store(tmp_path / "s.sqlite3") as store:
            _run(store, psu_ip="100.96.178.101")

        assert seen == {"txn": "100.96.178.101", "bal": "100.96.178.101"}
