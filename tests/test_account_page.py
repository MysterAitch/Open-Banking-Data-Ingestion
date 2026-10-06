"""The account page over three years of invented rows: its order, its month picker, its sealed
figures, its row anchors, and where its dangerous controls sit.

Every expectation comes from how `account_page_corpus` is built (decided there before any page
read it): HELD is a parent with Spaces, protected through 2025-01-31, held back from 2025-03-31
by a balance stated 12.50 too high, newest month 2026-09 holding fifty rows; AGREEING has the
same shape of history with every balance true and nothing protected; UNKNOWN has rows and no
balance. The page is read over real HTTP, masked by GET and with values by POST.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from account_page_corpus import (
    AGREEING,
    FAULT_DAY,
    HELD,
    NEWEST_MONTH,
    NEWEST_MONTH_ROWS,
    PROTECTED_THROUGH,
    UNKNOWN,
    corpus_environment,
    served_corpus,
)
from obdi.ledger import build_ledger
from obdi.store import Store


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("account-page")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with served_corpus(root) as address:
        yield address


@pytest.fixture(autouse=True)
def _corpus_environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in corpus_environment(root).items():
        monkeypatch.setenv(name, value)


@pytest.fixture(scope="module")
def db(base: str, root: Path) -> Path:
    """The store the server reads, which exists once `base` has built it."""
    return root / "store.sqlite3"


def get(base: str, ref: str = HELD, month: str = "") -> str:
    params = {"ref": ref, **({"month": month} if month else {})}
    response = httpx.get(f"{base}/ledger", params=params, timeout=60)
    assert response.status_code == 200
    return response.text


def shown(base: str, ref: str = HELD, month: str = "") -> httpx.Response:
    return httpx.post(
        f"{base}/ledger", data={"ref": ref, "month": month}, follow_redirects=False, timeout=60
    )


def at(page: str, text: str) -> int:
    assert text in page, f"{text!r} is not on the page"
    return page.index(text)


def said(page: str) -> str:
    """The words of the page: no tags (a date is set in a span that never breaks), entities read."""
    return html.unescape(re.sub(r"<[^>]+>", "", page))


class TestTheOrderOfTheFirstScreens:
    def test_HeldAccount_MaskedPage_RunsNameTrustStripToDoValuesMonthThenTheFiveFolds(
        self, base
    ):
        page = get(base)

        order = [
            at(page, f"<h1>{HELD}</h1>"),
            at(page, 'class="trust bad"'),
            at(page, 'class="tap strip"'),
            at(page, 'class="todos"'),
            at(page, "<h2>Last 30 days, 2026-09-01 to 2026-09-30</h2>"),
            at(page, ">Show values</button>"),
            at(page, '<li class="txn'),
            at(page, "Add a transaction by hand"),
            at(page, "What the bars show, and the full timeline"),
            at(page, "<summary>Known balances ("),
            at(page, "<summary>Locking in ("),
            at(page, "<summary>How this was checked</summary>"),
            at(page, "<summary>Rename or archive</summary>"),
        ]
        assert order == sorted(order)

    def test_AgreeingAccount_MaskedPage_OffersToLockInBeforeTheTransactionsAndHasNoLockToRemove(
        self, base
    ):
        page = get(base, AGREEING)

        assert "Lock in to 2026-09-30" in said(page)
        assert at(page, "Lock in to <span") < at(page, ">Show values</button>")
        assert "Remove the lock" not in page, "nothing to remove"
        assert 'class="trust bad"' not in page and 'class="trust none"' not in page

    def test_Page_NamesTheAccountsOwnNameAsItsHeading_AndKeepsLedgerForTheBrowserTitle(
        self, base
    ):
        page = get(base)

        assert re.search(r"<title>(\[[^\]]*\] )?Ledger</title>", page)
        assert page.count("<h1>") == 1
        assert f"<h1>{HELD}</h1>" in page


class TestTheVerdictAndTheBox:
    def test_HeldAccount_SaysWhatIsLockedWhatAddsUpAndWhereItStopsAddingUp(self, base):
        page = get(base)

        assert f"Does not add up from {FAULT_DAY}." in said(page)
        assert f"Locked in to {PROTECTED_THROUGH}." in said(page)
        assert "Adds up to the known balances to 2025-02-28." in said(page)
        assert 'href="#opening"' in page, "the thing to do links into the same page"

    def test_HeldAccount_SaysWhatStopsItOnceAndLeavesTheKnownBalancesFolded(self, base):
        page = get(base)

        sentence = f"do not add up to the known balance for {FAULT_DAY}"
        assert said(page).count(sentence) == 1, "once, in the thing to do, and not again in a box"
        assert "<details><summary>Known balances (" in page
        assert '<details open><summary>Known balances' not in page, (
            "thirty-five known balances are never opened by a page that is already asking"
        )

    def test_AgreeingAccount_SaysWhatAddsUpAndNothingOfLockingBeyondTheOfferAndLeavesBalancesFolded(
        self, base
    ):
        page = html.unescape(get(base, AGREEING))

        state = said(page[at(page, 'class="acct-state"') : at(page, 'class="acct-txns"')])
        assert "Adds up to the known balances to 2026-09-30." in state
        assert "Locked in to" not in state, "nothing is locked, so the sentence says nothing of it"
        assert "<details><summary>Known balances (35 add up, none differ)</summary>" in page, (
            "36 month ends, the first sets the opening"
        )

    def test_AccountWithNoKnownBalance_SaysSoAndOffersNoLock(self, base):
        page = get(base, UNKNOWN)

        assert 'class="trust none"' in page
        assert "Nothing to check against." in page
        assert 'action="/protect"' not in page
        assert "Known balances (none stated)" in page


class TestTheMonthPicker:
    def test_Page_ListsEveryYearWithRowsAndMarksTheMonthsWithNone(self, base):
        page = get(base)

        years = re.findall(r'<span class="year-label mono">(\d{4})</span>', page)
        assert years == ["2023", "2024", "2025", "2026"]
        assert page.count('<span class="absent">') == 9 + 3, (
            "2023 holds rows from October, and 2026 stops at September"
        )

    def test_Page_GivesEachMonthItsRowCountAsSmallText_AgreeingWithTheLedgerItself(
        self, base, db
    ):
        page = get(base, month=NEWEST_MONTH)
        with Store(db) as store:
            for month in ("2024-02", "2025-03", NEWEST_MONTH):
                held = len(build_ledger(store, HELD, month, bound=True).rows)
                assert (
                    f'aria-label="{_name(month)}, {held} transactions"'
                    f'{_current(month)}>{_abbreviation(month)}<small class="count">{held}</small>'
                ) in page
        assert NEWEST_MONTH_ROWS == 50

    def test_Page_MarksTheMonthOnShow(self, base):
        page = get(base, month=NEWEST_MONTH)
        body = page[at(page, "<body"):]

        assert body.count('aria-current="true"') == 1
        assert 'month=2026-09" aria-label="Sep 2026, 50 transactions" aria-current="true"' in body

    def test_Page_OnAWindowOfDays_MarksNoMonthAsTheOneOnShow(self, base):
        page = get(base)

        assert 'aria-current="true"' not in page[at(page, "<body"):]

    def test_MaskedPicker_IsPlainLinksAndNoForm(self, base):
        page = get(base)
        picker = page[at(page, "Choose a month") : at(page, 'class="txcount"')]

        assert '<a class="tap" href="/ledger?ref=starling-personal&amp;month=2025-03"' in picker
        assert "<form" not in picker and "<button" not in picker

    def test_PickerWithValuesShown_IsPostedButtonsAndNeverAnAddress(self, base):
        response = shown(base, month="2026-09")
        page = response.text
        picker = page[at(page, "Choose a month") : at(page, 'class="txcount"')]

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert '<form method="post" action="/ledger">' in picker
        assert 'name="month" value="2025-03"' in picker
        assert "href=" not in picker, "no month is an address while values are shown"

    def test_PostedPickerButton_AnswersThatMonthWithValuesStillShown(self, base):
        response = shown(base, month="2025-03")

        assert "<h2>2025-03</h2>" in response.text
        assert "VALUES ARE SHOWN" in response.text

    def test_Page_KeepsPreviousAndNextMonthBesideTheMonth(self, base):
        page = get(base, month="2025-03")

        assert "Previous month, 2025-02" in page
        assert "Next month, 2025-04" in page
        newest = get(base)
        assert "Next month" not in newest, "there is nothing after the newest month"


def _current(month: str) -> str:
    return ' aria-current="true"' if month == NEWEST_MONTH else ""


def _name(month: str) -> str:
    return f"{_abbreviation(month)} {month[:4]}"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _abbreviation(month: str) -> str:
    return _MONTHS[int(month[5:7]) - 1]


class TestSealedFigures:
    def test_MaskedPage_SealsEveryRowsFigureAndDescription(self, base):
        page = get(base)

        figures = re.findall(r'<span class="t-fig [^"]*">', page)
        assert len(figures) == NEWEST_MONTH_ROWS
        assert all("sealed" in figure for figure in figures)
        assert page.count('<strong class="txt sealed">') == NEWEST_MONTH_ROWS

    def test_PageWithValuesShown_SealsNothingOnAnyRow(self, base):
        page = shown(base).text

        figures = re.findall(r'<span class="t-fig [^"]*">', page)
        assert len(figures) == NEWEST_MONTH_ROWS
        assert not any("sealed" in figure for figure in figures)
        assert '<strong class="txt">' in page

    def test_SealingChangesNoCharacter_TheMaskedFigureIsStillWhatMaskingMade(self, base):
        page = get(base)

        figure = re.search(r'<span class="t-fig [^"]*">([^<]*)</span>', page)
        assert figure is not None
        assert re.fullmatch(r"(in|out) £[9,]+\.99", figure.group(1)), figure.group(1)


class TestOtherPagesAreUntouchedBySealing:
    @pytest.mark.parametrize(
        "route",
        ["/", "/accounts", "/position", f"/balance-chart?ref={HELD}", f"/account?ref={HELD}"],
    )
    def test_OtherPages_Masked_CarryNoSealClassInTheirMarkup(self, base, route):
        masked = httpx.get(f"{base}{route}", timeout=60)
        assert masked.status_code == 200
        assert 'sealed"' not in masked.text[masked.text.index("<body") :]
        assert " sealed" not in masked.text[masked.text.index("<body") :]


class TestRowAnchors:
    def test_EachRowCarriesAnOpaqueUniqueAnchor(self, base):
        page = get(base)

        anchors = re.findall(r'<li class="txn[^"]*" id="(t-[0-9a-f]{12})"', page)
        assert len(anchors) == NEWEST_MONTH_ROWS
        assert len(set(anchors)) == NEWEST_MONTH_ROWS

    def test_Anchors_AreTheSameMaskedAndShown_SoALinkSurvivesPressingShowValues(self, base):
        masked = re.findall(r'<li class="txn[^"]*" id="(t-[0-9a-f]+)"', get(base))
        revealed = re.findall(r'<li class="txn[^"]*" id="(t-[0-9a-f]+)"', shown(base).text)

        assert masked == revealed

    def test_EachRowsDisclosureOpensWithItsOwnDate_SoNoTwoSummariesReadAlike(self, base):
        page = get(base)

        summaries = re.findall(
            r'<summary class="t-row">(?:(?!</summary>).)*?'
            r'<span class="t-when mono nowrap" title="[^"]*">(\d{4}-\d{2}-\d{2})</span>'
            r'(?:(?!</summary>).)*?<span class="visually-hidden">What each source reported</span>'
            r"</summary>",
            page,
        )
        dates = re.findall(
            r'<span class="t-when mono nowrap" title="[^"]*">(\d{4}-\d{2}-\d{2})', page
        )
        assert summaries == dates and len(dates) == NEWEST_MONTH_ROWS
        assert len(set(dates)) > 20, "the fifty rows are spread over the month"

    def test_ABookedRowThatIsClearedLeavesItsStatusToTheChipThatImpliesIt(self, base):
        page = get(base, AGREEING)

        assert '<span class="pill pill-ok visually-hidden">booked</span>' in page
        assert re.search(
            r'<span class="pill pill-ok" title="[^"]*">cleared by <code>starling</code></span>',
            page,
        )
        # The opposite: it is hidden only where "cleared by" stands in for it, never otherwise.
        assert page.count('visually-hidden">booked</span>') == page.count(">cleared by ")


class TestRemovingAndRetiring:
    def test_Removals_SitInTheKnownBalancesFoldAndNowhereElse(self, base):
        page = httpx.get(f"{base}/ledger", params={"ref": HELD, "balances": "all"}, timeout=60).text

        fold = at(page, "<summary>Known balances (")
        locking = at(page, "<summary>Locking in (")
        removals = [m.start() for m in re.finditer("Remove the known balance for", page)]
        assert len(removals) == 36, "one for each known balance, a month end for 36 months"
        assert all(fold < position < locking for position in removals)
        assert page.count('class="ledger-danger"') == 1
        assert at(page, "Archive this account") > at(page, "<summary>Rename or archive</summary>")

    def test_Removals_OnTheDefaultPage_AreForTheBalancesListedAndALinkToTheRest(self, base):
        page = get(base)

        fold = at(page, "<summary>Known balances (")
        locking = at(page, "<summary>Locking in (")
        removals = [m.start() for m in re.finditer("Remove the known balance for", page)]
        # The opening's balance, ten agreeing ones, and the nineteen that differ.
        assert len(removals) == 1 + 10 + 19
        assert all(fold < position < locking for position in removals)
        assert "Remove an older known balance from the full list" in page[fold:locking]

    def test_RenameOrArchive_IsTheLastThingOnThePage(self, base):
        page = get(base)

        tail = page[at(page, '<details class="ledger-danger">') :]
        assert "Show values" not in tail and "<h2>" not in tail
        assert "/accounts" in tail, "the name is changed where accounts are declared"

    def test_RemovalButton_StillAsksBeforeItActs(self, base):
        response = httpx.post(
            f"{base}/ledger-anchor-remove",
            data={"ref": HELD, "month": NEWEST_MONTH, "day": "2025-04-30"},
            follow_redirects=False,
            timeout=60,
        )

        assert response.status_code == 200
        assert "Are you sure?" in response.text
        assert "Remove the known balance for the end of 2025-04-30" in response.text

    def test_AccountWithNoStatedBalance_HasNoRemovalButton(self, base):
        assert "Remove the known balance for" not in get(base, UNKNOWN)


class TestTheRailOnARow:
    def test_RowSeenByOneFeedWhereTwoFeedTheAccount_CarriesTheAmberRailAndOthersDoNot(self, base):
        page = get(base, AGREEING)

        # Six in seven rows are seen by both feeds; the seventh by the feed alone. A row one
        # source lists is unproven, which is amber; red is kept for rows that disagree.
        doubtful = page.count('class="txn doubtful"')
        assert 0 < doubtful < NEWEST_MONTH_ROWS
        assert page.count(">one source only<") == doubtful
        assert page.count('class="txn flagged"') == 0
        assert page.count('<li class="txn"') == NEWEST_MONTH_ROWS - doubtful
