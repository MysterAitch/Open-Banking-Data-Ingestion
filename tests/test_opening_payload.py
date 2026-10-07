"""An account's opening balance reaches Actual as one extra row.

The household is the one from test_transfer_pairs_payload, built through real
Starling feed artefacts and `rebuild_from_raw`: main's rows (2000.00 in,
1500.00 to the pot, 100.00 out) sum to +40,000 by 2026-09-03, and the pot's
(1500.00 in, 1200.00 out) to +30,000. Their history begins on 2026-09-01 (main)
and 2026-09-02 (pot).

Worked out before the first run:

    stated 1,400.00 (140,000) at the end of 2026-09-03 on main
        opening = 140,000 - 40,000 = 100,000, at the end of 2026-08-31
        so the act-main payload sums to 100,000 + 40,000 = 140,000
    stated 500.00 (50,000) at the end of 2026-09-04 on the pot
        rows through 09-04 are +30,000, so opening = 20,000, at the end of 2026-09-01
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from obdi.actual_push import build_audit_envelope, build_envelope, build_prune_envelope
from obdi.balance_anchors import record_stated_anchor, remove_stated_anchor
from obdi.ingest.store import Store
from obdi.ledger import build_ledger
from obdi.replay import (
    OPENING_IMPORTED_ID_PREFIX,
    ActualAccountBinding,
    OpeningBalance,
    build_opening_entries,
    build_payload,
    opening_imported_id,
    to_actual_opening,
)
from test_transfer_pairs_payload import (
    BOUND_BOTH,
    MAIN,
    POT,
    _household,
    _sums,
)

MAIN_OPENING_ID = f"obdi-opening:{MAIN}"
POT_OPENING_ID = f"obdi-opening:{POT}"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "opening-payload.sqlite3") as opened:
        _household(opened)
        yield opened


def state_main(store: Store) -> None:
    record_stated_anchor(store, MAIN, "2026-09-03", "1400.00")


def opening_rows(envelope: dict[str, object], account: str) -> list[dict[str, object]]:
    accounts = envelope["accounts"]
    assert isinstance(accounts, dict)
    return [
        row
        for row in accounts.get(account, [])
        if str(row["imported_id"]).startswith("obdi-opening:")
    ]


class TestAnAccountWithAnOpeningBalance:
    def test_Payload_CarriesExactlyOneOpeningRowWithTheDerivedIdDateAndAmount(self, store):
        state_main(store)

        envelope = build_envelope(store, BOUND_BOTH, {})

        assert opening_rows(envelope, "act-main") == [
            {
                "imported_id": MAIN_OPENING_ID,
                "date": "2026-08-31",
                "amount": 100000,
                "payee_name": "Starting Balance",
                "starting_balance_flag": True,
                "cleared": True,
            }
        ]

    def test_Payload_TheAccountSumsToTheBalanceThatWasStated(self, store):
        state_main(store)

        envelope = build_envelope(store, BOUND_BOTH, {})
        accounts = envelope["accounts"]

        assert isinstance(accounts, dict)
        assert _sums(accounts)["act-main"] == 140000

    def test_Envelope_NamesTheOpeningRowForTheApplierToKeepExact(self, store):
        state_main(store)

        envelope = build_envelope(store, BOUND_BOTH, {})

        assert envelope["version"] == 3
        assert envelope["opening_balances"] == [
            {
                "account": "act-main",
                "imported_id": MAIN_OPENING_ID,
                "date": "2026-08-31",
                "amount": 100000,
            }
        ]

    def test_AuditAndPruneEnvelopes_CarryTheSameRowAndTheSameListAsAPush(self, store):
        state_main(store)

        push = build_envelope(store, BOUND_BOTH, {})
        audit = build_audit_envelope(store, BOUND_BOTH)
        prune = build_prune_envelope(store, BOUND_BOTH)

        assert audit["opening_balances"] == prune["opening_balances"] == push["opening_balances"]
        assert opening_rows(audit, "act-main") == opening_rows(push, "act-main")
        assert opening_rows(prune, "act-main") == opening_rows(push, "act-main")
        audit_accounts = audit["accounts"]
        assert isinstance(audit_accounts, dict)
        assert _sums(audit_accounts)["act-main"] == 140000, "the audit's expected balance"

    def test_OpeningRow_IsInTheListEveryAccountOnlyOnce_EvenWhenBuiltAgain(self, store):
        state_main(store)

        first = build_envelope(store, BOUND_BOTH, {})
        second = build_envelope(store, BOUND_BOTH, {})

        assert first["accounts"] == second["accounts"]
        assert len(opening_rows(second, "act-main")) == 1

    def test_Ledger_SentBalanceOnABoundAccount_EqualsWhatThePayloadSums(self, store):
        """The page and the push must say the same thing: both are sums over
        the same rows plus the same opening."""
        state_main(store)

        envelope = build_envelope(store, BOUND_BOTH, {})
        ledger = build_ledger(store, MAIN, "2026-09", bound=True)
        accounts = envelope["accounts"]

        assert isinstance(accounts, dict)
        assert ledger.position is not None
        assert ledger.position.sent_balance.minor == _sums(accounts)["act-main"] == 140000
        assert ledger.position.store_balance.minor == 140000


    def test_Ledger_SentBalanceOnAnUnboundAccount_DoesNotClaimTheOpeningIsSent(self, store):
        """Nothing is sent for an unbound account, opening row included, so the
        figure for what would be sent is zero while the store's own figure still
        has the opening."""
        state_main(store)

        ledger = build_ledger(store, MAIN, "2026-09", bound=False)

        assert ledger.position is not None
        assert ledger.position.store_balance.minor == 140000
        assert ledger.position.sent_balance.minor == 0
        assert ledger.position.differs is True
        assert ledger.position.opening_included is True


class TestAnAccountWithoutOne:
    def test_Payload_SendsNoOpeningRowAndTheAccountSumsToItsRowsAlone(self, store):
        state_main(store)

        envelope = build_envelope(store, BOUND_BOTH, {})

        assert opening_rows(envelope, "act-pot") == []
        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert _sums(accounts)["act-pot"] == 30000

    def test_Envelope_WhenNoAccountHasAnAnchor_ListsNoOpeningBalancesAndSendsNoRow(self, store):
        for envelope in (
            build_envelope(store, BOUND_BOTH, {}),
            build_audit_envelope(store, BOUND_BOTH),
            build_prune_envelope(store, BOUND_BOTH),
        ):
            assert envelope["opening_balances"] == []
            assert opening_rows(envelope, "act-main") == []
            assert opening_rows(envelope, "act-pot") == []

    def test_Removing_TheLastAnchor_MakesTheRowNoLongerExpected(self, store):
        state_main(store)
        assert opening_rows(build_envelope(store, BOUND_BOTH, {}), "act-main")

        remove_stated_anchor(store, MAIN, "2026-09-03")

        for envelope in (
            build_envelope(store, BOUND_BOTH, {}),
            build_audit_envelope(store, BOUND_BOTH),
            build_prune_envelope(store, BOUND_BOTH),
        ):
            assert opening_rows(envelope, "act-main") == []
            assert envelope["opening_balances"] == []

    def test_Account_WhoseAnchorsAreStatedAboutAnUnboundAccount_IsNotSent(self, store):
        state_main(store)

        envelope = build_envelope(store, [ActualAccountBinding(POT, "act-pot")], {})

        assert envelope["opening_balances"] == []
        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert "act-main" not in accounts


class TestEveryAccountThatHasOne:
    def test_Payload_CarriesOneRowPerAccountEachUnderItsOwnId(self, store):
        state_main(store)
        record_stated_anchor(store, POT, "2026-09-04", "500.00")

        envelope = build_envelope(store, BOUND_BOTH, {})

        assert [r["imported_id"] for r in opening_rows(envelope, "act-main")] == [MAIN_OPENING_ID]
        assert opening_rows(envelope, "act-pot") == [
            {
                "imported_id": POT_OPENING_ID,
                "date": "2026-09-01",
                "amount": 20000,
                "payee_name": "Starting Balance",
                "starting_balance_flag": True,
                "cleared": True,
            }
        ]
        assert {e["imported_id"] for e in envelope["opening_balances"]} == {  # type: ignore[attr-defined]
            MAIN_OPENING_ID,
            POT_OPENING_ID,
        }
        accounts = envelope["accounts"]
        assert isinstance(accounts, dict)
        assert _sums(accounts) == {"act-main": 140000, "act-pot": 50000}


class TestTheIdAndTheRows:
    def test_Id_IsThePrefixAndTheCanonicalReferenceVerbatim(self):
        assert opening_imported_id("truelayer:a b/c") == "obdi-opening:truelayer:a b/c"

    def test_Id_IsNeverTheShapeOfAPaymentsId(self, store):
        """The applier tells ours from the importer's by shape, so the opening
        id must not be mistakable for a content key and occurrence."""
        state_main(store)

        envelope = build_envelope(store, BOUND_BOTH, {})
        accounts = envelope["accounts"]

        assert isinstance(accounts, dict)
        ids = [str(row["imported_id"]) for row in accounts["act-main"]]
        payments = [i for i in ids if not i.startswith("obdi-opening:")]
        assert len(payments) == 3
        assert all(len(i.split(":")[0]) == 64 for i in payments)
        assert len({i.split(":")[0] for i in ids}) == len(ids)

    def test_OpeningEntries_OmitAnOpeningWhoseAccountIsUnbound(self):
        entries = build_opening_entries(
            [ActualAccountBinding("a", "act-a")],
            [OpeningBalance("a", date(2026, 1, 1), 5), OpeningBalance("b", date(2026, 1, 1), 7)],
        )

        assert [e["account"] for e in entries] == ["act-a"]

    def test_Row_IsClearedAndFlaggedAsActualsStartingBalance(self):
        row = to_actual_opening(OpeningBalance("a", date(2026, 1, 1), -250))

        assert row["cleared"] is True
        assert row["starting_balance_flag"] is True
        assert row["payee_name"] == "Starting Balance"
        assert row["amount"] == -250
        assert row["date"] == "2026-01-01"

    def test_Payload_ForAnOpeningWithNoBinding_IsLeftOut(self):
        payload = build_payload(
            [], [ActualAccountBinding("a", "act-a")],
            [OpeningBalance("zzz", date(2026, 1, 1), 5)],
        )

        assert payload == {}


class TestThePythonAndNodeSidesAgreeOnTheId:
    def test_TheAppliersRecogniserIsBuiltFromThePrefixTheStoreSends(self):
        """A prefix that differed on one side would make every opening row look
        like somebody else's to the audit and prune, so neither would ever see
        it. The two sides share no code, so the literal is compared."""
        audit = (Path(__file__).resolve().parents[1] / "applier" / "audit.mjs").read_text(
            encoding="utf-8"
        )

        assert f"/^{OPENING_IMPORTED_ID_PREFIX}.+$/s" in audit

    def test_TheAppliersOwnTestsExerciseTheSamePrefix(self):
        tests = (
            Path(__file__).resolve().parents[1] / "applier" / "opening.engine.test.mjs"
        ).read_text(encoding="utf-8")

        assert f"'{OPENING_IMPORTED_ID_PREFIX}household-main'" in tests


class TestTheManualReplayCommand:
    def test_Replay_WritesTheSameOpeningRowAndListTheQueuedPushCarries(
        self, store, tmp_path, monkeypatch, capsys
    ):
        from obdi import cli

        state_main(store)
        store.connection.commit()
        account_map = tmp_path / "accounts.json"
        account_map.write_text(
            json.dumps(
                {
                    "actual": [
                        {"canonical_id": MAIN, "actual_account_id": "act-main"},
                        {"canonical_id": POT, "actual_account_id": "act-pot"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
        monkeypatch.setenv("OBDI_DB_PATH", str(tmp_path / "opening-payload.sqlite3"))
        out = tmp_path / "payload.json"

        exit_code = cli.main(["replay", "--out", str(out)])

        written = json.loads(out.read_text(encoding="utf-8"))
        capsys.readouterr()
        assert exit_code == 0
        assert [e["imported_id"] for e in written["opening_balances"]] == [MAIN_OPENING_ID]
        main_rows = written["accounts"]["act-main"]
        assert [r["amount"] for r in main_rows if r["imported_id"] == MAIN_OPENING_ID] == [100000]
