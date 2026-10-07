"""Coverage by source: which sources feed each account, and how far each reaches.

The owner met a page ten phone screens tall: one block for each (source, account) PAIR, so an
account fed by four sources appeared four times and scattered, each block carrying its own
archive form, a sentence saying several sources feed it, and a ten-year bar that was a sliver at
the right-hand end. These read the rebuilt page over the invented household of
`coverage_page_world` (19 accounts, 28 pairs, 4 archived Spaces; every answer written there before
the first run) and hold the shape he asked for: one block per account, a grid he can read at a
glance, the archive action gone to the account's own page, and no sentence said once for every
account.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

import coverage_page_world as world
from obdi.core.page_times import date_with_age
from obdi.read.coverage_timeline import AGGREGATOR, EXPORT, FEED, KIND_NAMES, STATEMENT
from page_dom import Node, elements, inside, parse
from section_harness import config, environment, serve_config


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("coverage-page")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    mp = pytest.MonkeyPatch()
    environment(mp, root)
    world.write_map(root)
    db = root / "store.sqlite3"
    world.build(db)
    address, stop = serve_config(config(db))
    try:
        yield address
    finally:
        stop()
        mp.undo()


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, root)


@pytest.fixture(scope="module")
def html(base: str) -> str:
    response = httpx.get(f"{base}/coverage", timeout=60)
    assert response.status_code == 200
    return response.text


@pytest.fixture(scope="module")
def dom(html: str) -> Node:
    return parse(html)


def blocks(dom: Node) -> dict[str, Node]:
    found: dict[str, Node] = {}
    for node in elements(dom, "section"):
        if "cov-account" in node.classes:
            ref = node.attrs["data-ref"]
            assert ref not in found, f"{ref} has two blocks"
            found[ref] = node
    return found


def own_text(block: Node) -> str:
    """The block's own words, leaving out the Spaces nested under it."""
    parts = [
        child.text()
        for child in block.children
        if isinstance(child, Node) and "cov-spaces" not in child.classes
    ]
    return " ".join(parts)


def source_rows(block: Node) -> list[list[Node]]:
    """The cells of each source line of this block's own table, not of a Space nested under it."""
    for table in elements(block, "table"):
        owner = next(a for a in table.ancestors() if "cov-account" in a.classes)
        if "cov-sources" in table.classes and owner is block:
            return [
                [cell for cell in row.children if isinstance(cell, Node)]
                for row in elements(table, "tr")
                if any(isinstance(c, Node) and c.tag == "td" for c in row.children)
            ]
    return []


def grid(dom: Node) -> Node:
    return next(t for t in elements(dom, "table") if "cov-grid" in t.classes)


def grid_rows(dom: Node) -> dict[str, list[Node]]:
    rows: dict[str, list[Node]] = {}
    for row in elements(grid(dom), "tr"):
        head = next((c for c in row.children if isinstance(c, Node) and c.tag == "th"), None)
        cells = [c for c in row.children if isinstance(c, Node) and c.tag == "td"]
        if head is not None and cells:
            rows[head.text()] = cells
    return rows


TODAY = world.today()


def day(ago: int) -> date:
    return TODAY - timedelta(days=ago)


class TestOneBlockForEachAccount:
    def test_Page_AnAccountFedByFourSources_AppearsOnce(self, dom):
        found = blocks(dom)

        assert len(found) == len(world.ACCOUNTS) == 19
        assert "main" in found

    def test_Page_AnAccountFedByFourSources_ListsItsSourcesTogether(self, dom):
        rows = source_rows(blocks(dom)["main"])

        names = [row[0].text() for row in rows]
        assert names == [world.FEED, world.AGGREGATOR, world.EXPORT, world.STATEMENT]

    def test_Page_ASourceLine_GivesTransactionCountAndFirstToLastDay(self, dom):
        rows = {r[0].text(): r for r in source_rows(blocks(dom)["main"])}

        feed = rows[world.FEED]
        assert feed[1].text() == "15"
        assert feed[2].text() == f"{day(400).isoformat()} to {day(1).isoformat()}"

    def test_Page_ASourceLine_SaysHowLongAgoALastDayWasWhereItIsOld(self, dom):
        rows = {r[0].text(): r for r in source_rows(blocks(dom)["main"])}

        assert rows[world.EXPORT][2].text() == (
            f"{day(700).isoformat()} to {date_with_age(day(130), TODAY)}"
        )
        assert "ago" in rows[world.EXPORT][2].text()
        assert "ago" not in rows[world.FEED][2].text()

    def test_Page_AnAccountWithOneSource_StillShowsItsOneLine(self, dom):
        rows = source_rows(blocks(dom)["wallet"])

        assert [r[0].text() for r in rows] == [world.FEED]

    def test_Page_TheAccountsName_IsItsLabelWithTheReferenceAsCodeOnce(self, dom):
        heading = next(elements(blocks(dom)["main"], "h3"))

        assert heading.text() == "Joint current main by day"
        codes = list(elements(heading, "code"))
        assert [c.text() for c in codes] == ["main"]

    def test_Page_TheAccountsName_LinksToItsOwnPage(self, dom):
        heading = next(elements(blocks(dom)["main"], "h3"))
        link = next(elements(heading, "a"))

        assert link.attrs["href"] == "/ledger?ref=main"

    def test_Page_EachAccountLinksToItsOwnCoverageTimeline(self, dom):
        heading = next(elements(blocks(dom)["main"], "h3"))
        links = [a.attrs["href"] for a in elements(heading, "a")]

        assert "/coverage-timeline?ref=main" in links

    def test_Page_NoSentenceSaysSeveralSourcesFeedAnAccount(self, html):
        assert "several sources feed" not in html


class TestTheGlanceGrid:
    def test_Summary_SaysHowManyAccountsSourcesAndAccountsBehind(self, dom):
        summary = next(p for p in elements(dom, "p") if "cov-summary" in p.classes)

        assert summary.text() == (
            "19 accounts, 4 of them archived, are fed by 4 sources. "
            "3 accounts have a source that has fallen behind the others."
        )

    def test_Grid_HasOneRowForEachAccountThatIsNotArchived(self, dom):
        rows = grid_rows(dom)

        assert len(rows) == len(world.LIVE) == 15
        assert "Joint current" in rows
        assert "Old A" not in rows

    def test_Grid_HasOneColumnForEachKindOfSourceHeldAndNoOther(self, dom):
        heads = [c.text() for c in elements(grid(dom), "th") if c.attrs.get("scope") == "col"]

        assert heads == [
            "Account",
            KIND_NAMES[FEED],
            KIND_NAMES[AGGREGATOR],
            KIND_NAMES[EXPORT],
            KIND_NAMES[STATEMENT],
        ]

    def test_Grid_ACell_IsTheLastDayThatKindOfSourceHoldsForTheAccount(self, dom):
        main = grid_rows(dom)["Joint current"]

        assert main[0].text() == day(1).isoformat()
        assert main[1].text() == day(2).isoformat()
        assert main[2].text() == date_with_age(day(130), TODAY)
        assert main[3].text() == date_with_age(day(35), TODAY)

    def test_Grid_ForAnAccountWithNoSourceOfAKind_TheCellIsEmpty(self, dom):
        rows = grid_rows(dom)

        assert rows["Rainy day"][0].text() == ""
        assert rows["Wallet"][1].text() == ""

    def test_Grid_MarksExactlyTheSourcesWellBehindTheNewest(self, dom):
        marked = {
            (name, index)
            for name, cells in grid_rows(dom).items()
            for index, cell in enumerate(cells)
            if "cov-behind" in cell.classes
        }

        assert marked == {("Joint current", 2), ("Rainy day", 2), ("Joint savings", 2)}

    def test_Grid_AStatementThirtyFourDaysBehind_IsOrdinaryNotAMark(self, dom):
        assert "cov-behind" not in grid_rows(dom)["Joint current"][3].classes

    def test_Grid_AQuietAccountWhoseSourcesAllStoppedTogether_IsNotMarked(self, dom):
        cells = grid_rows(dom)["Old card"]

        assert all("cov-behind" not in c.classes for c in cells)

    def test_Grid_Key_NamesTheMarkAndTheThresholdOnce(self, dom):
        keys = [p for p in elements(dom, "p") if "cov-gridkey" in p.classes]

        assert len(keys) == 1
        assert f"{world.BEHIND_DAYS} days" in keys[0].text()

    def test_Grid_EachAccountNameLinksToItsBlockLowerDown(self, dom):
        row_link = next(
            a for a in elements(grid(dom), "a") if a.text() == "Joint current"
        )

        target = row_link.attrs["href"].removeprefix("#")
        assert blocks(dom)["main"].attrs["id"] == target


class TestTheGroups:
    def test_Page_FedAccounts_ComeFirstInNameOrderWithSpacesUnderTheirParent(self, dom):
        found = blocks(dom)
        main = found["main"]

        for space in ("main-bills", "main-holiday", "main-trip"):
            assert main in list(found[space].ancestors())
        top_names = [
            next(elements(found[ref], "h3")).text().rsplit(" ", 3)[0]
            for ref in ("bills-card", "card", "loan", "everyday", "holiday-fund", "main")
        ]
        assert top_names == [
            "Bills card", "Blue card", "Car loan", "Everyday", "Holiday fund", "Joint current",
        ]

    def test_Page_QuietAccounts_AreGroupedApartAfterTheFedOnes(self, dom):
        headings = [h.text() for h in elements(dom, "h2")]
        found = blocks(dom)

        assert "Accounts being fed" in headings
        assert "Quiet accounts" in headings
        assert headings.index("Accounts being fed") < headings.index("Quiet accounts")
        order = [b.attrs["data-ref"] for b in elements(dom, "section") if "cov-account" in b.classes]  # noqa: E501
        assert order.index("wallet") < order.index("old-card") < order.index("isa-old")
        assert found["old-card"] is not None

    def test_Page_ArchivedAccounts_AreOneClosedFoldAtTheFootHoldingEachOnce(self, dom):
        folds = [d for d in elements(dom, "details") if "cov-archived" in d.classes]

        assert len(folds) == 1
        fold = folds[0]
        assert "open" not in fold.attrs
        inside_fold = {
            b.attrs["data-ref"] for b in elements(fold, "section") if "cov-account" in b.classes
        }
        assert inside_fold == {a.ref for a in world.ARCHIVED}
        outside = set(blocks(dom)) - inside_fold
        assert outside == {a.ref for a in world.LIVE}

    def test_Page_ArchivedFold_ComesAfterEveryBlockThatIsNotArchived(self, dom):
        order = [
            (inside(b, "details"), b.attrs["data-ref"])
            for b in elements(dom, "section")
            if "cov-account" in b.classes
        ]
        flags = [folded for folded, _ in order]

        assert flags == sorted(flags), "an archived block sits between two that are not"

    def test_Page_ArchivedFold_SaysHowManyItHolds(self, dom):
        fold = next(d for d in elements(dom, "details") if "cov-archived" in d.classes)
        summary = next(elements(fold, "summary"))

        assert summary.text() == "Archived accounts and Spaces (4)"


class TestNothingToDoHere:
    def test_Page_HoldsNoArchiveOrUnarchiveForm(self, dom):
        actions = {f.attrs.get("action", "") for f in elements(dom, "form")}

        assert not actions & {"/archive-account", "/unarchive-account"}

    def test_Page_HoldsAtMostOneForm(self, dom):
        assert len(list(elements(dom, "form"))) <= 1

    def test_Page_AnArchivedAccount_LinksToThePageWhereItCanBeUnarchived(self, base, dom):
        link = next(elements(next(elements(blocks(dom)["main-old-a"], "h3")), "a"))

        page = httpx.get(base + link.attrs["href"], timeout=60).text

        assert "/unarchive-account" in page

    def test_Page_ALiveAccount_LinksToThePageWhereItCanBeArchived(self, base, dom):
        link = next(elements(next(elements(blocks(dom)["wallet"], "h3")), "a"))

        page = httpx.get(base + link.attrs["href"], timeout=60).text

        assert 'action="/archive-account"' in page

    def test_Page_AnArchivedAccount_SaysWhenItWasArchivedAndHowThatIsKnown(self, dom):
        assert "archived" in own_text(blocks(dom)["main-old-b"])


class TestFinalMovements:
    def test_Page_AnArchivedSpaceWithMovementsMissing_SaysWhatIsMissingAndWhatToDo(self, dom):
        text = own_text(blocks(dom)["main-old-a"])

        assert "1 transfer" in text
        assert "Joint current" in text
        assert "last movements" in text
        assert "open the account" in text.lower()

    @pytest.mark.parametrize("ref", ["main-old-b", "main-old-c", "main-old-d"])
    def test_Page_AnArchivedSpaceWithNothingMissing_SaysNothingAboutIt(self, dom, ref):
        text = own_text(blocks(dom)[ref])

        assert "transfer" not in text
        assert "movements" not in text

    def test_Page_NeverQuotesTheMeaningOfANonZeroCount(self, html):
        assert "A non-zero count means" not in html
        assert "Final movements not held" not in html


class TestBindings:
    def test_Page_ProviderIds_AreOnlyEverInsideAClosedFold(self, dom):
        codes = [c for c in elements(dom, "code") if "tl-main" in c.text()]

        assert codes
        for code in codes:
            fold = next(a for a in code.ancestors() if a.tag == "details")
            assert "open" not in fold.attrs

    def test_Page_TwoProvidersForOneAccount_IsNotWarnedAbout(self, dom):
        warned = [n for n in elements(blocks(dom)["main"], "p") if "warn" in n.classes]

        assert warned == []

    def test_Page_TwoAccountsOfOneProviderMergedIntoOne_IsWarnedAboutBesideTheAccount(self, dom):
        block = blocks(dom)["everyday"]
        warned = [n for n in elements(block, "p") if "warn" in n.classes]

        assert len(warned) == 1
        assert not inside(warned[0], "details")
        assert world.FEED in warned[0].text()
        assert "2" in warned[0].text()

    def test_Page_TheWarning_AppearsForNoOtherAccount(self, dom):
        warned = {
            ref
            for ref, block in blocks(dom).items()
            if any("warn" in n.classes for n in elements(block, "p"))
        }

        assert warned == {world.FAULTY_BINDING}


def _row(account, source, first, last, count=3):
    from obdi.verify.coverage import SourceCoverage

    return SourceCoverage(
        account_id=account, source=source, count=count, earliest=first, latest=last,
        inflow_minor=0, outflow_minor=0, with_durable_id=0,
    )


class TestUnusualHouseholds:
    FIXED = date(2026, 10, 5)

    def _page(self, **hooks):
        from obdi.web_sections import render_coverage

        return parse(render_coverage(today=self.FIXED, **hooks).decode())

    def test_Grid_WhenTwoExportsFeedOneAccount_ShowsTheLaterDayInOneCell(self):
        rows = [
            _row("acc", "bank-csv", date(2025, 1, 1), date(2026, 3, 1)),
            _row("acc", "other-qif", date(2025, 1, 1), date(2026, 9, 30)),
        ]

        page = self._page(holdings=lambda: rows)

        cells = [c.text() for c in elements(grid(page), "td")]
        assert cells == ["2026-09-30"]

    def test_Page_ForAnAccountWithNoNameButItsReference_SaysTheReferenceOnce(self):
        rows = [_row("plain-ref", "bank-csv", date(2026, 9, 1), date(2026, 9, 30))]

        page = self._page(holdings=lambda: rows)

        heading = next(elements(page, "h3"))
        assert heading.text() == "plain-ref by day"
        assert [c.text() for c in elements(heading, "code")] == ["plain-ref"]

    def test_Page_WhenEveryHookRaises_StillRendersWithNothingToShow(self):
        def boom():
            raise RuntimeError("store mid-write")

        page = self._page(
            holdings=boom, account_names=boom, account_feeders=boom, archive_notes=boom
        )

        assert "not wired" in page.text()

    def test_Page_WhenNothingIsBehind_SaysSo(self):
        rows = [_row("acc", "bank-csv", date(2026, 9, 1), date(2026, 9, 30))]

        page = self._page(holdings=lambda: rows)

        summary = next(p for p in elements(page, "p") if "cov-summary" in p.classes)
        assert summary.text() == (
            "1 account is fed by 1 source. No account has a source that has fallen behind "
            "the others."
        )

    def test_Page_AKnownAccountHoldingNothing_IsListedAsQuietWithAReasonToNameIt(self):
        rows = [_row("acc", "bank-csv", date(2026, 9, 1), date(2026, 9, 30))]

        page = self._page(
            holdings=lambda: rows, account_timelines=lambda: {"starling:b2cec056": {}}
        )

        assert "Nothing is held for this account yet" in page.text()
        assert any(f.attrs.get("action") == "/bind" for f in elements(page, "form"))


class TestWhatIsLeftIsNotSaidTwice:
    def test_Page_NoLineOfThreeWordsIsRepeatedMoreThanTwice(self, dom):
        assert world.repeated_lines(dom) == {}

    def test_Page_NeverSaysRowsAndHasNoAmountOrPayee(self, html):
        text = parse(html).text().lower()

        assert " rows" not in text
        assert "payee" not in text

    def test_Page_Words_StayUnderTheMeasuredBound(self, dom):
        # Measured over this household: 560 words (the page it replaced: 967, 432 of them in
        # lines said three or more times); the bound is that and a little more, so a sentence said
        # once for each of 19 accounts cannot return unnoticed.
        assert len(dom.text().split()) < 620
