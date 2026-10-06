"""The Overview and the Accounts page say what the ledger says, whatever changed the standing.

Both pages read every account's standing from a memo that was keyed by the counts and newest
stamps of a few tables. Editing a declared account's kind, refiling an artefact, assigning a
statement section, and editing the account-map file each changed a standing without moving that
key, so the two pages went on saying the old thing (while the ledger, which reads the store live,
said the new one) until something unrelated moved the key or the process restarted. A reviewer
saw "in agreement through 2026-06-30" on the ledger and "in agreement through no date yet" on
Accounts and the Overview at the same moment.

The key now includes the store's STANDING EPOCH, which every write to a table that can change a
standing moves in its own transaction (`store.EPOCH_TABLES`). The classification of every table
is pinned below so a table added later has to be placed in one list or the other.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import threading
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.balance_anchors import record_stated_anchor, remove_stated_anchor
from obdi.cli import build_web_config
from obdi.store import SCHEMA_VERSION, TABLE_NAMES, Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_balance_anchors import ACCOUNT, everyday

D = date

AGREED_THROUGH_MARCH_20 = "every known balance from 2026-03-05 to 2026-03-20"
AGREED_THROUGH_MARCH_25 = "every known balance from 2026-03-05 to 2026-03-25"


def declare(store: Store, ref: str = ACCOUNT, kind: str = "current") -> None:
    store.declare_account(AccountRecord(ref=AccountRef(ref), kind=kind, label=ref.title()))


@pytest.fixture
def db(tmp_path):
    """An account in agreement through 03-20, and a later stated balance the rows do not meet."""
    path = tmp_path / "epoch.sqlite3"
    with Store(path) as store:
        everyday(store)
        declare(store)
        for day, amount in (
            ("2026-03-05", "1000.00"),
            ("2026-03-10", "980.00"),
            ("2026-03-20", "952.00"),
            ("2026-03-25", "1.00"),
        ):
            record_stated_anchor(store, ACCOUNT, day, amount)
    return path


@pytest.fixture
def served(db, tmp_path, monkeypatch):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
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
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


def get(served: str, path: str, **params: str) -> str:
    return httpx.get(f"{served}{path}", params=params, timeout=30).text


def post(served: str, path: str, **fields: str) -> httpx.Response:
    return httpx.post(f"{served}{path}", data=fields, timeout=30)


def edit_kind(served: str, kind: str) -> None:
    response = post(
        served, "/save-account", original_ref=ACCOUNT, ref=ACCOUNT, label="Everyday", kind=kind
    )
    assert response.status_code == 200, response.text


def card(page: str) -> str:
    """The agreement sentence a page gives for the account, so a stale one names its date."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    found = re.search(
        r"every known balance from \d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d"
        r"|do not yet add up to any known balance",
        text,
    )
    return found.group(0) if found else "no sentence saying whether it adds up"


class TestEditingAnAccountMovesItsStanding:
    def test_AccountsPage_WhenKindEditedToBalanceOnly_SaysTheNewAgreementAtOnce(self, served):
        assert AGREED_THROUGH_MARCH_20 in card(get(served, "/accounts"))

        edit_kind(served, "balance-only")

        assert AGREED_THROUGH_MARCH_25 in card(get(served, "/accounts"))

    def test_AccountsPage_WhenNothingChanged_SaysTheSameThingAgain(self, served):
        first = card(get(served, "/accounts"))
        second = card(get(served, "/accounts"))

        assert AGREED_THROUGH_MARCH_20 in first
        assert second == first

    def test_Overview_WhenKindEditedToBalanceOnly_SaysTheNewAgreementWithoutWaiting(self, served):
        assert adds_up_to(get(served, "/")) == "2026-03-20"

        edit_kind(served, "balance-only")

        assert adds_up_to(get(served, "/")) == "2026-03-25"


def _cost_lines(run) -> list[str]:
    """The lines the memo prints each time it works a value out."""
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        run()
    return [line for line in captured.getvalue().splitlines() if "account standings" in line]


class TestTheMemoIsReusedWhenNothingChanged:
    def test_AccountsPage_WhenNothingChanged_RecomputesNothing(self, served):
        get(served, "/accounts")

        lines = _cost_lines(lambda: [get(served, "/accounts") for _ in range(3)])

        assert lines == []

    def test_AccountsPage_WhenKindEdited_RecomputesOnce(self, served):
        get(served, "/accounts")

        def act() -> None:
            edit_kind(served, "balance-only")
            get(served, "/accounts")
            get(served, "/accounts")

        assert len(_cost_lines(act)) == 1


class TestEveryTableIsClassified:
    def test_Schema_WhenATableIsInNeitherList_IsRefused(self):
        from obdi.store import EPOCH_TABLES, NOT_STANDING_TABLES

        unplaced = set(TABLE_NAMES) - set(EPOCH_TABLES) - set(NOT_STANDING_TABLES)
        assert not unplaced, (
            f"{sorted(unplaced)} are in no list: say whether a write to each moves an "
            "account's standing (EPOCH_TABLES) or why it cannot (NOT_STANDING_TABLES, "
            "with the reason)"
        )

    def test_Schema_WhenATableIsInBothLists_IsRefused(self):
        from obdi.store import EPOCH_TABLES, NOT_STANDING_TABLES

        assert not set(EPOCH_TABLES) & set(NOT_STANDING_TABLES)

    def test_Schema_EveryExcludedTableSaysWhyItCannotMoveAStanding(self):
        from obdi.store import NOT_STANDING_TABLES

        for table, reason in NOT_STANDING_TABLES.items():
            assert len(reason.split()) >= 6, f"{table}: the reason is not a reason"

    def test_Schema_EveryListedTableIsARealTable(self):
        from obdi.store import EPOCH_TABLES, NOT_STANDING_TABLES

        assert set(EPOCH_TABLES) <= set(TABLE_NAMES)
        assert set(NOT_STANDING_TABLES) <= set(TABLE_NAMES)

    def test_Epoch_WhenAnyClassifiedTableIsWritten_MovesExactlyWhenListed(self, tmp_path):
        """Every table is written through SQL the way a writer would, and the epoch is read.

        The writes name only the columns the table needs, so a table added later with a
        column this does not know fails here with the table's name rather than passing.
        """
        from obdi.store import EPOCH_TABLES, NOT_STANDING_TABLES

        with Store(tmp_path / "classified.sqlite3") as store:
            for table in TABLE_NAMES:
                if table == "standing_epoch":
                    continue
                before = store.standing_epoch()
                _write_one_row(store, table)
                moved = store.standing_epoch() != before
                assert moved == (table in EPOCH_TABLES), (
                    f"{table}: a write {'moved' if moved else 'did not move'} the epoch, "
                    f"but the table is listed in "
                    f"{'EPOCH_TABLES' if table in EPOCH_TABLES else 'NOT_STANDING_TABLES'}"
                )
            assert set(EPOCH_TABLES) | set(NOT_STANDING_TABLES) >= set(TABLE_NAMES)


def _write_one_row(store: Store, table: str) -> None:
    """One row written straight to `table`: the guard is about the table, not a door."""
    columns = {
        str(row["name"]): row
        for row in store.connection.execute(f"PRAGMA table_info({table})")
    }
    values: dict[str, object] = {}
    for name, info in columns.items():
        declared = str(info["type"]).upper()
        values[name] = 1 if "INT" in declared else "x"
        if name == "id":
            values.pop(name)
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    store.connection.execute(
        f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})",  # noqa: S608
        tuple(values.values()),
    )
    store.connection.commit()


class TestTheEpochMovesWithTheWriteThatChangesAStanding:
    def test_Epoch_WhenABalanceIsStatedAndRemoved_MovesEachTime(self, db):
        with Store(db) as store:
            before = store.standing_epoch()
            record_stated_anchor(store, ACCOUNT, "2026-03-26", "2.00")
            stated = store.standing_epoch()
            remove_stated_anchor(store, ACCOUNT, "2026-03-26")
            removed = store.standing_epoch()

        assert before < stated < removed

    def test_Epoch_WhenAnAccountIsEdited_Moves(self, db):
        with Store(db) as store:
            before = store.standing_epoch()
            declare(store, kind="balance-only")

            assert store.standing_epoch() > before

    def test_Epoch_WhenStoreIsOnlyOpenedAndRead_StaysPut(self, db):
        with Store(db) as store:
            before = store.standing_epoch()
        with Store(db) as store:
            store.declared_accounts()
            store.all_transactions()
            assert store.standing_epoch() == before

    def test_Epoch_WhenAFailedPullIsRecorded_StaysPut(self, db):
        with Store(db) as store:
            before = store.standing_epoch()
            store.record_attempt(
                source="truelayer",
                connection_id="c1",
                account_ref=ACCOUNT,
                asked="from=2026-03-01",
                request_meta="",
                outcome="error",
                detail="refused",
            )
            assert store.standing_epoch() == before

    def test_Schema_IsTheVersionThePreferencesTableWasAddedIn(self):
        assert SCHEMA_VERSION == 21


class TestTheAccountMapFileIsPartOfTheKey:
    def test_AccountsPage_WhenTheAccountMapFileIsRewritten_RecomputesOnceWithoutARestart(
        self, served, tmp_path, monkeypatch
    ):
        path = tmp_path / "map.json"
        path.write_text(json.dumps({"bindings": []}), encoding="utf-8")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(path))
        get(served, "/accounts")
        assert _cost_lines(lambda: get(served, "/accounts")) == []

        def act() -> None:
            path.write_text(json.dumps({"bindings": [], "accounts": []}), encoding="utf-8")
            get(served, "/accounts")
            get(served, "/accounts")

        assert len(_cost_lines(act)) == 1

    def test_AccountMapStamp_WhenTheFileChanges_Differs(self, tmp_path, monkeypatch):
        from obdi.cli import account_map_stamp

        path = tmp_path / "map.json"
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(path))
        assert account_map_stamp() == (), "a named file that is not there"
        path.write_text(json.dumps({"bindings": []}), encoding="utf-8")
        first = account_map_stamp()
        path.write_text(json.dumps({"bindings": [], "accounts": []}), encoding="utf-8")

        assert first != ()
        assert account_map_stamp() != first

    def test_AccountMapStamp_WhenNoFileIsNamed_IsEmptyAndStable(self, monkeypatch):
        from obdi.cli import account_map_stamp

        monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)

        assert account_map_stamp() == ()
        assert account_map_stamp() == ()


def adds_up_to(page: str) -> str:
    """The last day the account's transactions add up to, as Today's row states it."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    found = re.search(r"Adds up to the known balances to (\d{4}-\d\d-\d\d)", text)
    return found.group(1) if found else "no sentence saying what it adds up to"


def known_state(page: str) -> str:
    """What Today says of the account: the day it adds up to, and the day it stops adding up."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    stops = re.search(r"Does not add up from (\d{4}-\d\d-\d\d)", text)
    return f"{adds_up_to(page)} / {stops.group(1) if stops else 'never stops'}"


class TestTheOverviewIsNotOlderThanTheViewersOwnPress:
    def test_Overview_WhenABalanceIsStatedAfterItWasOpened_ShowsItAtOnce(self, served):
        assert known_state(get(served, "/")) == "2026-03-20 / 2026-03-25"

        # A balance the rows reproduce, stated for a day before the one they do not.
        response = post(
            served, "/ledger-anchor", ref=ACCOUNT, month="2026-03", day="2026-03-22",
            amount="952.00",
        )
        assert response.status_code == 200, response.text

        assert known_state(get(served, "/")) == "2026-03-22 / 2026-03-25"

    def test_Overview_WhenABalanceIsRemovedAfterItWasOpened_ShowsItAtOnce(self, served):
        assert known_state(get(served, "/")) == "2026-03-20 / 2026-03-25"

        response = post(
            served, "/ledger-anchor-remove", ref=ACCOUNT, month="2026-03", day="2026-03-25",
            confirmed="yes",
        )
        assert response.status_code == 200, response.text

        assert known_state(get(served, "/")) == "2026-03-20 / never stops"

    def test_Overview_WhenNothingWasPressed_IsTheSameHeldPageWithinTheMinute(self, served):
        first = get(served, "/")
        second = get(served, "/")

        assert known_state(first) == known_state(second) == "2026-03-20 / 2026-03-25"
        assert _cost_lines(lambda: get(served, "/")) == []


def _land_truelayer_row(store: Store, account_ref: str) -> int:
    from obdi.providers.truelayer import artefact_for

    body = json.dumps(
        {
            "results": [
                {
                    "transaction_id": "t-1",
                    "normalised_provider_transaction_id": "txn-aaa",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "amount": -12.34,
                    "currency": "GBP",
                    "description": "COFFEE SHOP",
                }
            ],
            "status": "Succeeded",
        }
    ).encode()
    store.land_artefact(
        artefact_for(
            body,
            account_id="acc-1",
            kind="booked",
            requested="from=2026-06-01&to=2026-07-31",
            account_ref=account_ref,
        )
    )
    row = store.connection.execute(
        "SELECT rowid FROM raw_artefacts WHERE account_ref = ?", (account_ref,)
    ).fetchone()
    return int(row[0])


@pytest.fixture
def filed(tmp_path, monkeypatch):
    """Two declared accounts; one holds a payment and two stated balances that agree across it."""
    from obdi.cli import replay_single_artefact

    path = tmp_path / "filed.sqlite3"
    with Store(path) as store:
        declare(store, "held-a")
        declare(store, "held-b")
        artefact = _land_truelayer_row(store, "held-a")
    replay_single_artefact(path, artefact)
    with Store(path) as store:
        record_stated_anchor(store, "held-a", "2026-06-30", "100.00")
        record_stated_anchor(store, "held-a", "2026-07-01", "87.66")
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(path)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", artefact, path
    finally:
        httpd.shutdown()


def agreements(page: str) -> list[str]:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    return re.findall(
        r"add up to every known balance from \d{4}-\d\d-\d\d to (\d{4}-\d\d-\d\d)", text
    )


class TestRefilingAnArtefactMovesTheStandingsOfBothAccounts:
    def test_AccountsPage_WhenAnArtefactIsRefiled_StopsSayingTheOldAccountAgrees(self, filed):
        served, artefact, _ = filed
        assert "2026-07-01" in agreements(get(served, "/accounts"))

        response = post(
            served, "/refile-artefact", id=str(artefact), account="held-b", confirm="yes"
        )
        assert response.status_code == 200, response.text

        assert "2026-07-01" not in agreements(get(served, "/accounts"))

    def test_AccountsPage_WhenTheRefileIsRefusedBecauseNothingWasChosen_StillAgrees(self, filed):
        served, artefact, _ = filed
        get(served, "/accounts")

        post(served, "/refile-artefact", id=str(artefact), account="", confirm="")

        assert "2026-07-01" in agreements(get(served, "/accounts"))


class TestAssigningAStatementSectionMovesTheEpoch:
    def test_Epoch_WhenASectionIsAssigned_Moves(self, db):
        with Store(db) as store:
            before = store.standing_epoch()
            store.assign_statement_section("d" * 64, "section-1", ACCOUNT, "Everyday")

            assert store.standing_epoch() > before


class TestUpgradingAStoreHoldingTheVersionBefore:
    def test_Store_OpenedAtSchemaSixteen_GainsTheEpochAndItsTriggersOnOpen(self, tmp_path):
        import sqlite3

        path = tmp_path / "sixteen.sqlite3"
        with Store(path) as store:
            declare(store, "kept")
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE standing_epoch")
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {name}")
        connection.execute(
            "UPDATE obdi_meta SET value = '16' WHERE key = 'schema_version'",
        )
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            before = reopened.standing_epoch()
            declare(reopened, "added-after-the-upgrade")
            assert reopened.standing_epoch() > before
            assert [str(r.ref) for r in reopened.declared_accounts()] == [
                "added-after-the-upgrade",
                "kept",
            ]

    def test_Store_WhenCreatedFresh_HasATriggerForEveryWriteKindOfEveryListedTable(self, tmp_path):
        from obdi.store import EPOCH_TABLES

        with Store(tmp_path / "fresh.sqlite3") as store:
            names = {
                str(row[0])
                for row in store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }

        assert names == {
            f"standing_epoch_{table}_{kind}" for table in EPOCH_TABLES for kind in "iud"
        }


class TestAFailedReadingOfAStatementIsNotHeldForTheLifeOfTheProcess:
    def test_Usable_WhenTheFirstReadingFailedAndTheStoreHasSinceChanged_ReadsAgain(
        self, tmp_path, monkeypatch
    ):
        from obdi import statement_terms

        calls: list[str] = []

        def flaky(store: Store, digest: str, account: str):
            calls.append(digest)

        monkeypatch.setattr(statement_terms, "_reading_of", flaky)
        monkeypatch.setattr(statement_terms, "_USABLE_BY_DIGEST", {})
        with Store(tmp_path / "usable.sqlite3") as store:
            assert statement_terms._usable(store, "abc", "acct") is None
            assert statement_terms._usable(store, "abc", "acct") is None
            assert calls == ["abc"], "a failure is held while the store is unchanged"
            declare(store, "something-changed")
            assert statement_terms._usable(store, "abc", "acct") is None

        assert calls == ["abc", "abc"], "a failure must be tried again once the store moves"
