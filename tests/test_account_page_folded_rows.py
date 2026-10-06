"""A month's copies that are deliberately not counted read as quiet history, not as faults.

The household is decided here before any page reads it. In September 2026 the main account holds:

- two payments both feeds list (the bank's own feed and the aggregator), the plain case;
- one payment only the bank's own feed lists: counted, and sighted by one source of two;
- one transfer the bank's feed calls internal whose other side is held nowhere: counted, and a
  claim nothing confirms;
- three bills paid from a Space, which the aggregator reports under the main account, so the main
  account holds three copies of them that are folded away and counted in the Space.

That is seven rows listed, four counted, three copies not counted. August holds one payment both
feeds list and no copy, so a month without copies reads as it always did.

Red is for rows that disagree and amber for a row to look at (`stylesheet`): a copy is neither, a
counted row one source lists is amber, and a claimed transfer stays red.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from obdi.store import Store
from served_store import environment_for, served_store
from test_space_attribution import AGGREGATOR, BILLS, FEED, MAIN, Household, pay

COPIES_WORDS = "transactions counted elsewhere (held in a Space, or listed by a statement)"
CHIP = "counted elsewhere"


def _arrive_household(store: Store) -> None:
    home = Household(store)
    for source_id, minor, day, text in (("a1", -1100, 2, "Cafe"), ("a2", -2300, 3, "Grocer")):
        home.arrive(
            pay(MAIN, FEED, f"f-{source_id}", minor, day, text),
            pay(MAIN, AGGREGATOR, f"tl-{source_id}", minor, day, text),
        )
    home.arrive(pay(MAIN, FEED, "f-a3", -3700, 4, "Bakery"))
    home.arrive(pay(MAIN, FEED, "f-a4", -4100, 6, "Elsewhere", internal=True))
    for index, day in enumerate((8, 9, 10)):
        minor = -5100 - index * 100
        home.arrive(
            pay(BILLS, FEED, f"f-bill-{index}", minor, day, "Water Co"),
            pay(MAIN, AGGREGATOR, f"tl-bill-{index}", minor, day, "WATER CO DD"),
        )
    august = date(2026, 8, 12)
    home.arrive(
        *(
            replace(
                pay(MAIN, source, source_id, -900, 12, "Pharmacy"),
                value_date=august,
                booking_date=august,
            )
            for source, source_id in ((FEED, "f-aug"), (AGGREGATOR, "tl-aug"))
        )
    )


def _served(root: Path):
    return served_store(root, _arrive_household, bound=[MAIN])


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("folded-rows")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with _served(root) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def masked(base: str, month: str = "2026-09") -> str:
    response = httpx.get(f"{base}/ledger", params={"ref": MAIN, "month": month}, timeout=60)
    assert response.status_code == 200
    return response.text


def shown(base: str, month: str = "2026-09") -> str:
    response = httpx.post(
        f"{base}/ledger", data={"ref": MAIN, "month": month}, follow_redirects=False, timeout=60
    )
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    return response.text


def items(page: str) -> list[str]:
    """Every transaction's list item, in page order."""
    return re.findall(r'<li class="txn[^"]*".*?</li>', page, flags=re.S)


def counted_items(page: str) -> list[str]:
    return [item for item in items(page) if 'class="txn folded' not in item]


def folded_items(page: str) -> list[str]:
    return [item for item in items(page) if 'class="txn folded' in item]


@pytest.fixture(params=["masked", "shown"])
def page(request: pytest.FixtureRequest, base: str) -> str:
    return masked(base) if request.param == "masked" else shown(base)


class TestTheMonthsCounts:
    def test_MonthWithCopies_HeaderSaysHowManyRowsAreCountedAndHowManyAreCopies(self, page):
        assert re.search(
            r'<p class="txcount">7 transactions: 4 counted, 3 transactions counted elsewhere\.',
            page,
        ), "the count compared with a bank's app is the counted one"

    def test_MonthWithoutCopies_HeaderKeepsItsPlainForm(self, base):
        page = masked(base, "2026-08")

        assert '<p class="txcount">1 transaction.' in page
        assert "transactions counted elsewhere" not in page
        assert f">{CHIP}<" not in page

    def test_MonthWithCopies_ListsFourCountedRowsAndThreeFolded(self, page):
        assert len(counted_items(page)) == 4
        assert len(folded_items(page)) == 3


class TestFoldedCopiesAreQuiet:
    def test_CopyRows_CarryNoRedRailAndNoOneSourceWarning(self, page):
        for item in folded_items(page):
            assert "flagged" not in item and "doubtful" not in item
            assert "one source only" not in item
            assert "pill-bad" not in item
            assert "withheld from Actual" not in item

    def test_CopyRows_SayWhatTheyAreInOneMutedChipUsingTheSameWordsAsTheFold(self, page):
        for item in folded_items(page):
            assert item.count(CHIP) == 1
            assert re.search(rf'<span class="pill pill-quiet"[^>]*>{CHIP}</span>', item)

    def test_CountedRows_NeverCarryTheCopyChip(self, page):
        assert all(CHIP not in item for item in counted_items(page))


class TestTheCollapsedLine:
    def test_Copies_SitInOneClosedDisclosureWhoseSummaryNamesTheCount(self, page):
        match = re.search(
            r'<details class="folded-rows">\s*<summary>([^<]*)</summary>', page
        )
        assert match is not None
        assert match.group(1) == f"3 {COPIES_WORDS}"

    def test_Copies_AreAtTheFootOfTheMonthsListAfterEveryCountedRow(self, page):
        last_counted = max(page.index(item) for item in counted_items(page))
        fold = page.index('<details class="folded-rows">')
        assert fold > last_counted
        assert all(page.index(item) > fold for item in folded_items(page))

    def test_Copies_AreInsideTheDisclosureSoNoScriptIsNeeded(self, page):
        fold = page[page.index('<details class="folded-rows">') :]
        fold = fold[: fold.index("</details>\n") if "</details>\n" in fold else None]
        assert len(re.findall(r'<li class="txn folded', fold)) == 3
        assert "<details class=\"folded-rows\" open" not in page

    def test_MonthWithoutCopies_HasNoDisclosureForThem(self, base):
        assert 'class="folded-rows"' not in masked(base, "2026-08")


class TestWhichCountedRowsAreFlaggedAndInWhatColour:
    def test_CountedRowOneSourceListsWhereTwoFeed_IsAmberNotRed(self, page):
        lone = [item for item in counted_items(page) if ">one source only<" in item]
        assert len(lone) == 2, "the bakery payment and the unconfirmed transfer"
        bakery = next(item for item in lone if "transfer?" not in item)
        assert bakery.startswith('<li class="txn doubtful"')
        assert re.search(r'<span class="pill pill-warn"[^>]*>one source only</span>', bakery)
        assert "pill-bad" not in bakery

    def test_CountedRowBothFeedsList_CarriesNoWarningAtAll(self, page):
        plain = [item for item in counted_items(page) if ">one source only<" not in item]
        assert len(plain) == 2
        assert all(item.startswith('<li class="txn"') for item in plain)

    def test_CountedTransferNothingConfirms_StaysRedWithItsRail(self, page):
        claimed = [item for item in counted_items(page) if ">transfer?<" in item]
        assert len(claimed) == 1
        assert claimed[0].startswith('<li class="txn flagged"')
        assert re.search(r'<span class="pill pill-bad"[^>]*>transfer\?</span>', claimed[0])

    def test_Page_HasOneRedRowAndOneAmberRowOfSeven_RedWinningWhereBothApply(self, page):
        assert len(re.findall(r'<li class="txn flagged"', page)) == 1, "the unconfirmed transfer"
        assert len(re.findall(r'<li class="txn doubtful"', page)) == 1, "the bakery payment"


class TestUnknownRowKindsFallBackToThePlainRow:
    def test_RowWithAnUnrecognisedStatus_DrawsPlainAndDoesNotFail(self):
        from types import SimpleNamespace

        from obdi.web_ledger import _row_html

        row = SimpleNamespace(
            origin="", dated=date(2026, 9, 1), observed=(), direction="out", currency="GBP",
            status="some-future-status", sources=("starling",), dates_differ=False,
            one_source=False, transfer="", transfer_other_account="", review_open=False,
            withheld="", unsendable=False, shares_identity=False, absorbed_ids=0,
            has_counterparty=False, annotated_by="", description="d", counterparty="",
            amount="1", review_reason="", send_refusal="", category="", payee="",
            cleared_by=(), feed_at=None, sightings=(), anchor="",
        )

        drawn = _row_html(row, unmasked=False)

        assert drawn.startswith('<li class="txn"')
        assert "some-future-status" in drawn
