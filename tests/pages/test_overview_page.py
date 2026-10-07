"""The home page as a person meets it: over real HTTP, and by direct render.

The household and its known answers are in test_overview.py. What is asserted
here is what the PAGE says about them - the words, the order, the links, and
above all what it does not say, because this is served by GET and no GET shows
a monetary value.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from html import unescape as html_unescape
from http.server import HTTPServer
from typing import ClassVar

import httpx
import pytest

from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.store import Store
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig, render_index
from obdi.read.alerts import Finding
from obdi.read.overview import STATE_RULES, OverviewCache
from test_account_pages import assert_tap_targets_are_thumb_sized
from test_balance_reconciliation import _built
from test_ledger import build_household
from test_ledger_page import SECRET_FIGURES, SECRET_TEXT
from test_overview import assemble, household  # noqa: F401


@pytest.fixture(autouse=True)
def live_instance(monkeypatch):
    """An unlabelled instance prefixes every title and heading, which these
    assertions are not about."""
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")


def serve(tmp_path, overview, **hooks):
    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        overview=overview,
        **hooks,
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


def home(tmp_path, overview, path="/", **hooks) -> str:
    httpd, base = serve(tmp_path, overview, **hooks)
    try:
        response = httpx.get(f"{base}{path}", timeout=20)
    finally:
        httpd.shutdown()
    assert response.status_code == 200
    return response.text


def text_of(page: str) -> str:
    return re.sub(r"<[^>]+>", " ", page)


def verdict_of_page(page: str) -> str:
    found = re.search(r'<p class="verdict[^"]*" id="verdict"><span>(.*?)</span>', page)
    assert found is not None, "the page has no verdict"
    return html_unescape(found.group(1))


def row_of(page: str, ref: str) -> str:
    """One account's row: the link, which is all a person reads of it."""
    marker = f'<a class="tap arow" href="/ledger?ref={ref}">'
    assert marker in page, f"no row for {ref}"
    return page.split(marker)[1].split("</a>")[0]


def rows_in(page: str) -> list[str]:
    """The live accounts' rows: the archived accounts' own rows are inside their fold."""
    live = re.sub(r"<details><summary>\d+ archived accounts?</summary>.*?</details>", "", page,
                  flags=re.DOTALL)
    return re.findall(r'<a class="tap arow" href="/ledger\?ref=(acct-[a-z]+)">', live)


class TestNeedsAttention:
    def test_Home_ByDefault_IsTitledAndHeadedOverview(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        assert "<title>Overview</title>" in page
        assert "<h1>Overview</h1>" in page
        assert "Bank connections" not in page

    def test_Home_WhenNothingNeedsAttention_SaysSoInAPositiveVerdictAndKeepsTheChecksBehindAFold(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))

        assert verdict_of_page(page) == "Everything checked is in order."
        assert 'class="verdict ok' in page
        assert "<summary>19 checks ran at 15:02</summary>" in page, "14:02 UTC is 15:02 BST"
        assert re.search(r"\d\d:\d\dZ", page) is None, "a time carries no zone mark"
        assert "UTC" not in page, "the owner reads the page's times as his own clock"
        assert '<ol class="attention">' not in page
        assert 'class="todos"' not in page, "a day with nothing to do says so in one line and stops"

    def test_Home_WhenSomethingNeedsAttention_RanksItInBandsNamedByWhatToDoAndLinksWhereItGoes(
        self, tmp_path, household
    ):
        page = home(
            tmp_path,
            lambda fresh: assemble(
                household,
                findings=lambda: [
                    Finding("disk:data", "the data volume is 91% full"),
                    Finding("silent-feed:acct-silent:starling", "acct-silent: gone quiet"),
                ],
            ),
        )

        assert verdict_of_page(page) == "1 fault to look at now and 1 thing to look at soon."
        assert "Everything checked is in order" not in page
        assert page.index("gone quiet") < page.index("the data volume is 91% full")
        assert page.index('class="todo now"') < page.index('class="todo soon"')
        assert 'href="/account?ref=acct-silent">See the feed</a>' in page
        assert 'href="/admin">See the space</a>' in page
        for old in ("Data at risk", "Will break soon", "Housekeeping"):
            assert old not in page
        assert ">Open</a>" not in page, "every control says what it does"

    def test_Home_WhenOnlyRemindersRemain_SaysThereAreNoFaultsBeforeCountingThem(
        self, tmp_path, household
    ):
        page = home(
            tmp_path,
            lambda fresh: assemble(
                household, findings=lambda: [Finding("scheduler-late-wait", "waiting")]
            ),
        )

        assert verdict_of_page(page) == "Everything checked is in order."
        evidence = page.split('class="evidence"')[1].split("</details>")[0]
        assert "waiting" in evidence, "a fact with nothing to do is said, quietly, in the evidence"
        assert 'class="todos"' not in page

    def test_Home_WhenACheckCouldNotRun_SaysThatCheckDidNotRunAndTheVerdictCallsItAFault(
        self, tmp_path, household
    ):
        def boom():
            raise RuntimeError("secret detail")

        page = home(tmp_path, lambda fresh: assemble(household, findings=boom))

        assert "The alert check could not run (RuntimeError)" in page
        assert "<summary>8 of 19 checks ran at 15:02; the rest could not run</summary>" in page
        assert "Only 8 of 19 checks could run" in page
        assert "fault to look at now" in verdict_of_page(page)
        assert "Everything checked is in order" not in page
        assert "secret detail" not in page

    def test_Home_WhenTheOverviewCannotBeAssembled_SaysNoChecksRanRatherThanShowingNothing(
        self, tmp_path
    ):
        def boom(fresh):
            raise RuntimeError("store locked at /private/path")

        page = home(tmp_path, boom)

        assert "The overview could not be assembled (RuntimeError), so no checks ran." in page
        assert "nothing needs attention" not in page
        assert "/private/path" not in page

    def test_Home_WhenNoOverviewIsWired_SaysNothingWasChecked(self, tmp_path):
        page = home(tmp_path, None)

        assert "no Overview wired, so nothing was checked" in page
        assert "nothing needs attention" not in page

    def test_Home_WhenAskedForAFreshOverview_BypassesTheHeldOne(self, tmp_path, household):
        cache = OverviewCache(seconds=3600)
        builds = []

        def load(fresh):
            def build():
                builds.append(1)
                return assemble(household)

            return cache.get(build, fresh=fresh)

        httpd, base = serve(tmp_path, load)
        try:
            httpx.get(f"{base}/", timeout=20)
            httpx.get(f"{base}/", timeout=20)
            assert len(builds) == 1
            page = httpx.get(f"{base}/?fresh=1", timeout=20).text
        finally:
            httpd.shutdown()

        assert len(builds) == 2
        assert "Assembled" in page and "reused for up to 60 seconds" in page
        assert 'href="/?fresh=1"' in page

    def test_Home_WithHostileText_EscapesItEverywhereItAppears(self, tmp_path, household):
        with Store(household) as store:
            store.declare_account(
                AccountRecord(ref=AccountRef("acct-hostile"), label="<script>alert(1)</script>")
            )
        page = home(
            tmp_path,
            lambda fresh: assemble(
                household, findings=lambda: [Finding("disk:data", "<img src=x onerror=1>")]
            ),
        )

        assert "<script>alert(1)</script>" not in page
        assert "<img src=x" not in page
        assert "&lt;script&gt;" in page and "&lt;img src=x" in page


class TestAccounts:
    def test_Home_AccountFedByThreeSources_AppearsAsOneRowAndNamesNoSource(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))

        assert page.count('<a class="tap arow" href="/ledger?ref=acct-multi">') == 1
        for source in ("csv-export", "truelayer"):
            assert source not in row_of(page, "acct-multi"), "sources are on the account's page"

    def test_Home_DeclaredButEmptyAccount_SaysNothingIsHeldAndDrawsNoFill(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))

        row = row_of(page, "acct-empty")
        assert "Nothing held yet." in row
        assert "Label of acct-empty" in row
        assert '<span class="bar" aria-hidden="true"></span>' in row

    def test_Home_ArchivedAccount_IsFoldedWithItsDateAndIsNoRow(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        assert "acct-old" not in rows_in(page)
        folded = page.split("<summary>1 archived account</summary>")[1].split("</details>")[0]
        assert 'href="/ledger?ref=acct-old"' in folded and "Archived 2026-01-31." in folded

    def test_Home_EveryLiveAccount_IsOneTapToItsPage(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        for ref in ("acct-current", "acct-multi", "acct-empty"):
            assert f'<a class="tap arow" href="/ledger?ref={ref}">' in page

    def test_Home_Accounts_AreListedHeldBackThenUnprovenThenInAgreementThenQuiet(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))

        order = rows_in(page)

        # None of the household has a known balance, so every live account is unproven;
        # the idle ones follow.
        assert order.index("acct-current") < order.index("acct-quiet") < order.index("acct-empty")

    def test_Home_AccountWithAFeedGoneQuiet_SaysWhatItWaitsForBesideItsNameInRed(
        self, tmp_path, household
    ):
        page = home(
            tmp_path,
            lambda fresh: assemble(
                household,
                findings=lambda: [Finding("silent-feed:acct-silent:starling", "a")],
            ),
        )

        assert '<span class="a-flag bad">Feed silent</span>' in row_of(page, "acct-silent")
        assert "a-flag" not in row_of(page, "acct-current")

    def test_Home_FeedStatesAndBindingsOfAnAccount_AreNotRepeatedOnEveryRow(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household, actual_bound={"acct-current"}))

        for state in STATE_RULES:
            assert f"<strong>{state}</strong> - " not in page
        assert "<dd>bound</dd>" not in page and "<dd>not bound</dd>" not in page

    def test_Home_WhenNoAccountIsHeld_SaysSoRatherThanShowingAnEmptyTable(self, tmp_path):
        path = tmp_path / "empty.sqlite3"
        with Store(path):
            pass

        page = home(tmp_path, lambda fresh: assemble(path))

        assert "No account is held or declared yet." in page
        assert 'class="accounts-list"' not in page.split('id="accounts"')[1].split("</section>")[0]

    def test_Home_Accounts_AreRowsAndNotATable_SoNothingScrollsSidewaysOnAPhone(
        self, tmp_path, household
    ):
        """Eight columns did not fit a phone: the reference wrapped mid-word and
        the links to each ledger were off the edge of the screen."""
        page = home(tmp_path, lambda fresh: assemble(household))
        accounts = page.split('id="accounts"')[1].split("</section>")[0]

        assert '<ul class="alist">' in accounts
        assert "<table" not in accounts

    def test_Home_Controls_AreThumbSizedTapTargets(self, tmp_path, household):
        page = home(
            tmp_path,
            lambda fresh: assemble(
                household, findings=lambda: [Finding("disk:data", "full")]
            ),
        )

        assert_tap_targets_are_thumb_sized(page)


def without_the_pages_own_clock(page: str) -> str:
    """The page less the one line that counts hours since the overview was assembled.

    The overview is assembled at a fixed moment and the page is served at the present one, so
    that line holds a number that changes every hour and is no stored value. On 2026-10-05 it
    read "Assembled 101 hours ago", and "101" is one of the planted figures: the test failed for
    one hour and would have passed the next.
    """
    return re.sub(r"Assembled [^.<]*?\bago\b", "Assembled earlier", page)


class TestNoFigureReachesTheOverview:
    def test_Get_WhenReconciliationHoldsFigures_ShowsNoBalanceAmountOrPayee(self, tmp_path):
        path = tmp_path / "r.sqlite3"
        with Store(path) as store:
            _built(store, omit=("e",))

        page = without_the_pages_own_clock(home(tmp_path, lambda fresh: assemble(path)))

        assert "break" in page or "day" in page
        for secret in ("Alpha Bakery", "Echo Cafe", "Charlie Payroll", "Delta Rail"):
            assert secret not in page
        for figure in ("1198.89", "119889", "100000", "1000.00", "7.77", "777", "12.34", "250.00"):
            assert figure not in page, figure

    def test_Get_WhenTheHouseholdHoldsPrivateValues_ShowsNoneOfThem(self, tmp_path):
        path = tmp_path / "h.sqlite3"
        with Store(path) as store:
            build_household(store)

        page = without_the_pages_own_clock(home(tmp_path, lambda fresh: assemble(path)))

        for secret in SECRET_TEXT:
            assert secret not in page, secret
        for figure in SECRET_FIGURES:
            assert figure not in text_of(page).replace(",", ""), figure


class TestTheExistingSectionsRemain:
    """Each section that left the home page is on the page that now carries it."""

    HOOKS: ClassVar[dict[str, Callable[[], object]]] = {
        "rebuild_derived": lambda: "started",
        "forget_actual": lambda: 0,
        "push_actual": lambda: "queued",
        "actual_status": lambda: [],
    }

    def test_Pages_EveryExistingSection_IsPresentOnThePageThatNowCarriesIt(
        self, tmp_path, household
    ):
        def page_at(path):
            return home(tmp_path, lambda fresh: assemble(household), path=path, **self.HOOKS)

        assert "Import a file" in page_at("/import")
        assert "Preview import" in page_at("/import")
        assert "Repairs" in page_at("/diagnostics")
        assert "Rebuild from raw" in page_at("/diagnostics")
        assert "Add a bank" in page_at("/connections")
        assert "Push to Actual now" in page_at("/actual")

    def test_Home_ChecksAndDiagnosticsAreReachedThroughMoreAndNotListedOnTheHomePage(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))
        strip = re.search(r'<nav class="sitenav".*?</nav>', page, re.S).group(0)
        body = page.replace(strip, "")

        assert 'href="/more"' in strip
        assert 'href="/checks"' not in strip and 'href="/diagnostics"' not in strip
        for route in ("/checks", "/diagnostics"):
            assert f'href="{route}"' not in body
        for route in ("/agreements", "/date-lag", "/balance-walk", "/artefacts", "/attempts"):
            assert f'href="{route}"' not in body

    def test_RenderIndex_CalledDirectlyWithoutAnOverview_StillRendersBothHalves(self, tmp_path):
        page = render_index(ConnectionStore(tmp_path / "c.json")).decode()

        assert "no Overview wired" in page
        assert 'id="verdict"' in page and 'id="system"' in page


@pytest.mark.parametrize("path", ["/checks", "/diagnostics", "/bring-in"])
def test_HubPage_WhenOpened_LinksHomeThroughTheStripAlone(tmp_path, path):
    page = home(tmp_path, None, path=path)

    strip = re.search(r'<nav class="sitenav".*?</nav>', page, re.S).group(0)
    assert 'href="/"' in strip
    assert "Back to overview" not in page
