"""An answer page about one account links to that account's ledger, and says what changed for it.

A reviewer drove every flow on invented data and each ended in a dead end: after an import, a
statement read in, an edit, a stated or removed balance, or a protection, the page offered only
"Back to overview" and the rows the flow had just changed were not reachable from the answer.
Removing a balance twice said "No such stated balance" with the Overview as the only way on, and
reloading the answer to an import said "Upload expired" with a bare token in quotes, which does
not tell a person who has just imported whether the first press landed.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.balance_anchors import record_stated_anchor
from obdi.cli import build_web_config, replay_single_artefact
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_balance_anchors import ACCOUNT, everyday

LEDGER_OF_EVERYDAY = f'href="/ledger?ref={ACCOUNT}"'
CSV = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes\n"
    b"01/03/2026,Cafe One,card,CARD,-3.50,96.50,\n"
    b"20/03/2026,Garage,card,CARD,-40.00,56.50,\n"
)


def declare(store: Store, ref: str, label: str = "") -> None:
    store.declare_account(
        AccountRecord(ref=AccountRef(ref), kind="current", label=label or ref.title())
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "answers.sqlite3"
    with Store(path) as store:
        everyday(store)
        declare(store, ACCOUNT)
        declare(store, "second-acct", "Second Account")
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


def post(served: str, path: str, **fields: str) -> httpx.Response:
    return httpx.post(f"{served}{path}", data=fields, timeout=60)


def text_of(page: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))


def import_csv(served: str, account: str) -> tuple[str, httpx.Response]:
    """Upload, preview, and confirm; returns the token and the confirm answer."""
    previewed = httpx.post(
        f"{served}/upload",
        data={"account": account},
        files={"statement": ("march.csv", CSV, "text/csv")},
        timeout=60,
    )
    assert previewed.status_code == 200, previewed.text
    token = previewed.text.split('name="token" value="')[1].split('"')[0]
    return token, post(served, "/upload-confirm", token=token, account=account)


class TestAnImportEndsOnTheAccountItLandedIn:
    def test_ImportResult_LinksToTheAccountsLedgerByNameBeforeAnythingElse(self, served):
        _, result = import_csv(served, "second-acct")

        assert result.status_code == 200, result.text
        link = 'href="/ledger?ref=second-acct"'
        assert link in result.text
        assert "Open the ledger for Second Account" in result.text
        assert result.text.index(link) < result.text.index("Back to import")

    def test_ImportResult_WhenTheAccountHadNothingBefore_SaysWhereItStandsNow(self, served):
        _, result = import_csv(served, "second-acct")

        assert "Second Account now has no known balance, so its rows cannot be verified." in (
            text_of(result.text)
        )

    def test_ImportResult_WhenTheFileCutsAnAccountsAgreementShort_SaysFromWhichDayToWhich(
        self, served
    ):
        """The invented file adds a row on 03-20 that the stated balances do not expect, so the
        agreement that reached 03-20 now stops at 03-10, the last balance before it."""
        _, result = import_csv(served, ACCOUNT)

        assert (
            "Everyday is now in agreement through 2026-03-10; before, it was through 2026-03-20."
            in text_of(result.text)
        )
        assert LEDGER_OF_EVERYDAY in result.text

    def test_ConfirmReloaded_AfterAnImportLanded_SaysSoAndLinksToTheAccount(self, served):
        token, first = import_csv(served, "second-acct")
        assert first.status_code == 200

        again = post(served, "/upload-confirm", token=token, account="second-acct")

        assert again.status_code == 410
        said = text_of(again.text)
        assert "march.csv was imported into Second Account" in said
        assert "nothing was imported twice" in said
        assert 'href="/ledger?ref=second-acct"' in again.text
        assert "Upload expired" not in again.text

    def test_ConfirmWithATokenNothingHolds_SaysItCannotTellAndLinksToTheAccount(self, served):
        gone = post(served, "/upload-confirm", token="never-held", account="second-acct")

        assert gone.status_code == 410
        said = text_of(gone.text)
        assert "Upload expired" in gone.text
        assert "may have landed and this page cannot tell" in said
        assert 'href="/ledger?ref=second-acct"' in gone.text
        assert "never-held" not in gone.text, "a bare token says nothing to the person"


class TestEditingAnAccountEndsOnItsLedger:
    def test_EditAnswer_SaysSavedNotDeclaredAndLinksToTheLedger(self, served):
        edited = post(
            served, "/save-account", original_ref=ACCOUNT, ref=ACCOUNT, label="Everyday",
            kind="balance-only",
        )

        said = text_of(edited.text)
        assert edited.status_code == 200
        assert "is saved as" in said
        assert "is declared as" not in said
        assert LEDGER_OF_EVERYDAY in edited.text
        assert (
            "Everyday is now in agreement through 2026-03-25; before, it was through 2026-03-20."
            in said
        )

    def test_DeclareAnswer_StillSaysDeclaredAndLinksToTheNewLedger(self, served):
        made = post(served, "/save-account", ref="brand-new", label="Brand New")

        said = text_of(made.text)
        assert "is declared as" in said
        assert 'href="/ledger?ref=brand-new"' in made.text

    def test_ArchiveAnswer_LinksToTheLedgerBeforeTheOtherButtons(self, served):
        done = post(served, "/archive-account", ref=ACCOUNT, closed="2026-04-01")

        assert done.status_code == 200
        assert done.text.index(LEDGER_OF_EVERYDAY) < done.text.index("Back to declared accounts")


class TestStatedBalancesEndOnTheLedger:
    def test_StateABalance_SaysTheAgreementAsBefore(self, served):
        saved = post(
            served, "/ledger-anchor", ref=ACCOUNT, month="2026-03", day="2026-03-26",
            amount="2.00",
        )

        assert saved.status_code == 200
        assert "Everyday is in agreement through 2026-03-20, as before." in text_of(saved.text)

    def test_StateABalance_WhenRefused_StillOffersTheLedger(self, served):
        refused = post(
            served, "/ledger-anchor", ref=ACCOUNT, month="2026-03", day="2026-03-26",
            amount="not money",
        )

        assert refused.status_code == 400
        assert LEDGER_OF_EVERYDAY in refused.text

    def test_RemoveABalanceTwice_TheSecondAnswerStillOffersTheLedger(self, served):
        first = post(
            served, "/ledger-anchor-remove", ref=ACCOUNT, month="2026-03", day="2026-03-25",
            confirmed="yes",
        )
        second = post(
            served, "/ledger-anchor-remove", ref=ACCOUNT, month="2026-03", day="2026-03-25",
            confirmed="yes",
        )

        assert first.status_code == 200
        assert second.status_code == 404
        assert "No such stated balance" in second.text
        assert LEDGER_OF_EVERYDAY in second.text

    def test_RemoveABalance_SaysTheAgreementNow(self, served):
        removed = post(
            served, "/ledger-anchor-remove", ref=ACCOUNT, month="2026-03", day="2026-03-25",
            confirmed="yes",
        )

        assert "Everyday is in agreement through 2026-03-20, as before." in text_of(removed.text)


def _truelayer_artefact(store: Store, account_ref: str) -> int:
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
            body, account_id="acc-1", kind="booked",
            requested="from=2026-06-01&to=2026-07-31", account_ref=account_ref,
        )
    )
    row = store.connection.execute(
        "SELECT rowid FROM raw_artefacts WHERE account_ref = ?", (account_ref,)
    ).fetchone()
    return int(row[0])


class TestRefilingAndReplayingEndOnTheLedger:
    def test_RefileAnswer_LinksToBothAccountsLedgers(self, served, db):
        with Store(db) as store:
            artefact = _truelayer_artefact(store, "second-acct")
        replay_single_artefact(db, artefact)

        refiled = post(
            served, "/refile-artefact", id=str(artefact), account=ACCOUNT, confirm="yes"
        )

        assert refiled.status_code == 200, refiled.text
        assert LEDGER_OF_EVERYDAY in refiled.text
        assert 'href="/ledger?ref=second-acct"' in refiled.text
        assert refiled.text.index(LEDGER_OF_EVERYDAY) < refiled.text.index(
            'href="/ledger?ref=second-acct"'
        )

    def test_ReplayAnswer_LinksToTheLedgerOfTheAccountTheArtefactIsFiledUnder(self, served, db):
        with Store(db) as store:
            artefact = _truelayer_artefact(store, "second-acct")

        replayed = post(served, "/replay-artefact", id=str(artefact))

        assert replayed.status_code == 200, replayed.text
        assert 'href="/ledger?ref=second-acct"' in replayed.text
