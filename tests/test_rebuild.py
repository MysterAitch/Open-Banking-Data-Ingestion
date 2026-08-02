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


class TestStarlingReplay:
    """The gap that let a live rebuild silently drop every Starling row:
    no test replayed a starling-feed artefact, and provider errors
    (RuntimeError subclasses) aborted the loop instead of skipping the
    one bad artefact."""

    def _feed_artefact(self, account_ref, items):
        import json as _json

        from obdi.providers.starling import artefact_for

        body = _json.dumps({"feedItems": items}).encode("utf-8")
        return artefact_for(
            body,
            account_id=account_ref,
            kind="feed",
            origin="https://api.example.com/feed?changesSince=x",
        )

    def _item(self, uid, minor_units, currency="GBP"):
        return {
            "feedItemUid": uid,
            "amount": {"currency": currency, "minorUnits": minor_units},
            "direction": "OUT",
            "transactionTime": "2026-03-14T09:15:00.000Z",
            "source": "MASTER_CARD",
            "status": "SETTLED",
            "counterPartyName": "Tesco",
            "reference": "TESCO STORES",
        }

    def test_StarlingFeedArtefacts_ReplayIntoTransactions(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                self._feed_artefact(
                    "starling:uid-1",
                    [self._item("f-1", 1499), self._item("f-2", 250)],
                )
            )

            report = rebuild_from_raw(store)

            assert report.transactions == 2
            assert report.problems == []
            rows = store.connection.execute(
                "SELECT account_id, amount_minor FROM transactions ORDER BY amount_minor"
            ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("starling:uid-1", -1499),
            ("starling:uid-1", -250),
        ]

    def test_PoisonArtefact_IsRecordedAndSkipped_TheRestReplays(self, tmp_path):
        """One non-GBP item once aborted the whole rebuild mid-loop -
        after the wipe. It must cost exactly its own artefact, loudly."""
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                self._feed_artefact(
                    "starling:uid-1", [self._item("f-bad", 900, currency="EUR")]
                )
            )
            store.land_artefact(
                self._feed_artefact("starling:uid-2", [self._item("f-good", 1499)])
            )

            report = rebuild_from_raw(store)

            assert report.transactions == 1
            assert len(report.problems) == 1
            assert "EUR" in report.problems[0]
            count = store.connection.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
        assert count == 1
