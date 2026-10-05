"""Accounts obdi holds, whether or not anybody declared them.

The household, worked out before the first run. Held or bound, none declared:

    starling-personal   main Starling account: rows from the feed
    starling-space-bills        a Space of it: rows from the feed, which files them
                                under the main account's uid
    starling-space-holiday      a Space of it, bound in the map, no rows yet
    card-one            rows, and a landed truelayer-card-booked artefact only
    current-one         rows, and a landed truelayer-booked artefact
    loose-one           rows and no artefact of any kind
    starling:unbound-x  rows under a provider-qualified fallback: no account can
                        carry that name, so it cannot be declared

and `tin`, declared with kind cash and holding nothing. So six accounts are held
and undeclared, one is declared, and one is unnamed. The only kinds structure can
state are starling-space for the Bills Space (the feed files it under the main
account) and credit-card for card-one (every artefact under it is a card's).
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, date, datetime
from http.server import HTTPServer

import httpx
import pytest

from obdi.account_names import accounts_shown
from obdi.accounts import AccountMap, AccountRecord, AccountRef
from obdi.cli import main as cli_main
from obdi.export_declared import export_declared
from obdi.identity import artefact_digest
from obdi.known_accounts import (
    declare_known_accounts,
    plan_parents,
    read_known_accounts,
    set_space_parents,
)
from obdi.models import RawArtefact
from obdi.navigation import DESTINATIONS
from obdi.providers import starling
from obdi.rebuild import rebuild_from_raw
from obdi.space_attribution import space_parents
from obdi.store import Store
from test_ledger import land, txn
from test_space_attribution import BILLS, BINDINGS, HOLIDAY, LANDER, MAIN, PROVIDER_MAP

CARD, CURRENT, LOOSE, UNBOUND = "card-one", "current-one", "loose-one", "starling:unbound-x"
LABELS = {MAIN: "Personal (starling)"}
HELD_UNDECLARED = {MAIN, BILLS, HOLIDAY, CARD, CURRENT, LOOSE}


def landed(store: Store, source: str, account: str, name: str) -> None:
    payload = json.dumps({"results": [], "name": name}).encode()
    store.land_artefact(
        RawArtefact(
            source=source,
            account_ref=account,
            fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
            media_type="application/json",
            digest=artefact_digest(payload),
            payload=payload,
            origin=name,
        )
    )


def build_household(store: Store) -> None:
    LANDER.land_accounts(store)
    LANDER.land_the_feed(store)
    rebuild_from_raw(store, account_map=PROVIDER_MAP)
    landed(store, "truelayer-card-booked", CARD, "card")
    landed(store, "truelayer-booked", CURRENT, "current")
    for ref in (CARD, CURRENT, LOOSE, UNBOUND):
        land(store, f"d-{ref}", txn(ref, "src-a", f"s-{ref}", date(2026, 9, 3), -100, "SHOP"))
    store.declare_account(AccountRecord(ref=AccountRef("tin"), kind="cash", label="Tin"))


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "known.sqlite3") as opened:
        build_household(opened)
        yield opened


def known(store: Store):
    return read_known_accounts(store, PROVIDER_MAP, named(store))


def named(store: Store):
    return accounts_shown(LABELS, store.declared_accounts())


class TestFindingWhatIsHeldButNotDeclared:
    def test_Known_ListsEveryHeldOrBoundOrDeclaredAccountAndSaysWhichAreDeclared(self, store):
        accounts = {a.ref: a for a in known(store).accounts}

        assert set(accounts) == HELD_UNDECLARED | {"tin"}
        assert [a.ref for a in known(store).accounts if a.declared] == ["tin"]
        assert accounts[HOLIDAY].rows == 0 and accounts[MAIN].rows == 1

    def test_Known_WhenHeldUnderAProviderQualifiedName_CountsItAsUnnamedNotDeclarable(self, store):
        result = known(store)

        assert result.unnamed == 1
        assert UNBOUND not in {a.ref for a in result.accounts}

    def test_Label_IsTheProvidersDisplayNameWhereKnownElseTheCanonicalName(self, store):
        accounts = {a.ref: a for a in known(store).accounts}

        assert accounts[MAIN].label == "Personal (starling)"
        assert accounts[LOOSE].label == LOOSE
        assert accounts["tin"].label == "Tin"

    def test_Kind_IsInferredOnlyFromStructureThatCanBeNamedAndTheReasonIsKept(self, store):
        accounts = {a.ref: a for a in known(store).accounts}

        assert (accounts[BILLS].kind, MAIN in accounts[BILLS].kind_reason) == (
            "starling-space", True,
        )
        assert accounts[CARD].kind == "credit-card"
        assert "truelayer-card-booked" in accounts[CARD].kind_reason
        for ref in (MAIN, HOLIDAY, CURRENT, LOOSE):
            assert (accounts[ref].kind, accounts[ref].kind_reason) == ("", ""), ref

    def test_Kind_WhenACardAccountAlsoHoldsANonCardSource_IsNotGuessedAsACard(self, store):
        landed(store, "truelayer-booked", CARD, "plain")

        assert {a.ref: a.kind for a in known(store).accounts}[CARD] == ""


class TestDeclaringWhatIsKnown:
    def test_Declare_DeclaresExactlyTheListGivenAndNamesEachOne(self, store):
        outcome = declare_known_accounts(store, PROVIDER_MAP, named(store),sorted(HELD_UNDECLARED))

        assert {a.ref for a in outcome.declared} == HELD_UNDECLARED
        records = {str(r.ref): r for r in store.declared_accounts()}
        assert set(records) == HELD_UNDECLARED | {"tin"}
        assert records[MAIN].label == "Personal (starling)" and records[MAIN].kind == ""
        assert records[BILLS].kind == "starling-space"
        assert records[CARD].kind == "credit-card"
        assert records[LOOSE].label == LOOSE

    def test_Declare_SetsASpacesParentWhenItsMainAccountIsDeclaredInTheSamePress(self, store):
        declare_known_accounts(store, PROVIDER_MAP, named(store),sorted(HELD_UNDECLARED))

        records = {str(r.ref): r for r in store.declared_accounts()}
        assert records[BILLS].parent == MAIN
        assert records[MAIN].parent is None

    def test_Declare_WhenTheMainAccountIsNotBeingDeclared_LeavesTheSpacesParentEmpty(self, store):
        declare_known_accounts(store, PROVIDER_MAP, named(store),[BILLS])

        (bills,) = [r for r in store.declared_accounts() if str(r.ref) == BILLS]
        assert bills.parent is None

    def test_Declare_WhenPressedAgain_DeclaresNothingAndReportsTheRefsAsSkipped(self, store):
        declare_known_accounts(store, PROVIDER_MAP, named(store),sorted(HELD_UNDECLARED))
        before = [str(r.ref) for r in store.declared_accounts()]

        again = declare_known_accounts(store, PROVIDER_MAP, named(store),sorted(HELD_UNDECLARED))

        assert again.declared == ()
        assert set(again.skipped) == HELD_UNDECLARED
        assert [str(r.ref) for r in store.declared_accounts()] == before

    def test_Declare_WhenAskedForSomethingObdiDoesNotHold_RefusesItAndDeclaresNoneOfIt(self, store):
        outcome = declare_known_accounts(store, PROVIDER_MAP, named(store),["made-up", UNBOUND])

        assert outcome.declared == () and set(outcome.skipped) == {"made-up", UNBOUND}
        assert {str(r.ref) for r in store.declared_accounts()} == {"tin"}

    def test_Declare_DoesNotTouchAnAccountThatWasDeclaredWithMoreThanAName(self, store):
        store.declare_account(
            AccountRecord(
                ref=AccountRef(LOOSE), kind="savings", label="Mine", opened=date(2020, 1, 1)
            )
        )

        declare_known_accounts(store, PROVIDER_MAP, named(store),[LOOSE])

        (loose,) = [r for r in store.declared_accounts() if str(r.ref) == LOOSE]
        assert (loose.kind, loose.label, loose.opened) == ("savings", "Mine", date(2020, 1, 1))


class TestSpaceParents:
    def declared(self, store, **parents: str) -> None:
        store.declare_account(AccountRecord(ref=AccountRef(MAIN), label="Personal"))
        store.declare_account(
            AccountRecord(
                ref=AccountRef(BILLS),
                kind="starling-space",
                parent=AccountRef(parents["bills"]) if "bills" in parents else None,
            )
        )

    def test_Plan_WhenTheRegistryNamesNoParentAndTheMainIsDeclared_OffersTheProvidersOne(
        self, store
    ):
        self.declared(store)

        plan = plan_parents(store, PROVIDER_MAP)

        assert [(c.space, c.main) for c in plan.settable] == [(BILLS, MAIN)]
        assert plan.waiting == () and plan.disagreeing == ()

    def test_Set_SetsTheParentAndASecondPressHasNothingLeftToSet(self, store):
        self.declared(store)

        first = set_space_parents(store, PROVIDER_MAP, [BILLS])
        second = set_space_parents(store, PROVIDER_MAP, [BILLS])

        assert [(c.space, c.main) for c in first.set_] == [(BILLS, MAIN)]
        assert second.set_ == () and second.skipped == (BILLS,)
        (bills,) = [r for r in store.declared_accounts() if str(r.ref) == BILLS]
        assert bills.parent == MAIN
        assert space_parents(store, PROVIDER_MAP)[BILLS] == MAIN

    def test_Set_OnlySetsTheSpacesItWasGiven(self, store):
        self.declared(store)

        outcome = set_space_parents(store, PROVIDER_MAP, [])

        assert outcome.set_ == ()
        (bills,) = [r for r in store.declared_accounts() if str(r.ref) == BILLS]
        assert bills.parent is None

    def test_Plan_WhenTheRegistryAlreadyNamesADifferentParent_ReportsItAndChangesNothing(
        self, store
    ):
        store.declare_account(AccountRecord(ref=AccountRef("starling-joint")))
        self.declared(store, bills="starling-joint")

        plan = plan_parents(store, PROVIDER_MAP)
        outcome = set_space_parents(store, PROVIDER_MAP, [BILLS])

        assert [(d.space, d.registry, d.provider) for d in plan.disagreeing] == [
            (BILLS, "starling-joint", MAIN)
        ]
        assert plan.settable == () and outcome.set_ == ()
        (bills,) = [r for r in store.declared_accounts() if str(r.ref) == BILLS]
        assert bills.parent == "starling-joint"
        with_registry = AccountMap(BINDINGS, records=store.declared_accounts())
        assert BILLS not in space_parents(store, with_registry), (
            "the fold refuses a Space the two disagree about, as this does"
        )

    def test_Plan_WhenTheRegistryAgreesWithTheProvider_HasNothingToSay(self, store):
        self.declared(store, bills=MAIN)

        plan = plan_parents(store, PROVIDER_MAP)

        assert (plan.settable, plan.waiting, plan.disagreeing) == ((), (), ())

    def test_Plan_WhenTheMainAccountIsNotDeclared_WaitsRatherThanDeclaringIt(self, store):
        store.declare_account(AccountRecord(ref=AccountRef(BILLS), kind="starling-space"))

        plan = plan_parents(store, PROVIDER_MAP)
        outcome = set_space_parents(store, PROVIDER_MAP, [BILLS])

        assert [(c.space, c.main) for c in plan.waiting] == [(BILLS, MAIN)]
        assert plan.settable == () and outcome.set_ == ()
        assert MAIN not in {str(r.ref) for r in store.declared_accounts()}


RENT_UID = "5e117000-0000-4000-8000-000000000002"

#: An account map binding only the main account, so a Space is unbound.
MAIN_ONLY = {
    "bindings": [
        {"canonical_id": MAIN, "source": "starling", "provider_account_id": "acc-main"}
    ]
}


def feed_transfer(when: str) -> dict:
    return {
        "feedItemUid": f"rent-{when}",
        "amount": {"currency": "GBP", "minorUnits": 5000},
        "direction": "OUT",
        "transactionTime": f"{when}T09:00:00.000Z",
        "source": "INTERNAL_TRANSFER",
        "counterPartyType": "CATEGORY",
        "counterPartyUid": RENT_UID,
        "counterPartyName": "Rent",
        "reference": "Rent",
    }


@pytest.fixture
def history(tmp_path, monkeypatch):
    """A deleted Space the provider's feed files under the main account's uid."""
    db = tmp_path / "history.sqlite3"
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps(MAIN_ONLY), encoding="utf-8")
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(accounts))
    monkeypatch.setenv("OBDI_CONNECTION_STORE", "")
    with Store(db) as opened:
        LANDER.land_accounts(opened)
        opened.land_artefact(
            starling.artefact_for(
                json.dumps(
                    {"feedItems": [feed_transfer("2021-06-01"), feed_transfer("2022-11-30")]}
                ).encode(),
                account_id="starling:cat-main",
                kind="feed",
                origin=(
                    "https://api.example.com/api/v2/feed/account/acc-main/"
                    f"category/{RENT_UID}?changesSince=2020-01-01T00:00:00Z"
                ),
            )
        )
    return db


class TestRecoveredSpacesGetTheirParent:
    def test_RecoverSpacesApply_WhenTheMainAccountIsDeclared_SetsTheParentAtDeclaration(
        self, history
    ):
        with Store(history) as opened:
            opened.declare_account(AccountRecord(ref=AccountRef(MAIN)))

        assert cli_main(["--db", str(history), "recover-spaces", "--apply"]) == 0

        with Store(history) as opened:
            (rent,) = [r for r in opened.declared_accounts() if str(r.ref) != MAIN]
        assert rent.kind == "starling-space" and rent.parent == MAIN

    def test_RecoverSpacesApply_WhenTheMainIsNotDeclared_LeavesItEmptyAndThePlanWaits(
        self, history
    ):
        assert cli_main(["--db", str(history), "recover-spaces", "--apply"]) == 0

        with Store(history) as opened:
            (rent,) = opened.declared_accounts()
            plan = plan_parents(opened, PROVIDER_MAP)
        assert rent.parent is None
        assert [(c.space, c.main) for c in plan.waiting] == [(str(rent.ref), MAIN)]

    def test_RecoverSpacesApply_WhenRunAgain_DoesNotClearAParentThatWasSet(self, history):
        cli_main(["--db", str(history), "recover-spaces", "--apply"])
        with Store(history) as opened:
            opened.declare_account(AccountRecord(ref=AccountRef(MAIN)))
            assert set_space_parents(
                opened, PROVIDER_MAP, [str(r.ref) for r in opened.declared_accounts()]
            ).set_

        cli_main(["--db", str(history), "recover-spaces", "--apply"])

        with Store(history) as opened:
            (rent,) = [r for r in opened.declared_accounts() if str(r.ref) != MAIN]
        assert rent.parent == MAIN

    def test_ADeclaredRecoveredSpaceWithNoParent_IsMatchedToTheProvidersStructureByItsUid(
        self, history
    ):
        cli_main(["--db", str(history), "recover-spaces", "--apply"])
        with Store(history) as opened:
            opened.declare_account(AccountRecord(ref=AccountRef(MAIN)))
            plan = plan_parents(opened, PROVIDER_MAP)

        assert [c.main for c in plan.settable] == [MAIN]


class TestTheExportKeepsAnAccountsDates:
    def test_Export_CarriesOpenedClosedParentAndBasisOfADeclaredAccount(self, tmp_path):
        with Store(tmp_path / "e.sqlite3") as opened:
            opened.declare_account(AccountRecord(ref=AccountRef("parent-one")))
            opened.declare_account(
                AccountRecord(
                    ref=AccountRef("child-one"),
                    parent=AccountRef("parent-one"),
                    opened=date(2021, 6, 1),
                    closed=date(2022, 11, 30),
                    date_basis="inferred from the feed",
                )
            )
            export_declared(opened, tmp_path / "out")

        exported = {
            e["ref"]: e
            for e in json.loads((tmp_path / "out" / "declared-accounts.json").read_text("utf-8"))
        }
        child = exported["child-one"]
        assert (child["opened_on"], child["closed_on"]) == ("2021-06-01", "2022-11-30")
        assert (child["parent"], child["date_basis"]) == ("parent-one", "inferred from the feed")
        assert exported["parent-one"]["opened_on"] == ""

    def test_Dates_SurviveABackupAndItsRestoreAsTheyWereExported(self, tmp_path):
        from obdi.backup import take_backup

        with Store(tmp_path / "e.sqlite3") as opened:
            opened.declare_account(
                AccountRecord(
                    ref=AccountRef("child-one"), opened=date(2021, 6, 1), closed=date(2022, 11, 30)
                )
            )
        take_backup(tmp_path / "e.sqlite3", tmp_path / "copy.sqlite3")

        with Store(tmp_path / "copy.sqlite3") as copy:
            export_declared(copy, tmp_path / "out")
            (record,) = copy.declared_accounts()

        (exported,) = json.loads((tmp_path / "out" / "declared-accounts.json").read_text("utf-8"))
        assert (record.opened, record.closed) == (date(2021, 6, 1), date(2022, 11, 30))
        assert (exported["opened_on"], exported["closed_on"]) == ("2021-06-01", "2022-11-30")


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def get(self, path: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", timeout=20)

    def post(self, path: str, data) -> httpx.Response:
        return httpx.post(f"{self.base}{path}", data=data, follow_redirects=False, timeout=20)

    def declared(self) -> set[str]:
        with Store(self.db) as opened:
            return {str(r.ref) for r in opened.declared_accounts()}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    from obdi.cli import build_web_config
    from obdi.web import AuthorisationSession, ConnectionHandler

    db = tmp_path / "known-web.sqlite3"
    with Store(db) as opened:
        build_household(opened)
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "bindings": [
                    {"canonical_id": b.canonical_id, "source": b.source,
                     "provider_account_id": b.provider_account_id}
                    for b in BINDINGS
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(accounts))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


def shown_refs(page: str) -> list[str]:
    form = page.split('action="/declare-known"')[1].split("</form>")[0]
    return re.findall(r'name="ref" value="([^"]+)"', form)


class TestTheAccountsPage:
    def test_AccountsPage_IsAPageOfItsOwnAndNotAnAnchorOnTheOverview(self):
        hrefs = {href for _, _, href in DESTINATIONS}

        assert not any("#" in href for href in hrefs)
        assert "/accounts" not in hrefs, "the accounts page has no tab: Today's list leads to it"

    def test_Page_LeadsWithEveryAccountHeldEachWithItsLedgerAndSaysWhichAreNotDeclared(self, lab):
        page = lab.get("/accounts").text

        assert page.index("Every account obdi holds") < page.index("Held but not declared")
        for ref in HELD_UNDECLARED | {"tin"}:
            assert f"/ledger?ref={ref}" in page, ref
        assert page.count("not declared</span>") == 6
        assert f"/edit-account?ref={MAIN}" not in page, "an undeclared account has nothing to edit"
        assert "/edit-account?ref=tin" in page
        assert "account (starling)" in page, "the provider's own display name"
        assert "1 more account is held under a provider-qualified name" in page

    def test_Page_ExplainsEachInferredKindWithItsReasonAndLeavesTheRestEmpty(self, lab):
        page = lab.get("/accounts").text

        assert "kind starling-space (Starling&#x27;s own feed files it under" in page
        assert "kind credit-card (its rows come only from truelayer-card-booked" in page
        assert page.count("no kind inferred, so it is left empty") == 4

    def test_Get_DeclaresNothingHoweverOftenItIsRead(self, lab):
        for _ in range(3):
            lab.get("/accounts")
            lab.get("/")

        assert lab.declared() == {"tin"}

    def test_Press_DeclaresExactlyWhatTheFormListedAndNamesEachOne(self, lab):
        page = lab.get("/accounts").text
        listed = shown_refs(page)
        assert "Declare these 6 accounts" in page

        result = lab.post("/declare-known", {"ref": listed})

        assert result.status_code == 200
        assert set(listed) == HELD_UNDECLARED
        assert lab.declared() == HELD_UNDECLARED | {"tin"}
        assert "Declared 6 accounts." in result.text
        for ref in HELD_UNDECLARED:
            assert f"<code>{ref}</code>" in result.text, ref

    def test_SecondPress_DeclaresNothingAndSaysSo(self, lab):
        listed = shown_refs(lab.get("/accounts").text)
        lab.post("/declare-known", {"ref": listed})

        again = lab.post("/declare-known", {"ref": listed})

        assert "Nothing was declared" in again.text
        assert "Declare these" not in lab.get("/accounts").text

    def test_Press_DoesNotDeclareAnAccountThatAppearedAfterTheListWasShown(self, lab):
        listed = shown_refs(lab.get("/accounts").text)
        with Store(lab.db) as opened:
            land(opened, "late", txn("late-one", "src-a", "late", date(2026, 9, 4), -5, "LATE"))

        lab.post("/declare-known", {"ref": listed})

        assert "late-one" not in lab.declared()
        assert "late-one" in lab.get("/accounts").text.split("Held but not declared")[1]

    def test_ParentsSection_OffersAnEmptyParentTheProviderNamesAndSetsItOnPress(self, lab):
        with Store(lab.db) as opened:
            opened.declare_account(AccountRecord(ref=AccountRef(MAIN)))
            opened.declare_account(AccountRecord(ref=AccountRef(BILLS), kind="starling-space"))

        page = lab.get("/accounts").text
        assert "Set these 1 parent" in page
        result = lab.post("/set-parents", {"space": [BILLS]})

        assert "Set 1 parent." in result.text
        with Store(lab.db) as opened:
            (bills,) = [r for r in opened.declared_accounts() if str(r.ref) == BILLS]
        assert bills.parent == MAIN
        assert "Set these" not in lab.get("/accounts").text

    def test_ParentsSection_SaysWhenTheRegistryAndTheProviderDisagreeAndOffersNoChange(self, lab):
        with Store(lab.db) as opened:
            for ref in (MAIN, "starling-joint"):
                opened.declare_account(AccountRecord(ref=AccountRef(ref)))
            opened.declare_account(
                AccountRecord(
                    ref=AccountRef(BILLS),
                    kind="starling-space",
                    parent=AccountRef("starling-joint"),
                )
            )

        page = lab.get("/accounts").text

        assert "The registry and the provider disagree" in page
        assert "Set these" not in page
        assert lab.post("/set-parents", {"space": [BILLS]}).text.count("No parent was set") == 1

    def test_ParentsSection_SaysAMainAccountMustBeDeclaredFirst(self, lab):
        with Store(lab.db) as opened:
            opened.declare_account(AccountRecord(ref=AccountRef(BILLS), kind="starling-space"))

        page = lab.get("/accounts").text

        assert "Declare the main account first" in page
        assert "Set these" not in page

    def test_NewPostRoutes_RefuseAPostDrivenByAnotherSite(self, lab):
        for route in ("/declare-known", "/set-parents"):
            response = httpx.post(
                f"{lab.base}{route}",
                data={"ref": MAIN},
                headers={"Origin": "https://evil.example"},
                follow_redirects=False,
            )
            assert response.status_code == 403, route
        assert lab.declared() == {"tin"}

    def test_DeclareSpacesPage_SetsTheParentWhenTheMainIsDeclared(self, tmp_path, monkeypatch):
        """The same press that declares a recovered Space names its parent."""
        from obdi.cli import build_web_config
        from obdi.web import AuthorisationSession, ConnectionHandler

        db = tmp_path / "ds.sqlite3"
        accounts = tmp_path / "accounts.json"
        accounts.write_text(json.dumps(MAIN_ONLY), encoding="utf-8")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(accounts))
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        with Store(db) as opened:
            LANDER.land_accounts(opened)
            opened.land_artefact(
                starling.artefact_for(
                    json.dumps({"feedItems": [feed_transfer("2021-06-01")]}).encode(),
                    account_id="starling:cat-main",
                    kind="feed",
                    origin=(
                        "https://api.example.com/api/v2/feed/account/acc-main/"
                        f"category/{RENT_UID}?changesSince=2020-01-01T00:00:00Z"
                    ),
                )
            )
            opened.declare_account(AccountRecord(ref=AccountRef(MAIN)))
        config = build_web_config(db)
        assert config is not None
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            response = httpx.post(
                f"http://127.0.0.1:{httpd.server_port}/declare-spaces", timeout=20
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 200
        with Store(db) as opened:
            (rent,) = [r for r in opened.declared_accounts() if str(r.ref) != MAIN]
        assert rent.parent == MAIN
