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
        # Each landed row is JOINED to its evidence: the digest is recorded
        # and resolves to the landed artefact's rowid for linking.
        assert all(row["artefact_digest"] for row in rows)
        assert all(row["artefact_id"] for row in rows)
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


class TestRebindCarriesEveryLayer:
    """A bind must not orphan the artefacts or the quota ledger.

    Under the label normalisation evidence keeps its provider-qualified
    label FOREVER - a bind edits the map, and the anchors see the evidence
    through the alias set. What the old contract achieved by moving labels
    (the probed-back-to anchor surviving a rename) is now achieved by
    translation, and this test asserts the outcome, not the mechanism.
    """

    def test_AfterBinding_TheAnchorSeesQualifiedEvidence(
        self, tmp_path, monkeypatch
    ):
        import json as _json
        from datetime import date

        from obdi.cli import _earliest_asked
        from obdi.providers.truelayer import artefact_for

        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            _json.dumps(
                {
                    "bindings": [
                        {
                            "canonical_id": "halifax-current",
                            "source": "truelayer",
                            "provider_account_id": "e9f8",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_path))
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                artefact_for(
                    b'{"results": [], "status": "Succeeded"}',
                    account_id="e9f8",
                    kind="booked",
                    requested="from=2020-08-01&to=2020-08-03",
                )
            )

            assert _earliest_asked(store, "halifax-current") == date(2020, 8, 1)

    def test_Rebind_StillMovesLegacyCanonicalLabels(self, tmp_path):
        """Evidence from before the normalisation carries canonical labels;
        a rename moves those the old way so no vintage is orphaned."""
        with Store(tmp_path / "s.sqlite3") as store:
            store.record_attempt(
                source="truelayer-booked",
                connection_id="halifax",
                account_ref="halifax-old-name",
                asked="from=2020-08-01&to=2020-08-03",
                request_meta="{}",
                outcome="landed",
                http_status=200,
            )

            store.rebind_account("halifax-old-name", "halifax-current")

            attempt_refs = [row["account_ref"] for row in store.attempts()]

        assert attempt_refs == ["halifax-current"]


class TestStarlingInstrumentationParity:
    """Every Starling fetch lands as evidence and hits the ledger - the
    TrueLayer lessons applied BEFORE the first real pull, not after."""

    def test_Pull_LandsEveryPayloadKind_AndLedgersTheFeedAsk(
        self, tmp_path, monkeypatch
    ):
        from obdi.providers.starling import Category
        from obdi.pull import pull_starling

        def fake_accounts(_token, **_kwargs):
            return (
                [{"accountUid": "acc-1", "defaultCategory": "cat-main", "name": "main"}],
                b'{"accounts": []}',
            )

        def fake_categories(_token, _account_uid, **_kwargs):
            return (
                [
                    Category(uid="cat-main", name="main", is_space=False),
                    Category(uid="space-1", name="holiday", is_space=True),
                ],
                b'{"savingsGoals": []}',
            )

        def fake_balance(_token, _account_uid, **_kwargs):
            return b'{"effectiveBalance": {}}'

        def fake_feed(_token, _account_uid, category_uid, **_kwargs):
            return (
                [],
                b'{"feedItems": []}',
                "changesSince=2016-08-04T00:00:00Z",
            )

        monkeypatch.setattr("obdi.pull.starling.fetch_accounts", fake_accounts)
        monkeypatch.setattr("obdi.pull.starling.fetch_categories", fake_categories)
        monkeypatch.setattr("obdi.pull.starling.fetch_balance", fake_balance)
        monkeypatch.setattr("obdi.pull.starling.fetch_feed", fake_feed)

        with Store(tmp_path / "s.sqlite3") as store:
            pull_starling(
                store, "token", account_map=AccountMap(), trigger="scheduled"
            )

            sources = {
                row[0]
                for row in store.connection.execute(
                    "SELECT DISTINCT source FROM raw_artefacts"
                ).fetchall()
            }
            attempts = store.attempts()

        assert sources == {
            "starling-accounts",
            "starling-spaces",
            "starling-balance",
            "starling-feed",
        }
        # Two categories -> two ledger rows, each carrying the real ask and
        # the trigger pathway.
        assert len(attempts) == 2
        assert all(row["outcome"] == "landed" for row in attempts)
        assert all(
            row["asked"] == "changesSince=2016-08-04T00:00:00Z" for row in attempts
        )
        assert all("scheduled" in str(row["request_meta"]) for row in attempts)


class TestOneRefusedCategoryDoesNotStarveTheRest:
    def test_Pull_ContinuesPastARefusedFeed_AndNotesIt(self, tmp_path, monkeypatch):
        from obdi.providers.starling import Category, StarlingError
        from obdi.pull import pull_starling

        def fake_accounts(_token, **_kwargs):
            return (
                [{"accountUid": "acc-1", "defaultCategory": "cat-main", "name": "main"}],
                b'{"accounts": []}',
            )

        def fake_categories(_token, _account_uid, **_kwargs):
            return (
                [
                    Category(uid="cat-main", name="main", is_space=False),
                    Category(uid="space-1", name="holiday", is_space=True),
                ],
                b'{"savingsGoals": []}',
            )

        calls = []

        def fake_feed(_token, _account_uid, category_uid, **_kwargs):
            calls.append(category_uid)
            if category_uid == "cat-main":
                raise StarlingError("Starling call failed (HTTP 429): slow down", status=429)
            return [], b'{"feedItems": []}', "changesSince=2016-08-04T00:00:00Z"

        monkeypatch.setattr("obdi.pull.starling.fetch_accounts", fake_accounts)
        monkeypatch.setattr("obdi.pull.starling.fetch_categories", fake_categories)
        monkeypatch.setattr(
            "obdi.pull.starling.fetch_balance", lambda *_a, **_k: b"{}"
        )
        monkeypatch.setattr("obdi.pull.starling.fetch_feed", fake_feed)

        with Store(tmp_path / "s.sqlite3") as store:
            result = pull_starling(store, "token", account_map=AccountMap())
            attempts = store.attempts()

        # Observed live: the first scheduled pull's second call drew a 429 and
        # the old behaviour starved every remaining category. Both categories
        # must be ASKED, the refusal noted and ledgered, the pull completing.
        assert calls == ["cat-main", "space-1"]
        assert any(row["outcome"] == "refused" for row in attempts)
        assert any("429" in note for note in result.notes)


class TestReconnectDriftIsDetected:
    """A reconnect via the generic picker can come back through the wrong
    bank or with a different subset of accounts approved - and both pass
    silently unless the two latest accounts payloads are compared."""

    def _land_accounts(self, store, payload_bytes):
        from obdi.providers.truelayer import artefact_for

        store.land_artefact(
            artefact_for(
                payload_bytes,
                account_id="halifax",
                kind="accounts",
                account_ref="halifax",
            )
        )

    def test_SameAccountsSameProvider_NoFindings(self, tmp_path):
        import json

        body = json.dumps(
            {
                "results": [
                    {
                        "account_id": "acc-1",
                        "provider": {"provider_id": "ob-halifax"},
                    }
                ]
            }
        ).encode()
        with Store(tmp_path / "s.sqlite3") as store:
            self._land_accounts(store, body)
            self._land_accounts(store, body + b" ")

            assert store.detect_reconnect_drift("halifax") == []

    def test_WrongBankAndVanishedAccounts_AreBothNamed(self, tmp_path):
        import json

        before = json.dumps(
            {
                "results": [
                    {"account_id": "acc-1", "provider": {"provider_id": "ob-halifax"}},
                    {"account_id": "acc-2", "provider": {"provider_id": "ob-halifax"}},
                ]
            }
        ).encode()
        after = json.dumps(
            {
                "results": [
                    {
                        "account_id": "acc-9",
                        "provider": {"provider_id": "ob-nationwide"},
                    }
                ]
            }
        ).encode()
        with Store(tmp_path / "s.sqlite3") as store:
            self._land_accounts(store, before)
            self._land_accounts(store, after)

            findings = store.detect_reconnect_drift("halifax")

        joined = " | ".join(findings)
        assert "ob-halifax -> ob-nationwide" in joined
        assert "2 account(s) no longer approved" in joined
        assert "1 new account(s) approved" in joined

    def test_FirstEverAuthorisation_HasNothingToCompare(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            assert store.detect_reconnect_drift("halifax") == []


class TestRecurringDeclarationsLandOnDeepPulls:
    """Standing orders and direct debits: fetched inside the attended
    window only, landed as evidence, ledgered like everything else - and
    NOT fetched on routine pulls, where the unattended quota is precious."""

    def _fakes(self, monkeypatch, regular_calls):
        def fake_accounts(_token, **_kwargs):
            return (
                [{"account_id": "acc-1", "display_name": "Current", "account_type": "T"}],
                b"{}",
            )

        def fake_transactions(_token, _account_id, **kwargs):
            return [], b'{"results": [], "status": "Succeeded"}', "from=2024-08-02&to=2026-08-02"

        def fake_regulars(_token, _account_id, kind, **_kwargs):
            regular_calls.append(kind)
            return b'{"results": []}'

        monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", fake_accounts)
        monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", fake_transactions)
        monkeypatch.setattr("obdi.pull.truelayer.fetch_balance", lambda *a, **k: ([], b"{}"))
        monkeypatch.setattr("obdi.pull.truelayer.fetch_regulars", fake_regulars)

    def test_DeepPull_LandsBothDeclarationKinds(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._fakes(monkeypatch, calls)

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
                deep=True,
            )
            sources = {
                row[0]
                for row in store.connection.execute(
                    "SELECT DISTINCT source FROM raw_artefacts"
                ).fetchall()
            }

        assert calls == ["standing_orders", "direct_debits"]
        assert "truelayer-standing_orders" in sources
        assert "truelayer-direct_debits" in sources

    def test_RoutinePull_NeverSpendsQuotaOnDeclarations(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._fakes(monkeypatch, calls)

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
            )

        assert calls == []


class TestCardsLandAndParse:
    """Cards fetch on deep pulls and now PARSE too: the sign convention
    was verified against landed evidence on 2026-08-02 (DEBIT positive,
    CREDIT negative, zero mixing) and the mapper negates into the store's
    outflow-negative canon with the type column verifying every row."""

    def test_DeepPull_LandsAndParsesCardWindows(self, tmp_path, monkeypatch):
        def fake_accounts(_token, **_kwargs):
            return ([], b'{"results": []}')

        def fake_cards(_token, **_kwargs):
            return (
                [{"account_id": "card-1"}],
                b'{"results": [{"account_id": "card-1"}]}',
            )

        def fake_card_txns(_token, card_id, **_kwargs):
            record = (
                '{"amount": 9.99, "currency": "GBP", "description": "COFFEE", '
                '"timestamp": "2026-07-01T00:00:00Z", '
                '"transaction_type": "DEBIT", "transaction_id": "c-1", '
                '"normalised_provider_transaction_id": "txn-c-1", '
                '"provider_transaction_id": "c-1"}'
            )
            return (
                ('{"results": [' + record + "]}").encode("utf-8"),
                "from=2026-05-04&to=2026-08-02",
            )

        monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", fake_accounts)
        monkeypatch.setattr("obdi.pull.truelayer.fetch_cards", fake_cards)
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_card_transactions", fake_card_txns
        )

        with Store(tmp_path / "s.sqlite3") as store:
            pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(tmp_path / "c.json"),
                account_map=AccountMap(),
                deep=True,
            )
            sources = {
                row[0]
                for row in store.connection.execute(
                    "SELECT DISTINCT source FROM raw_artefacts"
                ).fetchall()
            }
            transactions = store.counts()["transactions"]

        assert "truelayer-cards" in sources
        assert "truelayer-card-booked" in sources
        # Landed as evidence AND parsed: one purchase, stored negated.
        assert transactions == 1
        with Store(tmp_path / "s.sqlite3") as store:
            amount = store.connection.execute(
                "SELECT amount_minor FROM transactions"
            ).fetchone()[0]
        assert amount == -999
