"""The navigation strip every page carries, and the two index pages behind it.

Routes are read out of the dispatcher rather than listed here, the way
test_web_hardening.py reads the POST routes, so a page added later is held to
the strip the day it exists.
"""

from __future__ import annotations

import inspect
import re
import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.callback import render_page
from obdi.connections import ConnectionStore
from obdi.navigation import DESTINATIONS, SECTION_OF_ROUTE, navigation_html
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.web_indexes import EVIDENCE, REPORTS
from stylesheet_support import length_px, stylesheet

NAV = re.compile(r'<nav class="sitenav".*?</nav>', re.S)


@pytest.fixture(autouse=True)
def live_instance(monkeypatch):
    """An unlabelled instance prefixes every title and heading, which these
    assertions are not about."""
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    config = WebConfig(
        client_id="c",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path_factory.mktemp("nav") / "c.json"),
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def get_routes() -> list[str]:
    source = inspect.getsource(ConnectionHandler._dispatch_get)
    routes = sorted(set(re.findall(r'route == "(/[a-z-]+)"', source)))
    assert "/ledger" in routes and "/reports" in routes, routes
    assert len(routes) > 15, f"read too few routes out of the dispatcher: {routes}"
    return [route for route in routes if route != "/healthz"]


def strip_of(page: str) -> str:
    match = NAV.search(page)
    assert match, "no navigation strip"
    return match.group(0)


class TestEveryPageCarriesTheStrip:
    def test_EveryGetRouteTheDispatcherKnows_RendersTheStripWithEveryDestination(self, base):
        for route in get_routes():
            response = httpx.get(f"{base}{route}", timeout=20)
            strip = strip_of(response.text)
            for _, label, href in DESTINATIONS:
                assert f'href="{href}"' in strip and f">{label}</a>" in strip, (route, label)

    def test_UnknownRoute_RendersTheStripToo(self, base):
        response = httpx.get(f"{base}/no-such-page", timeout=20)

        assert response.status_code == 404
        strip_of(response.text)

    def test_Liveness_IsPlainTextWithNoStrip(self, base):
        assert "<nav" not in httpx.get(f"{base}/healthz", timeout=20).text

    def test_StripLinks_AllResolve(self, base):
        for _, _, href in DESTINATIONS:
            path = href.split("#")[0] or "/"
            assert httpx.get(f"{base}{path}", timeout=20).status_code == 200, href

    def test_AnchoredDestinations_PointAtIdsThatExistOnTheHomePage(self, base):
        page = httpx.get(f"{base}/", timeout=20).text

        anchors = [href.split("#")[1] for _, _, href in DESTINATIONS if "#" in href]
        assert anchors == [], "every destination is a page of its own"
        for anchor in anchors:
            assert f'id="{anchor}"' in page, anchor

    def test_AccountsDestination_IsTheAccountsPageWhileTheOverviewKeepsItsAccountCards(self, base):
        hrefs = {key: href for key, _, href in DESTINATIONS}

        assert hrefs["accounts"] == "/accounts"
        assert 'id="accounts"' in httpx.get(f"{base}/", timeout=20).text, (
            "other links to /#accounts must keep landing on the cards"
        )

    def test_Destinations_AreInOrderOfUse_DailyReadingFirstAndRepairsLast(self):
        keys = [key for key, _, _ in DESTINATIONS]

        assert keys[0] == "overview" and keys[-1] == "admin"
        assert keys.index("position") < keys.index("connections")
        assert keys.index("accounts") < keys.index("reports")

    def test_Destinations_ConnectionsActualAndAdmin_AreTheirOwnPages(self):
        hrefs = {key: href for key, _, href in DESTINATIONS}

        assert hrefs["connections"] == "/connections"
        assert hrefs["actual"] == "/actual"
        assert hrefs["admin"] == "/admin"

    def test_NoPage_LinksToAnAnchorThatNoLongerExists(self, base):
        for route in get_routes():
            page = httpx.get(f"{base}{route}", timeout=20).text
            for gone in ("/#connections", "/#actual", "/#admin"):
                assert gone not in page, (route, gone)

    def test_RenderPageCalledDirectly_CarriesTheStripBeforeTheHeading(self):
        page = render_page("Anything", "<p>x</p>").decode()

        assert page.index('<nav class="sitenav"') < page.index("<h1>Anything</h1>")

    def test_NoPage_StillSaysBackToConnections(self, base):
        for route in get_routes():
            assert "Back to connections" not in httpx.get(f"{base}{route}", timeout=20).text, route


class TestTheCurrentSectionIsMarked:
    @pytest.mark.parametrize(
        ("route", "label"),
        [
            ("/", "Overview"),
            ("/accounts", "Accounts"),
            ("/ledger", "Accounts"),
            ("/agreements", "Reports"),
            ("/reports", "Reports"),
            ("/identity-health", "Reports"),
            ("/evidence", "Evidence"),
            ("/attempts", "Evidence"),
            ("/spaces", "Evidence"),
            ("/connections", "Connections"),
            ("/actual", "Actual"),
            ("/admin", "Admin"),
            ("/coverage", "Accounts"),
            ("/import", "Accounts"),
            ("/review", "Accounts"),
        ],
    )
    def test_Page_InASection_MarksOnlyThatDestinationCurrent(self, base, route, label):
        strip = strip_of(httpx.get(f"{base}{route}", timeout=20).text)

        assert re.findall(r'aria-current="page">([A-Za-z]+)<', strip) == [label]

    def test_PageInNoSection_MarksNothingCurrent(self, base):
        # The OAuth callback answers a bare visit with a refusal page that
        # belongs to no section.
        assert "aria-current" not in strip_of(httpx.get(f"{base}/callback", timeout=20).text)

    def test_Mark_DoesNotLeakFromOneRequestToTheNext(self, base):
        httpx.get(f"{base}/reports", timeout=20)

        assert "aria-current" not in strip_of(httpx.get(f"{base}/callback", timeout=20).text)

    def test_EveryGetRoutePageThatIsReachedFromTheStrip_MarksItsOwnSection(self, base):
        for key, label, href in DESTINATIONS:
            if "#" in href:
                continue
            strip = strip_of(httpx.get(f"{base}{href}", timeout=20).text)
            assert re.findall(r'aria-current="page">([A-Za-z]+)<', strip) == [label], key

    def test_EveryRouteNamedInTheSectionTable_IsARouteTheDispatcherKnows(self):
        assert set(SECTION_OF_ROUTE) - set(get_routes()) - {"/"} == set()

    def test_NavigationHtml_ForAHostileRoute_MarksNothingAndInjectsNothing(self):
        strip = navigation_html('/"><script>')

        assert "aria-current" not in strip and "<script>" not in strip


class TestNavigationLinksAreThumbSizedButNotButtons:
    def test_Links_AreNotButtonsSoThePagesOwnActionStaysTheHeaviestThing(self):
        strip = navigation_html("/")

        assert 'class="button"' not in strip
        assert strip.startswith('<nav class="sitenav" aria-label="Sections"><ul>')
        assert strip.count("<li>") == len(DESTINATIONS)

    def test_Stylesheet_GivesEveryNavLinkAtLeastFortyFourPixelsOfHeight(self):
        css = stylesheet()

        rule = re.search(r"\.sitenav a \{[^}]*\}", css)
        assert rule
        height = re.search(r"min-height: ([^;]+);", rule.group(0))
        assert height and length_px(css, height.group(1)) >= 44
        # Eight text tabs in a grid of four columns: two rows on a phone.
        grid = re.search(r"\.sitenav ul \{[^}]*\}", css)
        assert grid and "display: grid" in grid.group(0)
        assert "repeat(4" in grid.group(0)

    def test_Stylesheet_GivesTapLinksAHitAreaOfAtLeastFortyFourPixelsWithoutGrowingTheLine(self):
        """A pseudo-element carries the hit area, so the link adds nothing to its line:
        a min-height on the link itself made the last line of a paragraph taller."""
        css = stylesheet()

        link = re.search(r"a\.tap \{[^}]*\}", css)
        assert link and "min-height" not in link.group(0)
        area = re.search(r"a\.tap::after \{[^}]*\}", css)
        assert area and "position: absolute" in area.group(0)
        inset = re.search(r"inset: -([\d.]+rem) -([\d.]+rem)", area.group(0))
        assert inset, area.group(0)
        # Above and below the link's own line, on top of a line of at least 20px.
        assert 20 + 2 * length_px(css, inset.group(1)) >= 44


class TestAPageOfCardsUsesAWideScreen:
    def test_WidePage_IsMarkedOnItsBody_AndAnOrdinaryPageIsNot(self):
        assert '<body class="wide">' in render_page("t", "", wide=True).decode()
        assert "<body>" in render_page("t", "").decode()

    def test_Stylesheet_WidensOnlyOnAWideScreen_AndKeepsProseToAReadableMeasure(self):
        css = render_page("t", "").decode()

        rule = re.search(r"@media \(min-width: 60rem\) \{(.*?)\n \}", css, re.S)
        assert rule, "the widening must sit inside a media query, or a phone gets it too"
        assert "body.wide {" in rule.group(1)
        assert re.search(r"body\.wide main > p[^{]*\{[^}]*max-width: 40rem", rule.group(1))

    def test_Overview_IsWide(self, base):
        assert '<body class="wide">' in httpx.get(f"{base}/", timeout=20).text

    def test_APageOfProseAndForms_IsNotWide(self, base):
        assert "<body>" in httpx.get(f"{base}/import", timeout=20).text


class TestTheIndexPages:
    def test_EveryDestinationOnThem_IsARouteTheDispatcherKnows(self):
        routes = set(get_routes())

        for route, _, _ in (*REPORTS, *EVIDENCE):
            assert route in routes, route

    def test_Reports_ListsTheSevenReportsEachWithOneSentence(self, base):
        page = httpx.get(f"{base}/reports", timeout=20).text

        assert [route for route, _, _ in REPORTS] == [
            "/agreements", "/identity-health", "/balance-reconciliation",
            "/period-reconciliation", "/balance-walk", "/date-lag", "/review-report",
        ]
        for route, title, question in REPORTS:
            assert f'href="{route}"' in page and title in page
            assert question.count("?") == 1, route
        assert 'href="/artefacts"' not in page

    def test_Evidence_ListsTheSixSourcesEachWithOneSentence(self, base):
        page = httpx.get(f"{base}/evidence", timeout=20).text

        assert [route for route, _, _ in EVIDENCE] == [
            "/artefacts", "/attempts", "/fetch-timeline", "/statement-shape",
            "/statements", "/spaces",
        ]
        for route, title, question in EVIDENCE:
            assert f'href="{route}"' in page and title in page
            assert question.count("?") == 1, route
        assert 'href="/agreements"' not in page

    def test_TheLinksRenderedOnThem_AreExactlyTheDeclaredDestinations(self, base):
        for index, entries in (("/reports", REPORTS), ("/evidence", EVIDENCE)):
            page = httpx.get(f"{base}{index}", timeout=20).text
            links = re.findall(r'<li class="row"><a class="tap" href="([^"]+)"', page)
            assert links == [route for route, _, _ in entries]
