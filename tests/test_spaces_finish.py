"""Declaring a recovered Space also binds it, so the next pull can ask for its history.

The household, invented, September 2026. Account `acc-1` (main category
`cat-main`, declared as `starling-personal`) has a live Space Bills and a closed
Space Rent (`cat-closed`) that the main feed names in an internal transfer and
the provider no longer lists. The first pull lands the main feed, which is what
lets the Spaces page recover Rent. Declaring Rent used to stop there: nothing
bound its category, so no later pull ever asked for its own feed.

Each Space's answer is known before the page is read: `starling-space-rent-catclose`
is the ref derived from the name and the first eight letters of the uid.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from http.server import HTTPServer

import httpx
import pytest

import test_closed_space_feed as household
from obdi.accounts import AccountRecord, AccountRef
from obdi.cli import _account_map, build_web_config
from obdi.cli import main as cli_main
from obdi.providers import starling
from obdi.pull import STARLING_CONNECTION, pull_starling
from obdi.space_windows import CLOSED_SPACE_MARK
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler

RENT = "starling-space-rent-catclose"
GARDEN = "starling-space-garden-catgarde"
ELSEWHERE = "starling-old-rent"


def strip_tags(page: str) -> str:
    return re.sub(r"<[^>]+>", "", page).replace("&#x27;", "'")


class Lab:
    def __init__(self, base: str, db, map_file, provider) -> None:
        self.base = base
        self.db = db
        self.map_file = map_file
        self.provider = provider

    def get(self, path: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", timeout=20)

    def press(self) -> httpx.Response:
        return httpx.post(f"{self.base}/declare-spaces", timeout=20)

    def spaces_page(self) -> str:
        return strip_tags(self.get("/spaces").text)

    def declared(self) -> dict[str, AccountRecord]:
        with Store(self.db) as opened:
            return {str(r.ref): r for r in opened.declared_accounts()}

    def bindings(self) -> dict[str, str]:
        raw = json.loads(self.map_file.read_text(encoding="utf-8"))
        return {b["provider_account_id"]: b["canonical_id"] for b in raw["bindings"]}

    def bind_by_hand(self, uid: str, canonical: str) -> None:
        raw = json.loads(self.map_file.read_text(encoding="utf-8"))
        raw["bindings"].append(
            {"source": "starling", "provider_account_id": uid, "canonical_id": canonical}
        )
        self.map_file.write_text(json.dumps(raw), encoding="utf-8")

    def pull(self) -> None:
        with Store(self.db) as store:
            pull_starling(store, "token", account_map=_account_map(store))

    def rows(self, ref: str) -> int:
        with Store(self.db) as store:
            return len(store.transactions_for_account(ref))


def start(tmp_path, monkeypatch, *, closed: str = "history", feed=None):
    db = tmp_path / "spaces.sqlite3"
    map_file = tmp_path / "accounts.json"
    map_file.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "source": "starling",
                        "provider_account_id": "acc-1",
                        "canonical_id": household.MAIN,
                    },
                    {
                        "source": "starling",
                        "provider_account_id": "cat-bills",
                        "canonical_id": household.BILLS,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_file))
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    if feed is not None:
        monkeypatch.setattr(household, "MAIN_FEED", feed)
    provider = household.Provider(monkeypatch, closed)
    with Store(db) as store:
        store.declare_account(AccountRecord(ref=AccountRef(household.MAIN)))
        store.declare_account(
            AccountRecord(
                ref=AccountRef(household.BILLS),
                kind="starling-space",
                parent=AccountRef(household.MAIN),
            )
        )
        pull_starling(store, "token", account_map=_account_map(store))
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, Lab(f"http://127.0.0.1:{httpd.server_port}", db, map_file, provider)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    httpd, opened = start(tmp_path, monkeypatch)
    try:
        yield opened
    finally:
        httpd.shutdown()


@pytest.fixture
def two_closed(tmp_path, monkeypatch):
    feed = [
        *household.MAIN_FEED,
        household.item("m-garden", 3, 5000, "OUT", to="cat-garden", name="Garden"),
    ]
    httpd, opened = start(tmp_path, monkeypatch, feed=feed)
    try:
        yield opened
    finally:
        httpd.shutdown()


def declare_as_an_older_version_did(lab: Lab, ref: str, name: str) -> None:
    """Declared by the press as it was before it bound anything."""
    with Store(lab.db) as store:
        store.declare_account(AccountRecord(ref=AccountRef(ref), kind="starling-space", label=name))


class TestPressingTheButtonOnAnUndeclaredClosedSpace:
    def test_Page_BeforeThePress_SaysTheSpaceIsNotDeclaredAndOffersToDeclareAndBindIt(self, lab):
        page = lab.spaces_page()

        assert RENT in page
        assert "not declared" in page
        assert "Declare and bind 1 Space" in page
        assert "Declare 1 account" not in page

    def test_Press_DeclaresTheSpaceAndBindsItsCategoryToTheAccountJustDeclared(self, lab):
        response = lab.press()

        assert response.status_code == 200
        assert RENT in lab.declared()
        assert lab.declared()[RENT].kind == "starling-space"
        assert lab.bindings()["cat-closed"] == RENT

    def test_NextPull_AfterThePress_AsksForTheClosedSpacesFeedAndHoldsItsRows(self, lab):
        assert "cat-closed" not in lab.provider.asks
        lab.press()

        lab.pull()

        assert lab.provider.asks.count("cat-closed") == 1
        assert lab.rows(RENT) == 2

    def test_NextPull_WhenTheSpaceIsDeclaredButNotPressedFor_StillDoesNotAskForIt(self, lab):
        declare_as_an_older_version_did(lab, RENT, "Rent")

        lab.pull()

        assert "cat-closed" not in lab.provider.asks

    def test_ResultPage_ListsWhatWasDoneAndSaysWhatHappensNext(self, lab):
        text = strip_tags(lab.press().text)

        assert f"{RENT} - declared and bound" in text
        assert "Declared 1 account and bound 1 category" in text
        assert "next pull asks for each Space's history" in text
        assert "obdi pull starling" in text

    def test_Pages_AreMaskedAndCarryNoFigure(self, lab):
        pages = [lab.get("/spaces").text, lab.press().text, lab.get("/spaces").text]

        for page in pages:
            assert "400.00" not in page
            assert "40000" not in page
            assert "100000" not in page
            assert "amount" not in strip_tags(page).lower()


class TestASpaceDeclaredByAVersionThatDidNotBind:
    def test_Page_SaysDeclaredButItsCategoryIsNotBoundAndOffersToFinishIt(self, lab):
        declare_as_an_older_version_did(lab, RENT, "Rent")

        page = lab.spaces_page()

        assert "declared, category not bound" in page
        assert "Declare and bind 1 Space" in page

    def test_Press_BindsItWithoutDeclaringItAgainOrChangingTheDeclaredRecord(self, lab):
        declare_as_an_older_version_did(lab, RENT, "Rent")
        before = lab.declared()[RENT]

        text = strip_tags(lab.press().text)

        assert lab.bindings()["cat-closed"] == RENT
        assert lab.declared()[RENT] == before
        assert f"{RENT} - bound (already declared)" in text
        assert "Declared 0 accounts and bound 1 category" in text

    def test_Press_AlsoFinishesAnUndeclaredSpaceAlongsideIt(self, two_closed):
        declare_as_an_older_version_did(two_closed, RENT, "Rent")

        text = strip_tags(two_closed.press().text)

        assert two_closed.bindings()["cat-closed"] == RENT
        assert two_closed.bindings()["cat-garden"] == GARDEN
        assert f"{RENT} - bound (already declared)" in text
        assert f"{GARDEN} - declared and bound" in text
        assert "Declared 1 account and bound 2 categories" in text


class TestASpaceBoundToADifferentAccount:
    def test_Press_LeavesItUntouchedAndSaysTheTwoDisagree(self, lab):
        lab.bind_by_hand("cat-closed", ELSEWHERE)
        before = lab.map_file.read_text(encoding="utf-8")

        text = strip_tags(lab.press().text)

        assert lab.map_file.read_text(encoding="utf-8") == before
        assert RENT not in lab.declared()
        assert f"{RENT} - category bound to {ELSEWHERE}, which disagrees" in text
        assert "Nothing to do" in text

    def test_Page_SaysItDisagreesAndDoesNotCountItAsUnfinished(self, lab):
        lab.bind_by_hand("cat-closed", ELSEWHERE)

        page = lab.spaces_page()

        assert f"bound to {ELSEWHERE}" in page
        assert "disagrees" in page
        assert "Declare and bind" not in page

    def test_Press_StillFinishesTheOtherSpacesInTheSamePress(self, two_closed):
        two_closed.bind_by_hand("cat-closed", ELSEWHERE)

        text = strip_tags(two_closed.press().text)

        assert two_closed.bindings()["cat-closed"] == ELSEWHERE
        assert two_closed.bindings()["cat-garden"] == GARDEN
        assert GARDEN in two_closed.declared()
        assert RENT not in two_closed.declared()
        assert "which disagrees" in text


class TestPressingASecondTime:
    def test_Press_WhenEverythingIsFinished_DoesNothingAndSaysSo(self, lab):
        lab.press()
        declared, bindings = lab.declared(), lab.map_file.read_text(encoding="utf-8")

        text = strip_tags(lab.press().text)

        assert "Nothing to do - no recovered Space is left to declare or bind" in text
        assert lab.declared() == declared
        assert lab.map_file.read_text(encoding="utf-8") == bindings
        assert "next pull" not in text

    def test_Page_AfterThePress_OffersNoButton(self, lab):
        lab.press()

        page = lab.spaces_page()

        assert "Declare and bind" not in page
        assert "declared and bound" in page


class TestReadingThePageChangesNothing:
    def test_PlainGet_DeclaresAndBindsNothing(self, lab):
        declared = lab.declared()
        bindings = lab.map_file.read_text(encoding="utf-8")

        for _ in range(2):
            assert lab.get("/spaces").status_code == 200

        assert lab.declared() == declared
        assert lab.map_file.read_text(encoding="utf-8") == bindings

    def test_PostFromAnotherSite_IsRefusedAndChangesNothing(self, lab):
        response = httpx.post(
            f"{lab.base}/declare-spaces",
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
        )

        assert response.status_code == 403
        assert RENT not in lab.declared()
        assert "cat-closed" not in lab.bindings()


class TestAPressThatCannotBind:
    def test_Press_WhenTheAccountMapIsNotConfigured_RefusesAndDeclaresNothing(
        self, lab, monkeypatch
    ):
        monkeypatch.delenv("OBDI_ACCOUNT_MAP")

        response = lab.press()

        assert response.status_code == 400
        assert "OBDI_ACCOUNT_MAP is not set" in strip_tags(response.text)
        assert "Nothing was declared" in strip_tags(response.text)
        assert RENT not in lab.declared()

    def test_Press_WhileARebuildIsInProgress_RefusesAndDeclaresNothing(self, lab):
        (lab.db.parent / "rebuild-status.json").write_text(
            json.dumps({"state": "running", "started_at": "2026-10-03T09:00:00Z"}),
            encoding="utf-8",
        )

        response = lab.press()

        assert response.status_code == 400
        assert "rebuild" in strip_tags(response.text)
        assert RENT not in lab.declared()
        assert "cat-closed" not in lab.bindings()

    def test_Press_WhenOneBindFails_KeepsTheDeclarationSaysSoAndTheNextPressRepairsIt(
        self, lab, monkeypatch
    ):
        import obdi.cli as cli

        real = cli.bind_to_canonical

        def refuse(db_path, provider_ref, canonical):
            raise ValueError("the target already holds rows with the same provider ids")

        monkeypatch.setattr(cli, "bind_to_canonical", refuse)
        text = strip_tags(lab.press().text)

        assert RENT in lab.declared()
        assert "cat-closed" not in lab.bindings()
        assert f"{RENT} - declared, but could not bind: the target already holds rows" in text
        assert "press again" in text
        assert "declared, category not bound" in lab.spaces_page()

        monkeypatch.setattr(cli, "bind_to_canonical", real)
        lab.press()

        assert lab.bindings()["cat-closed"] == RENT


class TestWhatThePageSaysOfEachSpacesFeed:
    @pytest.mark.parametrize(
        ("closed", "words"),
        [
            ("history", "its own history complete over 1 window (1 with rows, 0 empty)"),
            ("empty", "its own history complete over 1 window (0 with rows, 1 empty)"),
            (
                "refused",
                "its own history incomplete: 0 of 1 window landed (0 with rows, 0 empty); "
                "1 refused; newest ask refused (HTTP 404) on",
            ),
        ],
    )
    def test_Page_AfterAPullHasAskedForIt_SaysWhatCameBackAndWhen(
        self, tmp_path, monkeypatch, closed, words
    ):
        httpd, opened = start(tmp_path, monkeypatch, closed=closed)
        try:
            opened.press()
            opened.pull()

            page = opened.spaces_page()
        finally:
            httpd.shutdown()

        assert f"declared and bound; {words}" in page
        if closed == "refused":
            assert f"(HTTP 404) on {datetime.now(UTC).date().isoformat()}" in page

    def test_Page_WhenBoundButNeverAsked_SaysItsFeedHasNotBeenAskedForYet(self, lab):
        lab.press()

        assert "declared and bound; its own feed has not been asked for yet" in lab.spaces_page()

    def test_Page_WhenAnOlderRefusalWasFollowedByALandingOverTheSpan_SaysTheHistoryIsComplete(
        self, lab
    ):
        lab.press()
        everything = starling.window_spec(
            datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
        )
        for outcome, status, when in (
            ("refused", 404, datetime(2026, 9, 1, tzinfo=UTC)),
            ("landed", 200, datetime(2026, 9, 20, tzinfo=UTC)),
        ):
            with Store(lab.db) as store:
                store.record_attempt(
                    source="starling-feed",
                    connection_id=STARLING_CONNECTION,
                    account_ref="starling:cat-closed",
                    asked=everything,
                    request_meta="{}",
                    outcome=outcome,
                    http_status=status,
                    detail=CLOSED_SPACE_MARK,
                    now=when,
                )

        page = lab.spaces_page()

        assert "its own history complete over 1 window (1 with rows, 0 empty); 1 refused" in page
        assert "newest ask refused" not in page

    def test_Page_WhenTheAttemptWasFiledUnderTheBoundAccount_StillSaysWhatCameBack(self, lab):
        lab.press()
        with Store(lab.db) as store:
            store.record_attempt(
                source="starling-feed",
                connection_id=STARLING_CONNECTION,
                account_ref=RENT,
                asked=starling.window_spec(
                    datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
                ),
                request_meta="{}",
                outcome="refused",
                http_status=404,
                detail=f"{CLOSED_SPACE_MARK}: refused",
                now=datetime(2026, 9, 22, tzinfo=UTC),
            )

        assert "newest ask refused (HTTP 404) on 2026-09-22" in lab.spaces_page()


class TestTheRecoverSpacesCommand:
    def run(self, lab: Lab, *flags: str) -> int:
        return cli_main(["--db", str(lab.db), "recover-spaces", *flags])

    def test_WithApply_DeclaresAndBindsAndPointsAtTheNextPull(self, lab, capsys):
        assert self.run(lab, "--apply") == 0

        printed = capsys.readouterr().out
        assert lab.bindings()["cat-closed"] == RENT
        assert RENT in lab.declared()
        assert "declared and bound" in printed
        assert "next pull asks for each Space's history" in printed

    def test_WithoutApply_ReportsTheStateAndChangesNothing(self, lab, capsys):
        before = lab.map_file.read_text(encoding="utf-8")

        assert self.run(lab) == 0

        printed = capsys.readouterr().out
        assert "not declared" in printed
        assert "Nothing was declared or bound" in printed
        assert lab.declared().keys() == {household.MAIN, household.BILLS}
        assert lab.map_file.read_text(encoding="utf-8") == before

    def test_WithApply_WhenTheAccountMapIsNotConfigured_RefusesAndDeclaresNothing(
        self, lab, monkeypatch, capsys
    ):
        monkeypatch.delenv("OBDI_ACCOUNT_MAP")

        assert self.run(lab, "--apply") == 2

        assert "OBDI_ACCOUNT_MAP is not set" in capsys.readouterr().err
        assert RENT not in lab.declared()

    def test_WithApply_Twice_SecondRunDoesNothingAndSaysSo(self, lab, capsys):
        self.run(lab, "--apply")
        capsys.readouterr()

        assert self.run(lab, "--apply") == 0

        assert "Nothing to do - no recovered Space is left to declare or bind" in (
            capsys.readouterr().out
        )

    def test_WithApply_WhenBoundElsewhere_LeavesItAndSaysTheyDisagree(self, lab, capsys):
        lab.bind_by_hand("cat-closed", ELSEWHERE)

        assert self.run(lab, "--apply") == 0

        assert "which disagrees" in capsys.readouterr().out
        assert RENT not in lab.declared()
