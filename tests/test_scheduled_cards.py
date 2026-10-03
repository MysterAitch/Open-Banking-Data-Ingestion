"""The six-hourly scheduler asks about cards, not only current accounts.

Cards are a separate TrueLayer endpoint family, and they were once fetched on
attended (deep) pulls only. Three credit cards then landed nothing for about
sixty days while current accounts refreshed every cycle, because nothing
scheduled ever asked. These scenarios pin the scheduled path: cards follow the
same tiered windows as accounts, every ask is ledgered, and one card's refusal
never stops the rest of the connection.

The provider is faked at the same seams the other pull tests use.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from obdi.accounts import AccountMap
from obdi.connections import Connection, ConnectionStore
from obdi.providers.truelayer import TrueLayerError
from obdi.pull import pull_truelayer
from obdi.store import Store

CARD_RECORD = (
    '{"amount": 9.99, "currency": "GBP", "description": "COFFEE", '
    '"timestamp": "2026-07-01T00:00:00Z", '
    '"transaction_type": "DEBIT", "transaction_id": "c-1", '
    '"normalised_provider_transaction_id": "txn-c-1", '
    '"provider_transaction_id": "c-1"}'
)


def _connection() -> Connection:
    return Connection(
        connection_id="halifax",
        provider="halifax",
        access_token="a",
        refresh_token="r",
        access_expires_at="2099-01-01T00:00:00+00:00",
        consent_expires_at="2099-01-01T00:00:00+00:00",
        scopes="",
    )


class Provider:
    """What the fake provider was asked, and how it answers."""

    def __init__(
        self,
        monkeypatch,
        *,
        cards,
        card_txns=None,
        card_list_error=None,
        account_list_error=None,
    ) -> None:
        self.card_list_calls = 0
        self.card_windows: list[tuple[str, dict]] = []
        self.account_asks: list[str] = []
        self._card_txns = card_txns or {}
        self._cards = cards
        self._card_list_error = card_list_error
        self._account_list_error = account_list_error

        monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", self._list_accounts)
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_balance", lambda *a, **k: ([], b"{}")
        )
        monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", self._transactions)
        monkeypatch.setattr("obdi.pull.truelayer.fetch_cards", self._list_cards)
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_card_transactions", self._card_transactions
        )

    def _list_accounts(self, _token, **_kwargs):
        if self._account_list_error is not None:
            raise self._account_list_error
        return (
            [{"account_id": "acc-1", "display_name": "Current", "account_type": "T"}],
            b'{"results": []}',
        )

    def _transactions(self, _token, account_id, **kwargs):
        self.account_asks.append(account_id)
        return [], b'{"results": [], "status": "Succeeded"}', "from=2026-05-04&to=2026-08-02"

    def _list_cards(self, _token, **_kwargs):
        self.card_list_calls += 1
        if self._card_list_error is not None:
            raise self._card_list_error
        body = json.dumps({"results": [{"account_id": card} for card in self._cards]})
        return [{"account_id": card} for card in self._cards], body.encode()

    def _card_transactions(self, _token, card_id, **kwargs):
        self.card_windows.append((card_id, kwargs))
        outcome = self._card_txns.get(card_id, ('{"results": []}', "from=x&to=y"))
        if isinstance(outcome, Exception):
            raise outcome
        body, asked = outcome
        return body.encode(), asked


def _pull(tmp_path, store, **kwargs):
    kwargs.setdefault("trigger", "scheduled")
    return pull_truelayer(
        store,
        _connection(),
        client_id="i",
        client_secret="s",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        account_map=AccountMap(),
        **kwargs,
    )


def _refusal(code="sca_exceeded", status=403) -> TrueLayerError:
    return TrueLayerError(
        f"Card transaction fetch failed (HTTP {status}): {code}",
        status=status,
        code=code,
        description="refused",
    )


def _card_attempts(store):
    return [r for r in store.attempts() if r["source"] == "truelayer-card-booked"]


class TestAScheduledPullAsksAboutCards:
    def test_ScheduledPull_FetchesEveryCard_AndParsesItsRows(self, tmp_path, monkeypatch):
        provider = Provider(
            monkeypatch,
            cards=["card-1"],
            card_txns={"card-1": ('{"results": [' + CARD_RECORD + "]}", "from=x&to=y")},
        )

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store)
            amount = store.connection.execute(
                "SELECT amount_minor, account_id FROM transactions"
            ).fetchone()
            artefact_kinds = {
                row[0]
                for row in store.connection.execute(
                    "SELECT DISTINCT source FROM raw_artefacts"
                ).fetchall()
            }

        assert provider.card_list_calls == 1
        assert [card for card, _ in provider.card_windows] == ["card-1"]
        assert tuple(amount) == (-999, "truelayer:card-1")
        assert {"truelayer-cards", "truelayer-card-booked"} <= artefact_kinds

    def test_ScheduledPull_AsksCardsThroughTheSameTiersAsAccounts(
        self, tmp_path, monkeypatch
    ):
        provider = Provider(monkeypatch, cards=["card-1"])

        with Store(tmp_path / "s.sqlite3") as store:
            first = _pull(tmp_path, store)
            second = _pull(tmp_path, store)

        # No stamps yet means the widest tier, then the next same-day cycle
        # drops to the frequent window - exactly as the current account does.
        assert [kwargs["days"] for _, kwargs in provider.card_windows] == [56, 3]
        assert any("tier weekly" in note for note in first.notes)
        assert any("tier frequent" in note for note in second.notes)

    def test_ScheduledPull_CardAttemptsCarryTheScheduledTrigger(self, tmp_path, monkeypatch):
        Provider(monkeypatch, cards=["card-1"])

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store)
            attempts = _card_attempts(store)

        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "landed"
        # The alert layer reads only the scheduled conversation by this marker.
        assert json.loads(str(attempts[0]["request_meta"]))["trigger"] == "scheduled"

    def test_ScheduledPull_ACardWithNoNewRows_LandsEvidenceAndStoresNothing(
        self, tmp_path, monkeypatch
    ):
        Provider(monkeypatch, cards=["card-1"])

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store)
            attempts = _card_attempts(store)
            transactions = store.counts()["transactions"]
            card_artefacts = store.connection.execute(
                "SELECT COUNT(*) FROM raw_artefacts WHERE source = 'truelayer-card-booked'"
            ).fetchone()[0]

        assert [row["outcome"] for row in attempts] == ["landed"]
        assert card_artefacts == 1, "an empty window is still evidence the card was asked"
        assert transactions == 0


class TestOneCardRefusingDoesNotStopTheRest:
    def test_TwoCardsOneRefuses_TheOtherLands_AndTheAccountsAreUnaffected(
        self, tmp_path, monkeypatch
    ):
        provider = Provider(
            monkeypatch,
            cards=["card-1", "card-2"],
            card_txns={
                "card-1": ("{\"results\": [" + CARD_RECORD + "]}", "from=x&to=y"),
                "card-2": _refusal(),
            },
        )

        with Store(tmp_path / "s.sqlite3") as store:
            result = _pull(tmp_path, store)
            attempts = store.attempts()
            transactions = store.counts()["transactions"]

        card_rows = [r for r in attempts if r["source"] == "truelayer-card-booked"]
        assert {(r["account_ref"], r["outcome"]) for r in card_rows} == {
            ("truelayer:card-1", "landed"),
            ("truelayer:card-2", "refused"),
        }
        refused = next(r for r in card_rows if r["outcome"] == "refused")
        assert (refused["http_status"], refused["error_code"]) == (403, "sca_exceeded")
        assert transactions == 1
        assert provider.account_asks == ["acc-1", "acc-1"], "booked and pending"
        assert any("card card-2" in note for note in result.notes)
        account_attempts = [r for r in attempts if r["account_ref"] == "truelayer:acc-1"]
        assert {r["outcome"] for r in account_attempts} == {"landed"}

    def test_ARefusedCard_DoesNotHoldTheConnectionsTierHostage(self, tmp_path, monkeypatch):
        # A consent without card scope would otherwise pin every cycle to the
        # widest window and re-ask accounts for weeks of history, forever.
        Provider(monkeypatch, cards=["card-1"], card_txns={"card-1": _refusal()})

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store)
            stamp = store.provider_fact("truelayer", "halifax", "tier-last-weekly")

        assert stamp is not None

    def test_ACardWhoseRowCannotBeVerified_LeavesItsRawEvidenceBeforeFailingLoudly(
        self, tmp_path, monkeypatch
    ):
        unverifiable = CARD_RECORD.replace('"DEBIT"', '"FEE"')
        Provider(
            monkeypatch,
            cards=["card-1"],
            card_txns={"card-1": ("{\"results\": [" + unverifiable + "]}", "from=x&to=y")},
        )

        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(TrueLayerError, match="cannot be verified"):
                _pull(tmp_path, store)
            landed = store.connection.execute(
                "SELECT COUNT(*) FROM raw_artefacts WHERE source = 'truelayer-card-booked'"
            ).fetchone()[0]
            transactions = store.counts()["transactions"]

        assert landed == 1, "evidence is landed before anything is parsed"
        assert transactions == 0, "money that cannot be verified is never stored"


class TestConnectionsWithoutWorkingCards:
    def test_ScheduledPull_WhenTheConnectionHasNoCards_AsksNoCardWindows(
        self, tmp_path, monkeypatch
    ):
        provider = Provider(monkeypatch, cards=[])

        with Store(tmp_path / "s.sqlite3") as store:
            result = _pull(tmp_path, store)
            attempts = _card_attempts(store)

        assert provider.card_list_calls == 1
        assert provider.card_windows == []
        assert attempts == []
        assert result.accounts == 1

    def test_ScheduledPull_WhenTheCardListFails_AccountsStillLand_AndTheFailureIsNoted(
        self, tmp_path, monkeypatch
    ):
        provider = Provider(
            monkeypatch, cards=["card-1"], card_list_error=_refusal("forbidden")
        )

        with Store(tmp_path / "s.sqlite3") as store:
            result = _pull(tmp_path, store)
            account_landed = [
                r
                for r in store.attempts()
                if r["account_ref"] == "truelayer:acc-1" and r["outcome"] == "landed"
            ]
            stamp = store.provider_fact("truelayer", "halifax", "tier-last-weekly")

        assert provider.card_windows == []
        assert len(account_landed) == 2
        assert any(note.startswith("card list:") for note in result.notes)
        assert stamp is not None


class TestAProviderThatServesCardsAndNoCurrentAccounts:
    """A card issuer answers the account list with "not supported", not with an
    empty list: the real answer was HTTP 501 `endpoint_not_supported`, "Feature
    not supported by the provider"."""

    @staticmethod
    def _not_supported() -> TrueLayerError:
        return TrueLayerError(
            "Account fetch failed (HTTP 501): endpoint_not_supported - "
            "Feature not supported by the provider",
            status=501,
            code="endpoint_not_supported",
            description="Feature not supported by the provider",
        )

    def test_ScheduledPull_WhenTheAccountListIsNotSupported_StillFetchesTheCards(
        self, tmp_path, monkeypatch
    ):
        provider = Provider(
            monkeypatch,
            cards=["card-1"],
            card_txns={"card-1": ('{"results": [' + CARD_RECORD + "]}", "from=x&to=y")},
            account_list_error=self._not_supported(),
        )

        with Store(tmp_path / "s.sqlite3") as store:
            result = _pull(tmp_path, store)
            landed = [r for r in _card_attempts(store) if r["outcome"] == "landed"]
            stamp = store.provider_fact("truelayer", "halifax", "tier-last-weekly")

        assert result.accounts == 0
        assert [card for card, _ in provider.card_windows] == ["card-1"]
        assert len(landed) == 1
        assert provider.account_asks == []
        assert any("no current accounts" in note for note in result.notes)
        assert stamp is not None

    def test_DeepPull_WhenTheAccountListIsNotSupported_StillFetchesTheCards(
        self, tmp_path, monkeypatch
    ):
        provider = Provider(
            monkeypatch, cards=["card-1"], account_list_error=self._not_supported()
        )

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store, deep=True, trigger="post-auth-backfill")

        assert [card for card, _ in provider.card_windows] == ["card-1"]

    def test_ScheduledPull_WhenTheAccountListIsRefusedForAnotherReason_FailsAsBefore(
        self, tmp_path, monkeypatch
    ):
        # A refusal that could change - consent gone, the exemption expired -
        # is not "this provider has no accounts", and must stay loud.
        provider = Provider(
            monkeypatch, cards=["card-1"], account_list_error=_refusal("sca_exceeded")
        )

        with Store(tmp_path / "s.sqlite3") as store, pytest.raises(TrueLayerError):
            _pull(tmp_path, store)

        assert provider.card_windows == []


class TestOnlyRoutineScheduledCyclesAskForCards:
    def test_AnExplicitWindowProbe_NeverSpendsACallOnTheCardList(self, tmp_path, monkeypatch):
        provider = Provider(monkeypatch, cards=["card-1"])

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store, since=datetime.now(UTC).date() - timedelta(days=30))

        assert provider.card_list_calls == 0
        assert provider.card_windows == []

    def test_APullForOneNamedAccount_DoesNotFanOutToEveryCard(self, tmp_path, monkeypatch):
        provider = Provider(monkeypatch, cards=["card-1"])

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store, only_account="acc-1")

        assert provider.card_list_calls == 0

    def test_AnAttendedDeepPull_StillFetchesCards_AtTheRoutineWindow(
        self, tmp_path, monkeypatch
    ):
        provider = Provider(monkeypatch, cards=["card-1"])

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store, deep=True, trigger="attended", psu_ip="203.0.113.7")

        assert provider.card_list_calls == 1
        assert [kwargs["days"] for _, kwargs in provider.card_windows] == [90]

    def test_AnExtendWindowForACardRef_IsFetchedOnceNotTwice(self, tmp_path, monkeypatch):
        provider = Provider(monkeypatch, cards=["card-1"])

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(
                tmp_path,
                store,
                since=datetime.now(UTC).date() - timedelta(days=400),
                until=datetime.now(UTC).date() - timedelta(days=300),
                only_account="card-1",
            )

        assert provider.card_list_calls == 0
        assert [card for card, _ in provider.card_windows] == ["card-1"]
