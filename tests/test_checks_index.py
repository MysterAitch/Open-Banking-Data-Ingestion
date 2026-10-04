"""The Checks page: one row per check, with the result the home page's own checks last found.

KNOWN ANSWERS. Each Overview below is built by hand with a decided verdict for each check, so a
row that says the wrong thing is a disagreement with a number written before the page existed,
not a judgement about how a healthy page looks. The page reads the Overview the home page
already holds: a test counts the calls and fails if a row costs one.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pytest

from obdi import web_destinations
from obdi.checks_index import CHECKS, CheckResult, result_of
from obdi.navigation import PAGE_NAMES
from obdi.overview import (
    HOUSEKEEPING,
    INFORMATION,
    NOW,
    AccountOverview,
    AttentionItem,
    Overview,
)
from obdi.rebuild_hold import RebuildHold

WHEN = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)

SEVEN = [
    "/agreements",
    "/identity-health",
    "/balance-reconciliation",
    "/period-reconciliation",
    "/balance-walk",
    "/date-lag",
    "/review-report",
]


@pytest.fixture(autouse=True)
def live_instance(monkeypatch):
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")


def item(kind: str, message: str, *, severity: int = NOW) -> AttentionItem:
    return AttentionItem(
        kind=kind, severity=severity, message=message, remedy="r", href="/x", accounts=()
    )


def overview(*items: AttentionItem, rebuilding: RebuildHold | None = None) -> Overview:
    account = AccountOverview(
        ref="a", label="A", sources=(), rows=1, newest=None, last_asked=None, state="current",
        bound=None, items=0, closed=None, declared=True,
    )
    return Overview(
        generated_at=WHEN, checks_total=19, checks_run=19, items=items, accounts=(account,),
        rebuilding=rebuilding,
    )


def results(found: Overview | None) -> dict[str, CheckResult]:
    return {spec.route: result_of(spec, found) for spec in CHECKS}


class TestTheSevenChecks:
    def test_Checks_AreTheSevenReports_InTheOrderOfTheOldIndex(self):
        assert [spec.route for spec in CHECKS] == SEVEN

    def test_EachCheck_IsNamedByAQuestion_ThroughTheOneTableOfNames(self):
        for spec in CHECKS:
            assert PAGE_NAMES[spec.route].name.endswith("?"), spec.route
            assert PAGE_NAMES[spec.route].formerly, spec.route

    def test_EveryNewName_IsDistinct(self):
        names = [PAGE_NAMES[route].name for route in SEVEN]

        assert len(set(names)) == 7


class TestAHealthyOverview:
    def test_WhenNothingIsWrong_EveryCheckThatTheHomePageRunsIsInOrder(self):
        found = results(overview())

        for route in ("/agreements", "/identity-health", "/balance-reconciliation",
                      "/review-report"):
            assert found[route].state == "in order", route

    def test_ChecksTheHomePageDoesNotRun_SayCannotSayRatherThanInOrder(self):
        found = results(overview())

        assert found["/balance-walk"].state == "cannot say"
        assert found["/date-lag"].state == "cannot say"
        assert found["/period-reconciliation"].state == "cannot say"
        assert "open" in found["/date-lag"].sentence.lower()

    def test_StatementPeriods_WhenAnotherCheckFindsSomething_StillSayCannotSay(self):
        lapsed = item("agreement-lapsed", "A: in agreement through 2026-03-10.")
        found = results(overview(lapsed))

        assert found["/period-reconciliation"].state == "cannot say"

    def test_WhenAnAccountsAgreementHasLapsed_TheRowForDaysAddingUpLooks(self):
        message = "A: in agreement through 2026-03-10, more than 45 days ago."
        found = results(overview(item("agreement-lapsed", message)))

        assert found["/balance-reconciliation"].state == "look"
        assert found["/balance-reconciliation"].sentence == message
        assert [r for r, c in found.items() if c.state == "look"] == ["/balance-reconciliation"]


class TestAFault:
    def test_WhenTheIdentityCheckFindsPaymentsHeldTwice_ThatRowSaysLookAndNoOtherDoes(self):
        message = "Identity health: 2 payments are held by more than one row."
        found = results(overview(item("identity-health", message)))

        assert found["/identity-health"].state == "look"
        assert found["/identity-health"].sentence == message
        assert [r for r, c in found.items() if c.state == "look"] == ["/identity-health"]

    def test_WhenAMovementIsMissing_TheIdentityRowLooks_BecauseThatReportHoldsIt(self):
        found = results(overview(item("movement-completeness", "Movement completeness: 1 day.")))

        assert found["/identity-health"].state == "look"

    def test_WhenTheBalancesDisagree_OnlyTheBalanceRowLooks(self):
        found = results(overview(item("balance", "A: 1 break in the bank's chain.")))

        assert found["/balance-reconciliation"].state == "look"
        assert found["/identity-health"].state == "in order"

    def test_WhenTwoFaultsFallOnOneCheck_TheSentenceSaysHowManyMore(self):
        found = results(
            overview(item("balance", "A: 1 break."), item("balance", "B: 2 breaks."))
        )

        assert found["/balance-reconciliation"].sentence == "A: 1 break. And 1 more."

    def test_WhenOnlyInformationIsRaised_TheReviewRowStillLooks_BecauseFlagsAreOpen(self):
        found = results(
            overview(item("review", "3 transactions are flagged.", severity=INFORMATION))
        )

        assert found["/review-report"].state == "look"

    def test_WhenOnlyHousekeepingIsRaised_TheChipIsNotTheFaultChip(self):
        found = results(
            overview(item("identity-health", "Identity health: 1 id.", severity=HOUSEKEEPING))
        )

        assert found["/identity-health"].tone == "warn"

    def test_WhenAFaultIsUrgent_TheChipIsTheFaultChip(self):
        found = results(overview(item("balance", "A: 1 break.")))

        assert found["/balance-reconciliation"].tone == "bad"


class TestWhenTheAnswerIsNotKnown:
    def test_WithNoOverview_EveryCheckIsCannotSay_AndNoneIsInOrder(self):
        found = results(None)

        assert {c.state for c in found.values()} == {"cannot say"}

    def test_WhenACheckCouldNotRun_ItsRowIsCannotSay_NotInOrder(self):
        failed = item(
            "check-failed",
            "The balance reconciliation check could not run (OSError), so that condition "
            "is unwatched until it does.",
        )
        found = results(overview(failed))

        assert found["/balance-reconciliation"].state == "cannot say"
        assert found["/identity-health"].state == "in order"

    def test_WhenARebuildHoldsTheDerivedRows_TheChecksThatReadThemAreCannotSay(self):
        hold = RebuildHold(started="2026-10-01T12:00:00Z")
        found = results(overview(rebuilding=hold))

        for route in ("/identity-health", "/balance-reconciliation", "/review-report"):
            assert found[route].state == "cannot say", route
            assert "rebuild" in found[route].sentence.lower()


@pytest.fixture
def serve(serve_hub):
    return serve_hub


class TestThePage:
    def test_ChecksPage_HasOneRowPerCheck_EachLinkingToItsReport(self, serve):
        page = httpx.get(f"{serve(overview=lambda fresh: overview())}/checks", timeout=20).text

        links = re.findall(
            r'<li class="hub-row[^"]*"><div class="hub-head">'
            r'<a class="tap hub-name" href="([^"]+)"',
            page,
        )
        assert links == SEVEN

    def test_ChecksPage_GivesARailToTheRowsThatNeedALookAndToNoOther(self, serve):
        found = overview(item("balance", "A: 1 break."))
        page = httpx.get(f"{serve(overview=lambda fresh: found)}/checks", timeout=20).text
        main = page.split("<main", 1)[1]

        assert main.count("hub-row-bad") == 1
        assert main.count("hub-row-warn") == 0
        assert main.count("hub-row-") == 1

    def test_ChecksPage_WhenAllInOrder_GivesNoRowARail(self, serve):
        page = httpx.get(f"{serve(overview=lambda fresh: overview())}/checks", timeout=20).text

        assert "hub-row-" not in page.split("<main", 1)[1]

    def test_ChecksPage_SaysEachChecksResultInOneChip(self, serve):
        found = overview(item("balance", "A: 1 break."))
        page = httpx.get(f"{serve(overview=lambda fresh: found)}/checks", timeout=20).text
        page = page.split("<main", 1)[1]

        assert page.count("pill-bad") == 1
        assert "A: 1 break." in page
        assert page.count(">in order<") == 3
        assert page.count(">cannot say<") == 3

    def test_ChecksPage_CallsTheOverviewHookOnceForAllSevenRows(self, serve):
        calls: list[bool] = []

        def hook(fresh: bool) -> Overview:
            calls.append(fresh)
            return overview()

        httpx.get(f"{serve(overview=hook)}/checks", timeout=20)

        assert calls == [False]

    def test_ChecksAgain_AsksTheHomePageForAFreshOverview(self, serve):
        calls: list[bool] = []

        def hook(fresh: bool) -> Overview:
            calls.append(fresh)
            return overview()

        httpx.get(f"{serve(overview=hook)}/checks?fresh=1", timeout=20)

        assert calls == [True]

    def test_ChecksPage_WhenTheOverviewHookRaises_StillAnswersWithEveryRowCannotSay(self, serve):
        def boom(fresh: bool) -> Overview:
            raise OSError("store unreadable")

        response = httpx.get(f"{serve(overview=boom)}/checks", timeout=20)

        assert response.status_code == 200
        assert response.text.count(">cannot say<") == 7
        assert "store unreadable" not in response.text

    def test_ChecksPage_WhenNoOverviewIsWired_StillAnswersWithEveryRowCannotSay(self, serve):
        response = httpx.get(f"{serve()}/checks", timeout=20)

        assert response.status_code == 200
        assert response.text.count(">cannot say<") == 7

    def test_ChecksPage_SaysWhenItWasRead_AndWhatItWasReadFrom(self, serve):
        page = httpx.get(f"{serve(overview=lambda fresh: overview())}/checks", timeout=20).text

        assert "12:30" in page and "Check again" in page

    def test_Reports_StillAnswersWithTheChecksPage(self, serve):
        base = serve(overview=lambda fresh: overview())

        assert httpx.get(f"{base}/reports", timeout=20).text == httpx.get(
            f"{base}/checks", timeout=20
        ).text

    def test_ChecksPage_SaysItsOldName_OnceOnThePage(self, serve):
        page = httpx.get(f"{serve(overview=lambda fresh: overview())}/checks", timeout=20).text

        assert page.count("Formerly called Reports.") == 1

    def test_ReviewFlagsAction_WhenThatRouteIsNotServed_IsNotLinked(self, serve, monkeypatch):
        monkeypatch.setattr(web_destinations, "dispatcher_serves", lambda route: False)
        page = httpx.get(f"{serve(overview=lambda fresh: overview())}/checks", timeout=20).text

        assert "/review-flags" not in page

    def test_ReviewFlagsAction_WhenThatRouteIsServed_SitsInTheReviewFlagsRow(
        self, serve, monkeypatch
    ):
        monkeypatch.setattr(web_destinations, "dispatcher_serves", lambda route: True)
        page = httpx.get(f"{serve(overview=lambda fresh: overview())}/checks", timeout=20).text

        row = page.split('href="/review-report"')[1].split("</li>")[0]
        assert 'href="/review-flags"' in row
        assert page.count('href="/review-flags"') == 1

    def test_DispatcherServes_ForARealRouteAndAnAbsentOne_SaysYesAndNo(self):
        assert web_destinations.dispatcher_serves("/checks") is True
        assert web_destinations.dispatcher_serves("/no-such-page") is False
