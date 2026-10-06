"""Values shown for a sitting: one deliberate POST, then every page that can show them does.

The scenarios are the owner's: he presses "Show values on every page" once, reads Today, the
ledger, and the reports without pressing again, sees on every page that values are shown and how
to stop, and is back to masked pages when he hides them, when twelve hours have passed, or when the
cookie is not one the server issued. A request that carries no valid cookie is the masked request
it always was, which `test_get_routes_hold_no_stored_values.py` holds across every route.

KNOWN ANSWERS: the store holds one invented payee and one invented amount; with a valid cookie the
ledger carries both and `no-store`; without one it carries neither. The cookie's issue time is
chosen by the test, so "valid", "just inside twelve hours", and "just outside" are decided before
the first request.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from obdi import values_sitting
from obdi.balance_anchors import record_stated_anchor
from obdi.cli import build_web_config
from obdi.ingest import import_file
from obdi.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "tok-current"
PAYEE = "Zephyrine Quokka Ltd"
AMOUNT = "7391.28"
LEDGER = f"/ledger?ref={ACCOUNT}&month=2026-09"
BANNER = "Values are shown on every page for this sitting"
PAGE_NOTICE = "VALUES ARE SHOWN on this page"
EVERY_PAGE = "Show values on every page"
EVIDENCE_OF_VALUES = (PAYEE.casefold(), AMOUNT, "7,391.28")

#: Every page whose handler takes a masked or unmasked rendering, by address. The first two are
#: the pages the owner reads daily; the rest are the reports.
UNMASKED_PAGES = (
    LEDGER,
    "/position",
    f"/balance-chart?ref={ACCOUNT}",
    "/review",
    "/review-report",
    "/agreements",
    "/balance-walk",
    "/balance-reconciliation",
    "/period-reconciliation",
    "/review-flags",
)


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("sitting")
    csv = root / "current.csv"
    csv.write_text(
        "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
        f"02/09/2026,{PAYEE},QQ-REF-84213,CARD,-{AMOUNT},0\n",
        encoding="utf-8",
    )
    db = root / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
        # The balance chart compares the rows with stated balances, so it draws nothing without
        # them and carries no values control.
        record_stated_anchor(store, ACCOUNT, "2026-09-01", "1000.00")
        record_stated_anchor(store, ACCOUNT, "2026-09-05", "1234.56")
    mp = pytest.MonkeyPatch()
    environment(mp, root)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()
    mp.undo()


def cookie_issued(ago: timedelta) -> dict[str, str]:
    return {"Cookie": f"{values_sitting.COOKIE}={values_sitting.issue(datetime.now(UTC) - ago)}"}


FRESH = timedelta(minutes=5)


def get(base: str, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.get(f"{base}{path}", headers=headers or {}, timeout=60, follow_redirects=False)


def shows_values(page: str) -> bool:
    lowered = page.casefold()
    return any(form in lowered for form in EVIDENCE_OF_VALUES)


class TestAPageIsMaskedUnlessTheSittingShowsValues:
    def test_Ledger_WithAValidCookie_ShowsThePayeeAndAmountAndIsNotKept(self, served):
        response = get(served, LEDGER, cookie_issued(FRESH))

        assert response.status_code == 200
        assert PAYEE.casefold() in response.text.casefold()
        assert AMOUNT in response.text or "7,391.28" in response.text
        assert response.headers["Cache-Control"] == "no-store"

    def test_Ledger_WithNoCookie_ShowsNeitherAndIsMasked(self, served):
        response = get(served, LEDGER)

        assert response.status_code == 200
        assert not shows_values(response.text)
        assert "Cache-Control" not in response.headers

    def test_Ledger_WithACookieIssuedJustOverTwelveHoursAgo_IsMaskedAgain(self, served):
        response = get(
            served, LEDGER, cookie_issued(timedelta(hours=values_sitting.SITTING_HOURS, seconds=30))
        )

        assert not shows_values(response.text)
        assert BANNER not in response.text

    def test_Ledger_WithACookieIssuedJustUnderTwelveHoursAgo_StillShowsValues(self, served):
        response = get(
            served,
            LEDGER,
            cookie_issued(timedelta(hours=values_sitting.SITTING_HOURS, seconds=-60)),
        )

        assert PAYEE.casefold() in response.text.casefold()

    @pytest.mark.parametrize(
        "value",
        [
            "shown",
            "shown.1",
            "shown.abc.def",
            "shown." + "9" * 40 + ".00",
            "hidden.1700000000.0000",
            "",
        ],
    )
    def test_Ledger_WithAMalformedCookie_IsMaskedAndStillServed(self, served, value):
        response = get(served, LEDGER, {"Cookie": f"{values_sitting.COOKIE}={value}"})

        assert response.status_code == 200
        assert not shows_values(response.text)

    def test_Ledger_WithACookieWhoseTimeHasBeenEdited_IsMasked(self, served):
        """The signature covers the issue time, so a cookie edited to look recent is refused."""
        old = datetime.now(UTC) - timedelta(days=3)
        value = values_sitting.issue(old)
        issued = int(old.timestamp())
        edited = value.replace(f".{issued}.", f".{issued + 3 * 86400 - 60}.")

        response = get(served, LEDGER, {"Cookie": f"{values_sitting.COOKIE}={edited}"})

        assert edited != value
        assert not shows_values(response.text)

    def test_Ledger_WithACookieDatedInTheFuture_IsMasked(self, served):
        response = get(served, LEDGER, cookie_issued(-timedelta(hours=2)))

        assert not shows_values(response.text)

    @pytest.mark.parametrize("path", UNMASKED_PAGES)
    def test_PageWithAnUnmaskedRendering_WithAValidCookie_IsNotKeptAndCarriesTheBannerOnly(
        self, served, path
    ):
        response = get(served, path, cookie_issued(FRESH))

        assert response.status_code == 200, path
        assert response.headers["Cache-Control"] == "no-store", path
        assert BANNER in response.text, path
        assert PAGE_NOTICE not in response.text, path
        assert EVERY_PAGE not in response.text, path

    @pytest.mark.parametrize("path", UNMASKED_PAGES)
    def test_PageWithAnUnmaskedRendering_WithNoCookie_IsMaskedAndOffersTheSittingPress(
        self, served, path
    ):
        response = get(served, path)

        assert response.status_code == 200, path
        assert "Cache-Control" not in response.headers, path
        assert BANNER not in response.text, path
        assert not shows_values(response.text), path

    @pytest.mark.parametrize("path", UNMASKED_PAGES)
    def test_PageWithAShowValuesPress_WithNoCookie_OffersTheSittingPressBesideIt(
        self, served, path
    ):
        page = parse(get(served, path).text)

        presses = [
            form
            for form in elements(page, "form")
            if form.attrs.get("action") == values_sitting.SHOW
        ]
        assert len(presses) >= 1, path
        carried = [
            node.attrs.get("value")
            for press in presses
            for node in elements(press, "input")
            if node.attrs.get("name") == values_sitting.RETURN_FIELD
        ]
        assert carried and all(value == path for value in carried), path


class TestEveryPageSaysWhetherValuesAreShown:
    @pytest.mark.parametrize("path", ["/", LEDGER, "/more", "/connections", "/bring-in"])
    def test_Page_WhileValuesAreShown_CarriesTheBannerWithItsUntilTimeAndHideControl(
        self, served, path
    ):
        response = get(served, path, cookie_issued(FRESH))

        text = parse(response.text)
        banner = [n for n in elements(text, "div") if "sitting" in n.classes]
        assert len(banner) == 1, path
        assert re.search(r"\(until \d\d:\d\d UTC\)", banner[0].text()), path
        hide = [
            form
            for form in elements(banner[0], "form")
            if form.attrs.get("action") == values_sitting.HIDE
            and form.attrs.get("method") == "post"
        ]
        assert len(hide) == 1 and "Hide values" in hide[0].text(), path

    def test_Banner_NamesTheEndOfTheSittingFromTheTimeTheCookieWasIssued(self, served):
        issued = datetime.now(UTC) - timedelta(hours=2, minutes=3)
        value = values_sitting.issue(issued)
        expected = datetime.fromtimestamp(int(issued.timestamp()), UTC) + timedelta(hours=12)

        response = get(served, "/", {"Cookie": f"{values_sitting.COOKIE}={value}"})

        assert f"(until {expected:%H:%M} UTC)" in response.text

    @pytest.mark.parametrize("path", ["/", LEDGER, "/more", "/position", "/agreements"])
    def test_Page_WhileValuesAreHidden_CarriesNoBanner(self, served, path):
        assert BANNER not in get(served, path).text, path

    def test_Page_ForAnExpiredCookie_CarriesNoBanner(self, served):
        expired = cookie_issued(timedelta(hours=13))

        assert BANNER not in get(served, "/", expired).text

    def test_ErrorPage_WhileValuesAreShown_CarriesTheBannerToo(self, served):
        response = get(served, "/no-such-page", cookie_issued(FRESH))

        assert response.status_code == 404
        assert BANNER in response.text

    def test_AnswerToAPost_WhileValuesAreShown_CarriesTheBannerAndNeverReturnsToAnAddress(
        self, served
    ):
        response = httpx.post(
            f"{served}/review-report", data={}, headers=cookie_issued(FRESH), timeout=60
        )

        assert BANNER in response.text
        assert f'name="{values_sitting.RETURN_FIELD}" value=""' in response.text


class TestMoreSaysWhetherValuesAreShown:
    def test_More_WhileHidden_SaysSoAndOffersToShowValuesOnEveryPage(self, served):
        page = parse(get(served, "/more").text)

        assert "Values are hidden" in page.text()
        presses = [
            f for f in elements(page, "form") if f.attrs.get("action") == values_sitting.SHOW
        ]
        assert len(presses) == 1 and EVERY_PAGE in presses[0].text()

    def test_More_WhileShown_SaysSoAndOffersToHide(self, served):
        page = parse(get(served, "/more", cookie_issued(FRESH)).text)

        assert "Values are shown this sitting" in page.text()
        hides = [f for f in elements(page, "form") if f.attrs.get("action") == values_sitting.HIDE]
        assert len(hides) == 2, "the banner's control and More's own"
        assert not [
            f for f in elements(page, "form") if f.attrs.get("action") == values_sitting.SHOW
        ]


class TestBeginningAndEndingASitting:
    def post(
        self,
        base: str,
        route: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return httpx.post(
            f"{base}{route}",
            data=data or {},
            headers=headers or {},
            follow_redirects=False,
            timeout=60,
        )

    def test_PressingShowOnEveryPage_SetsAHardenedSessionCookieAndReturnsToThePage(self, served):
        response = self.post(served, "/values-shown", data={"return_to": LEDGER})

        assert response.status_code == 303
        assert response.headers["Location"] == LEDGER
        assert response.headers["Cache-Control"] == "no-store"
        cookie = response.headers["Set-Cookie"]
        assert cookie.startswith(f"{values_sitting.COOKIE}=shown.")
        for attribute in ("HttpOnly", "SameSite=Strict", "Path=/"):
            assert attribute in cookie
        assert "Max-Age" not in cookie and "Expires" not in cookie, "a session cookie"
        assert "Secure" not in cookie, "plain http to this machine's own loopback"

    def test_ThePressedCookie_WhenSentBack_ShowsValuesOnThePageItReturnedTo(self, served):
        pressed = self.post(served, "/values-shown", data={"return_to": LEDGER})
        value = pressed.headers["Set-Cookie"].split(";")[0]

        page = get(served, pressed.headers["Location"], {"Cookie": value})

        assert PAYEE.casefold() in page.text.casefold()
        assert BANNER in page.text

    def test_PressingHide_ClearsTheCookieAndReturnsToThePage(self, served):
        response = self.post(
            served,
            "/values-hidden",
            data={"return_to": "/more"},
            headers=cookie_issued(FRESH),
        )

        assert response.status_code == 303
        assert response.headers["Location"] == "/more"
        cookie = response.headers["Set-Cookie"]
        assert cookie.startswith(f"{values_sitting.COOKIE}=;") and "Max-Age=0" in cookie

    def test_ACookieSetOverHttpsBehindTheProxy_IsMarkedSecure(self, served):
        response = self.post(
            served,
            "/values-shown",
            data={},
            headers={"X-Forwarded-Proto": "https"},
        )

        assert "; Secure" in response.headers["Set-Cookie"]

    def test_ACookieSetOverHttpBehindTheProxy_IsNotMarkedSecure(self, served):
        response = self.post(
            served,
            "/values-shown",
            data={},
            headers={"X-Forwarded-Proto": "http", "Host": "obdi.example.net"},
        )

        assert "Secure" not in response.headers["Set-Cookie"]

    def test_ACookieSetWhereTheSchemeIsNotSaid_OnAnotherHost_IsMarkedSecure(self, served):
        response = self.post(
            served, "/values-shown", data={}, headers={"Host": "obdi.example.net"}
        )

        assert "; Secure" in response.headers["Set-Cookie"]

    @pytest.mark.parametrize(
        "target",
        [
            "https://evil.example/steal",
            "//evil.example/steal",
            "/\\evil.example",
            "javascript:alert(1)",
            "evil.example",
            "/ledger\r\nSet-Cookie: x=y",
            "/with space",
            "/values-shown",
            "",
        ],
    )
    def test_ReturnField_WhenItNamesAnythingButAPathOnThisSite_ReturnsToTheHomePage(
        self, served, target
    ):
        response = self.post(served, "/values-shown", data={"return_to": target})

        assert response.status_code == 303
        assert response.headers["Location"] == "/"

    @pytest.mark.parametrize("route", ["/values-shown", "/values-hidden"])
    def test_APostFromAnotherSite_IsRefusedAndSetsNoCookie(self, served, route):
        response = self.post(
            served, route, data={"return_to": "/"}, headers={"Origin": "https://evil.example"}
        )

        assert response.status_code == 403
        assert "Set-Cookie" not in response.headers

    @pytest.mark.parametrize("route", ["/values-shown", "/values-hidden"])
    def test_ARouteThatChangesTheSitting_IsNotReachableByGet(self, served, route):
        response = get(served, route)

        assert response.status_code == 404
        assert "Set-Cookie" not in response.headers


class TestReturnAddressesInTheControls:
    def test_Controls_CarryThePageTheyAreOnIncludingItsQuery_EscapedForTheAttribute(self, served):
        page = get(served, f"/ledger?ref={ACCOUNT}&month=2026-09").text

        assert f'name="return_to" value="/ledger?ref={ACCOUNT}&amp;month=2026-09"' in page

    def test_Controls_DropAFieldNoPageReads_SoAStrayFieldIsNeverEchoed(self, served):
        """The address a control returns to is rebuilt from the fields the site's pages read.
        Carried verbatim, a query field nobody reads came straight back in the page, which is
        what every page's own "never echoes an unknown field" test exists to refuse."""
        page = get(served, f"/ledger?ref={ACCOUNT}&month=2026-09&note=zz9").text

        assert "zz9" not in page
        assert f'name="return_to" value="/ledger?ref={ACCOUNT}&amp;month=2026-09"' in page

    def test_Controls_NeverCarryAnAddressThatIsNotAPathOnThisSite(self, served):
        """The request line is whatever the client sent; only a local path reaches a form."""
        response = httpx.get(
            f"{served}//evil.example/x?y=1", timeout=60, follow_redirects=False
        )

        assert "evil.example" not in "".join(
            node.attrs.get("value", "") for node in elements(parse(response.text), "input")
        )


class TestTheCookieNeverOutlivesItsRules:
    def test_Cookie_IssuedByAnotherRun_IsNotHonoured(self, served, monkeypatch):
        """A restart ends every sitting: the signing key lives only in the running process."""
        value = values_sitting.issue(datetime.now(UTC))
        monkeypatch.setattr(values_sitting, "_KEY", b"another run's key".ljust(32, b"."))

        header = f"{values_sitting.COOKIE}={value}"

        assert values_sitting.sitting_end(header, datetime.now(UTC)) is None

    def test_SittingEnd_AtTheExactTwelfthHour_IsOver(self):
        began = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
        header = f"{values_sitting.COOKIE}={values_sitting.issue(began)}"

        twelfth = began + timedelta(hours=12)

        assert values_sitting.sitting_end(header, twelfth - timedelta(seconds=1)) is not None
        assert values_sitting.sitting_end(header, twelfth) is None

    def test_SittingEnd_AmongOtherCookies_IsStillFound(self):
        began = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
        header = f"theme=dark; {values_sitting.COOKIE}={values_sitting.issue(began)}; a=b"

        assert values_sitting.sitting_end(header, began + timedelta(hours=1)) is not None

    def test_SittingEnd_ForAnUnparseableHeader_IsNone(self):
        assert values_sitting.sitting_end('obdi-values="unterminated', datetime.now(UTC)) is None


def test_TheFixtureStoreHoldsTheInventedPayeeSoAnAbsenceIsMeaningful(served):
    """A control: the unmasked POST shows the planted values, so their absence elsewhere is real."""
    response = httpx.post(f"{served}/ledger", data={"ref": ACCOUNT, "month": "2026-09"}, timeout=60)

    assert PAYEE.casefold() in response.text.casefold()
