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
from http.server import HTTPServer
from typing import ClassVar

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.alerts import Finding
from obdi.connections import ConnectionStore
from obdi.overview import STATE_RULES, OverviewCache
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig, render_index
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


class TestNeedsAttention:
    def test_Home_ByDefault_IsTitledAndHeadedOverview(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        assert "<title>Overview</title>" in page
        assert "<h1>Overview</h1>" in page
        assert "Bank connections" not in page

    def test_Home_WhenNothingNeedsAttention_SaysSoInWordsWithWhatWasCheckedAndWhen(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))

        assert "17 checks run at 14:02Z: nothing needs attention." in page
        assert "Checked: silent feeds" in page
        assert 'class="attention"' not in page

    def test_Home_WhenSomethingNeedsAttention_ListsItWithItsLinkAndDoesNotSayAllClear(
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

        assert "nothing needs attention" not in page
        assert "2 things need attention." in page
        assert page.index("acct-silent: gone quiet") < page.index("the data volume is 91% full")
        assert 'href="/account?ref=acct-silent"' in page
        assert 'href="/admin"' in page
        assert "Data at risk" in page and "Will break soon" in page

    def test_Home_WhenACheckCouldNotRun_SaysThatCheckDidNotRunAndCountsOnlyThoseThatDid(
        self, tmp_path, household
    ):
        def boom():
            raise RuntimeError("secret detail")

        page = home(tmp_path, lambda fresh: assemble(household, findings=boom))

        assert "The alert check could not run (RuntimeError)" in page
        assert "7 of 17 checks run at 14:02Z; the rest could not run" in page
        assert "nothing needs attention" not in page
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
    def test_Home_AccountFedByThreeSources_AppearsAsOneRowWithAllThreeSources(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))

        assert page.count('<span class="mono muted">acct-multi</span>') == 1
        row = page.split('<span class="mono muted">acct-multi</span>')[1].split("</li>")[0]
        for source in ("csv-export", "starling", "truelayer"):
            assert f">{source}</span>" in row

    def test_Home_DeclaredButEmptyAccount_AppearsMarkedEmpty(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        card = page.split('<span class="mono muted">acct-empty</span>')[0].rsplit("<li", 1)[1]
        assert ">empty</span>" in card

    def test_Home_ArchivedAccount_IsLabelledWithItsDateAndListedLast(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        rows = re.findall(r'<span class="mono muted">(acct-[a-z]+)</span>', page)
        assert rows[-1] == "acct-old"
        card = page.split('<span class="mono muted">acct-old</span>')[0].rsplit("<li", 1)[1]
        assert ">archived</span> since 2026-01-31" in card

    def test_Home_EveryAccount_LinksToItsLedgerAndItsShapePage(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        for ref in ("acct-current", "acct-multi", "acct-empty", "acct-old"):
            assert f'href="/ledger?ref={ref}"' in page
            assert f'href="/account?ref={ref}"' in page

    def test_Home_AccountWithItemsConcerningIt_ShowsTheCountAsALinkToTheList(
        self, tmp_path, household
    ):
        page = home(
            tmp_path,
            lambda fresh: assemble(
                household,
                findings=lambda: [
                    Finding("silent-feed:acct-silent:starling", "a"),
                    Finding("refusals:halifax:starling:uid-silent", "b"),
                ],
            ),
        )

        row = page.split('<span class="mono muted">acct-silent</span>')[1].split("</li>")[0]
        assert 'href="#attention">2 items</a>' in row
        other = page.split('<span class="mono muted">acct-current</span>')[1].split("</li>")[0]
        assert "none</span>" in other

    def test_Home_ShowsEachStateAsAWordAndALegendWithEveryRule(self, tmp_path, household):
        page = home(tmp_path, lambda fresh: assemble(household))

        for state, rule in STATE_RULES.items():
            assert f"<strong>{state}</strong> - " in page
            assert rule.split(";")[0].split(",")[0][:30] in text_of(page).replace("&#x27;", "'")
        for word in ("current", "quiet", "silent", "never asked", "file-only"):
            assert f">{word}</span>" in page

    def test_Home_BoundToActual_SaysBoundOrNotBoundOrNothingWhereActualIsOff(
        self, tmp_path, household
    ):
        on = home(
            tmp_path, lambda fresh: assemble(household, actual_bound={"acct-current"})
        )
        off = home(tmp_path, lambda fresh: assemble(household))

        assert "<dd>bound</dd>" in on and "<dd>not bound</dd>" in on
        assert "not bound" not in off and "<dd>bound</dd>" not in off

    def test_Home_WhenNoAccountIsHeld_SaysSoRatherThanShowingAnEmptyTable(self, tmp_path):
        path = tmp_path / "empty.sqlite3"
        with Store(path):
            pass

        page = home(tmp_path, lambda fresh: assemble(path))

        assert "No account is held or declared yet." in page
        assert 'class="accounts"' not in page.split('id="accounts"')[1].split("</section>")[0]

    def test_Home_Accounts_AreCardsAndNotATable_SoNothingScrollsSidewaysOnAPhone(
        self, tmp_path, household
    ):
        """Eight columns did not fit a phone: the reference wrapped mid-word and
        the links to each ledger were off the edge of the screen."""
        page = home(tmp_path, lambda fresh: assemble(household))
        accounts = page.split('id="accounts"')[1].split("</section>")[0]

        assert '<ul class="accounts">' in accounts
        assert "<table" not in accounts

    def test_Home_Controls_AreThumbSizedTapTargets(self, tmp_path, household):
        page = home(
            tmp_path,
            lambda fresh: assemble(
                household, findings=lambda: [Finding("disk:data", "full")]
            ),
        )

        assert_tap_targets_are_thumb_sized(page)


class TestNoFigureReachesTheOverview:
    def test_Get_WhenReconciliationHoldsFigures_ShowsNoBalanceAmountOrPayee(self, tmp_path):
        path = tmp_path / "r.sqlite3"
        with Store(path) as store:
            _built(store, omit=("e",))

        page = home(tmp_path, lambda fresh: assemble(path))

        assert "break" in page or "day" in page
        for secret in ("Alpha Bakery", "Echo Cafe", "Charlie Payroll", "Delta Rail"):
            assert secret not in page
        for figure in ("1198.89", "119889", "100000", "1000.00", "7.77", "777", "12.34", "250.00"):
            assert figure not in page, figure

    def test_Get_WhenTheHouseholdHoldsPrivateValues_ShowsNoneOfThem(self, tmp_path):
        path = tmp_path / "h.sqlite3"
        with Store(path) as store:
            build_household(store)

        page = home(tmp_path, lambda fresh: assemble(path))

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
        assert "Danger zone" in page_at("/admin")
        assert "Rebuild from raw" in page_at("/admin")
        assert "Add a bank" in page_at("/connections")
        assert "Push to Actual now" in page_at("/actual")

    def test_Home_ReportAndEvidenceAreReachedFromTheStripAndNotListedOnTheHomePage(
        self, tmp_path, household
    ):
        page = home(tmp_path, lambda fresh: assemble(household))
        strip = re.search(r'<nav class="sitenav".*?</nav>', page, re.S).group(0)
        body = page.replace(strip, "")

        assert 'href="/reports"' in strip and 'href="/evidence"' in strip
        for route in ("/reports", "/evidence"):
            assert f'href="{route}"' not in body
        for route in ("/agreements", "/date-lag", "/balance-walk", "/artefacts", "/attempts"):
            assert f'href="{route}"' not in body

    def test_RenderIndex_CalledDirectlyWithoutAnOverview_StillRendersBothHalves(self, tmp_path):
        page = render_index(ConnectionStore(tmp_path / "c.json")).decode()

        assert "no Overview wired" in page
        assert 'id="attention"' in page and 'id="system"' in page


@pytest.mark.parametrize("path", ["/reports", "/evidence"])
def test_IndexPage_WhenOpened_LinksBackToTheOverview(tmp_path, path):
    page = home(tmp_path, None, path=path)

    assert "Back to overview" in page
