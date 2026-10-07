"""The account ledger over real HTTP: masked on a GET, unmasked only on a POST.

An assistant that helps maintain the system reads only GET pages, so the masked
rendering must carry every structural fact - counts, dates, sources, flags,
whether two sums differ - and none of the values. The owner's page is the same
page produced by a POST.

The private figures and words come from `build_household`, whose scenario fixes
every count before the first run. A leak is asserted against EVERY description,
amount, sum, and annotation the scenario holds, generated from its data rather
than from a list somebody remembered to extend.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.store import Store
from obdi.read.ledger import build_ledger
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_account_pages import assert_tap_targets_are_thumb_sized
from test_ledger import (
    CURRENT,
    PRIVATE_CATEGORY,
    PRIVATE_PAYEE,
    PRIVATE_REFERENCE,
    build_household,
    land,
    txn,
)

#: Accounts the hook treats as having an Actual destination.
BOUND = {CURRENT, "plain", "markup", "balanced"}

#: Every distinct string the household holds that a masked page must not show.
SECRET_TEXT = (
    PRIVATE_PAYEE,
    PRIVATE_REFERENCE,
    PRIVATE_CATEGORY,
    "COFFEE",
    "SALARY",
    "PENDING PARKING",
    "VANISHED",
    "TO SAVINGS",
    "TO SOMEWHERE",
    "REVIEWED PAYMENT",
    "same-source rule",
)

#: Every amount and sum, in minor units and in pounds-and-pence form.
SECRET_FIGURES = (
    "73913", "739.13", "1250", "12.50", "250000", "2,500.00", "987", "9.87",
    "4321", "43.21", "5000", "50.00", "3000", "30.00", "1111", "11.11",
    "164739", "1,647.39", "172739", "1,727.39", "162538", "1,625.38",
    "170538", "1,705.38", "2000", "20.00", "101", "1.01",
)


def _plain_and_markup(store: Store) -> None:
    land(
        store,
        "digest-plain",
        txn("plain", "src-a", "p1", date(2026, 3, 1), -500, "ONE"),
        txn("plain", "src-a", "p2", date(2026, 3, 2), 900, "TWO"),
        txn(
            "markup", "src-a", "m1", date(2026, 3, 3), -1234,
            "<script>alert(1)</script>", counterparty='"><img src=x onerror=1>',
        ),
        # A month that nets to exactly nothing, so its sums have no direction.
        txn("balanced", "src-a", "z1", date(2026, 3, 1), -777, "OUT"),
        txn("balanced", "src-a", "z2", date(2026, 3, 2), 777, "BACK"),
    )


@pytest.fixture
def served(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with Store(path) as store:
        build_household(store)
        _plain_and_markup(store)

    def ledger_data(ref: str, month: str, window=None):
        with Store(path) as store:
            return build_ledger(
                store, ref, month or None, bound=ref in BOUND, label="", window=window
            )

    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        ledger_data=ledger_data,
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


def _unscoped(response: httpx.Response) -> httpx.Response:
    """The page without its header-cell scopes.

    These tests assert on a cell's text and its neighbour, which is what a reader
    sees; that every header cell carries a scope is asserted once, over every page,
    in test_page_accessibility.py and test_page_structure.py.
    """
    response._content = re.sub(rb' scope="(?:row|col)"', b"", response.content)
    return response


def get(base: str, **params: str) -> httpx.Response:
    return _unscoped(httpx.get(f"{base}/ledger", params=params, timeout=20))


def post(base: str, ref: str = CURRENT, month: str = "2026-03", **kwargs) -> httpx.Response:
    return _unscoped(
        httpx.post(
            f"{base}/ledger",
            data={"ref": ref, "month": month},
            follow_redirects=False,
            timeout=20,
            **kwargs,
        )
    )


class TestTheMaskedPageHoldsNoValue:
    def test_Get_ContainsNoDescriptionPayeeReferenceCategoryOrReason(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        for secret in SECRET_TEXT:
            assert secret not in page, secret

    def test_Get_ContainsNoAmountSumOrBalanceInAnyForm(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        for figure in SECRET_FIGURES:
            assert figure not in page, figure

    def test_Get_AcrossEveryMonthAndAccount_ContainsNoFigureOrText(self, served):
        for ref, month in (
            (CURRENT, "2026-01"), (CURRENT, "2026-02"), (CURRENT, "2026-04"),
            (CURRENT, "2026-05"), (CURRENT, ""), ("savings-account", "2026-03"),
            ("plain", "2026-03"),
        ):
            page = get(served, ref=ref, month=month).text
            for secret in (*SECRET_TEXT, *SECRET_FIGURES):
                assert secret not in page, (ref, month, secret)

    @pytest.mark.parametrize("query", ["values=1", "unmask=1", "show=1", "unmasked=true"])
    def test_Get_WithAnyParameterOneMightTry_IsStillMasked(self, served, query):
        key, value = query.split("=")
        page = get(served, ref=CURRENT, month="2026-03", **{key: value}).text

        for secret in (*SECRET_TEXT, *SECRET_FIGURES):
            assert secret not in page, secret
        assert "Show values" in page

    def test_Get_ShowsTheShapeOfAValueInPlaceOfIt(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        assert "£999.99" in page  # the shape of the 739.13 payment
        assert "Xxxxx Xxxxxxxx Xxxx" in page  # the payee, case and length kept

    def test_Get_OfAnAccountWithMarkupInADescription_NeitherLeaksNorInjects(self, served):
        page = get(served, ref="markup", month="2026-03").text

        assert "<script>alert" not in page
        assert "alert" not in page
        assert "onerror" not in page


class TestTheMaskedPageCarriesEveryStructuralFact:
    def test_Page_StatesTheMonthsCounts(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        for expected in (
            "<th>Transactions in the month</th><td>8</td>",
            "<th>Transactions per source</th>"
            "<td><code>src-a</code>: 7, <code>src-b</code>: 2</td>",
            "<th>Seen by more than one source</th><td>1</td>",
            "<th>Pending</th><td>1</td>",
            "<th>Void</th><td>1</td>",
            "<th>Internal transfers, confirmed</th><td>1</td>",
            "<th>Internal transfers, claimed but unpaired</th><td>1</td>",
            "<th>Open review flags</th><td>1</td>",
            "<th>Would be sent to Actual</th><td>7</td>",
        ):
            assert expected in page, expected

    def test_Page_StatesWhyRowsAreWithheld(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        assert "<th>Withheld from Actual</th><td>1 (void: 1)</td>" in page

    def test_Page_ForAnUnboundAccount_SaysNothingIsSentSoThereIsNothingToCompare(
        self, served
    ):
        """Nothing of an unbound account is sent, so a verdict on whether the
        two figures differ would be true and tell the reader nothing."""
        page = get(served, ref="savings-account", month="2026-03").text

        sentence = "Nothing in this account is sent to Actual, so there is nothing to compare."
        assert page.count(sentence) == 2, "once under the month sums, once under the positions"
        assert "The two month sums" not in page
        assert "The two positions" not in page

    def test_Page_ForABoundAccountHoldingTransfers_SaysTheSumsAgree(self, served):
        """Transfers travel to Actual like any other row, so they no longer
        open a gap between the store's figure and what is sent."""
        page = get(served, ref=CURRENT, month="2026-03").text

        assert "The two month sums agree." in page
        assert "The two positions agree." in page

    def test_Page_SaysTheyAgreeWhenTheyDo(self, served):
        page = get(served, ref="plain", month="2026-03").text

        assert "The two month sums agree." in page
        assert "The two positions agree." in page

    def test_Page_CarriesDirectionsDatesStatusesAndSources(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        assert "2026-03-05" in page and "2026-03-28" in page
        assert "out £" in page and "in £" in page
        assert ">booked<" in page and ">pending<" in page and ">void<" in page
        assert ">src-a<" in page and ">src-b<" in page
        assert "net in" in page  # the month's sum, direction only

    def test_Page_CarriesEveryFlagWhereItApplies(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        for flag in (
            ">one source only<",
            ">transfer with savings-account<",
            ">transfer?<",
            ">review<",
            ">withheld from Actual: void<",
            ">absorbed 2 ids<",
        ):
            assert flag in page, flag
        # A transfer is flagged as a transfer, and is not withheld for being one.
        assert "withheld from Actual: internal transfer" not in page

    def test_Page_OmitsFlagsNoRowEarnsInThatMonth(self, served):
        january = get(served, ref=CURRENT, month="2026-01").text
        march = get(served, ref=CURRENT, month="2026-03").text

        assert ">shares identity<" in january
        assert ">shares identity<" not in march
        assert ">review<" not in january
        assert ">transfer?<" not in january
        assert ">push would fail<" not in march

    def test_Page_ForASingleSourceAccount_NeverSaysOneSource(self, served):
        page = get(served, ref="savings-account", month="2026-03").text

        assert ">one source only<" not in page
        assert ">transfer with current-account<" in page

    def test_Page_ForAnUnboundAccount_NamesTheMissingBinding(self, served):
        page = get(served, ref="savings-account", month="2026-03").text

        assert "not sent to Actual" in page
        assert "<th>Would be sent to Actual</th><td>0</td>" in page

    def test_Page_SaysWhatItDoesNotDetect(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        assert "booked transaction that a source reported once and stopped reporting" in page
        assert "not a pass" in page

    def test_Page_StatesTheDateFieldAndTheStatementCost(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        assert "value date" in page
        from obdi.read.ledger import QUERIES_PER_PAGE

        assert f"makes {QUERIES_PER_PAGE} database queries" in page

    def test_Page_LinksToTheShapePageAndHome(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        assert 'href="/account?ref=current-account"' in page
        assert 'href="/"' in page


NO_FLAG_COUNTS = (
    "seen by more than one source, seen by one source only, pending, void, "
    "transfers confirmed, transfers unpaired, open review flags, "
    "and refused by the push builder"
)


class TestTheMonthSummaryShowsOnlyWhatIsNotZero:
    def test_Summary_WhenSomeFlagCountsAreZero_NamesThemInOneSentence(self, served):
        """March holds one row of every kind except one the push builder refuses."""
        page = get(served, ref=CURRENT, month="2026-03").text

        assert "None this month: refused by the push builder." in page
        assert "<th>Refused by the push builder</th>" not in page

    def test_Summary_WhenEveryFlagCountIsZero_NamesAllEightInOneSentence(self, served):
        page = get(served, ref="plain", month="2026-03").text

        assert f"None this month: {NO_FLAG_COUNTS}." in page
        for label in (
            "Seen by more than one source", "Pending", "Void", "Open review flags",
            "Internal transfers, confirmed", "Internal transfers, claimed but unpaired",
            "Seen by one source only", "Refused by the push builder",
        ):
            assert f"<th>{label}" not in page, label

    def test_Summary_WhenEveryFlagCountIsZero_StillStatesTheAlwaysMeaningfulRows(self, served):
        page = get(served, ref="plain", month="2026-03").text

        for expected in (
            "<th>Transactions in the month</th><td>2</td>",
            "<th>Transactions per source</th><td><code>src-a</code>: 2</td>",
            "<th>Would be sent to Actual</th><td>2</td>",
            "<th>Withheld from Actual</th><td>0 (none)</td>",
        ):
            assert expected in page, expected
        assert (
            "<th>Sum of the transactions counted (void, counted elsewhere, and reversed "
            "excluded)</th>"
        ) in page
        assert "<th>Sum of what would be sent to Actual</th>" in page

    def test_Summary_WhenACountIsNotZero_IsNeverNamedAmongTheZeroOnes(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text
        sentence = page.split("None this month:", 1)[1].split("</p>", 1)[0]

        for nonzero in ("pending", "void", "transfers confirmed", "open review flags"):
            assert nonzero not in sentence, nonzero
        assert "<th>Pending</th><td>1</td>" in page


class TestANilAmountIsJustNil:
    def test_BalancedMonth_PrintsNilAloneWhereItsSumsAreNil(self, served):
        masked = get(served, ref="balanced", month="2026-03").text

        assert (
            "<th>Sum of the transactions counted (void, counted elsewhere, and reversed "
            "excluded)</th><td>nil</td>" in masked
        )
        assert "<th>Sum of what would be sent to Actual</th><td>nil</td>" in masked
        assert "nil £" not in masked

    def test_BalancedMonth_ShownValues_PrintNilAloneToo(self, served):
        shown = post(served, ref="balanced").text

        assert (
            "<th>Sum of the transactions counted (void, counted elsewhere, and reversed "
            "excluded)</th><td>nil</td>" in shown
        )
        assert "nil £" not in shown
        assert "nil 0" not in shown

    def test_BalancedMonth_RunningPositionPrintsNilAlone(self, served):
        masked = get(served, ref="balanced", month="2026-03").text

        assert "<th>Balance by the transactions held</th><td>nil</td>" in masked
        assert "<th>Balance by what would be sent to Actual</th><td>nil</td>" in masked

    def test_UnboundAccount_NothingSent_PrintsNilAloneAtTheSentFigures(self, served):
        masked = get(served, ref="savings-account", month="2026-03").text
        shown = post(served, ref="savings-account").text

        for page in (masked, shown):
            assert "<th>Sum of what would be sent to Actual</th><td>nil</td>" in page
            assert "nil £" not in page

    def test_NonNilSum_StillPrintsItsDirectionAndFigure(self, served):
        masked = get(served, ref=CURRENT, month="2026-03").text
        shown = post(served).text

        assert "net in £" in masked
        assert "<td>net in £" in shown or "<td>net out £" in shown


class TestMonthLinksSitAtTheTop:
    def test_Masked_PreviousAndNextAreOrdinaryLinksBeforeTheSummary(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text
        top = page[: page.index("<th>Transactions in the month</th>")]

        for month in ("2026-02", "2026-04"):
            link = f'href="/ledger?ref=current-account&amp;month={month}"'
            assert link in top, month
        assert 'class="button" href="/ledger?ref=current-account&amp;month=2026-02"' not in top
        assert 'class="tap" href="/ledger?ref=current-account&amp;month=2026-02"' in top

    def test_Masked_NoMonthStepIsAPrimaryButtonAnywhere(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        assert 'class="button" href="/ledger' not in page
        assert 'class="button" href="/account' not in page
        assert 'class="button" href="/"' not in page

    def test_Unmasked_StepsStayPostedFormsAndSitBeforeTheSummary(self, served):
        page = post(served).text
        top = page[: page.index("<th>Transactions in the month</th>")]

        assert 'name="month" value="2026-02"' in top
        assert 'name="month" value="2026-04"' in top
        assert 'href="/ledger?ref=current-account&amp;month=2026-02"' not in page
        assert "Previous month, 2026-02" in top

    def test_EmptyMonth_StillOffersBothStepsAndTheNewestMonthAtTheTop(self, served):
        page = get(served, ref=CURRENT, month="2026-04").text
        top = page.split("<summary>Running position</summary>")[0]

        assert "Previous month, 2026-03" in top
        assert "Next month, 2026-05" in top
        assert "Newest month with rows, 2026-05" in top

    def test_OldestMonth_HasNoPreviousLinkAndNewestHasNoNext(self, served):
        oldest = get(served, ref=CURRENT, month="2026-01").text
        newest = get(served, ref=CURRENT, month="2026-05").text

        assert "Previous month" not in oldest
        assert "Next month" not in newest


class TestMonthNavigation:
    def test_NoMonth_ShowsTheLastDaysOrTransactionsWhicheverIsWiderIncludingTheNewestRow(
        self, served
    ):
        page = get(served, ref=CURRENT).text

        assert re.search(
            r"<h2>Last \d+ transactions, 2026-\d\d-\d\d to \d{4}-\d\d-\d\d "
            r"\(more than 30 days, so that \d+ are shown\)</h2>",
            page,
        )
        assert "2026-05-" in page, "the newest month's row is among them"

    def test_Navigation_StepsThroughAnEmptyMonthInBothDirections(self, served):
        page = get(served, ref=CURRENT, month="2026-04").text

        assert "No transactions are dated in this month" in page
        assert 'href="/ledger?ref=current-account&amp;month=2026-03"' in page
        assert 'href="/ledger?ref=current-account&amp;month=2026-05"' in page
        assert "Newest month with rows, 2026-05" in page
        assert "Show values" in page

    def test_EmptyMonth_DoesNotLookLikeAHealthyEmptyResult(self, served):
        page = get(served, ref=CURRENT, month="2026-04").text

        assert 'class="warn"' in page
        assert "not a clean result" in page
        assert "<th>Transactions in the month</th>" not in page

    def test_Month_WhenNotAMonth_Is400(self, served):
        assert get(served, ref=CURRENT, month="2026-13").status_code == 400

    def test_Ref_WhenMissing_Is400(self, served):
        assert get(served).status_code == 400


class TestAccountsThatAreNotHealthyEmptyOnes:
    def test_UnknownAccount_SaysUnknownWith404(self, served):
        response = get(served, ref="no-such-account", month="2026-03")

        assert response.status_code == 404
        assert "Unknown account" in response.text
        assert "not an empty account" in response.text

    def test_AccountWithNoRows_SaysItHoldsNothingAtAll(self, tmp_path):
        from obdi.ingest.accounts import AccountRecord, AccountRef

        path = tmp_path / "empty.sqlite3"
        with Store(path) as store:
            store.declare_account(AccountRecord(ref=AccountRef("empty-isa"), label="Empty ISA"))

        def ledger_data(ref, month, window=None):
            with Store(path) as store:
                return build_ledger(store, ref, month or None, bound=False, window=window)

        base, httpd = _serve(tmp_path, ledger_data)
        try:
            response = get(base, ref="empty-isa")
        finally:
            httpd.shutdown()

        assert response.status_code == 200
        assert "holds no transactions at all" in response.text
        assert "not a clean month" in response.text


class TestShowingValuesTakesAPost:
    def test_Post_ShowsEveryValueTheMaskedPageHides(self, served):
        page = post(served).text

        for expected in (
            "£739.13",
            PRIVATE_PAYEE,
            PRIVATE_REFERENCE,
            PRIVATE_CATEGORY,
            "SALARY ZEBRA LTD",
            "kept apart by the same-source rule",
            "£1,647.39",
        ):
            assert expected in page, expected

    def test_Post_IsAnsweredDirectly_NotRedirectedToAnAddress(self, served):
        response = post(served)

        assert response.status_code == 200
        assert "location" not in response.headers

    def test_Post_IsNeverCached(self, served):
        assert post(served).headers["Cache-Control"] == "no-store"

    def test_Post_SaysProminentlyThatValuesAreShown_AndLinksBackToTheMaskedGet(self, served):
        page = post(served).text

        assert "VALUES ARE SHOWN" in page
        assert 'href="/ledger?ref=current-account&amp;month=2026-03"' in page
        assert "Show values" not in page

    def test_UnmaskedPage_MovesBetweenMonthsByPost_SoValuesStayShownAndGainNoAddress(
        self, served
    ):
        """Somebody reading several months of values asked for them once.
        Each step must keep them shown, and must not hand out an address
        that shows values."""
        page = post(served, month="2026-04").text

        assert 'name="month" value="2026-03"' in page
        assert 'name="month" value="2026-05"' in page
        assert 'href="/ledger?ref=current-account&amp;month=2026-03"' not in page
        assert 'href="/ledger?ref=current-account&amp;month=2026-05"' not in page
        # The one address left is the way back to the masked view of this month.
        assert 'href="/ledger?ref=current-account&amp;month=2026-04"' in page

    def test_UnmaskedPage_SteppedToAnotherMonth_StillShowsValuesAndIsNotKept(self, served):
        response = post(served, month="2026-01")

        assert "VALUES ARE SHOWN" in response.text
        assert response.headers["Cache-Control"] == "no-store"

    def test_MaskedPage_OffersOneButton_PostingTheRefAndMonth(self, served):
        page = get(served, ref=CURRENT, month="2026-03").text

        # The one-page press, and beside it the quieter press that shows values on every page.
        assert page.count("Show values") == 2
        assert page.count("Show values on every page") == 1
        assert '<form method="post" action="/ledger">' in page
        assert 'name="ref" value="current-account"' in page
        assert 'name="month" value="2026-03"' in page

    def test_Get_OfTheSameFields_NeverShowsValues(self, served):
        """The unmasked page has no address: the POST's fields on a GET are masked."""
        page = httpx.get(
            f"{served}/ledger?ref={CURRENT}&month=2026-03&values=1", timeout=20
        ).text

        assert "£739.13" not in page

    def test_CrossSitePost_IsRefusedAndShowsNothing(self, served):
        response = post(served, headers={"Origin": "https://evil.example"})

        assert response.status_code == 403
        assert "another site" in response.text
        assert PRIVATE_PAYEE not in response.text

    def test_PostFromObdisOwnOrigin_IsAccepted(self, served):
        assert post(served, headers={"Origin": served}).status_code == 200

    def test_Post_OfAnUnknownAccount_Is404AndSaysUnknown(self, served):
        response = post(served, ref="no-such-account")

        assert response.status_code == 404
        assert "Unknown account" in response.text

    def test_Post_EscapesMarkupInAFeedDescription(self, served):
        page = post(served, ref="markup").text

        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
        assert "<script>alert" not in page
        assert "onerror=1>" not in page
        assert "&quot;&gt;&lt;img src=x onerror=1&gt;" in page

    def test_Ref_WithMarkup_IsEscapedOnTheUnknownAccountPage(self, served):
        page = get(served, ref="<b>x</b>").text

        assert "<b>x</b>" not in page
        assert "&lt;b&gt;x&lt;/b&gt;" in page


class TestThePagesAreUsableOnAPhone:
    def test_BothRenderings_UseThumbSizedTargets(self, served):
        assert_tap_targets_are_thumb_sized(get(served, ref=CURRENT, month="2026-03").text)
        assert_tap_targets_are_thumb_sized(post(served).text)

    def test_TheTransactions_AreAListNeedingNoSidewaysScroll(self, served):
        """A seven-column table was wider than a phone and wider than the page
        column on a desktop, so its flags sat off-screen in a scrolling box."""
        page = get(served, ref=CURRENT, month="2026-03").text

        # The page's content, not its stylesheet: the shared stylesheet may
        # mention any property, and what must not exist is an element sized
        # wider than the screen.
        content = page.split("</style>", 1)[1]

        assert 'class="txns"' in content
        assert "min-width" not in content
        assert "<th>Flags</th>" not in page
        assert "<th>Sources</th>" not in page


def _transaction_items(page: str) -> list[str]:
    block = page.split('<ul class="txns">', 1)[1].split("</ul>", 1)[0]
    return [item for item in block.split("<li") if item.strip()]


def _item_holding(page: str, text: str) -> str:
    holding = [item for item in _transaction_items(page) if text in item]
    assert len(holding) == 1, (text, len(holding))
    return holding[0]


class TestEachTransactionKeepsEverythingTheTableShowed:
    def test_Salary_CarriesDateAmountDescriptionStatusAndSourceTogether(self, served):
        item = _item_holding(post(served).text, "SALARY ZEBRA LTD")

        assert re.search(r'class="t-when mono nowrap" title="[^"]*">2026-03-09</span>', item)
        assert 'class="t-fig mono nowrap fig">in £2,500.00</span>' in item
        assert 'class="mk ' in item, "one mark for whether it is cleared"
        assert ">booked<" in item
        assert ">src-b<" in item

    def test_Coffee_SeenByBothSources_ListsBothAndIsNotFlaggedOneSource(self, served):
        item = _item_holding(post(served).text, "COFFEE QUAGGA CAFE")

        assert ">src-a<" in item and ">src-b<" in item
        assert ">one source only<" not in item

    def test_Zebra_CarriesItsFlagsAndAnnotationBesideTheRow(self, served):
        item = _item_holding(post(served).text, "ZEBRA " + PRIVATE_REFERENCE)

        assert ">one source only<" in item
        assert ">absorbed 2 ids<" in item
        assert f"category: {PRIVATE_CATEGORY}" in item
        assert f"payee: {PRIVATE_PAYEE}" in item
        assert "set by" in item

    def test_Transfer_NamesTheOtherAccountBesideTheRow(self, served):
        item = _item_holding(post(served).text, "TO SAVINGS")

        assert ">transfer with savings-account<" in item

    def test_UnpairedTransfer_ReviewAndVoidRows_EachCarryTheirOwnFlag(self, served):
        page = post(served).text

        assert ">transfer?<" in _item_holding(page, "TO SOMEWHERE")
        review = _item_holding(page, "REVIEWED PAYMENT")
        assert ">review<" in review and "kept apart by the same-source rule" in review
        void = _item_holding(page, "VANISHED PENDING")
        assert ">void<" in void and ">withheld from Actual: void<" in void
        assert ">pending<" in _item_holding(page, "PENDING PARKING")

    def test_RowWithNoFlagsOrAnnotation_SaysNeitherNor(self, served):
        item = _item_holding(post(served).text, "SALARY ZEBRA LTD")

        assert "withheld" not in item
        assert "set by" not in item

    def test_MaskedPage_HasTheSameItemsWithShapesInPlaceOfValues(self, served):
        masked = _transaction_items(get(served, ref=CURRENT, month="2026-03").text)
        shown = _transaction_items(post(served).text)

        assert len(masked) == len(shown) == 8


def _serve(tmp_path, ledger_data):
    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        ledger_data=ledger_data,
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_port}", httpd


class TestWiring:
    def test_Page_WhenNotWired_IsNotFound_ForGetAndPost(self, tmp_path):
        base, httpd = _serve(tmp_path, None)
        try:
            assert get(base, ref="x").status_code == 404
            assert post(base, ref="x").status_code == 404
        finally:
            httpd.shutdown()

    def test_Page_WhenTheHookFails_NamesTheFailureRatherThanShowingAnEmptyLedger(
        self, tmp_path
    ):
        def broken(ref, month, window=None):
            raise RuntimeError("the store would not open")

        base, httpd = _serve(tmp_path, broken)
        try:
            for response in (get(base, ref="x"), post(base, ref="x")):
                assert response.status_code == 500
                assert "the store would not open" in response.text
        finally:
            httpd.shutdown()

    def test_CoveragePage_LinksEachHeldAccountToItsOwnPage_WhichIsItsLedger(self, tmp_path):
        from obdi.verify.coverage import SourceCoverage
        from obdi.web_sections import render_coverage

        holdings = [
            SourceCoverage(
                account_id="halifax-current",
                source="truelayer",
                count=3,
                earliest=date(2026, 1, 1),
                latest=date(2026, 2, 1),
                inflow_minor=1,
                outflow_minor=1,
                with_durable_id=3,
            )
        ]
        page = render_coverage(holdings=lambda: holdings).decode()

        # The field-by-field shape page is no longer linked from every block of this page: the
        # account's own page is the one onward link, and holds the archive action.
        assert 'href="/ledger?ref=halifax-current"' in page
        assert page.count('href="/ledger?ref=halifax-current"') == 1

    def test_Hook_BuiltFromTheRealConfiguration_ReadsTheStoreAndTheActualBindings(
        self, tmp_path, monkeypatch
    ):
        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            build_household(store)
        account_map = tmp_path / "accounts.json"
        account_map.write_text(
            json.dumps({"actual": [{"canonical_id": CURRENT, "actual_account_id": "a-1"}]}),
            encoding="utf-8",
        )
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
        for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
            monkeypatch.delenv(variable, raising=False)
        config = build_web_config(db)

        assert config is not None and config.ledger_data is not None
        bound = config.ledger_data(CURRENT, "2026-03")
        unbound = config.ledger_data("savings-account", "2026-03")

        assert bound.actual_bound is True and unbound.actual_bound is False
        assert bound.summary is not None and bound.summary.would_send == 7
        assert next(row.amount.minor for row in bound.rows if row.status == "booked") != 0
