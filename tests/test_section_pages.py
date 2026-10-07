"""The pages that used to be sections of the home page, and the home page that remains.

Expected content is stated from the design - what each page is for, what it
leads with, what sits behind a fold, where a result page sends a person back -
and every invented figure is distinctive so that an amount reaching a page
cannot hide among ordinary digits.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import HTTPServer

import httpx
import pytest

from obdi.alerts import CONSENT_RUNGS
from obdi.coverage import SourceCoverage
from obdi.ingest.connections import Connection, ConnectionStore
from obdi.ingest.probing import elapsed_words, sca_note
from obdi.overview import Overview
from obdi.web import AuthorisationSession, ConnectionHandler, ExtendableAccount, WebConfig
from page_dom import elements, parse
from stylesheet_support import length_px
from test_navigation import get_routes

#: Only a first-rung consent gets the heavy Reconnect button.
FIRST_RUNG_DAYS = max(threshold for threshold, _, _ in CONSENT_RUNGS)

AMOUNT_ONE = "987654321"
AMOUNT_TWO = "424242424"

#: Every control the old home page carried, by the form action or link it posted to.
MOVED_FORM_ACTIONS = (
    "/push-actual",
    "/audit-actual",
    "/prune-actual",
    "/rename-connection",
    "/rebuild-derived",
    "/forget-actual-bindings",
    "/starling-probe",
    "/upload",
    "/bind",
    "/extend",
    "/extend-max",
    "/connect",
    "/archive-account",
)


@pytest.fixture(autouse=True)
def live_instance(monkeypatch):
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")


def connection(name: str, *, expires_in: timedelta) -> Connection:
    return Connection(
        connection_id=name,
        provider="p",
        refresh_token="r",
        consent_expires_at=(datetime.now(UTC) + expires_in).isoformat(),
    )


@pytest.fixture
def serve(tmp_path):
    servers: list[HTTPServer] = []

    def start(connections: tuple[Connection, ...] = (), **hooks) -> str:
        store = ConnectionStore(tmp_path / f"c{len(servers)}.json")
        for one in connections:
            store.put(one)
        config = WebConfig(
            client_id="c",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=store,
            **hooks,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}"

    yield start
    for httpd in servers:
        httpd.shutdown()


def quiet_overview(fresh: bool) -> Overview:
    """Today's checks having run and found nothing, so the page's own lines are what is read."""
    return Overview(datetime.now(UTC), 19, 19, (), ())


def fetch(base: str, path: str) -> str:
    response = httpx.get(f"{base}{path}", timeout=20)
    assert response.status_code == 200, (path, response.status_code)
    return response.text


def inside_details(page: str, needle: str) -> bool:
    """Is the first occurrence of `needle` within an open `<details>` element?"""
    before = page[: page.index(needle)]
    return len(re.findall(r"<details\b", before)) > before.count("</details>")


def current_section(page: str) -> list[str]:
    return re.findall(r'aria-current="page">([A-Za-z ]+)<', page)


def coverage_rows() -> list[SourceCoverage]:
    return [
        SourceCoverage(
            account_id="halifax-current",
            source="truelayer",
            count=12,
            earliest=date(2026, 1, 1),
            latest=date(2026, 2, 1),
            inflow_minor=1,
            outflow_minor=1,
            with_durable_id=12,
        )
    ]


def results() -> list[dict[str, object]]:
    return [
        {
            "kind": "audit",
            "ok": True,
            "finished_at": "2026-10-01T13:00:00Z",
            "accounts": [
                {
                    "account_id": "alpha-id",
                    "name": "Alpha Current",
                    "expected": 10,
                    "present": 10,
                    "missing": 0,
                    "orphaned": 0,
                    "human": 0,
                    "diverged": 0,
                    "duplicated": 0,
                    "balance": {"agrees": True, "store_minor": int(AMOUNT_ONE)},
                },
                {
                    "account_id": "beta-id",
                    "name": "Beta Savings",
                    "expected": 10,
                    "present": 9,
                    "missing": 1,
                    "orphaned": 0,
                    "human": 0,
                    "diverged": 0,
                    "duplicated": 0,
                    "missing_sample": [
                        {
                            "imported_id": "obdi-beta-1",
                            "date": "2026-09-01",
                            "amount": -int(AMOUNT_TWO),
                        }
                    ],
                    "balance": {"agrees": False, "store_minor": int(AMOUNT_TWO)},
                },
            ],
        },
        {
            "kind": "push",
            "ok": True,
            "finished_at": "2026-10-01T12:00:00Z",
            "added": 3,
            "provisioned": 0,
        },
    ]


class TestEachPageOpensWithWhatItIsForAndMarksItsSection:
    @pytest.mark.parametrize(
        ("path", "section", "fragment"),
        [
            ("/connections", "Connections", "Add a bank"),
            ("/actual", "Connections", "Push to Actual now"),
            ("/coverage", "More", "fed by 1 source"),
            ("/import", "Bring in", "Preview import"),
            ("/diagnostics", "More", "Repairs"),
            ("/admin", "More", "Repairs"),
        ],
    )
    def test_Page_WhenFullyWired_RendersItsSectionsAndMarksItsNavigationEntry(
        self, serve, path, section, fragment
    ):
        base = serve(
            (connection("halifax", expires_in=timedelta(days=60)),),
            holdings=coverage_rows,
            push_actual=lambda: "queued",
            actual_status=lambda: results(),
            rebuild_derived=lambda: "started",
            forget_actual=lambda: 0,
            rename_connection=lambda a, b: "",
        )

        page = fetch(base, path)

        assert fragment in page
        assert current_section(page) == [section]
        # The Actual page opens with its verdict and Coverage by source with its counts, which
        # is what the lede is for elsewhere: a lede above either spent a line before the first fact.
        openings = {
            "/actual": 'id="verdict"',
            "/coverage": 'class="cov-summary"',
            "/connections": 'class="conn-out"',
        }
        opening = openings.get(path, '<p class="lede">')
        assert page.index(opening) < page.index(fragment)

    def test_CoveragePage_OpensWithItsCountsAndLinksTheTwoPagesThatTakeOverTheRest(self, serve):
        page = fetch(serve(holdings=coverage_rows), "/coverage")

        assert page.index('class="cov-summary"') < page.index('href="/coverage-timeline"')
        assert 'href="/bring-in"' in page

    def test_ImportPage_ExplainsBothDoorsAndLinksTheStatementUpload(self, serve):
        page = fetch(serve(), "/import")

        assert "Bank CSV or QIF exports" in page
        assert "PDF statement is kept as evidence" in page
        assert 'href="/statement-shape"' in page

    def test_DiagnosticsPage_OrdersTheRepairsThenTheRecordThenTheProbe(self, serve):
        base = serve(
            rebuild_derived=lambda: "started",
            forget_actual=lambda: 0,
            recent_rebuilds=lambda: [
                {
                    "ok": True,
                    "started_at": "2026-10-01T10:00:00Z",
                    "finished_at": "2026-10-01T10:01:00Z",
                }
            ],
            starling_probe=lambda cutoff: None,
        )

        page = fetch(base, "/diagnostics")

        assert (
            page.index("Repairs")
            < page.index("Recent rebuilds")
            < page.index("Starling changesSince probe")
        )
        assert inside_details(page, "Starling changesSince probe")
        assert not inside_details(page, "Rebuild from raw")


class TestTheHomePageIsTheOverviewAndNothingElse:
    def test_Home_WithEverythingWired_CarriesNoneOfTheMovedForms(self, serve):
        base = serve(
            (connection("halifax", expires_in=timedelta(days=60)),),
            holdings=coverage_rows,
            push_actual=lambda: "queued",
            audit_actual=lambda: "queued",
            prune_actual=lambda: "queued",
            actual_status=lambda: results(),
            rebuild_derived=lambda: "started",
            forget_actual=lambda: 0,
            rename_connection=lambda a, b: "",
            starling_probe=lambda cutoff: None,
            preview_upload=lambda *a: {},
            extendables=lambda: [
                ExtendableAccount(
                    connection="halifax", provider_ref="e9f8", display="Current", earliest=None
                )
            ],
        )

        page = fetch(base, "/")

        assert "<form" not in page
        for action in MOVED_FORM_ACTIONS:
            assert f'action="{action}"' not in page, action
        assert "Everything else, by section" not in page
        assert 'id="verdict"' in page

    def test_Home_CarriesFourSystemFactsEachLinkingToThePageThatOwnsIt(self, serve):
        page = fetch(serve(), "/")

        facts = re.findall(r'<li class="fact"><a class="tap" href="([^"]+)"', page)
        assert facts == ["/connections", "/connections", "/admin", "/admin"]

    def test_Home_StatesTheActualFactInTheEvidenceAndNotAsASystemFact(self, serve):
        page = fetch(serve(overview=quiet_overview), "/")

        evidence = page.split('class="evidence"')[1].split('id="system"')[0]
        assert "Not wired on this instance." in evidence
        assert "<strong>Actual</strong>" not in page.split('id="system"')[1]

    def test_Home_AccountsSection_LinksToTheAccountsPageAndNoLongerToTheOthers(self, serve):
        accounts = fetch(serve(overview=quiet_overview), "/").split('id="accounts"')[1]

        assert 'href="/accounts"' in accounts
        for href in ("/coverage", "/import", "/review"):
            assert f'href="{href}"' not in accounts, href

    def test_SystemStrip_StatesTheFactsFromTheirHooks(self, serve):
        soon = connection("halifax", expires_in=timedelta(days=40))
        later = connection("monzo", expires_in=timedelta(days=80))
        base = serve(
            (later, soon),
            overview=quiet_overview,
            actual_status=lambda: [
                {"kind": "push", "ok": False, "finished_at": "2026-10-01T12:00:00Z", "error": "x"}
            ],
            rebuild_status=lambda: {
                "state": "done",
                "ok": False,
                "finished_at": "2026-09-30T08:00:00Z",
            },
            scheduler_heartbeat=lambda: {
                "at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "interval_seconds": 21600,
            },
        )

        page = fetch(base, "/")
        strip = page.split('id="system"')[1]

        status = page.split('class="evidence"')[1]
        assert "The last push failed. The push of 2026-10-01 12:00 failed" in status
        assert "last rebuild FAILED, 2026-09-30 09:00" in strip, "08:00 UTC is 09:00 BST"
        assert "09:00Z" not in strip
        assert "2 banks connected" in strip
        expires = (datetime.now(UTC) + timedelta(days=40)).date().isoformat()
        assert f"soonest consent expires {expires}" in strip
        assert "scheduler last completed a cycle" in strip

    def test_SystemStrip_WithNothingWired_SaysSoRatherThanGoingMissing(self, serve):
        page = fetch(serve(overview=quiet_overview), "/")
        strip = page.split('id="system"')[1]

        assert "no scheduler cycle recorded" in strip
        assert "Not wired on this instance." in page.split('class="evidence"')[1]
        assert "no banks connected" in strip
        assert "no rebuild recorded" in strip

    def test_SlowHomeRender_NamesTheHookThatWasSlow(self, serve, monkeypatch, capsys):
        import time

        monkeypatch.setenv("OBDI_WEB_SLOW_RENDER_SECS", "0.01")

        def slow():
            time.sleep(0.05)
            return []

        fetch(serve(overview=quiet_overview, actual_status=slow), "/")

        out = capsys.readouterr().out
        assert "web timing: / rendered in" in out and "actual_status" in out

    def test_SlowSectionPage_NamesItsRouteAndHook(self, serve, monkeypatch, capsys):
        import time

        monkeypatch.setenv("OBDI_WEB_SLOW_RENDER_SECS", "0.01")

        def slow():
            time.sleep(0.05)
            return []

        fetch(serve(actual_status=slow), "/actual")

        out = capsys.readouterr().out
        assert "web timing: /actual rendered in" in out and "actual_status" in out


class TestConsentRowsSayWhenAndWeighReconnectByUrgency:
    def test_Row_WithAMonthLeft_ShowsTheExpiryDateAndOffersReconnectAsSecondary(self, serve):
        span = timedelta(days=30, hours=1)
        expires = datetime.now(UTC) + span
        page = fetch(serve((connection("halifax", expires_in=span),)), "/connections")

        assert f"expires {expires.date().isoformat()} (in 30 days)" in page
        assert 'href="/connect?name=halifax"' in page
        assert "Reconnect halifax" not in page

    def test_Row_AtTheFirstAlertRung_OffersReconnectAsPrimary(self, serve):
        span = timedelta(days=FIRST_RUNG_DAYS, hours=1)
        expires = datetime.now(UTC) + span
        page = fetch(serve((connection("halifax", expires_in=span),)), "/connections")

        assert f"expires {expires.date().isoformat()} (in {FIRST_RUNG_DAYS} days)" in page
        assert '<a class="button" href="/connect?name=halifax">Reconnect halifax</a>' in page

    def test_Row_OneDayOutsideTheFirstAlertRung_OffersNoThingToDoButStillAQuietReconnect(
        self, serve
    ):
        span = timedelta(days=FIRST_RUNG_DAYS + 1, hours=1)
        page = fetch(serve((connection("halifax", expires_in=span),)), "/connections")

        assert "Reconnect halifax" not in page
        assert page.count('href="/connect?name=halifax"') == 1

    def test_Row_WhenExpired_OffersReconnectAsPrimaryAndSaysWhenItLapsed(self, serve):
        lapsed = datetime.now(UTC) - timedelta(days=3)
        page = fetch(serve((connection("halifax", expires_in=timedelta(days=-3)),)), "/connections")

        assert f"expired {lapsed.date().isoformat()}" in page
        assert '<a class="button" href="/connect?name=halifax">Reconnect halifax</a>' in page

    def test_Row_WithNoConsentClock_SaysSoInsteadOfPrintingNoneDays(self, serve):
        store_connection = Connection(connection_id="starlingish", provider="p", refresh_token="r")
        page = fetch(serve((store_connection,)), "/connections")

        assert "no consent expiry recorded" in page
        assert "None days" not in page

    def test_RenameForm_IsBehindADisclosure(self, serve):
        page = fetch(
            serve(
                (connection("halifax", expires_in=timedelta(days=60)),),
                rename_connection=lambda a, b: "",
            ),
            "/connections",
        )

        assert inside_details(page, 'action="/rename-connection"')


class TestElapsedTimeReadsInDaysAndHours:
    def test_AuthorisedMonthsAgo_SaysDaysAndHoursNotFiveFigureMinutes(self):
        note = sca_note(
            authorised_at=datetime.now(UTC) - timedelta(minutes=85183),
            window_minutes=None,
            refusal_seen=False,
        )

        assert "authorised 59 days 3 hours ago" in note
        assert "85183" not in note

    @pytest.mark.parametrize(
        ("minutes", "said"),
        [(0, "0 min"), (5, "5 min"), (89, "89 min"), (120, "2 hours"), (2 * 1440, "2 days"),
         (2 * 1440 + 60, "2 days 1 hours")],
    )
    def test_Words_AcrossEachBand(self, minutes, said):
        assert elapsed_words(minutes) == said

    def test_WindowLongClosed_SaysSinceAuthorisationInDays(self):
        note = sca_note(
            authorised_at=datetime.now(UTC) - timedelta(days=10),
            window_minutes=5,
            refusal_seen=False,
        )

        assert "10 days since authorisation" in note


class TestExtendHistoryKeepsTheStateOutsideTheFold:
    def _stale_account(self) -> ExtendableAccount:
        return ExtendableAccount(
            connection="halifax",
            provider_ref="e9f8",
            display="Current Account",
            earliest=date(2024, 8, 2),
            covered_to=datetime.now(UTC).date() - timedelta(days=9),
        )

    def test_Buttons_AreBehindADisclosurePerAccount(self, serve):
        page = fetch(serve(extendables=lambda: [self._stale_account()]), "/connections")

        for days in (1, 7, 30, 90, 365, 730):
            assert inside_details(page, f'name="days" value="{days}"')
        assert inside_details(page, "Extend as far as possible")

    def test_StaleAccount_OpensTheFoldHoldingItSoTheMarkerIsInView(self, serve):
        page = fetch(serve(extendables=lambda: [self._stale_account()]), "/connections")

        assert "stale: 9 days behind" in page
        assert '<details class="manage" open>' in page

    def test_AccountThatIsCurrent_LeavesTheFoldShut(self, serve):
        current = ExtendableAccount(
            connection="halifax",
            provider_ref="e9f8",
            display="Current Account",
            earliest=date(2024, 8, 2),
            covered_to=datetime.now(UTC).date(),
        )
        page = fetch(serve(extendables=lambda: [current]), "/connections")

        assert '<details class="manage">' in page

    def test_ResultPageAfterAPress_KeepsTheButtonsOpenForPressingAgain(self, serve):
        base = serve(
            extendables=lambda: [self._stale_account()], extend_window=lambda **_: "landed 3"
        )

        page = httpx.post(
            f"{base}/extend", data={"connection": "halifax", "account": "e9f8", "days": "7"}
        ).text

        assert "<details open><summary>Extend this account's history" in page


class TestActualLeadsWithTheTwoLineSummaryAndPrintsNoAmount:
    def test_Summary_LeadsBeforeAnyControlOrDetail(self, serve):
        page = fetch(
            serve(push_actual=lambda: "q", audit_actual=lambda: "q", actual_status=results),
            "/actual",
        )

        assert "2026-10-01 12:00</span> - The last push was applied." in page
        assert "found differences in 1 of 2 accounts" in page
        assert page.index('id="verdict"') < page.index('action="/push-actual"')
        assert page.index("found differences in 1 of 2 accounts") < page.index(
            "<summary>1 account agrees</summary>"
        )

    def test_AccountsThatAgree_AreBehindADisclosureAndThoseThatDifferAreNot(self, serve):
        page = fetch(serve(push_actual=lambda: "q", actual_status=results), "/actual")

        assert inside_details(page, "Alpha Current: agrees")
        assert not inside_details(page, "Beta Savings</h4>")
        assert not inside_details(page, "audit: differences</span>")

    def test_Page_PrintsNoAmountNorTheFiguresBehindABalanceVerdict(self, serve):
        page = fetch(
            serve(push_actual=lambda: "q", audit_actual=lambda: "q", actual_status=results),
            "/actual",
        )

        assert AMOUNT_ONE not in page and AMOUNT_TWO not in page
        assert "amount" not in page.lower().replace("amounts are shown", "")

    def test_Summary_WhenTheNewestPushFailed_SaysSoAndNamesTheLastThatApplied(self, serve):
        history = [
            {"kind": "push", "ok": False, "finished_at": "2026-10-02T09:00:00Z", "error": "boom"},
            {"kind": "push", "ok": True, "finished_at": "2026-10-01T12:00:00Z", "added": 1},
        ]

        page = fetch(serve(push_actual=lambda: "q", actual_status=lambda: history), "/actual")

        assert "The newest push failed: boom." in page
        assert "The last push that applied was 2026-10-01 12:00." in page

    def test_Summary_WithNothingRecorded_SaysSoForBoth(self, serve):
        page = fetch(serve(push_actual=lambda: "q", actual_status=lambda: []), "/actual")

        assert "Nothing has been pushed yet" in page
        assert "No push has been recorded." in page
        assert "No audit has been run." in page

    def test_Summary_WhenTheResultsCannotBeRead_SaysUnreadableAndStillRenders(self, serve):
        def boom():
            raise RuntimeError("results directory locked")

        page = fetch(serve(push_actual=lambda: "q", actual_status=boom), "/actual")

        assert "could not be read" in page
        assert "results directory locked" not in page
        assert "Push to Actual now" in page

    def test_Page_WhenNothingIsWired_SaysSo(self, serve):
        page = fetch(serve(), "/actual")

        assert "The Actual sync is not wired on this instance" in page

    def test_Prune_IsBehindADisclosure(self, serve):
        page = fetch(serve(prune_actual=lambda: "q", push_actual=lambda: "q"), "/actual")

        assert inside_details(page, 'action="/prune-actual"')


class TestPagesTolerateHooksAsTheHomePageDid:
    def test_Connections_WhenAHookRaises_ThePageStillRendersWithoutThatPart(self, serve):
        def boom():
            raise RuntimeError("locked")

        page = fetch(
            serve(starling_status=boom, provider_knowledge=boom, extendables=boom), "/connections"
        )

        assert "Where data comes from" in page

    def test_Diagnostics_WhenTheRebuildHistoryHookRaises_ThePageStillRenders(self, serve):
        def boom():
            raise RuntimeError("locked")

        page = fetch(serve(rebuild_derived=lambda: "x", recent_rebuilds=boom), "/diagnostics")

        assert "Repairs" in page and "Recent rebuilds" not in page

    def test_Diagnostics_WhenNoRepairIsWired_OffersNoRepairAndStillListsThePages(self, serve):
        page = fetch(serve(), "/diagnostics")

        assert "Repairs" not in page and "<form" not in page
        assert 'href="/artefacts"' in page

    def test_Coverage_WhenNothingIsHeld_SaysSo(self, serve):
        assert "Coverage is not wired on this instance, or has nothing to show" in fetch(
            serve(), "/coverage"
        )


class TestControlsAreThumbSized:
    PAGES = ("/", "/connections", "/actual", "/coverage", "/import", "/admin")

    def test_EveryButtonAndLink_IsAButtonOrATapTarget(self, serve):
        base = serve(
            (connection("halifax", expires_in=timedelta(days=60)),),
            holdings=coverage_rows,
            push_actual=lambda: "q",
            audit_actual=lambda: "q",
            actual_status=results,
            rebuild_derived=lambda: "x",
            forget_actual=lambda: 0,
            rename_connection=lambda a, b: "",
            starling_probe=lambda c: None,
            archive_notes=lambda: {},
            extendables=lambda: [
                ExtendableAccount(
                    connection="halifax", provider_ref="e9f8", display="Current", earliest=None
                )
            ],
        )
        for path in self.PAGES:
            page = re.sub(
                r'<a class="skip" [^>]*>[^<]*</a>|<nav .*?</nav>', "", fetch(base, path), flags=re.S
            )
            for tag in re.findall(r"<button[^>]*>", page):
                assert 'class="button' in tag or "form button" in tag, (path, tag)
            for tag in re.findall(r"<a [^>]*>", page):
                assert 'class="button' in tag or 'class="tap' in tag, (path, tag)

    def test_Stylesheet_FloorsEveryButtonAtFortyFourPixels(self, serve):
        css = fetch(serve(), "/")

        rules = re.findall(r"a\.button, button\.button \{[^}]*\}", css)
        floors = [m for rule in rules for m in re.findall(r"min-height: ([^;]+);", rule)]
        assert any(length_px(css, floor) >= 44 for floor in floors)


class TestEveryResultPageOffersTheWayBackToWhereItCameFrom:
    @pytest.fixture
    def wired(self, serve):
        return serve(
            push_actual=lambda: "queued",
            audit_actual=lambda: "queued",
            prune_actual=lambda: "queued",
            rename_connection=lambda a, b: f"renamed {a}",
            rebuild_derived=lambda: "started",
            forget_actual=lambda: 2,
            bind_account=lambda a, c: f"bound {a}",
            extendables=lambda: [],
            extend_window=lambda **_: "landed",
            preview_upload=lambda *a: {},
        )

    def way_back_names(self, response: httpx.Response, page: str, label: str) -> None:
        assert f'href="{page}">{label}</a>' in response.text
        assert 'href="/">Back to overview</a>' in response.text

    @pytest.mark.parametrize(
        ("path", "data", "page", "label"),
        [
            ("/push-actual", {}, "/actual", "Back to Actual sync"),
            ("/audit-actual", {}, "/actual", "Back to Actual sync"),
            ("/prune-actual", {"confirm": "yes"}, "/actual", "Back to Actual sync"),
            ("/prune-actual", {}, "/actual", "Back to Actual sync"),
            (
                "/rename-connection",
                {"old_name": "a", "new_name": "b"},
                "/connections",
                "Back to bank connections",
            ),
            ("/rename-connection", {}, "/connections", "Back to bank connections"),
            (
                "/extend",
                {"connection": "halifax", "account": "x", "days": "7"},
                "/connections",
                "Back to bank connections",
            ),
            ("/rebuild-derived", {"confirm": "yes"}, "/diagnostics", "Back to Diagnostics"),
            ("/rebuild-derived", {}, "/diagnostics", "Back to Diagnostics"),
            ("/forget-actual-bindings", {"confirm": "yes"}, "/diagnostics", "Back to Diagnostics"),
            ("/forget-actual-bindings", {}, "/diagnostics", "Back to Diagnostics"),
            ("/upload", {}, "/import", "Back to import"),
        ],
    )
    def test_Post_AnswersWithTheWayBackToTheOriginatingPage(
        self, wired, path, data, page, label
    ):
        response = httpx.post(f"{wired}{path}", data=data, timeout=20)

        self.way_back_names(response, page, label)

    def test_Post_WhenTheHookRaises_StillOffersTheWayBack(self, serve):
        def boom():
            raise RuntimeError("queue directory missing")

        response = httpx.post(f"{serve(push_actual=boom)}/push-actual", timeout=20)

        assert response.status_code == 500
        self.way_back_names(response, "/actual", "Back to Actual sync")

    @pytest.mark.parametrize(
        ("referer_path", "page", "label"),
        [
            ("/actual", "/actual", "Back to Actual sync"),
            ("/connections", "/connections", "Back to bank connections"),
            ("/coverage", "/coverage", "Back to coverage by source"),
            (None, "/coverage", "Back to coverage by source"),
            ("/somewhere-unknown", "/coverage", "Back to coverage by source"),
        ],
    )
    def test_Bind_SendsBackToThePageItWasPressedOnElseCoverage(
        self, wired, referer_path, page, label
    ):
        headers = {"Referer": f"{wired}{referer_path}"} if referer_path else {}

        response = httpx.post(
            f"{wired}/bind", data={"account": "a", "canonical": "b"}, headers=headers, timeout=20
        )

        self.way_back_names(response, page, label)

    def test_Bind_WithAForeignRefererHost_OnlyEverChoosesAmongOurOwnLinks(self, wired):
        response = httpx.post(
            f"{wired}/bind",
            data={"account": "a", "canonical": "b"},
            headers={"Referer": "https://evil.example/actual"},
            timeout=20,
        )

        assert "evil.example" not in response.text
        self.way_back_names(response, "/actual", "Back to Actual sync")


class TestNavigationCoversTheNewRoutes:
    def test_Dispatcher_KnowsEveryPageTheStripAndTheAccountsSectionLinkTo(self):
        routes = set(get_routes())

        assert {"/connections", "/actual", "/coverage", "/import", "/admin"} <= routes


class TestMoreListsEverythingTheStripDoesNotName:
    def test_More_WhenOpened_ListsItsFourGroupsInPlainWords(self, serve):
        page = fetch(serve(), "/more")

        headings = [h.text() for h in elements(parse(page), "h2")]
        # Values is the sitting's line: whether values are shown on every page, and the control.
        assert headings == ["Accounts", "Checks", "Diagnostics", "Values"]
        assert "<title>More</title>" in page

    def test_More_EveryPageItLists_IsAPageThatAnswers(self, serve):
        base = serve()
        page = fetch(base, "/more")

        hrefs = re.findall(r'<a class="tap" href="([^"]+)">', page)
        assert {"/accounts", "/checks", "/diagnostics", "/coverage", "/review"} <= set(hrefs)
        for href in hrefs:
            # A page whose hook this bare instance does not wire says "not wired" with a 404, which
            # is still an answer; only a failure of the page itself is not.
            assert httpx.get(f"{base}{href}", timeout=20).status_code in (200, 404), href

    def test_More_MarksItselfCurrentInTheStripAndOffersNoWayBackToItself(self, serve):
        page = fetch(serve(), "/more")

        assert current_section(page) == ["More"]
        assert 'class="wayout"' not in page


class TestConnectionsHoldsSourcesInAndDestinationsOut:
    def test_Connections_WhenOpened_SaysWhereDataGoesOutAndWhereItComesFrom(self, serve):
        page = fetch(serve(), "/connections")

        assert page.index("Where data goes out") < page.index("Where data comes from")
        assert "<title>Connections</title>" in page
        assert current_section(page) == ["Connections"]

    def test_Connections_TheDestinationOut_IsActualInItsOwnVerdictWithALinkToItsPage(self, serve):
        page = fetch(serve(actual_status=lambda: results()), "/connections")

        out = page.split("Where data goes out")[1].split("Where data comes from")[0]
        assert "<strong>Actual</strong>" in out
        assert 'href="/actual">Open the Actual page</a>' in out

    def test_Connections_WhenActualIsNotWired_SaysSoAndStillLinksToItsPage(self, serve):
        out = fetch(serve(), "/connections").split("Where data goes out")[1]
        out = out.split("Where data comes from")[0]

        assert "Not wired on this instance." in out
        assert 'href="/actual"' in out

    def test_Connections_TheSourcesIn_AreTheBanksAndTheirConsent(self, serve):
        base = serve((connection("halifax", expires_in=timedelta(days=60)),))

        page = fetch(base, "/connections").split("Where data comes from")[1]

        assert "halifax" in page and "expires" in page
