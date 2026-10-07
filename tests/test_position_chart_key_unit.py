"""The Position chart's key says the unit the chart is drawn in.

"Partial" means that on a point's date some counted item had no known figure yet, so the
point leaves it out. A chart of days has no month on it, so its key must not call a point a
month. The invented household (`position_window_household`, today 2026-10-04) is incomplete
until 2026-09-14, when the last of its five counted items first has a figure, so a chart
over any of the windows below has an incomplete stretch and draws the dashed line. Decided
before the first run:

    window                  resolution   the dashed line's entry in the key
    last 90 days            day          Net worth on a day that leaves something out
    last 12 months          week         Net worth in a week that leaves something out
    everything held         month        Net worth in a month that leaves something out
    last 90 days, one item
      left out of the chart day          Total of the chosen accounts on a day that leaves ...

The same phrase is read by the chart's description and by the sentence beneath the chart,
and the month table, which is monthly whatever the chart is, keeps its "partial" pill.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from obdi.core.date_window import Resolution
from obdi.ingest.store import Store
from obdi.read.position import Position, read_position
from obdi.web_position import render_position, unit_word
from position_window_household import CARD, TODAY, window_household

PHRASES = {
    "day": "on a day that leaves something out",
    "week": "in a week that leaves something out",
    "month": "in a month that leaves something out",
}
WINDOW_FOR = {"day": "d90", "week": "m12", "month": "all"}


@pytest.fixture
def held(tmp_path) -> Position:
    with Store(tmp_path / "k.sqlite3") as store:
        window_household(store)
        return read_position(store, today=TODAY)


def page_for(position: Position, unit: str, **kwargs) -> str:
    return unescape(
        render_position(
            position, unmasked=True, window_fields={"window": WINDOW_FOR[unit]}, **kwargs
        ).decode()
    )


def key_entry(page: str) -> str:
    found = re.search(r'<li data-key="partial">.*?</svg>\s*([^<]*)</li>', page, re.S)
    assert found, "the dashed line has an entry in the key"
    return found.group(1).strip()


def description(page: str) -> str:
    found = re.search(r'<desc id="chart-desc">(.*?)</desc>', page, re.S)
    assert found
    return found.group(1)


def sentence_beneath(page: str) -> str:
    found = re.search(r'<p class="muted">(One figure per [^<]*dashed[^<]*)</p>', page)
    assert found, "the sentence under the chart says which line is dashed"
    return found.group(1)


class TestTheKeyNamesTheChartsUnit:
    @pytest.mark.parametrize("unit", ["day", "week", "month"])
    def test_Key_ForTheChartsResolution_NamesItsUnitAndNoOther(self, held, unit):
        entry = key_entry(page_for(held, unit))

        assert entry == f"Net worth {PHRASES[unit]}"
        for other in set(PHRASES) - {unit}:
            assert PHRASES[other] not in entry

    def test_Key_OfADailyChart_NeverCallsAPointAMonth(self, held):
        assert "month" not in key_entry(page_for(held, "day"))

    def test_Key_OfAChartOfChosenAccountsOnly_SaysSoAndKeepsTheUnit(self, held):
        keep = {item.key for item in held.chart_items} - {CARD}

        entry = key_entry(page_for(held, "day", chart_in=keep))

        assert entry == f"Total of the chosen accounts {PHRASES['day']}"

    def test_Key_OfTheDefaultChartWithNoWindow_IsMonthly(self, held):
        page = unescape(render_position(held, unmasked=True).decode())

        assert key_entry(page) == f"Net worth {PHRASES['month']}"


class TestTheThreePlacesAgree:
    @pytest.mark.parametrize("unit", ["day", "week", "month"])
    def test_KeyDescriptionAndSentenceBeneath_AllSayTheSamePhraseForTheUnit(self, held, unit):
        page = page_for(held, unit)

        for place in (key_entry(page), description(page), sentence_beneath(page)):
            assert PHRASES[unit] in place
            for other in set(PHRASES) - {unit}:
                assert PHRASES[other] not in place

    def test_UnitWord_ForEachResolution_IsTheNounTheThreePlacesUse(self):
        assert [unit_word(r) for r in (Resolution.DAY, Resolution.WEEK, Resolution.MONTH)] == [
            "day", "week", "month",
        ]


class TestTheMonthTableIsStillMonthly:
    def test_MonthTable_UnderADailyChart_KeepsItsPartialPill(self, held):
        page = page_for(held, "day")

        assert 'class="pill pill-warn"' in page
        assert ">partial</span>" in page
