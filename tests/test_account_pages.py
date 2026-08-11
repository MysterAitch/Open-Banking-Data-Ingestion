"""Declaring an account is something the application can do.

Until these pages existed there was no way to create an account from obdi
at all - not a page, not a command - so declaring one meant hand-editing
JSON on the Docker host. That blocked every account with no feed (a
passbook, an empty ISA) and blocked internal-transfer recognition, which
cannot recognise a leg as internal until BOTH ends exist.

Three properties get the hard tests here.

RENAMING MUST NOT RE-IDENTIFY. Both of an account's names are meant to be
renameable; the stable id is the thing that is not. A rename that minted a
new id would look like it worked and would quietly detach everything that
ever resolves through the identity.

CREATING AN ACCOUNT MUST BE A DELIBERATE ACT. The shared picker's
free-text box used to accept any typed name and create a reference for it
on the spot, so one typo produced a second account beside the real one -
with a statement filed into it, and nothing on any page saying so.

THE PAGES ARE USED FROM A PHONE. A bare submit control renders as a small
grey rectangle directly above a full-width link; missing it means leaving
the page instead of doing the thing.
"""

from __future__ import annotations

import re
import threading
from datetime import date

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef, LimitWindow, RateWindow
from obdi.connections import ConnectionStore
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

#: Controls as reachable as the links beside them. The class and the width
#: are asserted rather than looked at, because "it renders small" is
#: invisible to every test that never draws anything.
_BUTTON_TAG = re.compile(r"<button[^>]*>")
_ANCHOR_TAG = re.compile(r"<a [^>]*>")


def assert_tap_targets_are_thumb_sized(markup: str) -> None:
    buttons = _BUTTON_TAG.findall(markup)
    anchors = _ANCHOR_TAG.findall(markup)
    assert buttons or anchors, "a page with nothing to press is a dead end"
    for tag in buttons:
        assert 'class="button"' in tag, f"not styled as a tap target: {tag}"
        assert "width:100%" in tag, f"not full width: {tag}"
    for tag in anchors:
        assert 'class="button"' in tag, f"not styled as a tap target: {tag}"


class Lab:
    """The account pages over real HTTP, backed by a real store."""

    def __init__(self, base: str, db, calls: dict[str, list[object]]) -> None:
        self.base = base
        self.db = db
        self.calls = calls

    def get(self, path: str, **params) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", params=params, timeout=20)

    def post(self, path: str, data: dict[str, str], **kwargs) -> httpx.Response:
        return httpx.post(f"{self.base}{path}", data=data, timeout=20, **kwargs)

    def declared(self) -> list[AccountRecord]:
        with Store(self.db) as store:
            return store.declared_accounts()

    def declared_ref(self, ref: str) -> AccountRecord | None:
        with Store(self.db) as store:
            return store.declared_account(AccountRef(ref))

    def declare(self, record: AccountRecord) -> AccountRecord:
        with Store(self.db) as store:
            return store.declare_account(record)


@pytest.fixture
def lab(tmp_path):
    db = tmp_path / "store.sqlite3"
    calls: dict[str, list[object]] = {
        "refiled": [],
        "assigned": [],
        "previewed": [],
        "imported": [],
    }

    # A store per call, exactly as the serve wiring does it: the hooks run
    # on the server's thread and a sqlite connection belongs to the thread
    # that opened it.
    def declared_accounts() -> list[AccountRecord]:
        with Store(db) as store:
            return store.declared_accounts()

    def declare_account(record: AccountRecord) -> AccountRecord:
        with Store(db) as store:
            return store.declare_account(record)

    def refile_artefact(artefact_id: int, account: str) -> str:
        calls["refiled"].append((artefact_id, account))
        return "starling-personal"

    def assign_kept_statement(artefact_id: int, account: str) -> str:
        calls["assigned"].append((artefact_id, account))
        return "read in: 12 rows"

    def preview_upload(payload: bytes, filename: str, account: str) -> dict[str, object]:
        calls["previewed"].append((filename, account))
        return {
            "parser": "StarlingCsvParser",
            "date_format": "%d/%m/%Y",
            "rows": 1,
            "sample": [],
            "date_ambiguous": False,
            "earliest": "2026-01-01",
            "latest": "2026-01-31",
            "agreement_preview": [],
            "destination_doubt": None,
        }

    def confirm_upload(payload: bytes, filename: str, account: str) -> str:
        calls["imported"].append((filename, account))
        return "landed"

    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        declared_accounts=declared_accounts,
        declare_account=declare_account,
        display_labels=lambda: {"truelayer:xyz": "Old current (halifax)"},
        refile_artefact=refile_artefact,
        assign_kept_statement=assign_kept_statement,
        preview_upload=preview_upload,
        confirm_upload=confirm_upload,
    )
    handler = type(
        "AccountPagesHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db, calls)
    finally:
        httpd.shutdown()
        httpd.server_close()


HALIFAX = AccountRecord(
    ref=AccountRef("halifax-current"),
    kind="current-account",
    label="Halifax Current",
)


class TestTheDeclaredAccountsPage:
    """A registry nobody can read is a file on a host by another name."""

    def test_AccountsPage_WhenAccountsAreDeclared_ShowsNameReferenceAndKind(self, lab):
        lab.declare(HALIFAX)

        page = lab.get("/accounts").text

        assert "Halifax Current" in page
        assert "halifax-current" in page
        assert "current-account" in page

    def test_AccountsPage_WhenAnAccountIsStillOpen_SaysOpen(self, lab):
        lab.declare(HALIFAX)

        assert "open" in lab.get("/accounts").text.lower()

    def test_AccountsPage_WhenAnAccountHasBeenClosed_SaysClosedRatherThanOpen(self, lab):
        lab.declare(
            AccountRecord(
                ref=AccountRef("hsbc-old-current"),
                label="HSBC Old Current",
                closed=date(2020, 1, 31),
            )
        )

        row = lab.get("/accounts").text

        assert "closed" in row.lower()
        assert "2020-01-31" in row

    def test_AccountsPage_WhenAnAccountClosesInTheFuture_StillShowsItAsOpen(self, lab):
        # A closure declared ahead of time is still an account taking
        # transactions today, and calling it closed would make every one
        # of them read as an anomaly.
        lab.declare(
            AccountRecord(
                ref=AccountRef("halifax-current"),
                label="Halifax Current",
                closed=date(2099, 12, 31),
            )
        )

        row = lab.get("/accounts").text

        assert "open" in row.lower()
        assert "closes 2099-12-31" in row

    def test_AccountsPage_WhenNothingIsDeclared_SaysSoAndOffersTheForm(self, lab):
        page = lab.get("/accounts").text

        assert "no accounts" in page.lower()
        assert "/declare-account" in page

    def test_ConnectionsIndex_WhenRendered_LinksToTheDeclaredAccounts(self, lab):
        # A page reachable only by typing its address is a page that does
        # not exist on a phone.
        assert '"/accounts"' in lab.get("/").text

    def test_AccountsPage_WhenAnAccountIsDeclared_OffersAWayToEditIt(self, lab):
        lab.declare(HALIFAX)

        assert "/edit-account?ref=halifax-current" in lab.get("/accounts").text

    def test_AccountsPage_NeverShowsTheStableIdentity(self, lab):
        # Nobody types it and nothing displays it: an opaque identity on
        # screen is an invitation to quote it at something.
        stored = lab.declare(HALIFAX)

        assert stored.stable_id is not None
        assert stored.stable_id not in lab.get("/accounts").text

    def test_AccountsPage_EveryButton_IsAThumbSizedTapTarget(self, lab):
        lab.declare(HALIFAX)

        assert_tap_targets_are_thumb_sized(lab.get("/accounts").text)


class TestDeclaringAnAccount:
    def test_DeclareForm_WhenOpened_OffersNoFieldForTheStableIdentity(self, lab):
        page = lab.get("/declare-account").text

        assert 'name="ref"' in page
        assert 'name="label"' in page
        assert "stable_id" not in page

    def test_DeclareForm_EveryButton_IsAThumbSizedTapTarget(self, lab):
        assert_tap_targets_are_thumb_sized(lab.get("/declare-account").text)

    def test_DeclaringAnAccount_WithOnlyANameAndLabel_PutsItInTheRegistry(self, lab):
        lab.post(
            "/save-account",
            {"ref": "piggy-bank", "label": "Piggy bank", "kind": "cash"},
        )

        stored = lab.declared_ref("piggy-bank")
        assert stored is not None
        assert stored.label == "Piggy bank"
        assert stored.kind == "cash"

    def test_DeclaringAnAccount_MintsAStableIdWithoutAnybodyTypingOne(self, lab):
        lab.post("/save-account", {"ref": "piggy-bank", "label": "Piggy bank"})

        stored = lab.declared_ref("piggy-bank")
        assert stored is not None
        assert stored.stable_id is not None
        assert stored.stable_id.startswith("acc_")

    def test_DeclaringAnAccount_MakesItSelectableOnTheImportDoor(self, lab):
        # A registry nothing can select from is useless: the account exists
        # precisely so a statement can be filed into it.
        lab.post("/save-account", {"ref": "piggy-bank", "label": "Piggy bank"})

        assert '<option value="piggy-bank">Piggy bank</option>' in lab.get("/").text

    def test_DeclaringAnAccount_MakesItSelectableOnTheStatementAssignForm(self, lab):
        lab.post("/save-account", {"ref": "piggy-bank", "label": "Piggy bank"})

        # Typing it is no longer questioned, which is the picker's other
        # half of the same registry.
        lab.post(
            "/statement-assign",
            {"artefact": "7", "account": "", "account_other": "piggy-bank"},
        )

        assert lab.calls["assigned"] == [(7, "piggy-bank")]

    def test_DeclaringAnAccount_WithNoReference_IsRefusedAndStoresNothing(self, lab):
        response = lab.post("/save-account", {"ref": "", "label": "Nameless"})

        assert response.status_code == 400
        assert lab.declared() == []

    def test_DeclaringAnAccount_WithAReferenceThatIsNotAName_IsRefused(self, lab):
        # The colon is load-bearing: references are qualified as
        # "<provider>:<id>", so a canonical name holding one could pose as
        # evidence rather than as configuration.
        response = lab.post("/save-account", {"ref": "starling:abc", "label": "Sneaky"})

        assert response.status_code == 400
        assert lab.declared() == []

    def test_DeclaringAnAccount_WithADateNobodyCanRead_IsRefusedNamingTheField(self, lab):
        response = lab.post(
            "/save-account", {"ref": "piggy-bank", "opened": "last tuesday"}
        )

        assert response.status_code == 400
        assert "opened" in response.text
        assert lab.declared() == []

    def test_DeclaringAnAccount_UnderAReferenceAlreadyDeclared_RefusesRatherThanOverwrite(
        self, lab
    ):
        # Declaring is a CREATE act. Silently editing would overwrite an
        # account the person never had on screen - including the windows
        # the form does not carry - so it refuses and points at the edit
        # page instead.
        lab.declare(HALIFAX)

        response = lab.post(
            "/save-account", {"ref": "halifax-current", "label": "Something else"}
        )

        assert response.status_code == 409
        assert "/edit-account?ref=halifax-current" in response.text
        stored = lab.declared_ref("halifax-current")
        assert stored is not None
        assert stored.label == "Halifax Current"

    def test_DeclaringAnAccount_WhoseParentWasNeverDeclared_IsRefused(self, lab):
        response = lab.post(
            "/save-account", {"ref": "halifax-saver", "parent": "halifax-currnet"}
        )

        assert response.status_code == 400
        assert lab.declared() == []

    def test_DeclaringAnAccount_AsItsOwnParent_IsRefused(self, lab):
        response = lab.post(
            "/save-account", {"ref": "piggy-bank", "parent": "piggy-bank"}
        )

        assert response.status_code == 400
        assert lab.declared() == []

    def test_DeclaringAnAccount_ClosedBeforeItOpened_IsRefused(self, lab):
        # Two dates that cannot both be true are a typo, and the lifecycle
        # guard would otherwise call every row in the account an anomaly.
        response = lab.post(
            "/save-account",
            {"ref": "piggy-bank", "opened": "2026-01-01", "closed": "2025-01-01"},
        )

        assert response.status_code == 400
        assert lab.declared() == []


class TestEditingAnAccount:
    def test_EditForm_WhenOpenedForADeclaredAccount_CarriesEveryFieldBack(self, lab):
        # What you type is what comes back: a field that silently fails to
        # round-trip is a field the next edit erases.
        lab.declare(AccountRecord(ref=AccountRef("halifax-current"), label="Parent"))
        lab.post(
            "/save-account",
            {
                "ref": "halifax-clarity",
                "label": "Halifax Clarity",
                "kind": "credit-card",
                "parent": "halifax-current",
                "opened": "2025-07-01",
                "closed": "2026-03-31",
            },
        )

        page = lab.get("/edit-account", ref="halifax-clarity").text

        assert 'name="ref" value="halifax-clarity"' in page
        assert 'name="label" value="Halifax Clarity"' in page
        assert 'name="kind" value="credit-card"' in page
        assert 'name="parent" value="halifax-current"' in page
        assert 'name="opened" value="2025-07-01"' in page
        assert 'name="closed" value="2026-03-31"' in page

    def test_EditForm_ForAnAccountNobodyDeclared_SaysSoRatherThanShowingABlankForm(
        self, lab
    ):
        assert lab.get("/edit-account", ref="never-declared").status_code == 404

    def test_EditForm_NeverShowsTheStableIdentity(self, lab):
        stored = lab.declare(HALIFAX)

        page = lab.get("/edit-account", ref="halifax-current").text

        assert stored.stable_id is not None
        assert stored.stable_id not in page
        assert "stable_id" not in page

    def test_EditForm_EveryButton_IsAThumbSizedTapTarget(self, lab):
        lab.declare(HALIFAX)

        assert_tap_targets_are_thumb_sized(
            lab.get("/edit-account", ref="halifax-current").text
        )

    def test_EditingAnAccount_KeepsTheWindowsTheFormDoesNotCarry(self, lab):
        # Declaring REPLACES an account's limits and rates, and the form
        # holds neither - so a label change would otherwise silently
        # discard the credit limit and the reversion rate behind it.
        lab.declare(
            AccountRecord(
                ref=AccountRef("halifax-clarity"),
                label="Halifax Clarity",
                limits=(LimitWindow("credit", date(2025, 7, 1), None, 500000),),
                rates=(RateWindow("purchase", date(2025, 7, 1), None, 22.45),),
            )
        )

        lab.post(
            "/save-account",
            {
                "original_ref": "halifax-clarity",
                "ref": "halifax-clarity",
                "label": "Clarity card",
            },
        )

        stored = lab.declared_ref("halifax-clarity")
        assert stored is not None
        assert stored.label == "Clarity card"
        assert stored.limits == (LimitWindow("credit", date(2025, 7, 1), None, 500000),)
        assert stored.rates == (RateWindow("purchase", date(2025, 7, 1), None, 22.45),)

    def test_EditingAnAccount_ChangesTheFieldItWasGiven(self, lab):
        lab.declare(HALIFAX)

        lab.post(
            "/save-account",
            {
                "original_ref": "halifax-current",
                "ref": "halifax-current",
                "label": "Halifax Current Account",
                "kind": "current-account",
                "closed": "2026-06-30",
            },
        )

        stored = lab.declared_ref("halifax-current")
        assert stored is not None
        assert stored.label == "Halifax Current Account"
        assert stored.closed == date(2026, 6, 30)


class TestRenamingKeepsTheIdentity:
    """The property the whole three-name identity model exists for."""

    def test_RenamingTheDisplayName_LeavesTheStableIdUntouched(self, lab):
        minted = lab.declare(HALIFAX).stable_id

        lab.post(
            "/save-account",
            {
                "original_ref": "halifax-current",
                "ref": "halifax-current",
                "label": "Everyday account",
            },
        )

        renamed = lab.declared_ref("halifax-current")
        assert renamed is not None
        assert renamed.label == "Everyday account"
        assert renamed.stable_id == minted

    def test_RenamingTheCanonicalReference_LeavesTheStableIdUntouched(self, lab):
        minted = lab.declare(HALIFAX).stable_id

        lab.post(
            "/save-account",
            {
                "original_ref": "halifax-current",
                "ref": "halifax-everyday",
                "label": "Halifax Current",
            },
        )

        renamed = lab.declared_ref("halifax-everyday")
        assert renamed is not None
        assert renamed.stable_id == minted
        assert lab.declared_ref("halifax-current") is None
        assert len(lab.declared()) == 1, "a rename must not leave the old one behind"

    def test_RenamingBothNamesAtOnce_LeavesTheStableIdUntouched(self, lab):
        minted = lab.declare(HALIFAX).stable_id

        lab.post(
            "/save-account",
            {
                "original_ref": "halifax-current",
                "ref": "halifax-everyday",
                "label": "Everyday account",
            },
        )

        renamed = lab.declared_ref("halifax-everyday")
        assert renamed is not None
        assert renamed.label == "Everyday account"
        assert renamed.stable_id == minted

    def test_RenamingOntoANameAnotherAccountHolds_IsRefusedAndChangesNothing(self, lab):
        lab.declare(HALIFAX)
        second = lab.declare(
            AccountRecord(ref=AccountRef("halifax-saver"), label="Halifax Saver")
        )

        response = lab.post(
            "/save-account",
            {
                "original_ref": "halifax-saver",
                "ref": "halifax-current",
                "label": "Halifax Saver",
            },
        )

        assert response.status_code == 409
        unchanged = lab.declared_ref("halifax-saver")
        assert unchanged is not None
        assert unchanged.stable_id == second.stable_id


class TestTypingAnAccountThatIsNotDeclared:
    """Creating an account must be a distinct, confirmed act.

    The free-text box stays - naming a new destination while looking at
    the document is the workflow - but a name that matches nothing asks
    before it becomes an account.
    """

    def test_ATypedNameMatchingNothing_AsksInsteadOfCreatingItSilently(self, lab):
        response = lab.post(
            "/refile-artefact",
            {
                "id": "42",
                "account": "",
                "account_other": "piggy-bank",
                "confirm": "yes",
            },
        )

        assert response.status_code == 409
        assert "piggy-bank" in response.text
        assert lab.calls["refiled"] == [], "nothing may be filed into an account nobody declared"
        assert lab.declared() == []

    def test_ATypedNameOnceConfirmed_IsDeclaredAndTheRefileProceeds(self, lab):
        lab.post(
            "/refile-artefact",
            {
                "id": "42",
                "account": "",
                "account_other": "piggy-bank",
                "confirm": "yes",
                "confirm_new_account": "piggy-bank",
            },
        )

        assert lab.declared_ref("piggy-bank") is not None
        assert lab.calls["refiled"] == [(42, "piggy-bank")]

    def test_ATypedNameThatIsAPlausibleTypo_NamesTheRealAccountBack(self, lab):
        lab.declare(HALIFAX)

        response = lab.post(
            "/refile-artefact",
            {
                "id": "42",
                "account_other": "halifax-currnet",
                "confirm": "yes",
            },
        )

        assert response.status_code == 409
        assert "halifax-current" in response.text
        assert lab.calls["refiled"] == []

    def test_ATypedNameResemblingNothingDeclared_AsksWithoutInventingASuggestion(self, lab):
        lab.declare(HALIFAX)

        response = lab.post(
            "/refile-artefact",
            {"id": "42", "account_other": "piggy-bank", "confirm": "yes"},
        )

        assert response.status_code == 409
        assert "halifax-current" not in response.text, (
            "an unrelated name suggested as the closest match is worse than none"
        )

    def test_ATypedNameThatIsDeclared_ProceedsWithoutBeingQuestioned(self, lab):
        lab.declare(HALIFAX)

        response = lab.post(
            "/refile-artefact",
            {"id": "42", "account_other": "halifax-current", "confirm": "yes"},
        )

        assert response.status_code == 200
        assert lab.calls["refiled"] == [(42, "halifax-current")]

    def test_AnAccountChosenFromTheDropdown_IsNeverQuestioned(self, lab):
        # The dropdown only offers references that already exist, declared
        # or not: an unbound provider reference is a real destination.
        response = lab.post(
            "/refile-artefact",
            {"id": "42", "account": "truelayer:xyz", "confirm": "yes"},
        )

        assert response.status_code == 200
        assert lab.calls["refiled"] == [(42, "truelayer:xyz")]

    def test_ATypedNameThatCouldNeverBeAnAccountName_IsRefusedNotDeclared(self, lab):
        # The colon is load-bearing: a canonical name holding one could
        # pose as a source-qualified provider reference. The guard is the
        # last door where a name becomes an account, so it validates too.
        response = lab.post(
            "/refile-artefact",
            {"id": "42", "account_other": "starling:abc123", "confirm": "yes"},
        )

        assert response.status_code == 400
        assert lab.declared() == []
        assert lab.calls["refiled"] == []

    def test_TheAskPage_EveryButton_IsAThumbSizedTapTarget(self, lab):
        lab.declare(HALIFAX)

        response = lab.post(
            "/refile-artefact",
            {"id": "42", "account_other": "halifax-currnet", "confirm": "yes"},
        )

        assert_tap_targets_are_thumb_sized(response.text)

    def test_ATypedNameOnTheAssignForm_AsksBeforeAnythingIsReadIn(self, lab):
        response = lab.post(
            "/statement-assign",
            {"artefact": "7", "account": "", "account_other": "piggy-bank"},
        )

        assert response.status_code == 409
        assert lab.calls["assigned"] == []
        assert lab.declared() == []

    def test_ATypedNameOnTheAssignForm_OnceConfirmed_DeclaresItAndReadsIn(self, lab):
        lab.post(
            "/statement-assign",
            {
                "artefact": "7",
                "account_other": "piggy-bank",
                "confirm_new_account": "piggy-bank",
            },
        )

        assert lab.declared_ref("piggy-bank") is not None
        assert lab.calls["assigned"] == [(7, "piggy-bank")]

    def test_ATypedNameOnTheImportDoor_AsksBeforeTheFileIsEvenParsed(self, lab):
        response = lab.post(
            "/upload",
            {"account_other": "piggy-bank"},
            files={"statement": ("chunk.csv", b"a,b\n", "text/csv")},
        )

        assert response.status_code == 409
        assert lab.calls["previewed"] == []
        assert lab.declared() == []

    def test_ATypedNameOnTheImportDoor_OnceConfirmed_KeepsTheFileAndPreviewsIt(self, lab):
        # Refusing must not cost the person their upload: the file was
        # already sent, and a phone on a slow uplink does not send it twice.
        asked = lab.post(
            "/upload",
            {"account_other": "piggy-bank"},
            files={"statement": ("chunk.csv", b"a,b\n", "text/csv")},
        )
        token = asked.text.split('name="token" value="')[1].split('"')[0]

        preview = lab.post(
            "/upload-preview",
            {
                "token": token,
                "account_other": "piggy-bank",
                "confirm_new_account": "piggy-bank",
            },
        )

        assert preview.status_code == 200
        assert lab.declared_ref("piggy-bank") is not None
        assert lab.calls["previewed"] == [("chunk.csv", "piggy-bank")]

    def test_TheNearestAccountOffered_CanBeTakenWithoutDeclaringAnything(self, lab):
        lab.declare(HALIFAX)

        asked = lab.post(
            "/refile-artefact",
            {"id": "42", "account_other": "halifax-currnet", "confirm": "yes"},
        )
        assert 'value="halifax-current"' in asked.text

        taken = lab.post(
            "/refile-artefact",
            {"id": "42", "account": "halifax-current", "confirm": "yes"},
        )

        assert taken.status_code == 200
        assert lab.calls["refiled"] == [(42, "halifax-current")]
        assert len(lab.declared()) == 1, "taking the real one declares nothing new"
