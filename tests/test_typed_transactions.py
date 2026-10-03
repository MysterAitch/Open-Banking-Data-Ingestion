"""Transactions a person types, as evidence that survives a rebuild.

Every expectation was worked out from the scenario before the first run.

The account every scenario reads is `tin`, declared, GBP, with nothing fed to it:

    typed-1  2026-03-04  out   12.50   "Estate agent"      -1,250
    typed-2  2026-03-06  in   100.00   "Top up"           +10,000

so its rows total +8,750 and, with no stated balance, start from zero.

A second account, `current`, is fed by a source and is where a bank's own row
for a typed payment arrives.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import HTTPServer

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.actual_push import transactions_to_push
from obdi.backup import take_backup
from obdi.balance_anchors import AnchorRefused
from obdi.cli import build_web_config
from obdi.export_declared import export_declared
from obdi.identity import artefact_digest, content_key
from obdi.ledger import running_balance
from obdi.models import RawArtefact, SourceTier, TransactionStatus
from obdi.namespaces import MANUAL_SOURCE, MANUAL_WITHDRAWAL_SOURCE
from obdi.rebuild import rebuild_from_raw
from obdi.replay import ActualAccountBinding, build_payload
from obdi.store import Store
from obdi.typed_transactions import (
    TypedRefused,
    record_typed_transaction,
    typed_entries,
    withdraw_typed_transaction,
)
from obdi.web import AuthorisationSession, ConnectionHandler
from test_ledger import land, txn

TIN = "tin"
CURRENT = "current"
D = date
TODAY = D(2026, 6, 1)
START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

#: A figure and a description no other figure or word on any page can equal, so
#: finding either on a page can only mean it was echoed.
SECRET_FIGURE = "7391.42"
SECRET_WORDS = "Zebra Surveyors 4417"


def stamp(step: int) -> datetime:
    return START + timedelta(minutes=step)


def declare(store: Store, ref: str = TIN, kind: str = "cash") -> None:
    store.declare_account(AccountRecord(ref=AccountRef(ref), kind=kind, label=ref.title()))


def type_two(store: Store) -> tuple[str, str]:
    first = record_typed_transaction(
        store, TIN, "2026-03-04", "out", "12.50", "Estate agent",
        today=TODAY, now=stamp(1), entry_id="a1a1a1a1a1a1a1a1",
    )
    second = record_typed_transaction(
        store, TIN, "2026-03-06", "in", "100.00", "Top up",
        today=TODAY, now=stamp(2), entry_id="b2b2b2b2b2b2b2b2",
    )
    return first, second


def shape(store: Store, ref: str = TIN) -> list[tuple[str, str, int, int, int, str]]:
    """What a rebuild must reproduce: identity and content, entity id included."""
    return sorted(
        (t.entity_id, t.content_key, t.occurrence, t.amount_minor, t.value_date.toordinal(),
         t.status.value)
        for t in store.transactions_for_account(ref)
    )


def artefact_count(store: Store, source: str | None = None) -> int:
    if source is None:
        return int(store.connection.execute("SELECT COUNT(*) FROM raw_artefacts").fetchone()[0])
    return int(
        store.connection.execute(
            "SELECT COUNT(*) FROM raw_artefacts WHERE source = ?", (source,)
        ).fetchone()[0]
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "typed.sqlite3") as opened:
        declare(opened)
        yield opened


class TestTypingATransaction:
    def test_TypedTransaction_WhenSaved_LandsOneArtefactAndOneCountedRow(self, store):
        type_two(store)

        assert artefact_count(store, MANUAL_SOURCE) == 2
        rows = {t.description: t for t in store.transactions_for_account(TIN)}
        assert set(rows) == {"Estate agent", "Top up"}
        estate = rows["Estate agent"]
        assert (estate.amount_minor, estate.value_date) == (-1250, D(2026, 3, 4))
        assert (estate.source, estate.tier, estate.source_id) == (
            MANUAL_SOURCE, SourceTier.MANUAL, "a1a1a1a1a1a1a1a1",
        )
        assert estate.status is TransactionStatus.BOOKED
        assert running_balance(0, store.transactions_for_account(TIN)) == 8750

    def test_TypedTransaction_WhenPushed_CarriesAStableImportedIdOfItsOwn(self, store):
        type_two(store)
        binding = [ActualAccountBinding(TIN, "act-tin")]

        before = build_payload(transactions_to_push(store), binding)["act-tin"]
        rebuild_from_raw(store)
        after = build_payload(transactions_to_push(store), binding)["act-tin"]

        key = content_key(amount_minor=-1250, value_date=D(2026, 3, 4), description="Estate agent")
        assert f"{key}:0" in {row["imported_id"] for row in before}
        assert sorted(r["imported_id"] for r in before) == sorted(r["imported_id"] for r in after)

    def test_TypedTransactions_WhenRebuiltTwice_ReproduceTheSameEntityIdsAndRows(self, store):
        type_two(store)
        live = shape(store)

        rebuild_from_raw(store)
        once = shape(store)
        rebuild_from_raw(store)
        twice = shape(store)

        assert len(live) == 2
        assert live == once == twice

    def test_TwoTypedRows_WhenSameFigureOnTheSameDay_StayTwoWithNoReviewFlag(self, store):
        for step, entry in enumerate(("c3c3c3c3c3c3c3c3", "d4d4d4d4d4d4d4d4"), start=1):
            record_typed_transaction(
                store, TIN, "2026-03-04", "out", "20.00", "Rent",
                today=TODAY, now=stamp(step), entry_id=entry,
            )

        rows = store.transactions_for_account(TIN)
        assert [t.amount_minor for t in rows] == [-2000, -2000]
        assert sorted(t.occurrence for t in rows) == [0, 1]
        assert store.review_queue() == [], "two typed rows are a decision, not a puzzle"
        rebuild_from_raw(store)
        assert len(store.transactions_for_account(TIN)) == 2

    def test_TypedRow_WhenALaterFeedRowReportsTheSamePayment_BecomesOneRowSightedByBoth(
        self, store
    ):
        declare(store, CURRENT, "current-account")
        record_typed_transaction(
            store, CURRENT, "2026-03-10", "out", "50.00", "Gas bill",
            today=TODAY, now=stamp(1), entry_id="e5e5e5e5e5e5e5e5",
        )
        land(store, "feed-1", txn(CURRENT, "src-a", "bank-1", D(2026, 3, 12), -5000, "BRITISH GAS"))

        rows = store.transactions_for_account(CURRENT)
        assert len(rows) == 1
        sighted = {
            str(r[0])
            for r in store.connection.execute(
                "SELECT source FROM transaction_sources WHERE entity_id = ?", (rows[0].entity_id,)
            )
        }
        assert sighted == {MANUAL_SOURCE, "src-a"}

    def test_FeedRow_WhenTypedAfterwards_IsNotReplacedByWhatAPersonRemembered(self, store):
        declare(store, CURRENT, "current-account")
        land(store, "feed-1", txn(CURRENT, "src-a", "bank-1", D(2026, 3, 12), -5000, "BRITISH GAS"))

        record_typed_transaction(
            store, CURRENT, "2026-03-10", "out", "50.00", "Gas bill",
            today=TODAY, now=stamp(1), entry_id="e5e5e5e5e5e5e5e5",
        )

        (row,) = store.transactions_for_account(CURRENT)
        assert (row.description, row.source, row.value_date) == (
            "BRITISH GAS", "src-a", D(2026, 3, 12),
        )

    def test_TypedMortgagePayment_WhenTheBankRowLeavesACurrentAccount_PairsAsATransfer(
        self, store
    ):
        declare(store, "mortgage", "balance-only")
        declare(store, CURRENT, "current-account")
        land(store, "feed-1", txn(CURRENT, "src-a", "bank-1", D(2026, 3, 3), -90000, "MORTGAGE CO"))

        record_typed_transaction(
            store, "mortgage", "2026-03-03", "in", "900.00", "Monthly payment",
            today=TODAY, now=stamp(1), entry_id="f6f6f6f6f6f6f6f6",
        )

        confirmed = {t.account_id for t in store.all_transactions() if t.transfer_confirmed}
        assert confirmed == {"mortgage", CURRENT}


class TestRefusals:
    @pytest.mark.parametrize(
        ("ref", "day", "direction", "amount", "description"),
        [
            ("nowhere", "2026-03-04", "out", "12.50", "Estate agent"),
            (TIN, "2026-02-30", "out", "12.50", "Estate agent"),
            (TIN, "04/03/2026", "out", "12.50", "Estate agent"),
            (TIN, "2026-06-02", "out", "12.50", "Estate agent"),
            (TIN, "2026-03-04", "sideways", "12.50", "Estate agent"),
            (TIN, "2026-03-04", "", "12.50", "Estate agent"),
            (TIN, "2026-03-04", "out", "12.5.0", "Estate agent"),
            (TIN, "2026-03-04", "out", "1e3", "Estate agent"),
            (TIN, "2026-03-04", "out", "Infinity", "Estate agent"),
            (TIN, "2026-03-04", "out", "12.505", "Estate agent"),
            (TIN, "2026-03-04", "out", "-12.50", "Estate agent"),
            (TIN, "2026-03-04", "in", "(12.50)", "Estate agent"),
            (TIN, "2026-03-04", "out", "0.00", "Estate agent"),
            (TIN, "2026-03-04", "out", "", "Estate agent"),
            (TIN, "2026-03-04", "out", "12.50", ""),
            (TIN, "2026-03-04", "out", "12.50", "   "),
            (TIN, "2026-03-04", "out", "12.50", "x" * 141),
            (TIN, "2026-03-04", "out", "12.50", "line\nbreak"),
        ],
    )
    def test_TypedTransaction_WhenInputIsWrong_IsRefusedAndNothingIsWritten(
        self, store, ref, day, direction, amount, description
    ):
        with pytest.raises((TypedRefused, AnchorRefused)) as refusal:
            record_typed_transaction(
                store, ref, day, direction, amount, description, today=TODAY, now=stamp(1)
            )

        assert artefact_count(store) == 0
        assert store.transactions_for_account(TIN) == []
        said = str(refusal.value)
        assert said, "a refusal is a sentence"
        # Never the figure, the description, or the date that was typed.
        for typed in (amount, description, day):
            if len(typed.strip()) > 3:
                assert typed.strip() not in said


class TestWithdrawing:
    def test_Withdrawal_WhenMade_DropsTheRowFromEverythingButKeepsBothArtefacts(self, store):
        first, second = type_two(store)

        withdraw_typed_transaction(store, TIN, first, now=stamp(3))

        rows = store.transactions_for_account(TIN)
        assert [t.description for t in rows] == ["Top up"]
        assert running_balance(0, rows) == 10000
        assert artefact_count(store, MANUAL_SOURCE) == 2
        assert artefact_count(store, MANUAL_WITHDRAWAL_SOURCE) == 1
        pushed = build_payload(transactions_to_push(store), [ActualAccountBinding(TIN, "act-tin")])
        assert [row["amount"] for row in pushed["act-tin"]] == [10000]
        entries = {e.entry_id: e.withdrawn for e in typed_entries(store, TIN)}
        assert entries == {first: True, second: False}

    def test_Withdrawal_WhenRebuilt_LeavesTheSameStateAsTheLiveWithdrawal(self, store):
        first, _ = type_two(store)
        withdraw_typed_transaction(store, TIN, first, now=stamp(3))
        live = shape(store)

        rebuild_from_raw(store)

        assert shape(store) == live
        assert len(live) == 1

    def test_Withdrawal_WhenTheEntryWasWithdrawnBeforeALaterRebuild_StaysWithdrawnInArrivalOrder(
        self, store
    ):
        """The withdrawal arrives after the entry, and a replay that met them in
        the other order would have the same answer: a withdrawal retracts its
        entry whenever it landed."""
        first, _ = type_two(store)
        withdraw_typed_transaction(
            store, TIN, first, now=START - timedelta(days=1)
        )

        rebuild_from_raw(store)

        assert [t.description for t in store.transactions_for_account(TIN)] == ["Top up"]

    def test_Withdrawal_WhenRepeated_IsRefused(self, store):
        first, _ = type_two(store)
        withdraw_typed_transaction(store, TIN, first, now=stamp(3))

        with pytest.raises(TypedRefused, match="already been withdrawn"):
            withdraw_typed_transaction(store, TIN, first, now=stamp(4))

        assert artefact_count(store, MANUAL_WITHDRAWAL_SOURCE) == 1

    def test_Withdrawal_WhenTheRowIsAFeedRow_IsRefused(self, store):
        declare(store, CURRENT, "current-account")
        land(store, "feed-1", txn(CURRENT, "src-a", "bank-1", D(2026, 3, 12), -5000, "BRITISH GAS"))
        (bank_row,) = store.transactions_for_account(CURRENT)

        for what in (bank_row.entity_id, "bank-1", "0123456789abcdef", ""):
            with pytest.raises(TypedRefused, match="no typed transaction"):
                withdraw_typed_transaction(store, CURRENT, what)

        assert len(store.transactions_for_account(CURRENT)) == 1
        assert artefact_count(store, MANUAL_WITHDRAWAL_SOURCE) == 0

    def test_Withdrawal_WhenTheEntryBelongsToAnotherAccount_IsRefused(self, store):
        declare(store, CURRENT, "current-account")
        first, _ = type_two(store)

        with pytest.raises(TypedRefused, match="no typed transaction"):
            withdraw_typed_transaction(store, CURRENT, first)

        assert len(store.transactions_for_account(TIN)) == 2

    def test_Withdrawal_WhenAFeedHasClaimedTheEntry_RemovesOnlyTheTypedSighting(self, store):
        declare(store, CURRENT, "current-account")
        entry = record_typed_transaction(
            store, CURRENT, "2026-03-10", "out", "50.00", "Gas bill",
            today=TODAY, now=stamp(1), entry_id="e5e5e5e5e5e5e5e5",
        )
        land(store, "feed-1", txn(CURRENT, "src-a", "bank-1", D(2026, 3, 12), -5000, "BRITISH GAS"))

        withdraw_typed_transaction(store, CURRENT, entry, now=stamp(3))

        (row,) = store.transactions_for_account(CURRENT)
        assert row.description == "BRITISH GAS"
        sighted = {
            str(r[0])
            for r in store.connection.execute(
                "SELECT source FROM transaction_sources WHERE entity_id = ?", (row.entity_id,)
            )
        }
        assert sighted == {"src-a"}


class TestWhatAPersonTypedIsDeclaredState:
    def test_Irreplaceable_CountsWhatWasTypedAndWhatWasWithdrawn(self, store):
        assert store.irreplaceable()["typed transactions and withdrawals"] == 0
        first, _ = type_two(store)
        assert store.irreplaceable()["typed transactions and withdrawals"] == 2
        withdraw_typed_transaction(store, TIN, first, now=stamp(3))
        assert store.irreplaceable()["typed transactions and withdrawals"] == 3

    def test_Export_ListsEveryTypedTransactionWithItsWithdrawal(self, store, tmp_path):
        first, _ = type_two(store)
        withdraw_typed_transaction(store, TIN, first, now=stamp(3))

        result = export_declared(store, tmp_path / "out")

        exported = json.loads((tmp_path / "out" / "typed-transactions.json").read_text("utf-8"))
        assert result.counts["typed_transactions"] == 2
        assert {(e["entry_id"], e["withdrawn"]) for e in exported} == {
            (first, True), ("b2b2b2b2b2b2b2b2", False),
        }
        assert {e["amount_minor"] for e in exported} == {-1250, 10000}

    def test_Backup_WhenRestored_HoldsEveryTypedTransaction(self, store, tmp_path):
        type_two(store)

        take_backup(store.path, tmp_path / "copy.sqlite3")

        with Store(tmp_path / "copy.sqlite3") as copy:
            assert [e.entry_id for e in typed_entries(copy, TIN)] == [
                "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2",
            ]
            rebuild_from_raw(copy)
            assert running_balance(0, copy.transactions_for_account(TIN)) == 8750

    def test_Rebind_CarriesTheTypedTransactionsAndTheyReplayUnderTheNewName(self, store):
        type_two(store)

        store.rebind_account(TIN, "money-tin")
        rebuild_from_raw(store)

        assert store.transactions_for_account(TIN) == []
        assert len(store.transactions_for_account("money-tin")) == 2
        assert [e.account for e in typed_entries(store, "money-tin")] == ["money-tin"] * 2


class TestPayloadsThatCannotBeRead:
    def test_Rebuild_WhenATypedArtefactIsGarbage_RecordsAProblemAndKeepsTheRest(self, store):
        type_two(store)
        for source, payload, step in (
            (MANUAL_SOURCE, b'{"kind": "typed-transaction"}', 9),
            (MANUAL_WITHDRAWAL_SOURCE, b"not json", 10),
        ):
            store.land_artefact(
                RawArtefact(
                    source=source,
                    account_ref=TIN,
                    fetched_at=stamp(step),
                    media_type="application/json",
                    digest=artefact_digest(payload),
                    payload=payload,
                )
            )

        report = rebuild_from_raw(store)

        assert len(report.problems) == 2
        assert len(store.transactions_for_account(TIN)) == 2


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def get(self, path: str, **params: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", params=params, timeout=20)

    def ledger(self) -> httpx.Response:
        return self.get("/ledger", ref=TIN, month="2026-03")

    def post(self, path: str, data: dict[str, str]) -> httpx.Response:
        return httpx.post(f"{self.base}{path}", data=data, follow_redirects=False, timeout=20)

    def type(self, **override: str) -> httpx.Response:
        form = {
            "ref": TIN, "month": "2026-03", "day": "2026-03-09", "direction": "out",
            "amount": SECRET_FIGURE, "description": SECRET_WORDS,
        }
        return self.post("/ledger-typed", {**form, **override})

    def show_values(self, month: str = "2026-03") -> httpx.Response:
        return self.post("/ledger", {"ref": TIN, "month": month})


@pytest.fixture
def lab(tmp_path, monkeypatch):
    db = tmp_path / "typed-web.sqlite3"
    with Store(db) as opened:
        declare(opened)
        type_two(opened)
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps({"actual": [{"canonical_id": TIN, "actual_account_id": "act-tin"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(accounts))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


def assert_no_secret(page: str, *, where: str) -> None:
    for secret in (SECRET_FIGURE, "739142", "7,391.42", "Zebra", "4417"):
        assert secret not in page, f"{secret!r} {where}"


class TestTheTypedTransactionPages:
    def test_Ledger_Masked_OffersTheFormAndTheWithdrawControlAsSecondaryActions(self, lab):
        page = lab.ledger().text

        assert 'action="/ledger-typed"' in page
        assert page.count('action="/ledger-typed-withdraw"') == 2
        assert (
            '<button class="button secondary" type="submit" '
            'style="width:100%;font-size:inherit;cursor:pointer">Save typed transaction'
        ) in page
        assert "Withdraw this typed transaction" in page
        assert '<span class="pill pill-quiet" title="A person typed this' in page
        assert "Show values" in page

    def test_Save_WhenAccepted_AnswersMaskedAndNoStoreAndEchoesNeitherFigureNorWords(self, lab):
        response = lab.type()

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert "Saved: a typed transaction dated 2026-03-09" in response.text
        assert_no_secret(response.text, where="in the answer to a save")
        assert "Values are masked" in response.text

    def test_TypedFigure_OnceSaved_AppearsOnNoGetPageAndOnlyAfterShowValues(self, lab):
        lab.type()

        for path, params in (
            ("/ledger", {"ref": TIN, "month": "2026-03"}),
            ("/ledger", {"ref": TIN, "month": "2026-03", "values": "1"}),
            ("/position", {}),
            ("/accounts", {}),
            ("/", {}),
            ("/ledger-typed", {}),
        ):
            assert_no_secret(lab.get(path, **params).text, where=f"on GET {path}")
        shown = lab.show_values().text
        assert "£7,391.42" in shown
        assert SECRET_WORDS in shown

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("day", "2026-02-30"),
            ("amount", "7391.4.2"),
            ("amount", "-7391.42"),
            ("ref", "nowhere"),
            ("description", ""),
            ("direction", ""),
        ],
    )
    def test_Save_WhenRefused_SaysWhyInASentenceWithoutEchoingWhatWasTyped(
        self, lab, field, value
    ):
        response = lab.type(**{field: value})

        assert response.status_code == 400
        assert "Nothing was saved." in response.text
        assert_no_secret(response.text, where=f"in the refusal for {field}")
        assert "nowhere" not in response.text
        with Store(lab.db) as opened:
            assert len(opened.transactions_for_account(TIN)) == 2

    def test_Withdraw_WhenPosted_AnswersMaskedAndTheRowStopsCounting(self, lab):
        lab.type()
        with Store(lab.db) as opened:
            secret_entry = next(
                e.entry_id for e in typed_entries(opened, TIN) if e.description == SECRET_WORDS
            )

        response = lab.post(
            "/ledger-typed-withdraw", {"ref": TIN, "month": "2026-03", "entry": secret_entry}
        )

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert "Withdrawn: one typed transaction" in response.text
        assert_no_secret(response.text, where="in the answer to a withdrawal")
        with Store(lab.db) as opened:
            assert len(opened.transactions_for_account(TIN)) == 2
            assert artefact_count(opened, MANUAL_WITHDRAWAL_SOURCE) == 1
        shown = lab.show_values().text
        listed = shown.split("<h2>Transactions, newest first</h2>")[1].split("<h2>What this")[0]
        assert SECRET_WORDS not in listed, "a withdrawn entry is not among the counted rows"
        assert '<span class="pill pill-quiet">withdrawn</span>' in shown, (
            "it is still listed, as withdrawn, in the typed transactions"
        )

    def test_Withdraw_WhenTheEntryIsNotATypedOne_IsRefusedWithASentence(self, lab):
        response = lab.post(
            "/ledger-typed-withdraw",
            {"ref": TIN, "month": "2026-03", "entry": "0123456789abcdef"},
        )

        assert response.status_code == 400
        assert "no typed transaction with that identity" in response.text

    def test_TypedRoutes_WhenPostedFromAnotherSite_AreRefused(self, lab):
        for route in ("/ledger-typed", "/ledger-typed-withdraw"):
            response = httpx.post(
                f"{lab.base}{route}",
                data={"ref": TIN, "month": "2026-03"},
                headers={"Origin": "https://evil.example"},
                follow_redirects=False,
            )
            assert response.status_code == 403, route

    def test_Ledger_WhenTheAccountHoldsNoRows_StillOffersToTypeTheFirstTransaction(
        self, lab, tmp_path
    ):
        with Store(lab.db) as opened:
            declare(opened, "empty-pot")

        page = lab.get("/ledger", ref="empty-pot").text

        assert 'action="/ledger-typed"' in page
        assert "holds no transactions at all" in page

