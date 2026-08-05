"""Tiered windows: coverage kept, volume dropped, quota untouched.

TrueLayer filters on transaction date, so amendments to old records
arrive only through windows covering their dates. The tiers preserve
that coverage - each wider tier deliberately re-covers the narrower -
while replacing always-90-days with mostly-3-days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from obdi import tiers
from obdi.store import Store


class TestTierSelection:
    def test_AFirstRun_TakesTheWidestWindow(self, tmp_path):
        """No stamps means nothing proven covered - over-fetch once."""
        with Store(tmp_path / "s.sqlite3") as store:
            choice = tiers.select(store, "truelayer", "halifax")

        assert choice.label == "weekly"
        assert choice.days == 56

    def test_AfterAWeeklyRun_SameDayCyclesAreFrequent(self, tmp_path):
        """The weekly stamp also satisfies the daily cadence beneath it,
        because its window covers the daily window's job."""
        with Store(tmp_path / "s.sqlite3") as store:
            first = tiers.select(store, "truelayer", "halifax")
            tiers.stamp(store, "truelayer", "halifax", first)
            second = tiers.select(store, "truelayer", "halifax")

        assert second.label == "frequent"
        assert second.days == 3

    def test_ADayLater_TheDailyTierIsDue(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            choice = tiers.select(store, "truelayer", "halifax")
            tiers.stamp(store, "truelayer", "halifax", choice)
            # Warp the stamps: the weekly ran 2 days ago, so only the
            # daily cadence has come round.
            two_days_ago = (
                (datetime.now(UTC) - timedelta(days=2))
                .isoformat()
                .replace("+00:00", "Z")
            )
            store.record_provider_fact(
                "truelayer", "halifax", "tier-last-weekly", two_days_ago
            )
            store.record_provider_fact(
                "truelayer", "halifax", "tier-last-daily", two_days_ago
            )
            due = tiers.select(store, "truelayer", "halifax")

        assert due.label == "daily"
        assert due.days == 7

    def test_AWeekLater_TheWeeklyTierOutranksTheDaily(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            eight_days_ago = (
                (datetime.now(UTC) - timedelta(days=8))
                .isoformat()
                .replace("+00:00", "Z")
            )
            store.record_provider_fact(
                "truelayer", "halifax", "tier-last-weekly", eight_days_ago
            )
            store.record_provider_fact(
                "truelayer", "halifax", "tier-last-daily", eight_days_ago
            )
            due = tiers.select(store, "truelayer", "halifax")

        assert due.label == "weekly"

    def test_WindowsAreConfigurable_PerTier(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBDI_TL_WEEKLY_DAYS", "90")
        with Store(tmp_path / "s.sqlite3") as store:
            choice = tiers.select(store, "truelayer", "halifax")

        assert choice.days == 90

    def test_TwoConnections_KeepIndependentSchedules(self, tmp_path):
        """Halifax sweeping must not satisfy Nationwide's cadence."""
        with Store(tmp_path / "s.sqlite3") as store:
            first = tiers.select(store, "truelayer", "halifax")
            tiers.stamp(store, "truelayer", "halifax", first)
            other = tiers.select(store, "truelayer", "nationwide")

        assert other.label == "weekly"


class TestTheTierReachesTheProvider:
    def test_ARoutinePull_AsksTheTierWindow_AndNotesIt(
        self, tmp_path, monkeypatch
    ):
        """End to end through pull_truelayer: the tier picks the window,
        the ask carries it, the notes say so, completion stamps it."""
        from datetime import date

        from obdi.accounts import AccountMap
        from obdi.connections import Connection, ConnectionStore
        from obdi.pull import pull_truelayer

        asked_since: list[date | None] = []

        def fake_transactions(_token, _account_id, **kwargs):
            asked_since.append(kwargs.get("since"))
            if kwargs.get("pending"):
                return [], b'{"results": [], "status": "Succeeded"}', "pending"
            return [], b'{"results": [], "status": "Succeeded"}', "from=x&to=y"

        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_accounts",
            lambda _token, **_kw: (
                [{"account_id": "acc-1", "display_name": "C", "account_type": "T"}],
                b'{"results": []}',
            ),
        )
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_transactions", fake_transactions
        )
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_balance", lambda *a, **k: ([], b"{}")
        )
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_cards", lambda *a, **k: ([], b'{"results": []}')
        )
        monkeypatch.setattr(
            "obdi.pull.ensure_access_token", lambda c, **_kw: c
        )

        connection = Connection(
            connection_id="halifax",
            provider="halifax",
            access_token="a",
            refresh_token="r",
            access_expires_at="2099-01-01T00:00:00+00:00",
            consent_expires_at="2099-01-01T00:00:00+00:00",
            scopes="",
        )
        connections = ConnectionStore(tmp_path / "c.json")
        connections.put(connection)

        with Store(tmp_path / "s.sqlite3") as store:
            result = pull_truelayer(
                store,
                connection,
                client_id="x",
                client_secret="y",
                connection_store=connections,
                account_map=AccountMap(),
            )
            second = pull_truelayer(
                store,
                connection,
                client_id="x",
                client_secret="y",
                connection_store=connections,
                account_map=AccountMap(),
            )

        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        today = _dt.now(_UTC).date()
        assert asked_since[0] == today - timedelta(days=56), (
            "first ever routine pull takes the widest window"
        )
        assert any("tier weekly" in note for note in result.notes)
        assert asked_since[-1] == today - timedelta(days=3), (
            "the next same-day cycle drops to the frequent window"
        )
        assert any("tier frequent" in note for note in second.notes)

    def test_AnExplicitWindow_BypassesTiering(self, tmp_path, monkeypatch):
        from datetime import date

        from obdi.accounts import AccountMap
        from obdi.connections import Connection, ConnectionStore
        from obdi.pull import pull_truelayer

        asked_since: list[date | None] = []
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_accounts",
            lambda _token, **_kw: (
                [{"account_id": "acc-1", "display_name": "C", "account_type": "T"}],
                b'{"results": []}',
            ),
        )

        def fake_transactions(_token, _account_id, **kwargs):
            asked_since.append(kwargs.get("since"))
            return [], b'{"results": [], "status": "Succeeded"}', "from=x&to=y"

        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_transactions", fake_transactions
        )
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_balance", lambda *a, **k: ([], b"{}")
        )
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_cards", lambda *a, **k: ([], b'{"results": []}')
        )
        monkeypatch.setattr("obdi.pull.ensure_access_token", lambda c, **_kw: c)

        connection = Connection(
            connection_id="halifax",
            provider="halifax",
            access_token="a",
            refresh_token="r",
            access_expires_at="2099-01-01T00:00:00+00:00",
            consent_expires_at="2099-01-01T00:00:00+00:00",
            scopes="",
        )
        connections = ConnectionStore(tmp_path / "c.json")
        connections.put(connection)

        deliberate = date(2026, 1, 1)
        with Store(tmp_path / "s.sqlite3") as store:
            result = pull_truelayer(
                store,
                connection,
                client_id="x",
                client_secret="y",
                connection_store=connections,
                account_map=AccountMap(),
                since=deliberate,
            )
            planted = store.provider_fact("truelayer", "halifax", "tier-last-weekly")

        assert asked_since == [deliberate]
        assert not any("tier " in note for note in result.notes)
        assert planted is None, "a deliberate ask must not stamp a tier"
