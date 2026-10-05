"""Bring in and Diagnostics: the two hub pages beside Checks, and the names the reports now carry.

Bring in says the state of every way data enters; Diagnostics says what each page there is for
and holds the two repairs apart. Every number below is decided by the connections and kept
statements the test builds, so a page that says another is wrong against a known answer.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pytest

from obdi.connections import Connection, ConnectionStore
from obdi.namespaces import UNASSIGNED_ACCOUNT
from obdi.navigation import PAGE_NAMES
from obdi.store import Store

#: Far enough ahead that the date is the same whenever the test runs.
CONSENT_FAR = "2099-06-15T00:00:00+00:00"
CONSENT_SOON = "2099-02-03T00:00:00+00:00"


@pytest.fixture(autouse=True)
def live_instance(monkeypatch):
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")


@pytest.fixture
def serve(serve_hub):
    return serve_hub


def bank(name: str, consent: str = CONSENT_FAR) -> Connection:
    return Connection(
        connection_id=name, provider="p", refresh_token="r", consent_expires_at=consent
    )


def banks(tmp_path, *connections: Connection) -> ConnectionStore:
    store = ConnectionStore(tmp_path / "banks.json")
    for one in connections:
        store.put(one)
    return store


def statement(account: str, *, parser: str | None = "uk", refusal: str = "") -> dict[str, object]:
    return {
        "id": 1, "account_ref": account, "parser": parser, "refusal": refusal, "origin": "x.pdf",
    }


class TestBringIn:
    def test_BringInPage_NamesEveryWayDataEnters_AndLinksToEach(self, serve):
        page = httpx.get(f"{serve()}/bring-in", timeout=20).text

        for href in ("/connections", "/import", "/statement-shape", "/statements"):
            assert f'href="{href}"' in page, href

    def test_BringInPage_WithNoBank_SaysNoBankIsConnected(self, serve):
        page = httpx.get(f"{serve()}/bring-in", timeout=20).text

        assert "No bank is connected." in page

    def test_BringInPage_WithTwoBanks_SaysHowManyAndWhoseConsentExpiresFirst(
        self, serve, tmp_path
    ):
        store = banks(tmp_path, bank("halifax", CONSENT_FAR), bank("monzo", CONSENT_SOON))
        answered = {"halifax": "2026-10-01T09:15:00+00:00"}

        base = serve(store, connection_last_answered=lambda: answered)
        page = httpx.get(f"{base}/bring-in", timeout=20).text

        assert "2 banks connected through the aggregator." in page
        assert "The soonest consent expires 2099-02-03" in page
        assert "<strong>halifax</strong> - last answered 2026-10-01 09:15;" in page
        assert "<strong>monzo</strong> - has never answered" in page
        assert page.count("Times are UTC.") == 1, "the zone is said once, never as a trailing Z"
        assert "09:15Z" not in page

    def test_BringInPage_WithOneBank_SaysBankNotBanks(self, serve, tmp_path):
        base = serve(banks(tmp_path, bank("halifax")))

        page = httpx.get(f"{base}/bring-in", timeout=20).text

        assert "1 bank connected through the aggregator." in page
        assert "Times are UTC." not in page, "no instant is shown, so no zone is said"

    def test_BringInPage_StatementsKept_CountsUnassignedAndUnread(self, serve):
        kept = [
            statement("acct-1"),
            statement(UNASSIGNED_ACCOUNT),
            statement(UNASSIGNED_ACCOUNT, parser=None),
            statement("acct-2", refusal="balances do not carry"),
        ]
        page = httpx.get(f"{serve(kept_statements=lambda: kept)}/bring-in", timeout=20).text

        assert "4 kept, 2 waiting for an account, 2 not read." in page
        assert 'class="pill pill-warn">waiting<' in page

    def test_BringInPage_StatementsKept_WhenAllFiledAndRead_SaysSo(self, serve):
        kept = [statement("acct-1"), statement("acct-2")]
        page = httpx.get(f"{serve(kept_statements=lambda: kept)}/bring-in", timeout=20).text

        assert "2 kept, 0 waiting for an account, 0 not read." in page
        assert ">all filed<" in page

    def test_BringInPage_WhenNoStatementIsKept_SaysNoneIsKept(self, serve):
        page = httpx.get(f"{serve(kept_statements=list)}/bring-in", timeout=20).text

        assert "No statement has been kept yet." in page

    def test_BringInPage_WhenKeptStatementsCannotBeRead_StillAnswersAndSaysSo(self, serve):
        def boom() -> list[dict[str, object]]:
            raise OSError("locked")

        response = httpx.get(f"{serve(kept_statements=boom)}/bring-in", timeout=20)

        assert response.status_code == 200
        assert "could not be read" in response.text and "locked" not in response.text

    def test_BringInPage_WhenTheLastAnsweredHookRaises_StillListsTheBanks(self, serve, tmp_path):
        def boom() -> dict[str, str]:
            raise OSError("locked")

        base = serve(banks(tmp_path, bank("halifax")), connection_last_answered=boom)
        page = httpx.get(f"{base}/bring-in", timeout=20).text

        assert "<strong>halifax</strong> - has never answered" in page

    def test_BringInPage_ShowsNoAmountOrDescription(self, serve):
        page = httpx.get(f"{serve(kept_statements=lambda: [statement('acct-1')])}/bring-in").text
        main = page.split("<main", 1)[1]

        assert not re.search(r"\d[\d,]*\.\d{2}\b", main)


class TestWhenEachConnectionLastAnswered:
    """Read through the ledger's own door: an ask is recorded, then the page's source is asked."""

    def test_ConnectionWithALandedAsk_IsStampedWithItsNewestOne(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            for day in (1, 3, 2):
                store.record_attempt(
                    source="truelayer", connection_id="halifax", account_ref="a", asked="x",
                    request_meta="{}", outcome="landed",
                    now=datetime(2026, 10, day, 8, 0, tzinfo=UTC),
                )

            assert store.last_landed_by_connection() == {"halifax": "2026-10-03T08:00:00+00:00"}

    def test_ConnectionWhoseAsksAllFailed_IsAbsent_SoThePageSaysItNeverAnswered(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.record_attempt(
                source="truelayer", connection_id="monzo", account_ref="a", asked="x",
                request_meta="{}", outcome="refused", http_status=403,
                now=datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
            )

            assert store.last_landed_by_connection() == {}

    def test_TwoConnections_AreStampedSeparately(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            for name, day in (("halifax", 1), ("monzo", 2)):
                store.record_attempt(
                    source="truelayer", connection_id=name, account_ref="a", asked="x",
                    request_meta="{}", outcome="landed",
                    now=datetime(2026, 10, day, 8, 0, tzinfo=UTC),
                )

            assert set(store.last_landed_by_connection()) == {"halifax", "monzo"}


FEED_ANSWER = "2026-10-04T15:57:12.345678+00:00"


def held_by(source: str, *accounts: str) -> dict[tuple[str, str], list[str]]:
    return {(account, source): ["connection"] for account in accounts}


class TestBringInTheBanksOwnFeed:
    """The household's main bank is also read directly, with a token the owner holds.

    The answers are decided by what the hooks give: the feed's last landed ask, which accounts
    hold rows it delivered, and whether its token resolved when the process started.
    """

    def page(self, serve, tmp_path, *, aggregator=("halifax", "monzo"), **hooks) -> str:
        store = banks(tmp_path, *(bank(name) for name in aggregator))
        return httpx.get(f"{serve(store, **hooks)}/bring-in", timeout=20).text

    def test_FeedConfiguredAndAnswered_IsAConnectionOfItsOwnWithItsLastAnswerAndAccounts(
        self, serve, tmp_path
    ):
        page = self.page(
            serve,
            tmp_path,
            starling_probe=lambda cutoff: None,
            connection_last_answered=lambda: {"starling-api": FEED_ANSWER},
            source_connections=lambda: {
                **held_by("starling", "starling-personal", "starling-joint"),
                **held_by("truelayer", "halifax-current"),
            },
            display_labels=lambda: {"starling-personal": "Main account"},
        )

        assert "The bank&#x27;s own feed" in page
        assert "Read directly from the bank, not through the aggregator." in page
        assert "Last answered 2026-10-04 15:57." in page
        assert "Feeds 2 accounts: starling-joint, Main account (starling-personal)." in page
        assert "halifax-current" not in page, "an account only the aggregator feeds is not its"
        assert "no answer yet" not in page

    def test_HeaderCount_NamesBothKindsOfConnectionInThePagesOwnTerms(self, serve, tmp_path):
        page = self.page(serve, tmp_path, starling_probe=lambda cutoff: None)

        assert "Connected: 1 bank&#x27;s own feed and 2 banks through the aggregator." in page

    def test_HeaderCount_WithOneAggregatorBank_UsesTheSingular(self, serve, tmp_path):
        page = self.page(
            serve, tmp_path, aggregator=("halifax",), starling_probe=lambda cutoff: None
        )

        assert "Connected: 1 bank&#x27;s own feed and 1 bank through the aggregator." in page

    def test_FeedConfiguredButNeverAnswered_SaysSoWithAChipAndNoAccounts(self, serve, tmp_path):
        page = self.page(serve, tmp_path, starling_probe=lambda cutoff: None)

        assert "Has never answered." in page
        assert 'class="pill pill-warn">no answer yet<' in page
        assert "No account is fed yet." in page

    def test_FeedNotConfigured_SaysSoQuietlyAndIsNotCountedAsConnected(self, serve, tmp_path):
        page = self.page(serve, tmp_path)

        assert "Not configured here: the bank&#x27;s access token is not set" in page
        assert "Connected: 2 banks through the aggregator." in page
        assert "own feed and" not in page
        assert "no answer yet" not in page
        assert 'pill-bad' not in page.split("The bank&#x27;s own feed")[1].split("</li>")[0]

    def test_FeedNotConfiguredNowButAnsweredBefore_SaysWhenItLastAnsweredAndWhatItFed(
        self, serve, tmp_path
    ):
        page = self.page(
            serve,
            tmp_path,
            connection_last_answered=lambda: {"starling-api": "2026-09-01T08:00:00+00:00"},
            source_connections=lambda: held_by("starling", "starling-personal"),
        )

        assert "Not configured here" in page
        assert "It last answered 2026-09-01 08:00." in page
        assert "Feeds 1 account: starling-personal." in page

    def test_FeedFeedingMoreThanThreeAccounts_SaysTheCountAlone(self, serve, tmp_path):
        spaces = tuple(f"starling-space-{n}" for n in range(1, 5))
        page = self.page(
            serve,
            tmp_path,
            starling_probe=lambda cutoff: None,
            connection_last_answered=lambda: {"starling-api": FEED_ANSWER},
            source_connections=lambda: held_by("starling", *spaces),
        )

        assert "Feeds 4 accounts." in page
        assert "starling-space-1" not in page

    def test_FeedsInstant_IsInTheHouseFormWithTheZoneSaidOnceOnThePage(self, serve, tmp_path):
        page = self.page(
            serve,
            tmp_path,
            starling_probe=lambda cutoff: None,
            connection_last_answered=lambda: {
                "starling-api": FEED_ANSWER,
                "halifax": "2026-10-01T09:15:00+00:00",
            },
        )

        assert page.count("Times are UTC.") == 1
        assert not re.search(r"\d{2}:\d{2}Z|\d{2}:\d{2}:\d{2}", page.split("<body")[1])

    def test_PageWithAFeed_NamesNoVariableAndShowsNoPartOfAToken(self, serve, tmp_path):
        page = self.page(serve, tmp_path, starling_probe=lambda cutoff: None)

        assert "OBDI_" not in page and "STARLING_PERSONAL" not in page
        assert "TRUELAYER_" not in page

    def test_FeedsHookThatFails_LeavesTheRowSayingSoAndTheRestOfThePage(self, serve, tmp_path):
        def boom() -> dict[tuple[str, str], list[str]]:
            raise OSError("locked")

        response = httpx.get(
            f"{serve(banks(tmp_path, bank('halifax')), source_connections=boom)}/bring-in",
            timeout=20,
        )

        assert response.status_code == 200
        assert "The bank&#x27;s feed could not be read just now." in response.text
        assert "1 bank connected through the aggregator." in response.text
        assert "locked" not in response.text

    def test_FeedHookNotWired_PageStillAnswersWithAQuietRow(self, serve, tmp_path):
        from obdi.web_destinations import render_bring_in

        page = render_bring_in(banks(tmp_path, bank("halifax"))).decode()

        assert "Not available on this instance." in page


class TestDiagnostics:
    def test_DiagnosticsPage_OpensBySayingItsPagesExistToExplainAFault(self, serve):
        page = httpx.get(f"{serve()}/diagnostics", timeout=20).text

        assert page.index("exist to explain a fault") < page.index('href="/artefacts"')

    def test_DiagnosticsPage_ListsTheRawPagesAndNotTheChecks(self, serve):
        page = httpx.get(f"{serve()}/diagnostics", timeout=20).text
        main = page.split("<main", 1)[1]

        for href in ("/artefacts", "/attempts", "/fetch-timeline", "/spaces"):
            assert f'href="{href}"' in main, href
        for href in ("/agreements", "/identity-health", "/balance-walk"):
            assert f'href="{href}"' not in main, href

    def test_DiagnosticsPage_OffersFieldStatisticsForEachHeldAccount(self, serve):
        base = serve(
            held_accounts=lambda: ["acct-b", "acct-a"],
            display_labels=lambda: {"acct-a": "Current"},
        )

        page = httpx.get(f"{base}/diagnostics", timeout=20).text

        assert page.index('href="/account?ref=acct-a"') < page.index('href="/account?ref=acct-b"')
        assert ">Current</a>" in page and ">acct-b</a>" in page
        assert "Field statistics for each of 2 accounts" in page

    def test_DiagnosticsPage_WhenNoAccountIsHeld_OffersNoStatisticsList(self, serve):
        page = httpx.get(f"{serve(held_accounts=list)}/diagnostics", timeout=20).text

        assert "Field statistics for each" not in page

    def test_Repairs_WhenWired_SitInOneBorderedBlockEachBehindItsConfirmation(self, serve):
        base = serve(rebuild_derived=lambda: "started", forget_actual=lambda: 0)

        page = httpx.get(f"{base}/diagnostics", timeout=20).text
        block = re.search(r'<section class="diag-danger".*?</section>', page, re.S)

        assert block is not None
        forms = re.findall(r"<form .*?</form>", block.group(0), re.S)
        assert len(forms) == 2
        for form in forms:
            assert 'name="confirm" value="yes" required' in form
            assert form.count("<p>") == 1, "one sentence says what it does"
        assert 'action="/rebuild-derived"' in forms[0]
        assert 'action="/forget-actual-bindings"' in forms[1]
        assert page.count("<form") == 2

    def test_Repairs_WhenOnlyRebuildIsWired_OffersOnlyRebuild(self, serve):
        page = httpx.get(f"{serve(rebuild_derived=lambda: 'x')}/diagnostics", timeout=20).text

        assert 'action="/rebuild-derived"' in page
        assert "forget-actual-bindings" not in page

    def test_AdminAndEvidence_StillAnswerWithTheDiagnosticsPage(self, serve):
        base = serve(rebuild_derived=lambda: "x")

        diagnostics = httpx.get(f"{base}/diagnostics", timeout=20).text
        for alias in ("/admin", "/evidence"):
            assert httpx.get(f"{base}{alias}", timeout=20).text == diagnostics, alias

    def test_DiagnosticsPage_SaysItsOldNames_OnceOnThePage(self, serve):
        page = httpx.get(f"{serve()}/diagnostics", timeout=20).text

        assert page.count("Formerly called Evidence and Admin.") == 1

    def test_ConfirmationStillRequired_RebuildWithoutItIsRefused(self, serve):
        base = serve(rebuild_derived=lambda: "x")

        response = httpx.post(f"{base}/rebuild-derived", data={}, timeout=20)

        assert response.status_code == 400


class TestTheRenamedPages:
    @pytest.mark.parametrize("route", sorted(r for r, p in PAGE_NAMES.items() if p.formerly))
    def test_RenamedPage_HasItsNewNameAsTitleAndHeading(self, route):
        # Pages that are not served without a hook are covered by the names table alone.
        assert PAGE_NAMES[route].name != PAGE_NAMES[route].formerly

    def test_EveryNewName_IsNotAnOldNameOfAnotherPage(self):
        new = {p.name for p in PAGE_NAMES.values()}
        old = {p.formerly for p in PAGE_NAMES.values() if p.formerly}

        assert new & old == set()

    def test_AccountFieldStatistics_IsNamedForWhatItIsAndSaysItWasShape(self):
        assert PAGE_NAMES["/account"].name == "Field statistics"
        assert PAGE_NAMES["/account"].formerly == "Shape"


def test_Today_IsTheHomePageNamedByWhenItIsRead(serve):
    page = httpx.get(f"{serve()}/", timeout=20).text
    strip = re.search(r'<nav class="sitenav".*?</nav>', page, re.S)

    assert strip is not None and '<a href="/" aria-current="page">Today</a>' in strip.group(0)
