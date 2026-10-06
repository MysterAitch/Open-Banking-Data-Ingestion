"""The ledger page's opening balance and anchors, over real HTTP and a real store.

The page is built by the application's own configuration (`build_web_config`),
so the hooks under test are the ones a person's browser reaches.

The account is `everyday` from test_balance_anchors (rows sum to +3,950 by the
end of 03-20; +6,750 by the end of 03-10). The figures a person states here are
chosen so that nothing derived from them can appear on a page by coincidence,
and each derived figure was worked out before the first run:

    stated 4,517.89 (451,789) at the end of 03-10
        opening        451,789 - 6,750          = 445,039   (4,450.39)
    stated 4,000.00 (400,000) at the end of 03-20
        rows predict   445,039 + 3,950          = 448,989
        difference     400,000 - 448,989        = -48,989   (489.89)
    running position through 03-31, opening included
                       445,039 + 3,950          = 448,989   (4,489.89)

The page's rule is that every GET is masked whatever its query string, and
values appear only in the direct response to a POST of the "Show values" form.
A balance a person types must never come back on any page that an address can
reach, so a save answers with the MASKED ledger.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date
from http.server import HTTPServer
from types import MappingProxyType

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.balance_anchors import STATED, Anchor, record_stated_anchor, stated_anchors
from obdi.cli import build_web_config
from obdi.masking import MASKED_TOTAL
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_account_pages import assert_tap_targets_are_thumb_sized
from test_balance_anchors import ACCOUNT, everyday
from test_ledger import land, txn

STATED_FIRST = "4517.89"
STATED_SECOND = "4000.00"

#: An account whose reference is markup, to prove it is escaped wherever printed.
MARKUP_REF = 'mark"><b>up'

#: Every figure a masked page must not show, in minor units and pounds and pence.
SECRET_FIGURES = (
    "451789", "4,517.89", "445039", "4,450.39", "448989", "4,489.89",
    "400000", "4,000.00", "48989", "489.89",
    # The rows' own figures, which the masked page has always withheld.
    "1250", "12.50", "10000", "10,000.00", "3300", "33.00", "2000", "20.00",
)


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def get(self, ref: str = ACCOUNT, **params: str) -> httpx.Response:
        return httpx.get(
            f"{self.base}/ledger", params={"ref": ref, "month": "2026-03", **params}, timeout=20
        )

    def post(self, path: str, data: dict[str, str], **kwargs) -> httpx.Response:
        return httpx.post(
            f"{self.base}{path}", data=data, follow_redirects=False, timeout=20, **kwargs
        )

    def show_values(self, ref: str = ACCOUNT) -> httpx.Response:
        return self.post("/ledger", {"ref": ref, "month": "2026-03"})

    def state(self, day: str, amount: str, *, ref: str = ACCOUNT) -> httpx.Response:
        return self.post(
            "/ledger-anchor",
            {"ref": ref, "month": "2026-03", "day": day, "amount": amount, "currency": "GBP"},
        )

    def remove(
        self, day: str, *, ref: str = ACCOUNT, confirmed: bool = True, **kwargs
    ) -> httpx.Response:
        """The second press, which carries the confirmation the first press was answered with."""
        form = {"ref": ref, "month": "2026-03", "day": day}
        if confirmed:
            form["confirmed"] = "yes"
        return self.post("/ledger-anchor-remove", form, **kwargs)

    def stated(self, ref: str = ACCOUNT) -> list[Anchor]:
        with Store(self.db) as store:
            return stated_anchors(store, ref)

    def seed(self, day: str, amount: str, ref: str = ACCOUNT) -> None:
        with Store(self.db) as store:
            record_stated_anchor(store, ref, day, amount)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    db = tmp_path / "opening.sqlite3"
    with Store(db) as store:
        everyday(store)
        land(store, "digest-markup", txn(MARKUP_REF, "src-a", "m1", date(2026, 3, 3), -100, "ODD"))
    account_map = tmp_path / "accounts.json"
    account_map.write_text(
        json.dumps({"actual": [{"canonical_id": ACCOUNT, "actual_account_id": "act-everyday"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}", db)
    finally:
        httpd.shutdown()


def assert_no_secret(page: str, *, where: str = "") -> None:
    for figure in SECRET_FIGURES:
        assert figure not in page, f"{figure} {where}"


class TestAnAccountWithNoAnchor:
    def test_Page_SaysThereIsNoOpeningBalanceAndThatTheFiguresStartFromZero(self, lab):
        page = lab.get().text

        assert "Known balances (none stated)" in page
        assert "No opening balance: the figures on this page start from zero." in page
        assert "Opening balance, at the end of" not in page

    def test_Position_SaysNoOpeningBalanceIsIncluded(self, lab):
        page = lab.show_values().text

        assert "Neither figure includes an opening balance" in page
        assert "plus the opening balance" not in page
        assert "£39.50" in page, "the rows alone: +3,950 start from zero"

    def test_Page_OffersTheFormToStateABalanceAndNoRemovalOfAnythingYet(self, lab):
        page = lab.get().text

        assert 'action="/ledger-anchor"' in page
        assert 'action="/ledger-anchor-remove"' not in page


class TestOneStatedAnchor:
    def test_Masked_ShowsDateBasisAndTheSingleAnchorWarningButNoFigure(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        page = lab.get().text

        assert "end of 2026-03-10" in page
        assert "stated by you" in page
        assert "the opening balance is worked out from this" in page
        assert "absorbs every missing or surplus transaction before that day" in page
        assert "A second known balance turns it into a test." in page
        assert_no_secret(page)

    def test_Masked_ShowsTheDateTheOpeningAppliesButNotItsAmount(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        page = lab.get().text

        assert "Opening balance, at the end of 2026-03-01" in page
        assert MASKED_TOTAL in page
        assert "£9,999.99" not in page, "a balance must not keep its number of digits"

    def test_Values_ShowTheAnchorTheDerivedOpeningAndThePositionThatIncludesIt(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        page = lab.show_values().text

        assert "£4,517.89" in page
        assert "£4,450.39" in page
        assert "£4,489.89" in page
        assert "plus the opening balance" in page
        assert "Both figures start from the account's opening balance, worked out and shown" in page


class TestTwoAnchors:
    def test_Agreeing_SaysAgreesAndRaisesNoWarning(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        lab.seed("2026-03-20", "4489.89")

        page = lab.get().text

        assert '<span class="pill pill-ok">adds up</span>' in page
        assert "later known balance differs" not in page
        assert "later known balances differ" not in page
        assert "A second known balance turns it into a test." not in page
        assert_no_secret(page)

    def test_Differing_SaysDiffersMaskedAndTheSizeOnlyWhenValuesAreShown(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        lab.seed("2026-03-20", STATED_SECOND)

        masked = lab.get().text
        shown = lab.show_values().text

        assert '<span class="pill pill-bad">differs</span>' in masked
        assert "1 later known balance differs" in masked
        assert_no_secret(masked)
        assert "£489.89" in shown
        assert "£4,000.00" in shown
        assert re.search(r"overdrawn or owed <span[^>]*>£489\.89</span>", shown), (
            "the anchor is BELOW the prediction"
        )
        assert not re.search(r"(overdrawn or owed|in credit) <span", masked), (
            "masked, a balance carries no sign"
        )


#: With the opening at the end of 03-10, no row falls after 03-20, so a balance
#: stated for any later day in March agrees when it is 4,489.89.
AGREEING_LATER_DAYS = tuple(f"03-{day}" for day in range(20, 32))

#: Of the agreeing later balances, this many are listed and the rest are one line linking to all
#: of them (`web_ledger._SHOWN_AGREEING_ANCHORS`, which `test_account_page_known_balances` holds).
LISTED = 10

IN_AGREEMENT = '<span class="pill pill-ok">adds up</span>'
DIFFERS = '<span class="pill pill-bad">differs</span>'


class TestALongRunOfAgreeingAnchorsIsCutToTheNewest:
    def test_TwelveAgreeingLaterAnchors_ListTheNewestTenAndCountTheTwoEarlier(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        for day in AGREEING_LATER_DAYS:
            lab.seed(f"2026-{day}", "4489.89")

        page = lab.get().text

        assert page.count(IN_AGREEMENT) == LISTED
        assert "and 2 earlier known balances, all add up" in page
        assert "balances=all#opening" in page
        assert "End of <span class=\"mono nowrap\">2026-03-31</span>" in page
        assert "End of <span class=\"mono nowrap\">2026-03-21</span>" not in page
        assert "the opening balance is worked out from this" in page
        assert_no_secret(page)

    def test_TenAgreeingLaterAnchors_AreAllListedWithNoEarlierLine(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        for day in AGREEING_LATER_DAYS[:LISTED]:
            lab.seed(f"2026-{day}", "4489.89")

        page = lab.get().text

        assert page.count(IN_AGREEMENT) == LISTED
        assert "earlier known balance" not in page

    def test_ElevenAgreeingLaterAnchors_LeaveOneEarlierBalanceInTheSingular(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        for day in AGREEING_LATER_DAYS[: LISTED + 1]:
            lab.seed(f"2026-{day}", "4489.89")

        page = lab.get().text

        assert page.count(IN_AGREEMENT) == LISTED
        assert "and 1 earlier known balance, which adds up" in page

    def test_ADifferingAnchorAmongManyAgreeing_IsListedAlongsideTheNewestAgreeingOnes(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        for day in AGREEING_LATER_DAYS:
            lab.seed(f"2026-{day}", "4489.89")
        lab.seed("2026-04-02", STATED_SECOND)

        page = lab.get().text
        shown = lab.show_values().text

        assert page.count(DIFFERS) == 1
        assert page.count(IN_AGREEMENT) == LISTED
        assert "and 2 earlier known balances, all add up" in page
        assert "1 later known balance differs" in page
        assert re.search(r"overdrawn or owed <span[^>]*>£489\.89</span>", shown)
        assert_no_secret(page)

    def test_FullList_ListsEveryAgreeingAnchorAndOffersTheWayBack(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        for day in AGREEING_LATER_DAYS:
            lab.seed(f"2026-{day}", "4489.89")

        page = lab.get(balances="all").text

        assert page.count(IN_AGREEMENT) == len(AGREEING_LATER_DAYS)
        assert "earlier known balance" not in page
        assert "Every known balance is listed. Show only the newest" in page

    def test_OnlyDifferingAnchors_NeedNoEarlierLine(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        for day in AGREEING_LATER_DAYS[:5]:
            lab.seed(f"2026-{day}", STATED_SECOND)

        page = lab.get().text

        assert "earlier known balance" not in page
        assert page.count(DIFFERS) == 5


class TestANilBalanceIsJustNil:
    def test_OpeningThatDerivesToNothing_PrintsNilWithNoFigure(self, lab):
        """Rows through 03-10 sum to +6,750, so stating exactly that leaves an
        opening of nothing."""
        lab.seed("2026-03-10", "67.50")

        shown = lab.show_values().text
        assert "at the end of 2026-03-01:</strong> nil." in shown
        assert "nil £" not in shown
        # Masked, a nil is a sealed slot like any other balance: the owner saw "nil" printed
        # where every other figure was sealed, which told him the figure exactly.
        masked = lab.get().text
        assert "at the end of 2026-03-01:</strong> nil" not in masked
        assert re.search(r'at the end of 2026-03-01:</strong> <span class="[^"]*sealed"', masked)


class TestOnlyShowValuesIsAPrimaryButton:
    def test_Masked_AnAccountWithNoAnchor_HasConfirmThisBalanceAsItsOnlyPrimaryButton(self, lab):
        page = lab.get().text
        primary = re.findall(r'<(?:a|button)[^>]*class="button"[^>]*>([^<]*)<', page)

        # The thing to do is the page's one filled control, so showing values is outlined, and
        # archiving, consequential and done once in an account's life, never competes either.
        assert primary == ["Confirm this balance"]
        assert re.search(r'class="button secondary" type="submit"[^>]*>Show values<', page)
        assert "Archive this account</button>" in page

    def test_Masked_MonthLinksAreOrdinaryLinksAtTheTop(self, lab):
        page = lab.get(month="2026-04").text

        assert 'class="tap" href="/ledger?ref=everyday&amp;month=2026-03"' in page
        assert page.index("Previous month") < page.index("<summary>Known balances (")


class TestMaskingHoldsWhateverTheQueryString:
    @pytest.mark.parametrize(
        "query", ["values=1", "unmask=1", "show=1", "unmasked=true", "masked=0"]
    )
    def test_Get_WithAnyParameterOneMightTry_IsStillMasked(self, lab, query):
        lab.seed("2026-03-10", STATED_FIRST)
        lab.seed("2026-03-20", STATED_SECOND)
        key, value = query.split("=")

        page = lab.get(**{key: value}).text

        assert_no_secret(page, where=query)
        assert "VALUES ARE SHOWN" not in page

    def test_Get_AcrossEveryMonth_NeverShowsAStatedFigure(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        for month in ("2026-02", "2026-03", "2026-04", ""):
            page = httpx.get(
                f"{lab.base}/ledger", params={"ref": ACCOUNT, "month": month}, timeout=20
            ).text
            assert_no_secret(page, where=month)

    def test_ShowValues_IsNotKeptByTheBrowser(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        response = lab.show_values()

        assert "no-store" in response.headers["cache-control"]


class TestStatingABalanceThroughThePage:
    def test_Save_StoresTheAnchorAndAnswersWithTheMaskedLedgerAndAConfirmation(self, lab):
        response = lab.state("2026-03-10", STATED_FIRST)

        assert response.status_code == 200
        assert lab.stated() == [Anchor(date(2026, 3, 10), 451789, STATED)]
        page = response.text
        assert "Saved: a known balance for the end of 2026-03-10." in page
        assert "VALUES ARE SHOWN" not in page
        assert "Opening balance, at the end of 2026-03-01" in page
        assert_no_secret(page, where="the save response")
        assert "no-store" in response.headers["cache-control"]

    def test_Save_NeverPutsTheTypedAmountBackIntoAnyFormField(self, lab):
        page = lab.state("2026-03-10", STATED_FIRST).text

        assert 'name="amount" inputmode="decimal" autocomplete="off" required>' in page
        assert "4517.89" not in page

    def test_Save_ThenGet_StillShowsNoFigure(self, lab):
        lab.state("2026-03-10", STATED_FIRST)

        assert_no_secret(lab.get().text, where="a later GET")

    def test_Save_ForADateAlreadyStated_ReplacesIt(self, lab):
        lab.state("2026-03-10", STATED_FIRST)
        lab.state("2026-03-10", "1000.00")

        assert [a.balance_minor for a in lab.stated()] == [100000]

    @pytest.mark.parametrize(
        ("changes", "why"),
        [
            ({"day": "2999-01-01"}, "a future date"),
            ({"day": "10/03/2026"}, "a malformed date"),
            ({"day": ""}, "no date"),
            ({"amount": "7777.777"}, "sub-penny precision"),
            ({"amount": "seven thousand"}, "words"),
            ({"amount": "1e5"}, "exponent form"),
            ({"amount": ""}, "no amount"),
            ({"currency": "EUR"}, "another currency"),
            ({"ref": "no-such-account"}, "an unknown account"),
            ({"ref": ""}, "no account"),
        ],
    )
    def test_Save_WhenRefused_ChangesNothingAndEchoesNoAmount(self, lab, changes, why):
        form = {
            "ref": ACCOUNT, "month": "2026-03", "day": "2026-03-10",
            "amount": "7777.77", "currency": "GBP", **changes,
        }

        response = lab.post("/ledger-anchor", form)

        assert response.status_code == 400, why
        assert lab.stated() == [], why
        assert "7777" not in response.text, why
        assert "Nothing was saved" in response.text

    def test_Save_ForAnAccountNamedWithMarkup_EscapesItWhereverItIsPrinted(self, lab):
        page = lab.get(MARKUP_REF).text

        assert "<b>up" not in page
        assert 'value="mark&quot;&gt;&lt;b&gt;up"' in page
        response = lab.state("2026-03-10", "10.00", ref=MARKUP_REF)
        assert response.status_code == 200
        assert "<b>up" not in response.text

    def test_Forms_AreThumbSizedTapTargets(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        assert_tap_targets_are_thumb_sized(lab.get().text)


class TestRemovingAStatedBalance:
    def test_Remove_DeletesItAndAnswersWithTheMaskedLedgerAndAConfirmation(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        response = lab.remove("2026-03-10")

        assert response.status_code == 200
        assert lab.stated() == []
        assert "Removed: the known balance for the end of 2026-03-10." in response.text
        assert "No opening balance: the figures on this page start from zero." in response.text
        assert_no_secret(response.text)

    def test_Remove_OffersOneButtonPerStatedAnchorAndNoneForOthers(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)
        lab.seed("2026-03-20", STATED_SECOND)

        page = lab.get().text

        assert page.count('action="/ledger-anchor-remove"') == 2
        assert "Remove the known balance for the end of 2026-03-10" in page
        assert "Remove the known balance for the end of 2026-03-20" in page

    def test_Remove_WhenNothingWasStatedForThatDate_SaysSoAndChangesNothing(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        response = lab.remove("2026-03-11")

        assert response.status_code == 404
        assert len(lab.stated()) == 1
        assert "nothing was removed" in response.text

    def test_Remove_WhenTheDateIsMalformed_IsRefusedAndChangesNothing(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        response = lab.remove("not-a-date")

        assert response.status_code == 400
        assert len(lab.stated()) == 1


SAVE_FORM = {
    "ref": ACCOUNT, "month": "", "day": "2026-03-10", "amount": "10.00", "currency": "GBP",
}


class TestCrossSiteRequestsAreRefused:
    EVIL = MappingProxyType({"Origin": "https://evil.example"})

    def test_Save_FromAnotherSite_IsRefusedAndStoresNothing(self, lab):
        response = lab.post("/ledger-anchor", SAVE_FORM, headers=dict(self.EVIL))

        assert response.status_code == 403
        assert lab.stated() == []

    def test_Remove_FromAnotherSite_IsRefusedAndRemovesNothing(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        response = lab.remove("2026-03-10", headers=dict(self.EVIL))

        assert response.status_code == 403
        assert len(lab.stated()) == 1

    def test_ShowValues_FromAnotherSite_IsRefusedToo(self, lab):
        lab.seed("2026-03-10", STATED_FIRST)

        response = lab.post(
            "/ledger", {"ref": ACCOUNT, "month": "2026-03"}, headers=dict(self.EVIL)
        )

        assert response.status_code == 403
        assert_no_secret(response.text)

    def test_Save_FromThePagesOwnOrigin_IsAccepted(self, lab):
        response = lab.post("/ledger-anchor", SAVE_FORM, headers={"Origin": lab.base})

        assert response.status_code == 200
        assert len(lab.stated()) == 1

    def test_AGet_OfEitherNewRoute_IsNotAWayToChangeAnything(self, lab):
        for route in ("/ledger-anchor", "/ledger-anchor-remove"):
            response = httpx.get(
                f"{lab.base}{route}",
                params={"ref": ACCOUNT, "day": "2026-03-10", "amount": "10.00"},
                timeout=20,
            )
            assert response.status_code == 404, route
        assert lab.stated() == []


class TestADeclaredAccountThatHoldsNoRows:
    def test_Page_OffersToStateTheBalanceOfAnAccountNothingHasMovedIn(self, lab):
        with Store(lab.db) as store:
            store.declare_account(AccountRecord(ref=AccountRef("passbook"), label="Passbook"))

        page = lab.get("passbook").text

        assert "This account holds no transactions at all" in page
        assert "No opening balance: the figures on this page start from zero." in page
        assert 'action="/ledger-anchor"' in page
        assert lab.state("2026-03-10", "250.00", ref="passbook").status_code == 200
        assert "Opening balance, at the end of 2026-03-10" in lab.get("passbook").text
