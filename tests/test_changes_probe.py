"""The changesSince experiment must read its evidence correctly.

The probe's whole value is saying no more and no less than one response
proves. A verdict that overclaimed would poison the sync design it
exists to inform; one that underclaimed would leave the 96% growth
problem standing for no reason.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from obdi.probe import (
    ProbeReport,
    amendment_cutoff_suggestions,
    parse_cutoff,
    probe_starling_changes,
)
from obdi.providers import starling
from obdi.store import Store


def _item(uid: str, txn_time: str, updated: str | None = None, minor: int = 100) -> dict:
    item = {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "OUT",
        "transactionTime": txn_time,
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": "Tesco",
        "reference": "REF",
    }
    if updated:
        item["updatedAt"] = updated
    return item


class TestVerdictsAreEvidenceBounded:
    def _run(self, monkeypatch, tmp_path, feed_items):
        from obdi.accounts import AccountMap

        monkeypatch.setattr(
            "obdi.providers.starling.fetch_accounts",
            lambda token: (
                [{"accountUid": "acc-1", "defaultCategory": "cat-1"}],
                b'{"accounts": []}',
            ),
        )
        monkeypatch.setattr(
            "obdi.providers.starling.fetch_feed",
            lambda token, a, c, since_at=None: (
                feed_items,
                json.dumps({"feedItems": feed_items}).encode(),
                "changesSince=x",
            ),
        )
        with Store(tmp_path / "s.sqlite3") as store:
            return probe_starling_changes(
                store,
                "token",
                datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
                account_map=AccountMap(),
            )

    def test_AnItemOlderThanTheCutoff_ProvesUpdateSemantics(
        self, monkeypatch, tmp_path
    ):
        """The decisive case: a transaction from July returned by an
        August cutoff can only be there because its record changed."""
        report = self._run(
            monkeypatch,
            tmp_path,
            [
                _item("old", "2026-07-15T09:00:00.000Z"),
                _item("new", "2026-08-02T09:00:00.000Z"),
            ],
        )

        assert report.before_cutoff == 1
        assert "UPDATE-TIME SEMANTICS DEMONSTRATED" in report.verdict()

    def test_OnlyNewerItems_IsHonestlyInconclusive(self, monkeypatch, tmp_path):
        """Both semantics produce this response; the verdict must say so
        rather than declare transaction-time filtering."""
        report = self._run(
            monkeypatch, tmp_path, [_item("new", "2026-08-02T09:00:00.000Z")]
        )

        assert report.before_cutoff == 0
        assert "INCONCLUSIVE" in report.verdict()
        assert "DEMONSTRATED" not in report.verdict()

    def test_AnEmptyResponse_IsInconclusiveNotNegative(self, monkeypatch, tmp_path):
        report = self._run(monkeypatch, tmp_path, [])

        assert "INCONCLUSIVE" in report.verdict()

    def test_TheResponsesLandAsEvidence(self, monkeypatch, tmp_path):
        from obdi.accounts import AccountMap

        monkeypatch.setattr(
            "obdi.providers.starling.fetch_accounts",
            lambda token: (
                [{"accountUid": "acc-1", "defaultCategory": "cat-1"}],
                b'{"accounts": []}',
            ),
        )
        monkeypatch.setattr(
            "obdi.providers.starling.fetch_feed",
            lambda token, a, c, since_at=None: (
                [],
                b'{"feedItems": []}',
                "changesSince=2026-08-01T12:00:00Z",
            ),
        )
        with Store(tmp_path / "s.sqlite3") as store:
            probe_starling_changes(
                store,
                "token",
                datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
                account_map=AccountMap(),
            )
            landed = store.connection.execute(
                "SELECT origin FROM raw_artefacts WHERE source = 'starling-feed'"
            ).fetchall()

        assert len(landed) == 1
        assert "changesSince=2026-08-01T12:00:00Z" in str(landed[0][0])


class TestSuggestionsComeFromWitnessedAmendments:
    def _land(self, store, items, fetched_suffix):
        store.land_artefact(
            starling.artefact_for(
                json.dumps({"feedItems": items}).encode(),
                account_id="starling:cat-1",
                kind="feed",
                origin=f"https://api.example.com/feed/a/c?x={fetched_suffix}",
            )
        )

    def test_AnAmendedItem_YieldsACutoffBetweenItsTwoTimestamps(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            self._land(
                store,
                [_item("uid-1", "2026-07-10T09:00:00.000Z", minor=9900)],
                1,
            )
            self._land(
                store,
                [
                    _item(
                        "uid-1",
                        "2026-07-10T09:00:00.000Z",
                        updated="2026-07-13T02:00:00.000Z",
                        minor=2500,
                    )
                ],
                2,
            )
            suggestions = amendment_cutoff_suggestions(store)

        assert len(suggestions) == 1
        cutoff = suggestions[0].cutoff
        assert "2026-07-10T09:00:00" < cutoff < "2026-07-13T02:00:00"
        assert suggestions[0].item_hint == "uid-1"[:8]

    def test_AnUnchangedItem_SuggestsNothing(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            same = [_item("uid-1", "2026-07-10T09:00:00.000Z")]
            self._land(store, same, 1)
            self._land(store, same, 2)
            suggestions = amendment_cutoff_suggestions(store)

        assert suggestions == []


class TestCutoffParsing:
    def test_CommonHumanForms_AllParse(self):
        for raw in (
            "2026-08-03T09:00:00Z",
            "2026-08-03T09:00:00",
            "2026-08-03",
        ):
            parsed = parse_cutoff(raw)
            assert parsed is not None, raw
            assert parsed.tzinfo is not None

    def test_Nonsense_ReturnsNoneRatherThanGuessing(self):
        assert parse_cutoff("last tuesday") is None
        assert parse_cutoff("") is None


class TestTheProbePage:
    def test_Post_RendersTheVerdictAndTheNumbers(self, tmp_path):
        import threading
        from http.server import HTTPServer

        import httpx

        from obdi.connections import ConnectionStore
        from obdi.probe import ProbeAccount
        from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

        report = ProbeReport(cutoff="2026-08-01T12:00:00Z")
        report.accounts.append(
            ProbeAccount(
                label="starling-personal",
                items=3,
                before_cutoff=1,
                oldest_transaction_time="2026-07-15T09:00:00",
                newest_transaction_time="2026-08-02T09:00:00",
            )
        )
        config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            starling_probe=lambda cutoff: report,
        )
        handler = type(
            "H",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            response = httpx.post(
                f"http://127.0.0.1:{httpd.server_port}/starling-probe",
                data={"cutoff": "2026-08-01T12:00:00Z"},
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 200
        assert "UPDATE-TIME SEMANTICS DEMONSTRATED" in response.text
        assert "starling-personal" in response.text
        assert "decisive" in response.text
