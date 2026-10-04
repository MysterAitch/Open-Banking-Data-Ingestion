"""The position page over real HTTP and a real store, using test_position's household.

The page is built by the application's own configuration (`build_web_config`),
so the hooks under test are the ones a person's browser reaches. The household's
figures, and the working behind each, are in test_position's docstring.

The page's rule is the ledger's: every GET is masked whatever its query string,
and values, with the chart, appear only in the direct response to a POST of the
"Show values" form, which is never cached and never redirected.
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
from obdi.balance_anchors import record_stated_anchor
from obdi.cli import build_web_config
from obdi.masking import MASKED_TOTAL
from obdi.position import AccountInput, AssetInput, Observation, build_position
from obdi.store import Store
from obdi.valuations import Asset, AssetKind, record_observation
from obdi.web import AuthorisationSession, ConnectionHandler
from obdi.web_position import render_position
from test_position import household

#: Every figure the household makes, in pounds and pence and in minor units, so
#: that a masked page can be searched for any of them.
SECRET_FIGURES = (
    "269,359.87", "26935987", "6,559.87", "655987", "900.00", "90000",
    "1,700.00", "170000", "262,000.00", "26200000", "5,509.87", "550987",
    "1,050.00", "105000", "12,000.00", "1200000", "250,000.00", "25000000",
    "11,500.00", "1150000", "6,123.45", "612345", "5,555.55", "555555",
    "17,255.55", "1725555", "267,282.10", "26728210", "10,934.56", "1093456",
    "888.88", "88888", "1,500.00", "150000", "1,111.11", "111111",
    # The provisional figures: the total, and its month-ends from March and April.
    "270,248.75", "27024875", "18,144.43", "1814443", "268,170.98", "26817098",
)

PROVISIONAL_SENTENCE = re.compile(r"<p[^>]*>(<strong>)?Provisional, counting every account.*?</p>")


def provisional_paragraph(page: str) -> str:
    found = PROVISIONAL_SENTENCE.search(page)
    assert found, "the provisional total is not on the page"
    return found.group(0)


def chart_series(page: str) -> set[str]:
    return set(re.findall(r'data-series="(\w+)"', page))

EVIL = MappingProxyType({"Origin": "https://evil.example"})


class Lab:
    def __init__(self, base: str, db) -> None:
        self.base = base
        self.db = db

    def get(self, path: str = "/position", **params: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", params=params, timeout=30)

    def show_values(self, **kwargs) -> httpx.Response:
        return httpx.post(
            f"{self.base}/position", data={}, follow_redirects=False, timeout=30, **kwargs
        )

    def ledger_values(self, ref: str) -> httpx.Response:
        return httpx.post(
            f"{self.base}/ledger", data={"ref": ref, "month": ""}, follow_redirects=False,
            timeout=30,
        )


def serve(tmp_path, monkeypatch, populate):
    db = tmp_path / "position.sqlite3"
    with Store(db) as store:
        populate(store)
    account_map = tmp_path / "accounts.json"
    account_map.write_text(json.dumps({"actual": []}), encoding="utf-8")
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
    return httpd, Lab(f"http://127.0.0.1:{httpd.server_port}", db)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    httpd, lab = serve(tmp_path, monkeypatch, household)
    try:
        yield lab
    finally:
        httpd.shutdown()


@pytest.fixture
def empty_lab(tmp_path, monkeypatch):
    httpd, lab = serve(tmp_path, monkeypatch, lambda store: None)
    try:
        yield lab
    finally:
        httpd.shutdown()


def assert_no_secret(page: str, *, where: str = "") -> None:
    for figure in SECRET_FIGURES:
        assert figure not in page, f"{figure} {where}"


class TestMaskedByDefault:
    def test_Get_ShowsNoFigureTheHouseholdMakes(self, lab):
        assert_no_secret(lab.get().text)

    def test_Get_ShowsStructureButMasksFigures(self, lab):
        page = lab.get().text

        assert "Counts 4 accounts of 5 and 2 assets." in page
        assert "in credit" in page
        assert "overdrawn or owed" in page
        assert MASKED_TOTAL in page
        assert "VALUES ARE SHOWN" not in page

    def test_Get_IsAWidePage_SoItsCardsSitAbreastOnADesktop(self, lab):
        assert '<body class="wide">' in lab.get().text

    def test_Get_ShowsNoFigureByItsSizeEither(self, lab):
        """Every figure on the page is a balance or a sum, and the number of
        digits in one is most of what there is to know about it."""
        page = lab.get().text

        assert not re.search(r"£[\d9][\d9,]*\.\d\d", page), (
            "a figure survived masking with its length intact"
        )

    @pytest.mark.parametrize(
        "query", ["values=1", "unmask=1", "show=1", "unmasked=true", "masked=0"]
    )
    def test_Get_WithAnyParameterOneMightTry_IsStillMasked(self, lab, query):
        key, value = query.split("=")

        response = lab.get(**{key: value})

        assert_no_secret(response.text, where=query)
        assert "VALUES ARE SHOWN" not in response.text
        assert "<svg" not in response.text

    def test_Get_DrawsNoChartAtAllAndSaysWhenOneIsDrawn(self, lab):
        page = lab.get().text

        for fragment in ("<svg", "<polyline", "<path", "<circle", "viewBox"):
            assert fragment not in page, fragment
        assert "The chart is drawn when values are shown." in page

    def test_Get_HoldsTheMonthTableWithCountsButMaskedFigures(self, lab):
        page = lab.get().text

        assert "2026-04" in page
        assert "6 of 6" in page
        assert "5 of 6" in page
        assert "complete from <strong>2026-04</strong>" in page
        assert "<summary>Month table" not in page, "eleven months are shown, not folded"

    def test_Get_FoldsTheMonthTableWhenThereAreMoreThanTwelveMonths(self, tmp_path, monkeypatch):
        def long_history(store: Store) -> None:
            household(store)
            record_observation(
                store, Asset("old-fund", AssetKind.INVESTMENT),
                observed_at=date(2025, 6, 30), source="statement", value_minor=100,
            )

        httpd, lab = serve(tmp_path, monkeypatch, long_history)
        try:
            page = lab.get().text
        finally:
            httpd.shutdown()

        assert "<summary>Month table, newest first (17 months)</summary>" in page

    def test_Get_IsNotCachedAgainstAMaskedPage(self, lab):
        # Masked pages are free to cache; the unmasked response is the one that must not be.
        assert "no-store" not in lab.get().headers.get("cache-control", "")


class TestShowingValues:
    def test_Post_ShowsTheHeadlineSubtotalsAndEveryAccount(self, lab):
        response = lab.show_values()
        page = response.text

        assert response.status_code == 200
        assert "VALUES ARE SHOWN" in page
        assert "in credit <span class=\"mono nowrap\">£269,359.87</span>" in page
        for figure in (
            "£6,559.87", "£900.00", "£1,700.00", "£262,000.00", "£5,509.87", "£1,050.00",
        ):
            assert figure in page, figure
        assert "£11,500.00" in page and "£6,123.45" in page, "the entitlements' income"

    def test_Post_IsNeverCachedAndNeverRedirects(self, lab):
        response = lab.show_values()

        assert "no-store" in response.headers["cache-control"]
        assert "location" not in response.headers

    def test_Post_DrawsTheChartAsInlineSvgThatScalesAndWorksInBothThemes(self, lab):
        page = lab.show_values().text

        assert page.count('<svg role="img"') == 1, "one chart, beside the swatches of its key"
        assert page.count("<svg") == 1 + page.count("<li data-key=")
        assert 'role="img"' in page
        assert "<title" in page and "<desc" in page
        assert "viewBox=" in page
        assert "currentColor" in page
        assert "<polyline" in page
        assert "stroke-dasharray" in page, "the partial months are drawn differently"
        assert "<script" not in page
        assert "href=\"http" not in page and "src=\"http" not in page, "no external resource"

    def test_Post_ShowsTheMonthEndFiguresItHasWorkedOut(self, lab):
        page = lab.show_values().text

        for figure in ("£17,255.55", "£267,282.10", "£10,934.56", "£269,359.87"):
            assert figure in page, figure

    def test_Post_ChartCarriesLowestHighestAndLatest(self, lab):
        page = lab.show_values().text

        assert "lowest £1,500.00" in page
        # The scale covers both lines, so its top is the provisional total's.
        assert "highest £270,248.75" in page
        assert "latest £269,359.87" in page

    def test_Post_FromAnotherSite_IsRefusedAndShowsNothing(self, lab):
        response = lab.show_values(headers=dict(EVIL))

        assert response.status_code == 403
        assert_no_secret(response.text)

    def test_Post_FromThePagesOwnOrigin_IsAccepted(self, lab):
        assert lab.show_values(headers={"Origin": lab.base}).status_code == 200


class TestWhatIsNotCounted:
    def test_AccountWithNoOpening_HasItsOwnHeadingAndALinkToStateABalance(self, lab):
        page = lab.get().text

        assert "Not counted: no opening balance" in page
        assert "unanchored" in page
        assert 'href="/ledger?ref=unanchored"' in page
        assert "State a balance on its ledger" in page

    def test_Headline_SaysHowManyAccountsAreNotCounted(self, lab):
        page = lab.get().text

        assert "1 account is not counted" in page

    def test_ItsRowsAreInNoTotal(self, lab):
        page = lab.show_values().text

        # Its +888.88 appears as a movement, and in no real figure.
        headline = page.split("<h2>Net worth</h2>")[1].split("<h2>")[0]
        assert 'in credit <span class="mono nowrap">£269,359.87</span>' in headline
        assert "£270,248.75" not in headline.split("Provisional")[0]
        assert page.count("£269,359.87") >= 1
        accounts = page.split("<h2>Accounts</h2>")[1].split("<h2>Not counted")[0]
        assert "£888.88" not in accounts and "£270,248.75" not in accounts

    def test_StatingABalanceForIt_MovesItIntoTheTotalAndRemovesTheHeading(self, lab):
        with Store(lab.db) as store:
            record_stated_anchor(store, "unanchored", "2026-03-31", "1000.00")

        masked = lab.get().text
        shown = lab.show_values().text

        assert "Counts 5 accounts of 5 and 2 assets." in masked
        assert "Not counted: no opening balance" not in masked
        assert "are not counted" not in masked and "is not counted" not in masked
        assert "£270,359.87" in shown, "26,935,987 + 100,000"

    def test_ADifferingLaterAnchor_IsFlaggedOnTheAccountThatStaysCounted(self, lab):
        page = lab.get().text

        assert "1 check differ" in page
        assert "balance may be wrong. It is still counted." in page

    def test_StateAndDefinedBenefitPensions_AreListedAsIncomeAndNotAsWealth(self, lab):
        page = lab.get().text

        assert "Income entitlements, not counted as wealth" in page
        assert "state-pension-forecast" in page
        assert "teachers-scheme" in page
        # Not in the asset subtotal: 12,000.00 + 250,000.00 only.
        assert "£262,000.00" in lab.show_values().text


class TestTheBalanceMatchesTheLedger:
    @pytest.mark.parametrize(
        ("ref", "words", "figure"),
        [
            ("everyday", "in credit", "£5,509.87"),
            ("card", "overdrawn or owed", "£900.00"),
            ("drifter", "in credit", "£1,050.00"),
            ("oldsaver", "in credit", "£1,700.00"),
        ],
    )
    def test_Account_ShowsTheSameBalanceAsItsLedgersRunningPosition(self, lab, ref, words, figure):
        ledger = lab.ledger_values(ref).text
        position = lab.show_values().text

        assert f"{words} £{figure.removeprefix('£')}" in ledger
        assert f'{words} <span class="mono nowrap">{figure}</span>' in position


class TestArchivedAccounts:
    def test_ArchivedAccount_IsCountedAndFoldedIntoItsOwnGroup(self, lab):
        page = lab.get().text

        assert re.search(r"<details><summary><strong>Archived accounts \(1\)", page)
        assert page.index("Archived accounts") < page.index("Old saver")
        assert 'class="pill pill-quiet">archived' in page

    def test_ArchivedAccount_IsInTheNetWorth(self, lab):
        # 26,935,987 includes oldsaver's 170,000; without it: 26,765,987.
        assert "£269,359.87" in lab.show_values().text


class TestDegenerateCases:
    def test_AnEmptyStore_SaysNothingIsHeldAndStatesNoNetWorth(self, empty_lab):
        response = empty_lab.get()

        assert response.status_code == 200
        assert "Nothing is held." in response.text
        assert "£" not in response.text.split("<h2>Net worth</h2>")[1].split("<h2>")[0]
        assert "There is no history yet" in response.text
        assert "<svg" not in empty_lab.show_values().text

    def test_EveryAccountWithoutAnOpening_StatesNoNetWorthAndWhy(self, tmp_path, monkeypatch):
        from test_ledger import land, txn

        def only_rows(store: Store) -> None:
            land(store, "d", txn("a1", "s", "1", date(2026, 3, 1), 12345, "ROW"))
            land(store, "e", txn("a2", "s", "2", date(2026, 3, 2), 54321, "ROW TWO"))

        httpd, lab = serve(tmp_path, monkeypatch, only_rows)
        try:
            page = lab.show_values().text
        finally:
            httpd.shutdown()

        assert "No net worth is shown." in page
        assert "Counts 0 accounts of 2 and 0 assets." in page
        assert "2 accounts are not counted" in page
        assert "Nothing is counted" in page
        headline = page.split("<h2>Net worth</h2>")[1].split("<h2>")[0]
        assert 'class="figure"' not in headline, "no net worth is stated"
        # 123.45 + 543.21 = 666.66, each opening taken as nil.
        assert "in credit" in provisional_paragraph(page)
        assert "£666.66" in provisional_paragraph(page)
        assert "2 unknown opening balances" in provisional_paragraph(page)
        assert chart_series(page) == {"provisional"}

    def test_OnlyUncountedAccounts_PresentNoZeroAsANetWorth(self, tmp_path, monkeypatch):
        from test_ledger import land, txn

        httpd, lab = serve(
            tmp_path,
            monkeypatch,
            lambda store: land(store, "d", txn("a1", "s", "1", date(2026, 3, 1), 100, "R")),
        )
        try:
            page = lab.get().text
        finally:
            httpd.shutdown()

        headline = page.split("<h2>Net worth</h2>")[1].split("<h2>")[0]
        assert 'class="figure"' not in headline
        assert "£0.00" not in page and "£9.99" not in page

    def test_AccountNamedWithMarkup_IsEscapedWhereverPrinted(self, tmp_path, monkeypatch):
        def marked(store: Store) -> None:
            household(store)
            store.declare_account(
                AccountRecord(ref=AccountRef("oddball"), label='<b onclick="x">Odd</b>')
            )

        httpd, lab = serve(tmp_path, monkeypatch, marked)
        try:
            page = lab.get().text
        finally:
            httpd.shutdown()

        assert '<b onclick="x">' not in page
        assert "&lt;b onclick=" in page


class TestChartEdgeCases:
    TODAY = date(2026, 10, 2)

    def rendered(self, *assets: AssetInput) -> str:
        position = build_position([], list(assets), today=self.TODAY)
        return render_position(position, unmasked=True).decode()

    def test_OnePoint_IsADotAndNotADivisionByZero(self):
        page = self.rendered(
            AssetInput("only", (Observation(date(2026, 10, 1), "other", 4242, None, "n"),))
        )

        assert "<circle" in page
        assert "<polyline" not in page
        assert "nan" not in page.lower().replace("financial", "")

    def test_AllEqualValues_IsAFlatLineAndNotADivisionByZero(self):
        page = self.rendered(
            AssetInput(
                "flat",
                (
                    Observation(date(2026, 8, 1), "other", 5000, None, "n"),
                    Observation(date(2026, 9, 1), "other", 5000, None, "n"),
                ),
            )
        )

        assert "<polyline" in page
        assert "all months £50.00" in page
        ys = set(re.findall(r"polyline points=\"([^\"]+)\"", page)[0].split())
        assert len({point.split(",")[1] for point in ys}) == 1, "every point at one height"

    def test_ARangeCrossingZero_DrawsANilBaseline(self):
        page = self.rendered(
            AssetInput(
                "swing",
                (
                    Observation(date(2026, 8, 1), "other", -5000, None, "n"),
                    Observation(date(2026, 9, 1), "other", 7000, None, "n"),
                ),
            )
        )

        assert ">nil</text>" in page
        assert "lowest -£50.00" in page

    def test_ARangeThatStaysPositive_DrawsNoBaseline(self):
        page = self.rendered(
            AssetInput(
                "up",
                (
                    Observation(date(2026, 8, 1), "other", 1000, None, "n"),
                    Observation(date(2026, 9, 1), "other", 7000, None, "n"),
                ),
            )
        )

        assert ">nil</text>" not in page

    def test_NoHistory_IsASentenceAndNoChart(self):
        page = render_position(build_position([], [], today=self.TODAY), unmasked=True).decode()

        assert "<svg" not in page
        assert "There is no history yet" in page

    def test_PartialMonthsAreMarkedAndTheCompletePointIsStated(self):
        late = AssetInput("late", (Observation(date(2026, 9, 1), "other", 100, None, "n"),))
        early = AssetInput("early", (Observation(date(2026, 7, 1), "other", 200, None, "n"),))
        page = render_position(
            build_position([], [early, late], today=self.TODAY), unmasked=True
        ).decode()

        assert page.count('class="pill pill-warn"') == 2, "July and August are partial"
        assert "complete from <strong>2026-09</strong>" in page


def every_account_counted(store: Store) -> None:
    household(store)
    record_stated_anchor(store, "unanchored", "2026-03-31", "1000.00")


class TestWhatAnUncountedAccountHasMoved:
    def test_Post_ShowsTheMovementWordedSoItCannotBeReadAsABalance(self, lab):
        page = lab.show_values().text
        card = page.split("<h2>Not counted: no opening balance</h2>")[1].split("<h2>")[0]

        assert (
            'Moved: in <span class="mono nowrap">£888.88</span> since 2026-03-01 '
            "(its first row here). Its balance is this plus an opening balance that is "
            "not known." in card
        )
        assert "in credit" not in card and "Balance" not in card

    def test_Get_MasksTheMovementToTheFixedTokenAndKeepsTheDate(self, lab):
        card = lab.get().text.split("<h2>Not counted: no opening balance</h2>")[1].split("<h2>")[0]

        assert f'Moved: in <span class="mono nowrap">{MASKED_TOTAL}</span> since 2026-03-01' in card

    def test_AnAccountWithNoRows_ShowsNoMovementLine(self, tmp_path, monkeypatch):
        def dormant(store: Store) -> None:
            household(store)
            store.declare_account(AccountRecord(ref=AccountRef("dormant"), label="Dormant"))

        httpd, lab = serve(tmp_path, monkeypatch, dormant)
        try:
            page = lab.show_values().text
        finally:
            httpd.shutdown()

        assert page.count("Moved: ") == 1, "only unanchored has moved"
        assert "2 accounts are not counted" in page
        assert "2 unknown opening balances" in provisional_paragraph(page)


class TestTheProvisionalTotalOnThePage:
    def test_Post_SitsBelowTheRealNetWorthWithItsCaveat(self, lab):
        page = lab.show_values().text
        headline = page.split("<h2>Net worth</h2>")[1].split("<h2>")[0]

        assert headline.index("£269,359.87") < headline.index("Provisional, counting every account")
        assert (
            'Provisional, counting every account: in credit <span class="mono nowrap">'
            "£270,248.75</span>. This takes 1 unknown opening balance as nil, so it is off by "
            "whatever that account held before its history began. The movement is real; "
            "the level is not." in provisional_paragraph(page)
        )

    def test_Post_TheRealNetWorthAndSubtotalsAreUnchanged(self, lab):
        page = lab.show_values().text

        assert "in credit <span class=\"mono nowrap\">£269,359.87</span>" in page
        assert "Counts 4 accounts of 5 and 2 assets." in page
        assert "1 account is not counted" in page
        for figure in ("£6,559.87", "£900.00", "£1,700.00", "£262,000.00"):
            assert figure in page, figure

    def test_Caveat_NamesTheCountOfUnknownOpeningsAndSaysNil(self, lab):
        sentence = provisional_paragraph(lab.show_values().text)

        assert "1 unknown opening balance" in sentence
        assert "nil" in sentence

    def test_Get_MasksTheProvisionalTotalButKeepsItsCaveat(self, lab):
        sentence = provisional_paragraph(lab.get().text)

        assert MASKED_TOTAL in sentence
        assert "1 unknown opening balance" in sentence and "nil" in sentence

    @pytest.mark.parametrize(
        "query", ["values=1", "unmask=1", "show=1", "unmasked=true", "masked=0"]
    )
    def test_Get_WithAnyParameterOneMightTry_MasksEveryProvisionalFigureAndDrawsNoChart(
        self, lab, query
    ):
        key, value = query.split("=")

        page = lab.get(**{key: value}).text

        assert_no_secret(page, where=query)
        assert MASKED_TOTAL in provisional_paragraph(page)
        assert "<svg" not in page and chart_series(page) == set()

    def test_Post_MonthTableGainsAProvisionalColumnWithTheWorkedFigures(self, lab):
        page = lab.show_values().text
        table = page.split("<table>")[1].split("</table>")[0]

        assert "Provisional total" in table
        for figure in ("£18,144.43", "£268,170.98", "£270,248.75"):
            assert figure in table, figure
        assert "complete from <strong>2026-04</strong>" in page
        assert "6 of 6" in table and "5 of 6" in table

    def test_Get_MonthTableColumnIsMasked(self, lab):
        table = lab.get().text.split("<table>")[1].split("</table>")[0]

        assert "Provisional total" in table
        assert table.count(MASKED_TOTAL) >= 22, "both columns, eleven months each"

    def test_Post_ChartDrawsTwoDistinguishableSeriesAndALegendSaying(self, lab):
        page = lab.show_values().text

        assert chart_series(page) == {"known", "provisional"}
        known = re.search(r'<polyline[^>]*data-series="known"[^>]*>', page)
        dotted = re.search(r'<polyline[^>]*data-series="provisional"[^>]*>', page)
        assert known and dotted
        assert 'stroke-dasharray="1 5"' in dotted.group(0), "dotted, not dashed"
        assert 'stroke-dasharray="1 5"' not in known.group(0)
        assert "The solid blue line is the net worth that is known" in page
        assert "The dotted line is the provisional total" in page
        assert (
            "The provisional line's shape shows real movement; its height is offset by the "
            "unknown opening balances." in page
        )

    def test_Post_ChartWithNothingCounted_DrawsTheProvisionalLineAlone(self, tmp_path, monkeypatch):
        from test_ledger import land, txn

        def one_row(store: Store) -> None:
            land(store, "d", txn("a1", "s", "1", date(2026, 3, 1), 12345, "ROW"))

        httpd, lab = serve(tmp_path, monkeypatch, one_row)
        try:
            page = lab.show_values().text
        finally:
            httpd.shutdown()

        assert chart_series(page) == {"provisional"}
        assert "The solid blue line" not in page
        assert "highest" in page or "all months £123.45" in page


class TestNothingProvisionalWhenEveryAccountIsCounted:
    def test_Page_NeverMentionsAProvisionalFigureColumnOrLine(self, tmp_path, monkeypatch):
        httpd, lab = serve(tmp_path, monkeypatch, every_account_counted)
        try:
            masked, shown = lab.get().text, lab.show_values().text
        finally:
            httpd.shutdown()

        for page in (masked, shown):
            assert "provisional" not in page.lower()
            assert "Moved: " not in page
            assert "unknown opening" not in page
        assert chart_series(shown) == {"known"}
        assert "£270,359.87" in shown


class TestTheLimitsAndTheAnswerAboutOlderStatements:
    def test_Page_SaysAProvisionalFigureTreatsUnknownOpeningsAsNil(self, lab):
        page = lab.get().text
        limits = page.split("What this page does not check")[1]

        assert "provisional figure treats unknown opening balances as nil" in limits

    def test_Page_SaysStatingABalanceFixesItAtThatDateSoOlderStatementsStayRight(self, lab):
        page = lab.get().text

        assert (
            "Stating a balance for a date fixes the balance at that date, so importing older "
            "statements later does not make it wrong: the opening balance moves back in time "
            "and is re-derived from the same stated figure." in page
        )


class TestTheChartsProvisionalEdgeCases:
    TODAY = date(2026, 10, 2)

    def rendered(self, *rows_sets: list[tuple[date, int]]) -> str:
        from obdi.balance_anchors import derive_opening
        from test_ledger import txn

        inputs = []
        for index, spec in enumerate(rows_sets):
            rows = tuple(
                txn(f"u{index}", "s", f"{index}-{n}", day, minor, "ROW")
                for n, (day, minor) in enumerate(spec)
            )
            ref = f"u{index}"
            inputs.append(
                AccountInput(ref, ref.upper(), "", False, derive_opening(ref, [], rows), rows)
            )
        return render_position(build_position(inputs, [], today=self.TODAY), unmasked=True).decode()

    def test_OnePointOfProvisional_IsADotAndNotADivisionByZero(self):
        page = self.rendered([(date(2026, 10, 1), 4242)])

        assert "<circle" in page
        assert "<polyline" not in page
        assert "nan" not in page.lower().replace("financial", "")

    def test_AllEqualProvisionalValues_IsAFlatLine(self):
        page = self.rendered([(date(2026, 8, 5), 5000)])

        assert "<polyline" in page
        assert "all months £50.00" in page

    def test_AProvisionalRangeCrossingZero_DrawsANilBaseline(self):
        page = self.rendered([(date(2026, 8, 5), -5000), (date(2026, 9, 5), 9000)])

        assert ">nil</text>" in page
        assert "lowest -£50.00" in page


class TestAnUnwiredDeployment:
    def test_Page_SaysNothingIsWiredAndStillResolvesLikeEveryStripDestination(self, tmp_path):
        from obdi.connections import ConnectionStore
        from obdi.web import WebConfig

        config = WebConfig(
            client_id="c",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            for method in (httpx.get, httpx.post):
                response = method(f"http://127.0.0.1:{httpd.server_port}/position", timeout=20)
                assert response.status_code == 200
                assert "no position wired" in response.text
        finally:
            httpd.shutdown()


class TestNavigation:
    def test_Navigation_OffersThePositionPageAndMarksItCurrentThere(self, lab):
        page = lab.get().text

        assert '<li><a href="/position" aria-current="page">Position</a></li>' in page

    def test_Overview_LinksToThePositionPage(self, lab):
        assert 'href="/position"' in lab.get("/").text
