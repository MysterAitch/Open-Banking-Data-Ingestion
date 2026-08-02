"""The founding promise made executable: derived layers regenerate from raw.

The decisive property is idempotence against the live pipeline: a store
built by pulls and imports, wiped and rebuilt, must resolve to the same
transactions - same entities, same counts - because both routes run the
same rules over the same bytes in the same order.
"""

from __future__ import annotations

import json

from obdi.accounts import AccountMap
from obdi.connections import Connection, ConnectionStore
from obdi.pull import pull_truelayer
from obdi.rebuild import rebuild_from_raw
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


def _fake_provider(monkeypatch, records):
    def fake_accounts(_token, **_kwargs):
        return (
            [{"account_id": "acc-1", "display_name": "Current", "account_type": "T"}],
            b'{"results": []}',
        )

    def fake_transactions(_token, _account_id, **kwargs):
        if kwargs.get("pending"):
            return [], b'{"results": [], "status": "Succeeded"}', "pending"
        body = json.dumps({"results": records, "status": "Succeeded"}).encode()
        return records, body, "from=2026-05-04&to=2026-08-02"

    monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", fake_accounts)
    monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", fake_transactions)
    monkeypatch.setattr("obdi.pull.truelayer.fetch_balance", lambda *a, **k: ([], b"{}"))


class TestRebuildFromRaw:
    def test_Rebuild_ReproducesThePipelineExactly(self, tmp_path, monkeypatch):
        records = [
            {
                "transaction_id": "t-1",
                "normalised_provider_transaction_id": "txn-aaa",
                "timestamp": "2026-07-01T00:00:00Z",
                "amount": -12.34,
                "currency": "GBP",
                "description": "COFFEE SHOP",
            },
            {
                "transaction_id": "t-2",
                "normalised_provider_transaction_id": "txn-bbb",
                "timestamp": "2026-07-02T00:00:00Z",
                "amount": 2500.00,
                "currency": "GBP",
                "description": "SALARY",
            },
        ]
        _fake_provider(monkeypatch, records)

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
            )
            query = (
                "SELECT account_id, amount_minor, value_date, description "
                "FROM transactions ORDER BY value_date, amount_minor"
            )
            before = store.connection.execute(query).fetchall()
            assert len(before) == 2

            report = rebuild_from_raw(store)

            after = store.connection.execute(query).fetchall()

        # Every observable fact about every payment reproduces exactly.
        # Entity ids are deliberately NOT compared: they are minted at first
        # sighting and a rebuild re-mints them - which is why downstream
        # consumers must key on content, never on stored entity ids.
        assert [tuple(r) for r in after] == [tuple(r) for r in before]
        assert report.transactions == 2
        assert report.problems == []

    def test_Rebuild_KeepsEvidenceAndLearntFacts(self, tmp_path, monkeypatch):
        _fake_provider(monkeypatch, [])

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
            )
            store.record_provider_fact("truelayer", "halifax", "sca_window_minutes", "5")
            artefacts_before = store.counts()["raw_artefacts"]
            attempts_before = len(store.attempts())

            rebuild_from_raw(store)

            assert store.counts()["raw_artefacts"] == artefacts_before
            assert len(store.attempts()) == attempts_before
            assert (
                store.provider_fact("truelayer", "halifax", "sca_window_minutes") == "5"
            )

    def test_Rebuild_IsIdempotent(self, tmp_path, monkeypatch):
        _fake_provider(
            monkeypatch,
            [
                {
                    "transaction_id": "t-1",
                    "normalised_provider_transaction_id": "txn-ccc",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "amount": -5.00,
                    "currency": "GBP",
                    "description": "BUS",
                }
            ],
        )

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
            )
            first = rebuild_from_raw(store)
            second = rebuild_from_raw(store)
            count = store.counts()["transactions"]

        assert first.transactions == second.transactions == 1
        assert count == 1
