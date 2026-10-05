"""The What to fetch next page: its sentences, order, links, and what it must not show.

The household and every gap in it are decided in `fetch_gaps_world`'s docstring; the page's
verdict for it is decided here before the first run: 13 things to fetch for 11 accounts, 2 accounts
needing nothing. The page is drawn from the data (`render_gaps`), and once over HTTP to show
the route, the hook, and the shared navigation are joined up.
"""

from __future__ import annotations

import html
import re
import urllib.request
from datetime import date

import pytest

from fetch_gaps_world import TODAY, Loaded, load_household
from obdi.account_names import AccountsShown, accounts_shown
from obdi.fetch_gaps import AccountOutlook, Basis, FetchGap, FetchReport, GapKind
from obdi.store import Store
from obdi.web_gaps import render_gaps, verdict_sentence

D = date
MONEY_FIGURE = re.compile(r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return load_household(tmp_path_factory.mktemp("gaps-page"))


@pytest.fixture(scope="module")
def repaired(tmp_path_factory):
    return load_household(tmp_path_factory.mktemp("gaps-page-repaired"), repaired=True)


def names_of(loaded: Loaded) -> AccountsShown:
    with Store(loaded.db) as store:
        return accounts_shown({}, store.declared_accounts())


def page_of(loaded: Loaded) -> str:
    return render_gaps(loaded.report, names_of(loaded)).decode()


def block(page: str, label: str) -> str:
    """The section of one account, from its heading to the next section."""
    sections = re.split(r'(?=<section class="gaps-account")', page)
    return next(s for s in sections if f'aria-label="{label}"' in s)


def text(markup: str) -> str:
    """The words of `markup`, as read: tags gone, entities decoded, no space before a stop."""
    words = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(markup))).strip()
    return re.sub(r" ([.,;:])", r"\1", words)


class TestTheVerdict:
    def test_Page_WhenGapsExist_LeadsWithTheCountOfThingsAndAccounts(self, world):
        assert verdict_sentence(world.report) == (
            "13 things to fetch for 11 accounts; 2 accounts need nothing."
        )
        assert "13 things to fetch for 11 accounts; 2 accounts need nothing." in page_of(world)

    def test_Verdict_WhenOneOfEachIsCounted_AgreesInNumber(self):
        gap = FetchGap("a", GapKind.NO_BALANCE, D(2026, 8, 1), D(2026, 8, 2), Basis.STATED, "", "")
        report = FetchReport((AccountOutlook("a", (gap,)), AccountOutlook("b", ())), TODAY)

        assert verdict_sentence(report) == (
            "1 thing to fetch for 1 account; 1 account needs nothing."
        )

    def test_Verdict_WhenNothingIsNeeded_SaysSoAndWhenTheNextStatementIsExpected(self, repaired):
        quiet = FetchReport(tuple(o for o in repaired.report.accounts if not o.gaps), TODAY)

        assert verdict_sentence(quiet) == (
            "Nothing to fetch for 7 accounts. The next statement is expected about 2026-10-10."
        )

    def test_Verdict_WhenNoAccountHoldsRows_SaysThereIsNothingToFetch(self):
        assert verdict_sentence(FetchReport((), TODAY)) == (
            "No account holds rows yet, so there is nothing to fetch."
        )

    def test_Verdict_WhenSomethingIsNeededAndNothingElseIs_OmitsTheClauseAboutRest(self):
        gap = FetchGap("a", GapKind.NO_BALANCE, D(2026, 8, 1), D(2026, 8, 2), Basis.STATED, "", "")

        assert verdict_sentence(FetchReport((AccountOutlook("a", (gap,)),), TODAY)) == (
            "1 thing to fetch for 1 account."
        )


class TestTheSentences:
    def test_Card_WhenNewestStatementsAreMissing_SaysWhatToFetchWhyAndWhatIsInferred(self, world):
        said = text(block(page_of(world), "Behind card"))

        assert "2026-07-11 to 2026-10-05" in said
        assert "Fetch every statement after 2026-07-10." in said
        assert "Rows are held to 2026-10-05 with no known balance since 2026-07-10." in said
        assert (
            "Probably 2 statements are waiting, closing about 2026-08-10 and 2026-09-10. "
            "That is inferred from how regularly the statements held arrive."
        ) in said
        assert "Statement read as santander-cc-pdf stated count inferred" in said

    def test_Card_WhenOnlyOneStatementIsHeld_SaysNothingAboutHowManyAreWaiting(self, world):
        said = text(block(page_of(world), "Late-one card"))

        assert "Fetch every statement after 2026-06-10." in said
        assert "Probably" not in said

    def test_Card_WhenAHoleIsInferred_IsDrawnDashedAndSaysItIsAGuess(self, world):
        section = block(page_of(world), "Hole card")

        assert "gaps-inferred" in section and "gaps-stated" not in section
        said = text(section)
        assert "Fetch the statement closing between 2026-03-10 and 2026-05-10." in said
        # The two statements' balances meet, so this is a probable hole whose movements net to
        # nil, and the sentence says what the balances do and do not prove.
        assert "close 61 days apart" in said
        assert "would have to net to nil" in said
        assert "cannot be ruled out from the balances" in said
        assert "Probably 1 statement is missing here, closing about 2026-04-10." in said

    def test_Card_WhenAHoleIsStatedByTheStatement_IsDrawnSolidAndSaysWhatTheStatementSays(
        self, world
    ):
        section = block(page_of(world), "Virgin card")

        assert "gaps-stated" in section and "gaps-inferred" not in section
        said = text(section)
        assert "Fetch the statement covering 2026-06-05 to 2026-07-04." in said
        assert "The statement after it says its period begins on 2026-07-05" in said
        assert "Probably" not in said

    def test_MainAccount_WhenItsExportStops_SaysFromWhenToToday(self, world):
        said = text(block(page_of(world), "Main account"))

        assert "Export from 2026-08-05 to today." in said
        assert "The export last covers 2026-08-04; other sources hold rows to 2026-10-01." in said
        assert "Export 2026-03-01 to 2026-03-31." in said
        assert "Export read as starling-csv stated" in said

    def test_Card_WhenRowsPrecedeTheFirstStatement_NamesTheDaysAndTheClosingNeeded(self, world):
        said = text(block(page_of(world), "Early card"))

        assert (
            "Rows from 2026-06-01 to 2026-08-04 have no known balance before them, so they "
            "cannot be tested."
        ) in said
        assert "Fetch an earlier statement, closing on or before 2026-05-31." in said

    def test_Accounts_WhenNothingIsKnown_UseTheAccountsPagesWords(self, world):
        said = text(block(page_of(world), "Qif card"))
        assert "No known balance, so there is nothing to check the transactions against." in said
        assert "Fetch a statement covering 2026-08-03 to 2026-08-20, or state a balance." in said
        automatic = text(block(page_of(world), "Feed-only card"))
        assert "Only the bank's feed and the aggregator supply this account" in automatic

    def test_Account_WhenOneKnownBalanceIsHeld_SaysOnlyOneAndWhatSetsTheOpening(self, world):
        said = text(block(page_of(world), "Single card"))

        assert "Only one known balance is held" in said
        assert "closing on or before 2026-09-04, or the one after them" in said

    def test_Account_WhenAFlagWouldBeSettled_CarriesTheFlagsWordsAndLinksToTheFlags(self, world):
        section = block(page_of(world), "Card opens-on-day")

        assert "No known balance before 2026-09-14: a statement covering it would settle this." in (
            text(section)
        )
        assert 'href="/review-flags"' in section


class TestWhatNeedsNothing:
    def test_Page_ListsTheQuietAccountsWithWhenTheNextStatementIsExpected(self, world):
        said = text(page_of(world).split("Needs nothing</h2>")[1])

        assert "Quiet card" in said and "next statement expected about 2026-10-10" in said

    def test_Page_ForABalanceOnlyAccount_OffersToStateABalanceAndNoFile(self, world):
        page = page_of(world)
        line = next(
            part for part in page.split('<li class="gaps-quiet-item">') if "Hand savings" in part
        )

        assert "balances are stated by hand" in text(line)
        assert "State a balance" in line and "#opening" in line
        assert "Upload a statement" not in line

    def test_Page_NeverListsAnArchivedAccount(self, world):
        assert "Old card" not in page_of(world) and "card-old" not in page_of(world)

    def test_Page_WhenAnAccountNeedsNothing_HasNoBlockForIt(self, world):
        page = page_of(world)

        assert 'aria-label="Quiet card"' not in page


class TestOrderAndLinks:
    def test_Page_ListsTheMostUrgentAccountFirst(self, world):
        page = page_of(world)
        order = ("Late-one card", "Behind card", "Hole card", "Virgin card", "Main account")
        positions = [page.index(f'aria-label="{label}"') for label in order]

        assert positions == sorted(positions)

    def test_Block_ForAStatementGap_LinksToTheAccountAndToUploadAStatement(self, world):
        section = block(page_of(world), "Behind card")

        assert 'href="/ledger?ref=card-behind"' in section
        assert 'href="/statement-shape"' in section
        assert 'href="/import"' not in section

    def test_Block_ForAnExportGap_LinksToImportAnExportAndNotUploadAStatement(self, world):
        section = block(page_of(world), "Main account")

        assert 'href="/import"' in section
        assert 'href="/statement-shape"' not in section

    def test_Block_WhereNothingIsKnown_OffersToStateABalance(self, world):
        section = block(page_of(world), "Feed-only card")

        assert 'href="/ledger?ref=card-feed-only#opening"' in section

    def test_Gap_WhenTheTimelinePageIsServed_LinksToItWithAWindowAroundTheGap(
        self, world, monkeypatch
    ):
        monkeypatch.setattr(
            "obdi.web_destinations.dispatcher_serves", lambda route: route == "/coverage-timeline"
        )
        section = block(page_of(world), "Main account")

        assert section.count("See it on the coverage timeline") == 2
        assert (
            "/coverage-timeline?ref=main&amp;window=between&amp;window_from=2026-02-15"
            "&amp;window_to=2026-04-14"
        ) in section
        # A gap that runs to today is not given a window that runs past it.
        assert "window_from=2026-07-22&amp;window_to=2026-10-05" in section

    def test_Gap_WhenTheTimelinePageIsNotServed_OffersNoLinkToIt(self, world, monkeypatch):
        monkeypatch.setattr("obdi.web_destinations.dispatcher_serves", lambda route: False)

        assert "coverage-timeline" not in page_of(world)


class TestWhatThePageMustNotHold:
    def test_Page_HoldsNoAmountAndNoDescription(self, world):
        page = page_of(world)

        assert MONEY_FIGURE.search(page) is None
        for figure in world.house.figures:
            assert figure not in page, figure
        for payee in world.house.payees:
            assert payee not in page, payee

    def test_Page_RunsNoScript(self, world):
        assert "<script" not in page_of(world)


class TestServed:
    def test_Route_WhenServed_AnswersWithThePageUnderBringIn(self, tmp_path):
        from fetch_gaps_world import build_household
        from served_store import served_store

        build_household(tmp_path)
        with (
            served_store(tmp_path, lambda store: None, bound=[]) as base,
            urllib.request.urlopen(f"{base}/gaps") as response,  # noqa: S310
        ):
            body = response.read().decode()

        assert "<h1>What to fetch next</h1>" in body
        assert 'aria-current="page">Bring in<' in body
        assert "Back to Bring in" in body
        assert "Upload a statement" in body
        for payee in ("Zeppelin", "Lorry"):
            assert payee not in body
