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

from obdi.account_names import accounts_shown
from obdi.ingest.connections import Connection, ConnectionStore
from obdi.ingest.store import Store
from obdi.navigation import PAGE_NAMES

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
            account_names=lambda: accounts_shown({"acct-a": "Current"}, []),
        )

        page = httpx.get(f"{base}/diagnostics", timeout=20).text

        assert page.index('href="/account?ref=acct-a"') < page.index('href="/account?ref=acct-b"')
        assert ">Current</a>" in page and "><code>acct-b</code></a>" in page
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
