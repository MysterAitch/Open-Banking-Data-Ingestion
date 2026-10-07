"""A person's "Fetch now": the Connections page runs a connection's routine pull, attended.

THE OWNER'S QUESTION: could the page's buttons fill a recent gap? They walked
backward only, and the one thing that heals a hole was the command line.
A deliberate press is attended, so it does not spend the unattended allowance.

THE KNOWN ANSWERS are fixed from the construction in test_asked_coverage:

    authorisation window   2026-05-06 .. 2026-08-04
    a later landed ask     2026-09-30 .. 2026-10-02, recorded by hand
    the hole               2026-08-05 .. 2026-09-29 (56 days), within unattended reach
    the healing window     2026-08-04 .. 2026-09-30
    today                  2026-10-03, injected, never the wall clock's

The provider is the same double as there, recording the address each call declared.
The background run is injected as `spawn`, so every test joins it deliberately.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import _pull as cli_pull
from obdi.cli import pull_trigger_label, rebuild_in_progress_note, standing_trigger_label
from obdi.ingest import leases
from obdi.ingest.accounts import AccountMap
from obdi.ingest.asked_coverage import (
    ATTENDED_HEAL_ASKS_PER_CONNECTION,
    HEAL_ASKS_PER_CONNECTION,
    canonical_resolver,
    coverage_by_account,
)
from obdi.ingest.attended_fetch import (
    FETCH_NOW_TRIGGER,
    PressRefused,
    read_presses,
    record_press,
    start_press,
    write_status,
)
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.pull import PullResult, pull_truelayer
from obdi.ingest.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler, ExtendableAccount, WebConfig
from test_asked_coverage import (
    AUTHORISED,
    CARD_DATES,
    TODAY,
    Bank,
    _connection,
    _frequent_tier_only,
    _pull,
)

ADDRESS = "100.96.178.101"


class AddressRecordingBank(Bank):
    """The provider double, noting the address each call declared."""

    def __init__(self, *args, **kwargs) -> None:
        self.declared: list[str | None] = []
        super().__init__(*args, **kwargs)

    def _accounts(self, token, **kw):
        self.declared.append(kw.get("psu_ip"))
        return super()._accounts(token, **kw)

    def _cards(self, token, **kw):
        self.declared.append(kw.get("psu_ip"))
        return super()._cards(token, **kw)

    def _transactions(self, token, account_id, **kw):
        self.declared.append(kw.get("psu_ip"))
        return super()._transactions(token, account_id, **kw)

    def _card_transactions(self, token, card_id, **kw):
        self.declared.append(kw.get("psu_ip"))
        return super()._card_transactions(token, card_id, **kw)


class Household:
    """A store, a provider with a hole in one card, and a press wired as the app wires it."""

    def __init__(self, tmp_path, monkeypatch, cards=None) -> None:
        self.tmp_path = tmp_path
        self.db = tmp_path / "s.sqlite3"
        self.bank = AddressRecordingBank(
            monkeypatch, cards=cards or {"card-1": CARD_DATES}, accounts={}
        )
        self.queued: list = []
        with Store(self.db) as store:
            _pull(tmp_path, store, self.bank, AUTHORISED, deep=True, trigger="attended")
            _frequent_tier_only(store)
            for card in self.bank.card_rows:
                store.record_attempt(
                    source="truelayer-card-booked",
                    connection_id="halifax",
                    account_ref=f"truelayer:{card}",
                    asked="from=2026-09-30&to=2026-10-02",
                    request_meta="{}",
                    outcome="landed",
                    http_status=200,
                )
        self.bank.declared.clear()
        self.bank.card_asks.clear()
        self.bank.explicit_card_asks.clear()
        self.starling_pulls: list[tuple[str, str | None, str]] = []
        connections = ConnectionStore(tmp_path / "c.json")
        connections.put(_connection())
        self.config = WebConfig(
            client_id="client-1",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=connections,
            extendables=self.extendables,
            backfill_status=self.backfill_status,
            starling_status=lambda: None,
            fetch_now=self.fetch_now,
        )

    def extendables(self) -> list[ExtendableAccount]:
        with Store(self.db) as store:
            found = coverage_by_account(store, canonical_resolver(AccountMap()), TODAY)
        return [
            ExtendableAccount(
                connection="halifax",
                provider_ref=name.removeprefix("truelayer:"),
                display=f"{name} (credit card)",
                earliest=None,
                covered_to=coverage.last,
                covered_from=coverage.first,
                last_landed="2026-10-03T12:00:00",
                holes=coverage.holes,
            )
            for name, coverage in sorted(found.items())
        ]

    def backfill_status(self) -> dict:
        try:
            return json.loads((self.tmp_path / "backfill-status.json").read_text("utf-8"))
        except OSError:
            return {}

    def pull(self, name, address, trigger):
        self.bank.today = TODAY
        with Store(self.db) as store:
            result = pull_truelayer(
                store,
                _connection(),
                client_id="i",
                client_secret="s",
                connection_store=ConnectionStore(self.tmp_path / "c.json"),
                account_map=AccountMap(),
                psu_ip=address,
                trigger=trigger,
                today=TODAY,
            )
        return result.summary.inserted if result.summary else None

    def fetch_now(self, name, address):
        return start_press(
            name=name,
            psu_ip=address,
            db_path=self.db,
            connections=lambda: {"halifax", "starling"},
            pull=self.pull,
            account_map=lambda _store: AccountMap(),
            busy_note=lambda: rebuild_in_progress_note(self.db),
            spawn=self.queued.append,
            today=lambda: TODAY,
        )

    def finish_background_work(self) -> None:
        while self.queued:
            self.queued.pop(0)()

    def serve(self):
        handler = type(
            "H",
            (ConnectionHandler,),
            {"config": self.config, "session": AuthorisationSession()},
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    def press(self, base, *, address=ADDRESS, connection="halifax"):
        headers = {"X-Forwarded-For": address} if address else {}
        return httpx.post(f"{base}/fetch-now", data={"connection": connection}, headers=headers)

    def page(self, base) -> str:
        return httpx.get(f"{base}/connections").text

    def ledger(self):
        with Store(self.db) as store:
            return store.attempts()

    def locks(self):
        return leases.locks_dir(self.db)


@pytest.fixture
def household(tmp_path, monkeypatch):
    return Household(tmp_path, monkeypatch)


def _text(page: str) -> str:
    return " ".join(page.replace("<", " <").replace(">", "> ").split())


class TestAPressOnAConnectionWithAHole:
    def test_Press_OnAConnectionWithAHole_AsksForTheHoleAttendedAndThePageSaysItIsGone(
        self, household
    ):
        httpd, base = household.serve()
        try:
            before = household.page(base)
            answer = household.press(base)
            household.finish_background_work()
            after = household.page(base)
        finally:
            httpd.shutdown()

        assert "with 56 days not asked for: 2026-08-05 to 2026-09-29" in before
        assert "Fetch these now" in _text(before)
        assert answer.status_code == 200
        assert "Started an attended fetch for halifax" in answer.text
        assert ("card-1", date(2026, 8, 4), date(2026, 9, 30)) in household.bank.explicit_card_asks
        assert "not asked for" not in after
        assert "Fetch these now" not in after

    def test_Press_OnAConnectionWithAHole_EveryCallDeclaresTheRequestersAddress(
        self, household
    ):
        httpd, base = household.serve()
        try:
            household.press(base)
            household.finish_background_work()
        finally:
            httpd.shutdown()

        assert household.bank.declared, "the provider was asked something"
        assert set(household.bank.declared) == {ADDRESS}

    def test_Press_OnAConnectionWithAHole_EveryAskCarriesItsOwnTriggerLabelAndTheAddress(
        self, household
    ):
        httpd, base = household.serve()
        try:
            household.press(base)
            household.finish_background_work()
        finally:
            httpd.shutdown()

        pressed = [
            json.loads(row["request_meta"])
            for row in household.ledger()
            if row["request_meta"] != "{}" and "web-fetch-now" in row["request_meta"]
        ]
        assert pressed and FETCH_NOW_TRIGGER == "web-fetch-now"
        assert {meta["trigger"] for meta in pressed} == {"web-fetch-now"}
        assert {meta["attended_from"] for meta in pressed} == {ADDRESS}

    def test_Page_AfterAPressWithRows_SaysWhatWasAskedLandedAndNew(self, household):
        httpd, base = household.serve()
        try:
            household.press(base)
            household.finish_background_work()
            after = _text(household.page(base))
        finally:
            httpd.shutdown()

        assert "Last press finished" in after
        assert "0 refused" in after
        assert "2 asked, 2 landed" in after
        assert "new rows" in after
        assert "No days remain that no request has covered." in after

    def test_Page_WhileAPressRuns_SaysSoAndTheButtonStaysOffered(self, household):
        httpd, base = household.serve()
        try:
            household.press(base)
            running = _text(household.page(base))
            household.finish_background_work()
        finally:
            httpd.shutdown()

        assert "attended fetch running for halifax" in running
        assert "Running now." in running


class TestWhatTheAttendedBoundAllows:
    def test_Press_WithMoreHolesThanTheUnattendedBound_AsksThemAll(
        self, tmp_path, monkeypatch
    ):
        cards = {f"card-{n}": CARD_DATES for n in range(HEAL_ASKS_PER_CONNECTION + 1)}
        house = Household(tmp_path, monkeypatch, cards=cards)
        httpd, base = house.serve()
        try:
            house.press(base)
            house.finish_background_work()
        finally:
            httpd.shutdown()

        assert len(house.bank.explicit_card_asks) == HEAL_ASKS_PER_CONNECTION + 1

    def test_Press_WithMoreHolesThanTheAttendedBound_SaysHowManySpansRemainAndOffersItAgain(
        self, tmp_path, monkeypatch
    ):
        cards = {f"card-{n}": CARD_DATES for n in range(ATTENDED_HEAL_ASKS_PER_CONNECTION + 1)}
        house = Household(tmp_path, monkeypatch, cards=cards)
        httpd, base = house.serve()
        try:
            house.press(base)
            house.finish_background_work()
            after = _text(house.page(base))
            house.press(base)
            house.finish_background_work()
            again = _text(house.page(base))
        finally:
            httpd.shutdown()

        assert "56 days in 1 span still not asked for: press again." in after
        assert "Fetch these now" in after
        assert "No days remain that no request has covered." in again
        assert "Fetch these now" not in again


class TestAPressWithNoHonestAddress:
    def test_Press_FromOnlyALoopbackPeer_IsRefusedAndAsksTheProviderNothing(self, household):
        httpd, base = household.serve()
        try:
            answer = household.press(base, address=None)
        finally:
            httpd.shutdown()

        assert answer.status_code == 422
        assert "No address" in answer.text
        assert "not run" in answer.text
        assert household.bank.declared == [], "an unattended ask would spend the allowance"
        assert household.queued == []
        assert not leases.held(household.locks(), "post-auth-backfill")
        assert household.backfill_status() == {}

    def test_Press_WithNoAddress_NeverDeclaresALoopbackOrInventedAddress(self, household):
        httpd, base = household.serve()
        try:
            household.press(base, address=None)
            household.finish_background_work()
        finally:
            httpd.shutdown()

        assert all(
            declared is None for declared in household.bank.declared
        ), "nothing was sent, let alone 127.0.0.1"
        assert "127.0.0.1" not in json.dumps([row["request_meta"] for row in household.ledger()])


class TestAPressThatMustNotCollide:
    def test_Press_DuringARebuild_RefusesSaysWhyAndChangesNothing(self, household):
        leases.acquire(household.locks(), "rebuild-derived", "obdi-web", 3600)
        httpd, base = household.serve()
        try:
            answer = household.press(base)
        finally:
            httpd.shutdown()

        assert answer.status_code == 409
        assert "rebuild is replaying the store" in answer.text
        assert household.bank.declared == []
        assert household.queued == []
        assert not leases.held(household.locks(), "post-auth-backfill")

    def test_Press_WhileTheSchedulerHoldsItsCycle_RefusesAndSaysWhy(self, household):
        leases.acquire(household.locks(), "pull-cycle", "obdi-pull", 1800)
        httpd, base = household.serve()
        try:
            answer = household.press(base)
        finally:
            httpd.shutdown()

        assert answer.status_code == 409
        assert "scheduler is mid-cycle" in answer.text
        assert household.bank.declared == []
        assert household.queued == []

    def test_Press_DuringAStackUpdate_RefusesAndSaysWhy(self, household):
        leases.acquire(household.locks(), leases.STACK_UPDATE, "updater", 600)
        httpd, base = household.serve()
        try:
            answer = household.press(base)
        finally:
            httpd.shutdown()

        assert answer.status_code == 409
        assert "stack update is in progress" in answer.text
        assert household.queued == []

    def test_Press_DuringThePostAuthorisationBackfill_RefusesAndNamesIt(self, household):
        leases.acquire(household.locks(), "post-auth-backfill", "obdi-web", 900)
        write_status(
            household.tmp_path / "backfill-status.json", "halifax", state="running", stage="ladder"
        )
        httpd, base = household.serve()
        try:
            answer = household.press(base)
        finally:
            httpd.shutdown()

        assert answer.status_code == 409
        assert "post-authorisation backfill for halifax is running" in answer.text
        assert household.bank.declared == []

    def test_Press_TwiceInQuickSuccession_RunsOnceAndTheSecondSaysItIsRunning(self, household):
        httpd, base = household.serve()
        try:
            first = household.press(base)
            second = household.press(base)
            queued_while_running = len(household.queued)
            household.finish_background_work()
            third = household.press(base)
        finally:
            httpd.shutdown()

        assert first.status_code == 200
        assert second.status_code == 409
        assert "attended fetch for halifax is already running" in second.text
        assert queued_while_running == 1
        assert third.status_code == 200, "the lease is released when the run ends"

    def test_Press_WhenThePullBlowsUp_ReleasesTheLeaseAndRecordsTheStop(self, household):
        def explode(name, address, trigger):
            raise RuntimeError("the connection store vanished")

        household.pull = explode
        httpd, base = household.serve()
        try:
            household.press(base)
            household.finish_background_work()
            after = _text(household.page(base))
        finally:
            httpd.shutdown()

        assert not leases.held(household.locks(), "post-auth-backfill")
        assert "the connection store vanished" in after
        assert "The pull stopped at the refusal" in after


class TestARefusedProviderCall:
    def test_Page_WhenHealingIsRefused_ShowsTheProvidersReasonAndCodeAndKeepsTheHole(
        self, household
    ):
        household.bank.refuse_explicit_windows = True
        httpd, base = household.serve()
        try:
            household.press(base)
            household.finish_background_work()
            after = _text(household.page(base))
        finally:
            httpd.shutdown()

        assert "1 refused" in after
        assert "Refused with HTTP 429 and code quota" in after
        assert "Rate limited by the provider" in after
        assert "Healing stopped at the refusal" in after
        assert "56 days in 1 span still not asked for: press again." in after
        assert "with 56 days not asked for" in after

    def test_Press_WhenHealingIsRefused_LeavesTheTierAskLandedAndTheRefusalInTheLedger(
        self, household
    ):
        household.bank.refuse_explicit_windows = True
        httpd, base = household.serve()
        try:
            household.press(base)
            household.finish_background_work()
        finally:
            httpd.shutdown()

        pressed = [row for row in household.ledger() if "web-fetch-now" in row["request_meta"]]
        assert sorted(row["outcome"] for row in pressed) == ["landed", "refused"]
        assert {row["error_code"] for row in pressed if row["outcome"] == "refused"} == {"quota"}


class TestAPressOnStarling:
    def test_StarlingPress_RunsThePullStarlingRoutineWithTheTriggerAndNoAddress(
        self, tmp_path, monkeypatch
    ):
        seen: list[dict] = []

        def fake_pull_starling(store, token, *, account_map, since, trigger):
            seen.append({"token": token, "since": since, "trigger": trigger})
            return PullResult(provider="starling")

        monkeypatch.setattr("obdi.cli.pull_starling", fake_pull_starling)
        monkeypatch.setenv("STARLING_PERSONAL_ACCESS_TOKEN", "invented-token")
        house = Household(tmp_path, monkeypatch)
        house.config.starling_status = lambda: {"accounts": []}

        def pull(name, address, trigger):
            captured: list[PullResult] = []
            cli_pull(
                name, house.db, None, psu_ip=address, trigger=trigger,
                on_result=captured.append, raise_errors=True,
            )

        house.pull = pull
        httpd, base = house.serve()
        try:
            answer = house.press(base, address=None, connection="starling")
            house.finish_background_work()
            after = _text(house.page(base))
        finally:
            httpd.shutdown()

        assert answer.status_code == 200
        assert "no customer-present distinction" in answer.text
        assert seen == [{"token": "invented-token", "since": None, "trigger": "web-fetch-now"}]
        assert "This provider has no customer-present distinction" in after
        assert "Last press finished" in after

    def test_CliPull_WithRaiseErrorsOnAnUnknownConnection_RaisesRatherThanPrinting(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "c.json"))

        with pytest.raises(RuntimeError, match="No connection named 'nowhere'"):
            cli_pull("nowhere", tmp_path / "s.sqlite3", None, raise_errors=True)

        assert capsys.readouterr().err == ""


class TestHowACommandLinePullIsLabelled:
    def test_Label_WhenAttendanceIsDeclaredInsideTheSchedulersContainer_IsAttendedNotScheduled(
        self, monkeypatch
    ):
        monkeypatch.setenv("OBDI_TRIGGER", "scheduled")

        assert pull_trigger_label(None, ADDRESS) == "cli-attended"

    def test_Label_WhenAConnectionIsNamedByHandInsideTheSchedulersContainer_IsNotScheduled(
        self, monkeypatch
    ):
        monkeypatch.setenv("OBDI_TRIGGER", "scheduled")

        assert pull_trigger_label(None, None) == "cli"

    def test_StandingLabel_WhenTheLoopRunsTheBarePullInsideTheSchedulersContainer_IsScheduled(
        self, monkeypatch
    ):
        monkeypatch.setenv("OBDI_TRIGGER", "scheduled")

        assert standing_trigger_label() == "scheduled"

    def test_StandingLabel_WhenNothingIsSet_IsThePlainCommandLine(self, monkeypatch):
        monkeypatch.delenv("OBDI_TRIGGER", raising=False)

        assert standing_trigger_label() == "cli"

    def test_Label_WhenTheCallerNamesItsOwnPathway_KeepsThatName(self, monkeypatch):
        monkeypatch.setenv("OBDI_TRIGGER", "scheduled")

        assert pull_trigger_label("web-fetch-now", ADDRESS) == "web-fetch-now"

    def test_Label_WhenNothingIsSet_IsThePlainCommandLine(self, monkeypatch):
        monkeypatch.delenv("OBDI_TRIGGER", raising=False)

        assert pull_trigger_label(None, None) == "cli"


class TestWhatDoesNotStartAPress:
    def test_Get_OfTheConnectionsPage_StartsNothingAndTakesNoLease(self, household):
        httpd, base = household.serve()
        try:
            page = household.page(base)
            household.page(base)
        finally:
            httpd.shutdown()

        assert "Fetch now" in page
        assert household.queued == []
        assert household.bank.declared == []
        assert not leases.held(household.locks(), "post-auth-backfill")
        assert household.backfill_status() == {}

    def test_Press_OnAnUnknownConnection_IsRefusedWithNothingStarted(self, household):
        httpd, base = household.serve()
        try:
            answer = household.press(base, connection="nowhere")
        finally:
            httpd.shutdown()

        assert answer.status_code == 404
        assert "There is no connection named" in answer.text
        assert "nowhere" in answer.text
        assert household.queued == []
        assert not leases.held(household.locks(), "post-auth-backfill")

    def test_Press_WithNoConnectionNamed_IsABadRequest(self, household):
        httpd, base = household.serve()
        try:
            answer = httpx.post(f"{base}/fetch-now", data={})
        finally:
            httpd.shutdown()

        assert answer.status_code == 400
        assert household.queued == []

    def test_Press_DrivenByAnotherSite_IsRefusedBeforeAnythingStarts(self, household):
        httpd, base = household.serve()
        try:
            answer = httpx.post(
                f"{base}/fetch-now",
                data={"connection": "halifax"},
                headers={"Origin": "https://evil.example", "X-Forwarded-For": ADDRESS},
            )
        finally:
            httpd.shutdown()

        assert answer.status_code == 403
        assert household.queued == []

    def test_Page_WhenNoPressIsWired_OffersNoPress(self, household):
        household.config.fetch_now = None
        httpd, base = household.serve()
        try:
            page = household.page(base)
            answer = household.press(base)
        finally:
            httpd.shutdown()

        assert "Fetch now" not in page and "Fetch these now" not in page
        assert answer.status_code == 404


class TestTheSharedStatusFile:
    def test_PressResult_SurvivesTheBackfillWritingItsOwnProgress(self, tmp_path):
        path = tmp_path / "backfill-status.json"
        record_press(path, "halifax", {"asked": 3})

        write_status(path, "halifax", state="running", stage="ladder")

        assert read_presses(path) == {"halifax": {"asked": 3}}
        assert json.loads(path.read_text("utf-8"))["state"] == "running"

    def test_PressResults_AreKeptPerConnectionNotOverwrittenByTheNext(self, tmp_path):
        path = tmp_path / "backfill-status.json"

        record_press(path, "halifax", {"asked": 3})
        record_press(path, "monzo", {"asked": 1})

        assert read_presses(path) == {"halifax": {"asked": 3}, "monzo": {"asked": 1}}

    def test_Presses_OfAMissingOrCorruptFile_AreNone(self, tmp_path):
        path = tmp_path / "backfill-status.json"
        assert read_presses(path) == {}
        path.write_text("{not json", encoding="utf-8")
        assert read_presses(path) == {}


class TestThePressDirectly:
    def test_Press_ForAnAggregatorConnectionWithoutAnAddress_RaisesTheRefusal(self, tmp_path):
        with pytest.raises(PressRefused) as refused:
            start_press(
                name="halifax",
                psu_ip=None,
                db_path=tmp_path / "s.sqlite3",
                connections=lambda: {"halifax"},
                pull=lambda *a: None,
                account_map=lambda _s: AccountMap(),
                busy_note=lambda: None,
                spawn=lambda work: None,
            )

        assert refused.value.status == 422
