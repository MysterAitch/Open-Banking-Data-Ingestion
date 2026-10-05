"""The navigation strip every page carries, and the destination every route belongs to.

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
from obdi.navigation import (
    ALIASES,
    DESTINATIONS,
    SECTION_OF_ROUTE,
    answering,
    navigation_html,
    way_out_html,
    with_way_out,
)
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from stylesheet_support import length_px, stylesheet

NAV = re.compile(r'<nav class="sitenav".*?</nav>', re.S)

#: Mapped ahead of the page that will serve it, so that the page has a home the day it lands.
NOT_YET_SERVED = {"/review-flags"}

LABELS = r'aria-current="page">([A-Za-z ]+)<'


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


def post_routes() -> list[str]:
    source = inspect.getsource(ConnectionHandler._dispatch_post)
    routes = set(re.findall(r'route == "(/[a-z-]+)"', source))
    routes |= set(re.findall(r'"(/extend(?:-max)?)"', source))
    assert "/upload" in routes and "/rebuild-derived" in routes and "/extend" in routes, routes
    assert len(routes) > 30, f"read too few routes out of the dispatcher: {routes}"
    return sorted(routes)


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

    def test_Destinations_AreInOrderOfUse_DailyReadingFirstAndFaultFindingLast(self):
        keys = [key for key, _, _ in DESTINATIONS]

        assert keys == [
            "today", "accounts", "position", "actual", "bring-in", "checks", "diagnostics",
        ]

    def test_Strip_HasSevenWordsInPlainEnglish(self):
        assert [label for _, label, _ in DESTINATIONS] == [
            "Today", "Accounts", "Position", "Actual", "Bring in", "Checks", "Diagnostics",
        ]

    def test_Destinations_EachIsAPageOfItsOwn(self):
        hrefs = {key: href for key, _, href in DESTINATIONS}

        assert hrefs == {
            "today": "/",
            "accounts": "/accounts",
            "position": "/position",
            "actual": "/actual",
            "bring-in": "/bring-in",
            "checks": "/checks",
            "diagnostics": "/diagnostics",
        }

    def test_RetiredStripItems_ConnectionsReportsEvidenceAdmin_AreNoLongerInTheStrip(self, base):
        strip = strip_of(httpx.get(f"{base}/", timeout=20).text)

        for gone in ("Connections", "Reports", "Evidence", "Admin", "Overview"):
            assert f">{gone}</a>" not in strip, gone

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
            ("/", "Today"),
            ("/accounts", "Accounts"),
            ("/ledger", "Accounts"),
            ("/spaces", "Accounts"),
            ("/coverage", "Accounts"),
            ("/review", "Accounts"),
            ("/position", "Position"),
            ("/actual", "Actual"),
            ("/actual-history", "Actual"),
            ("/bring-in", "Bring in"),
            ("/connections", "Bring in"),
            ("/import", "Bring in"),
            ("/statements", "Bring in"),
            ("/statement-shape", "Bring in"),
            ("/callback", "Bring in"),
            ("/checks", "Checks"),
            ("/reports", "Checks"),
            ("/agreements", "Checks"),
            ("/identity-health", "Checks"),
            ("/diagnostics", "Diagnostics"),
            ("/evidence", "Diagnostics"),
            ("/admin", "Diagnostics"),
            ("/attempts", "Diagnostics"),
            ("/fetch-timeline", "Diagnostics"),
            ("/account", "Diagnostics"),
        ],
    )
    def test_Page_InASection_MarksOnlyThatDestinationCurrent(self, base, route, label):
        strip = strip_of(httpx.get(f"{base}{route}", timeout=20).text)

        assert re.findall(LABELS, strip) == [label]

    def test_PageThatNothingServes_MarksNothingCurrent(self, base):
        assert "aria-current" not in strip_of(httpx.get(f"{base}/no-such-page", timeout=20).text)

    def test_Mark_DoesNotLeakFromOneRequestToTheNext(self, base):
        httpx.get(f"{base}/reports", timeout=20)

        assert "aria-current" not in strip_of(httpx.get(f"{base}/no-such-page", timeout=20).text)

    def test_EveryGetRoutePageThatIsReachedFromTheStrip_MarksItsOwnSection(self, base):
        for key, label, href in DESTINATIONS:
            strip = strip_of(httpx.get(f"{base}{href}", timeout=20).text)
            assert re.findall(LABELS, strip) == [label], key

    def test_EveryRouteNamedInTheSectionTable_IsARouteTheDispatcherKnows(self):
        served = set(get_routes()) | set(post_routes())
        assert set(SECTION_OF_ROUTE) - served - NOT_YET_SERVED - {"/"} == set()

    def test_EveryGetRouteTheDispatcherKnows_BelongsToADestination(self):
        homeless = [route for route in get_routes() if not SECTION_OF_ROUTE.get(route)]

        assert homeless == [], f"GET routes with no destination: {homeless}"

    def test_EveryPostRouteTheDispatcherKnows_BelongsToADestination(self):
        homeless = [route for route in post_routes() if not SECTION_OF_ROUTE.get(route)]

        assert homeless == [], f"POST routes with no destination: {homeless}"

    def test_SectionTable_OnlyNamesDestinationsTheStripHas(self):
        keys = {key for key, _, _ in DESTINATIONS}

        assert set(SECTION_OF_ROUTE.values()) <= keys

    def test_AnswerPageOfAPost_MarksTheSectionOfTheActionThatAnsweredIt(self, base):
        response = httpx.post(f"{base}/save-account", data={}, timeout=20)

        assert re.findall(LABELS, strip_of(response.text)) == ["Accounts"]

    def test_AnswerPageOfAPost_ForAnotherSection_MarksThatSectionInstead(self, base):
        response = httpx.post(f"{base}/rebuild-derived", data={}, timeout=20)

        assert re.findall(LABELS, strip_of(response.text)) == ["Diagnostics"]

    def test_EveryAliasedAddress_IsMappedToTheSectionItsLandingPageIsIn(self):
        landing = {href: key for key, _, href in DESTINATIONS}

        for alias, target in ALIASES.items():
            assert SECTION_OF_ROUTE[alias] == landing[target], alias

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
        # Seven text tabs in a grid of four columns: two rows on a phone.
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

        # A page carries the rules without their indentation (`stylesheet.SERVED_STYLESHEET`), so
        # the block's closing brace is found on a line of its own, however far it is indented.
        rule = re.search(r"@media \(min-width: 60rem\) \{(.*?)\n\s*\}", css, re.S)
        assert rule, "the widening must sit inside a media query, or a phone gets it too"
        assert "body.wide {" in rule.group(1)
        assert re.search(r"body\.wide main > p[^{]*\{[^}]*max-width: 40rem", rule.group(1))

    def test_Overview_IsWide(self, base):
        assert '<body class="wide">' in httpx.get(f"{base}/", timeout=20).text

    def test_APageOfProseAndForms_IsNotWide(self, base):
        assert "<body>" in httpx.get(f"{base}/import", timeout=20).text


class TestTheWayOutUnderTheHeading:
    """A page deep in a flow leaves by a link to its destination, without scrolling."""

    def test_PageInASection_OffersBackToThatDestinationRightUnderTheHeading(self, base):
        page = httpx.get(f"{base}/agreements", timeout=20).text

        heading = page.index("</h1>")
        way = page.index('<a class="tap" href="/checks">Back to Checks</a>')
        assert heading < way < page.index("</main>")
        assert page[heading : way].count("<p") <= 1

    @pytest.mark.parametrize(
        ("route", "words"),
        [
            ("/spaces", "Back to Accounts"),
            ("/coverage", "Back to Accounts"),
            ("/review", "Back to Accounts"),
            ("/connections", "Back to Bring in"),
            ("/import", "Back to Bring in"),
            ("/statements", "Back to Bring in"),
            ("/statement-shape", "Back to Bring in"),
            ("/actual-history", "Back to Actual"),
            ("/balance-walk", "Back to Checks"),
            ("/review-report", "Back to Checks"),
            ("/artefacts", "Back to Diagnostics"),
            ("/fetch-timeline", "Back to Diagnostics"),
            ("/account", "Back to Diagnostics"),
        ],
    )
    def test_PageDeepInAFlow_LeavesByItsDestinationWithoutScrolling(self, base, route, words):
        page = httpx.get(f"{base}{route}", timeout=20).text

        top = page.split("</h1>", 1)[1][:400]
        assert words in top, (route, top)

    @pytest.mark.parametrize(
        ("route", "words"),
        [
            ("/rebuild-derived", "Back to Diagnostics"),
            ("/save-account", "Back to Accounts"),
            ("/push-actual", "Back to Actual"),
            ("/rename-connection", "Back to Bring in"),
        ],
    )
    def test_AnswerPageOfAPost_LeavesByItsDestinationToo(self, base, route, words):
        page = httpx.post(f"{base}{route}", data={}, timeout=20).text

        assert words in page.split("</h1>", 1)[1][:400], route

    def test_AnswerPageThatLeadsWithTheLedgerLink_KeepsItFirst_AndTheWayOutFollowsIt(self):
        from obdi.navigation import current_route
        from obdi.web_answers import ledger_link

        marked = current_route.set("/ledger-anchor")
        replying = answering.set(True)
        try:
            page = render_page("Balance stated", ledger_link("a", "A") + "<p>done</p>").decode()
        finally:
            answering.reset(replying)
            current_route.reset(marked)

        assert (
            page.index("Open the ledger for A")
            < page.index("Back to Accounts")
            < page.index("<p>done</p>")
        )

    def test_PageInAnotherSection_OffersItsOwnDestination_NotChecks(self, base):
        page = httpx.get(f"{base}/attempts", timeout=20).text

        assert "Back to Diagnostics" in page and "Back to Checks" not in page

    @pytest.mark.parametrize("route", ["/", "/accounts", "/checks", "/diagnostics", "/bring-in"])
    def test_DestinationsOwnPage_OffersNoWayOutToItself(self, base, route):
        page = httpx.get(f"{base}{route}", timeout=20).text

        assert 'class="wayout"' not in page

    @pytest.mark.parametrize("alias", sorted(ALIASES))
    def test_AliasedAddress_OffersNoWayOutToItsOwnLandingPage(self, base, alias):
        page = httpx.get(f"{base}{alias}", timeout=20).text

        assert 'class="wayout"' not in page

    def test_AnswerThatLeadsWithTheLedgerLink_KeepsThatLinkFirst(self):
        lead = '<p><a class="button" href="/ledger?ref=a">Open the ledger for A</a></p>'

        token = answering.set(True)
        try:
            body = with_way_out(lead + "<p>done</p>", "/ledger-anchor")
        finally:
            answering.reset(token)

        assert body.startswith(lead)
        assert body.index("Back to Accounts") > len(lead) - 1
        assert body.index("Back to Accounts") < body.index("<p>done</p>")

    def test_AnswerThatLeadsWithWhatHappened_KeepsThatSentenceFirst(self):
        token = answering.set(True)
        try:
            body = with_way_out("<p>Nothing queued: not configured.</p><p>more</p>", "/push-actual")
        finally:
            answering.reset(token)

        assert body.startswith("<p>Nothing queued: not configured.</p>")
        assert body.index("Back to Actual") < body.index("<p>more</p>")

    def test_PageThatIsNotAnAnswer_PutsTheWayOutBeforeItsFirstParagraph(self):
        body = with_way_out("<p>Lede.</p>", "/attempts")

        assert body.startswith('<p class="wayout">')

    def test_AnswerWithNoParagraphToLeadWith_StillGetsTheWayOut(self):
        token = answering.set(True)
        try:
            body = with_way_out("<h2>x</h2>", "/push-actual")
        finally:
            answering.reset(token)

        assert body.startswith('<p class="wayout">')

    def test_PageOfNoSection_GetsNoWayOut(self):
        assert way_out_html("/no-such-page") == ""
        assert with_way_out("<p>x</p>", "/no-such-page") == "<p>x</p>"

    def test_Overview_GetsNoWayOut_BecauseTheStripIsTheOneLinkHome(self):
        assert way_out_html("/") == ""

    def test_RenamedPage_SaysItsOldNameOnceUnderTheHeading(self):
        assert way_out_html("/balance-walk").count("Formerly called Balance walk.") == 1

    def test_PageThatWasNotRenamed_SaysNothingOfAFormerName(self):
        assert "Formerly" not in way_out_html("/attempts")

    def test_HostileRoute_InjectsNothing(self):
        assert way_out_html('/"><script>') == ""
