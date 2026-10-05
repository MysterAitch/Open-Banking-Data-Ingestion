"""Choosing an account, and describing one, without typing what the page could have offered.

The import and assignment pickers listed only DECLARED accounts, so a person whose accounts held
rows but had never been declared typed a name, was told "No such account", and was offered as the
FIRST button "Use <the closest name>" - a card statement was offered to an unrelated account.
Declare and Edit took Kind and Parent as free text ("type balance-only").
"""

from __future__ import annotations

import html
import re
import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.account_names import accounts_shown
from obdi.accounts import AccountRecord, AccountRef
from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from obdi.web_accounts import ACCOUNT_KINDS, picker_options
from test_balance_anchors import everyday

HELD_ONLY = "held-only"
BOTH = "both-ways"
DECLARED_ONLY = "declared-only"
CSV = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes\n"
    b"01/03/2026,Cafe One,card,CARD,-3.50,96.50,\n"
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pickers.sqlite3"
    with Store(path) as store:
        everyday(store, account=HELD_ONLY)
        everyday(store, account=BOTH)
        store.declare_account(
            AccountRecord(ref=AccountRef(BOTH), label="Both Ways", kind="savings")
        )
        store.declare_account(
            AccountRecord(ref=AccountRef(DECLARED_ONLY), label="Declared Only", kind="odd-kind")
        )
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


def option_of(page: str, ref: str) -> str:
    found = re.search(rf'<option value="{re.escape(ref)}"[^>]*>(.*?)</option>', page)
    assert found is not None, f"no option for {ref}"
    return found.group(1)


class TestThePickerOffersEveryAccountThatHoldsRowsOrIsDeclared:
    def test_ImportPicker_ListsAnAccountThatHoldsRowsButIsNotDeclared_AndSaysSo(self, served):
        page = httpx.get(f"{served}/import", timeout=60).text

        assert "holds rows, not declared" in option_of(page, HELD_ONLY)

    def test_ImportPicker_ListsADeclaredAccountThatHoldsNothing_AndSaysItIsDeclared(self, served):
        page = httpx.get(f"{served}/import", timeout=60).text

        said = option_of(page, DECLARED_ONLY)
        assert "Declared Only" in said
        assert "declared" in said
        assert "holds rows" not in said

    def test_ImportPicker_ForAnAccountBothDeclaredAndHeld_SaysBoth(self, served):
        page = httpx.get(f"{served}/import", timeout=60).text

        said = option_of(page, BOTH)
        assert "Both Ways" in said
        assert "declared, holds rows" in said

    def test_TypingTheNameOfAnAccountThatHoldsRows_IsAcceptedWithoutBeingQuestioned(self, served):
        answer = httpx.post(
            f"{served}/upload",
            data={"account_other": HELD_ONLY},
            files={"statement": ("march.csv", CSV, "text/csv")},
            timeout=60,
        )

        assert answer.status_code == 200
        assert "No such account" not in answer.text

    def test_Labels_WhenNoAccountHoldsRowsAndNoneIsDeclared_AreTheProvidersAlone(self):
        names = accounts_shown({"a": "A (provider)"}, [])

        assert picker_options(names, [], held=()) == {"a": "A (provider)"}


class TestAnUnknownNameIsNeverAnsweredWithAGuess:
    def ask(self, served: str, typed: str) -> httpx.Response:
        return httpx.post(
            f"{served}/upload",
            data={"account_other": typed},
            files={"statement": ("march.csv", CSV, "text/csv")},
            timeout=60,
        )

    def test_PlausibleTypo_OffersDeclaringWhatWasTypedFirstAndTheSuggestionSecond(self, served):
        asked = self.ask(served, "both-wayz")

        assert asked.status_code == 409
        primary = asked.text.index("Declare both-wayz and continue")
        suggestion = asked.text.index("Use both-ways instead")
        assert primary < suggestion

    def test_PlausibleTypo_SaysWhyTheSuggestionWasMade(self, served):
        asked = self.ask(served, "both-wayz")

        assert "suggested only because its spelling is close to what you typed" in asked.text

    def test_Suggestion_IsNotTheDefaultStyledButton(self, served):
        asked = self.ask(served, "both-wayz")

        suggestion = re.search(r"<button([^>]*)>Use both-ways instead</button>", asked.text)
        primary = re.search(r"<button([^>]*)>Declare both-wayz and continue</button>", asked.text)
        assert suggestion is not None and primary is not None
        assert suggestion.group(1) != primary.group(1)

    def test_NameResemblingNothing_OffersOnlyDeclaringIt(self, served):
        asked = self.ask(served, "piggy-bank")

        assert "Declare piggy-bank and continue" in asked.text
        assert "instead" not in asked.text.split("</style>")[1]

    def test_SuggestionPostsTheExistingAccountAndTheOtherPostsTheTypedName(self, served):
        asked = self.ask(served, "both-wayz").text

        assert asked.count('name="account" value="both-ways"') == 1
        assert 'name="account_other" value="both-wayz"' in asked


def declare_form(served: str, ref: str = "") -> str:
    if ref:
        return httpx.get(f"{served}/edit-account", params={"ref": ref}, timeout=60).text
    return httpx.get(f"{served}/declare-account", timeout=60).text


class TestKindIsChosenFromWhatTheCodeKnows:
    def test_DeclareForm_OffersKindAsASelectWithEveryKnownKindAndOther(self, served):
        page = declare_form(served)

        select = re.search(r'<select name="kind".*?</select>', page, re.S)
        assert select is not None
        for kind, _ in ACCOUNT_KINDS:
            assert f'value="{kind}"' in select.group(0)
        assert 'value="other"' in select.group(0)
        assert 'name="kind_other"' in page

    def test_DeclareForm_SaysOnOneLineWhatEachKindDoes(self, served):
        page = declare_form(served)

        for kind, line in ACCOUNT_KINDS:
            assert kind in page
            assert html.escape(line) in page
        assert "balance-only" in page
        assert "Tracked by the balances you state alone" in page

    def test_EditForm_ForAKindTheCodeDoesNotKnow_SelectsOtherAndKeepsTheText(self, served):
        page = declare_form(served, DECLARED_ONLY)

        assert re.search(r'<option value="other" selected>', page)
        assert 'name="kind_other" value="odd-kind"' in page

    def test_EditForm_ForAKnownKind_SelectsItAndLeavesOtherEmpty(self, served):
        page = declare_form(served, BOTH)

        assert re.search(r'<option value="savings" selected>', page)
        assert 'name="kind_other" value=""' in page

    def test_Save_WhenAKnownKindIsChosen_StoresIt(self, served, db):
        httpx.post(
            f"{served}/save-account",
            data={"ref": "mortgage-one", "kind": "balance-only"},
            timeout=60,
        )

        with Store(db) as store:
            assert store.declared_kind("mortgage-one") == "balance-only"

    def test_Save_WhenOtherIsChosen_StoresTheTypedKind(self, served, db):
        httpx.post(
            f"{served}/save-account",
            data={"ref": "tin-of-cash", "kind": "other", "kind_other": "biscuit-tin"},
            timeout=60,
        )

        with Store(db) as store:
            assert store.declared_kind("tin-of-cash") == "biscuit-tin"

    def test_Save_WhenOtherIsChosenWithNothingTyped_StoresNoKind(self, served, db):
        httpx.post(
            f"{served}/save-account",
            data={"ref": "no-kind", "kind": "other", "kind_other": ""},
            timeout=60,
        )

        with Store(db) as store:
            assert store.declared_kind("no-kind") == ""


class TestParentIsChosenFromDeclaredAccounts:
    def test_DeclareForm_OffersParentAsASelectOfDeclaredAccounts(self, served):
        page = declare_form(served)

        select = re.search(r'<select name="parent".*?</select>', page, re.S)
        assert select is not None
        assert f'value="{BOTH}"' in select.group(0)
        assert f'value="{DECLARED_ONLY}"' in select.group(0)
        assert f'value="{HELD_ONLY}"' not in select.group(0), "an account nobody declared"

    def test_EditForm_OffersEveryDeclaredAccountExceptItself(self, served):
        page = declare_form(served, BOTH)

        select = re.search(r'<select name="parent".*?</select>', page, re.S)
        assert select is not None
        assert f'value="{DECLARED_ONLY}"' in select.group(0)
        assert f'value="{BOTH}"' not in select.group(0)

    def test_Save_WithAParentChosen_StoresIt(self, served, db):
        httpx.post(
            f"{served}/save-account",
            data={"ref": "pot-one", "parent": BOTH},
            timeout=60,
        )

        with Store(db) as store:
            record = store.declared_account(AccountRef("pot-one"))
        assert record is not None
        assert str(record.parent) == BOTH
