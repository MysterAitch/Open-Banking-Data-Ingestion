"""The coverage timeline in miniature on an account's own page, over the household of
`coverage_timeline_world`, served by the real application with the day fixed at 2026-10-05.

The span is the twelve months ending with the month shown, cut at the first day anything is held
(2026-07-01) and at today. The drawing is 300 units wide with a 62 unit label column and a 6 unit
margin, so the days are drawn in 232 units. Where nothing is collapsed (this household has no
stretch in which nothing changes) a day is 232 / days units wide, and a cell is a week.

  MONTH 2026-09   window 2026-07-01 to 2026-09-30, 92 days, 232 / 92 = 2.5217 units a day.
                  The bracket is the month: 2026-09-01 is day 62, so it begins at
                  62 + 62 * 2.5217 = 218.3 and ends at the right edge, 294.0.
  MONTH 2026-07   window 2026-07-01 to 2026-07-31, 31 days, 7.4839 a day: the bracket is the
                  whole drawing, 62.0 to 294.0.
  FEED LANE       the feed's asks reach 07-01 to 08-20. In week cells from 07-01 (a cell is the
                  days 7k to 7k + 6 from 07-01) that is full through the cell ending 08-18 (7 cells,
                  49 days) and part in the one holding 08-19 and 08-20, then nothing.
  AGGREGATOR LANE asks reach 07-01 to 08-10 and 08-20 to 09-10, so, by week cell k from 07-01:
                  k0 to k4 full (to 08-04), k5 part (08-05 to 08-11 holds 08-11, which no ask
                  reached), k6 nothing, k7 part (08-19 is not reached), k8 and k9 full, k10 part
                  (09-09 and 09-10), then nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path

import httpx
import pytest

import obdi.web_ledger as web_ledger
from coverage_timeline_world import (
    CARD,
    LONG,
    MAIN,
    TODAY,
    build_card,
    build_main,
    land_long,
)
from obdi.statement_terms import keep_statement_readings
from obdi.store import Store
from served_store import environment_for, served_store

PER_DAY_SEP = 232 / 92
BRACKET = r'<rect class="cov-month" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"'


class _Fixed(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("account-timeline")


@pytest.fixture(scope="module", autouse=True)
def _day() -> Iterator[None]:
    patch = pytest.MonkeyPatch()
    patch.setattr(web_ledger, "datetime", _Fixed)
    yield
    patch.undo()


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    def build(store: Store) -> None:
        build_main(root, store)
        build_card(root, store)
        keep_statement_readings(store)

    with served_store(root, build, bound=[MAIN, CARD]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def page(base: str, ref: str, month: str = "") -> str:
    params = {"ref": ref, **({"month": month} if month else {})}
    response = httpx.get(f"{base}/ledger", params=params, timeout=120)
    assert response.status_code == 200
    return response.text


def block_of(text: str) -> str:
    start = text.index('<div class="cov-compact"')
    return text[start : text.index("</div>", text.index("Open the coverage timeline")) + 6]


def rects_in_row(block: str, row: int) -> list[dict[str, str]]:
    """The cell rectangles of the lane in row `row` (0 is verification): y = 16 + 17 row + 3."""
    y = f"{16 + 17 * row + 3:.1f}"
    found = []
    for tag in re.findall(r"<rect [^>]*>", block):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        if attrs.get("y") == y and attrs["class"] in ("cov-bar", "cov-unavailable"):
            found.append({"fill-opacity": "", **attrs})
    return found


class TestTheLine:
    def test_Main_SaysWhereEachWayInReachesAndWhatIsToFill(self, base):
        block = block_of(page(base, MAIN))
        line = re.search(r"<summary>([^<]*)</summary>", block)
        assert line is not None
        assert line.group(1) == (
            "The export reaches 2026-09-28; the bank feed reaches 2026-08-20; "
            "the aggregator reaches 2026-09-10; 1 gap to fill."
        )

    def test_Card_SaysWhenTheNextStatementIsExpected(self, base):
        block = block_of(page(base, CARD))
        assert (
            "Statements reach 2026-09-11; 1 gap to fill; next statement expected about "
            "2026-10-11." in block
        )

    def test_LongAccount_WithOneSourceAndNoGaps_SaysNothingNeedsFetching(self, tmp_path):
        with served_store(
            tmp_path, lambda store: land_long(tmp_path, store), bound=[LONG]
        ) as address:
            text = httpx.get(f"{address}/ledger", params={"ref": LONG}, timeout=120).text
        assert "The export reaches 2026-09-15; nothing needs fetching." in text


class TestPlacement:
    def test_Block_SitsUnderTheVerdictAndIsFoldedWithTheLineAsItsSummary(self, base):
        text = page(base, MAIN)
        assert text.index('class="verdict') < text.index('class="cov-compact"')
        assert text.index('class="cov-compact"') < text.index("Transactions, newest first")
        block = block_of(text)
        assert "<details><summary>" in block
        assert "<details open" not in block

    def test_Block_HasTheLinkToTheFullPage(self, base):
        assert "Open the coverage timeline" in block_of(page(base, MAIN))


class TestTheShownMonth:
    def test_NewestMonth_IsBracketedAndLabelledAtTheRightEnd(self, base):
        block = block_of(page(base, MAIN, "2026-09"))
        bracket = re.search(BRACKET, block)
        assert bracket is not None
        assert float(bracket.group(1)) == pytest.approx(62 + 62 * PER_DAY_SEP, abs=0.2)
        assert float(bracket.group(1)) + float(bracket.group(2)) == pytest.approx(294.0, abs=0.2)
        assert ">2026-09</text>" in block

    def test_EarlierMonth_IsBracketedOverTheWholeOfItsOwnShortWindow(self, base):
        block = block_of(page(base, MAIN, "2026-07"))
        bracket = re.search(BRACKET, block)
        assert bracket is not None
        assert float(bracket.group(1)) == pytest.approx(62.0, abs=0.2)
        assert float(bracket.group(1)) + float(bracket.group(2)) == pytest.approx(294.0, abs=0.2)
        assert ">2026-07</text>" in block

    def test_MonthShown_IsNeverTheSameBracketAsAnotherMonth(self, base):
        newest = block_of(page(base, MAIN, "2026-09"))
        earlier = block_of(page(base, MAIN, "2026-07"))
        assert newest != earlier


class TestCells:
    def test_FeedLane_IsFullThroughItsLastWholeWeekThenPartThenNothing(self, base):
        cells = rects_in_row(block_of(page(base, MAIN, "2026-09")), 1)
        assert [(c["class"], c["fill-opacity"]) for c in cells] == [
            ("cov-bar", ".3"), ("cov-bar", ".12")
        ]
        full, part = cells
        assert float(full["x"]) == pytest.approx(62.0, abs=0.2)
        assert float(full["width"]) == pytest.approx(49 * PER_DAY_SEP, abs=0.3)
        assert float(part["x"]) == pytest.approx(62 + 49 * PER_DAY_SEP, abs=0.3)
        assert float(part["width"]) == pytest.approx(7 * PER_DAY_SEP, abs=0.3)

    def test_AggregatorLane_ShowsItsUnaskedDaysAsNothingBetweenPartCells(self, base):
        cells = rects_in_row(block_of(page(base, MAIN, "2026-09")), 2)
        assert [c["fill-opacity"] for c in cells] == [".3", ".12", ".12", ".3", ".12"]
        starts = [round((float(c["x"]) - 62) / PER_DAY_SEP) for c in cells]
        widths = [round(float(c["width"]) / PER_DAY_SEP) for c in cells]
        assert starts == [0, 35, 49, 56, 70]
        assert widths == [35, 7, 7, 14, 7]


class TestMarksLinkToTheFullPage:
    def test_EveryMark_LandsOnAnAnchorThatExistsOnTheFullPage(self, base):
        block = block_of(page(base, MAIN, "2026-09"))
        links = re.findall(r'<a href="([^"]*)"><title>', block)
        assert len(links) >= 3
        for link in links:
            address = link.replace("&amp;", "&")
            path, _, fragment = address.partition("#")
            assert fragment.startswith("e-"), address
            full = httpx.get(f"{base}{path}", timeout=120).text
            assert f'id="{fragment}"' in full, address

    def test_OpenLink_GoesToTheFullPageOverTheSameSpan(self, base):
        block = block_of(page(base, MAIN, "2026-09"))
        href = re.search(r'<a class="tap" href="([^"]*)">Open the coverage timeline', block)
        assert href is not None
        assert "window_from=2026-07-01" in href.group(1)
        assert "window_to=2026-09-30" in href.group(1)


class TestMasking:
    def test_Block_IsIdenticalMaskedAndWithValuesShown(self, base):
        masked = block_of(page(base, MAIN, "2026-09"))
        shown = httpx.post(
            f"{base}/ledger", data={"ref": MAIN, "month": "2026-09"}, timeout=120
        )
        assert shown.status_code == 200
        assert block_of(shown.text) == masked

    def test_Block_HoldsNoAmountAndNoDescription(self, base):
        block = block_of(page(base, MAIN, "2026-09"))
        for private in ("Alpha", "Charlie", "Kilo", "792.00", "925.00", "700.00"):
            assert private not in block
        assert not re.search(r"\d[\d,]*\.\d\d(?!\d)", block)


class TestCost:
    def test_Block_IsASmallFixedSize(self, base):
        assert len(block_of(page(base, MAIN, "2026-09")).encode()) < 9000
        assert len(block_of(page(base, CARD)).encode()) < 9000

    def test_HookStatements_WhenWarm_AreTheSameFewForEveryAccount(self, base, root):
        from obdi.cli import build_web_config

        config = build_web_config(root / "store.sqlite3")
        assert config is not None and config.coverage_timeline_compact is not None
        original = Store.__init__
        counted: list[str] = []

        def traced(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            original(self, *args, **kwargs)
            self.connection.set_trace_callback(counted.append)

        hook = config.coverage_timeline_compact
        hook(MAIN, TODAY)
        hook(CARD, TODAY)
        monkey = pytest.MonkeyPatch()
        monkey.setattr(Store, "__init__", traced)
        try:
            hook(MAIN, TODAY)
            first = len(counted)
            counted.clear()
            hook(CARD, TODAY)
            second = len(counted)
        finally:
            monkey.undo()
        assert first == second
        assert first <= 20, f"{first} statements when warm"


def test_Fixture_Date_IsAsTheDocstringSays() -> None:
    assert date(2026, 10, 5) == TODAY
