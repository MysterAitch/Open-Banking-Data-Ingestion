"""The timeline and the values chart, over invented accounts with known faults.

The masked timeline is read by an assistant that must never see a figure, so
the strongest check is a comparison: the same structure over figures seven times
the size renders to the SAME bytes. The values chart is the only place sizes are
drawn, and it is reached only by a POST.

Pages are parsed with the standard HTML parser and asserted on elements and on
text, never matched as strings beyond a literal marker.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import replace
from datetime import date, timedelta
from html.parser import HTMLParser
from http.server import HTTPServer
from itertools import groupby, pairwise

import httpx
import pytest

import fault_structure_corpus as corpus
from obdi.balance_anchors import FamilyReading, FamilyWalk
from obdi.balance_chart import BalanceChart, chart_of_walk, empty_chart
from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.web_balance_chart import (
    MAX_PIXELS_PER_DAY,
    PIXELS_PER_DAY,
    TARGET_RANGE_WIDTH,
    choose_scale,
    nice_ticks,
    parse_range,
    render_balance_chart,
)

MAIN = corpus.MAIN
EDGE = 48


class Parsed(HTMLParser):
    """Every element with its attributes, and every piece of text."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.pieces: list[str] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, {k: v or "" for k, v in attrs}))

    def handle_data(self, data):
        self.pieces.append(data)

    def find(self, tag: str, **where: str) -> list[dict[str, str]]:
        return [
            attrs
            for name, attrs in self.elements
            if name == tag
            and all(attrs.get(k.rstrip("_").replace("_", "-")) == v for k, v in where.items())
        ]

    @property
    def text(self) -> str:
        return " ".join(self.pieces)

    def numbers(self) -> set[str]:
        """Every run of digits in the text, which is how a figure would appear."""
        runs = set()
        for is_digit, group in groupby(self.text, str.isdigit):
            if is_digit:
                runs.add("".join(group))
        return runs


def svg_named(page: Parsed, prefix: str) -> dict[str, str]:
    (found,) = [a for a in page.find("svg") if a.get("aria-labelledby", "").startswith(prefix)]
    return found


def mixed(scale: int = 1, **kwargs) -> BalanceChart:
    walk = corpus.walk(missing=True, late=True, monthly_twice=True, scale=scale, **kwargs)
    return chart_of_walk(MAIN, walk)


def masked(chart: BalanceChart, **kwargs) -> str:
    return render_balance_chart(chart, unmasked=False, **kwargs).decode()


def shown(chart: BalanceChart, **kwargs) -> str:
    return render_balance_chart(chart, unmasked=True, **kwargs).decode()


def figures_of(chart: BalanceChart) -> set[str]:
    """Every size and balance the corpus holds, as the digits a page would carry."""
    walk_figures = {abs(d) for d in chart.differences}
    for line in chart.lines:
        walk_figures |= {abs(v) for v in line.stated + line.predicted}
    found = {str(v) for v in walk_figures if v >= 100}
    found |= {f"{v // 100}" for v in walk_figures if v >= 10_000}
    return found


class TestTheMaskedTimelineHoldsNoSize:
    def test_Timeline_WhenMasked_CarriesNoFigureDescriptionOrMoneySymbol(self):
        chart = mixed()
        page = Parsed(masked(chart))

        assert figures_of(chart) & page.numbers() == set()
        assert "£" not in page.text
        assert "amount" not in masked(chart).lower()
        assert corpus.NAME not in masked(chart)

    def test_Timeline_WhenEverySizeIsSevenTimesAsLarge_RendersToTheSameBytes(self):
        assert masked(mixed(scale=1)) == masked(mixed(scale=7))

    def test_Timeline_WhenMasked_EveryMarkOfAKindHasTheSameHeightAndNoSeriesIsDrawn(self):
        page = Parsed(masked(mixed()))

        marks = page.find("rect", class_="mark")
        by_kind = {kind: {m["height"] for m in marks if m["data-kind"] == kind} for kind in
                   {m["data-kind"] for m in marks}}
        assert set(by_kind) == {"transient", "recurring", "unexplained"}
        assert all(len(heights) == 1 for heights in by_kind.values())
        assert len({height for heights in by_kind.values() for height in heights}) == 1
        assert [tag for tag, attrs in page.elements if "data-series" in attrs] == []
        assert not [tag for tag, _ in page.elements if tag in ("polyline", "circle", "polygon")]

    def test_Timeline_WhenMasked_HasOneMarkPerStepAndOneJoinPerPair(self):
        chart = mixed()
        page = Parsed(masked(chart))

        # Eight monthly charges, each in its own month's bin; one pair, whose later
        # end is a hollow cap and not a second change; one unexplained step.
        assert len(page.find("rect", class_="mark")) == 10
        assert len(page.find("rect", class_="cap")) == 1
        assert len(page.find("path", class_="join")) == 1
        assert len(page.find("rect", class_="level")) == len(chart.structure.whole.levels)

    def test_Timeline_WhenAFigureIsAskedForInTheQuery_StaysMasked(self):
        page = masked(mixed())

        assert "VALUES ARE SHOWN" not in page


class TestTheTimelineScale:
    def test_Timeline_WhenFullyDrawn_FitsTheScreenWhateverTheDaysCovered(self):
        page = Parsed(masked(mixed()))

        strip = svg_named(page, "bc-strip")
        assert strip["width"] == "100%"
        assert strip["viewbox"].split()[2] == "360"

    def test_Timeline_WhenMasked_NothingOnThePageIsASidewaysScrollingContainer(self):
        page = Parsed(masked(mixed()))

        # The counts table may scroll inside the box every wide table is given.
        regions = [r for r in page.find("div", role="region") if "data-table-scroll" not in r]
        assert regions == []

    def test_Timeline_LabelsEveryMonthWithItsYear_SoAnyScreenfulSaysTheDate(self):
        page = Parsed(masked(mixed()))

        assert "2026" in page.pieces
        assert {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"} <= set(page.pieces)

    def test_Timeline_WhenFullyDrawn_TheCountsTableLinksEachMonthToANarrowerMaskedRange(self):
        page = Parsed(masked(mixed()))

        links = {a["href"] for a in page.find("a") if "from=" in a.get("href", "")}
        assert f"/balance-chart?ref={MAIN}&from=2026-03-01&to=2026-03-31" in links

    def test_Timeline_WhenAMonthIsRequested_IsDrawnWithOnlyThatMonthsMarks(self):
        chart = mixed()
        page = Parsed(masked(chart, start=date(2026, 3, 1), end=date(2026, 3, 31)))

        steps_in_march = [s for s in chart.structure.whole.steps if s.day.month == 3]
        assert len(page.find("rect", class_="mark")) == len(steps_in_march) == 1

    def test_Scale_WhenSevenAndAHalfYearsAreHeld_IsAboutTenThousandPixelsWide(self):
        scale = choose_scale(date(2019, 1, 1), date(2026, 7, 1), None, None)

        assert 9_000 <= scale.width <= 11_000
        assert scale.per_day == PIXELS_PER_DAY

    @pytest.mark.parametrize(
        ("days", "per_day"),
        [(31, MAX_PIXELS_PER_DAY), (365, TARGET_RANGE_WIDTH / 365), (4000, PIXELS_PER_DAY)],
    )
    def test_Scale_WhenARangeIsRequested_FillsAboutTheTargetWidthWithinTheLimits(
        self, days, per_day
    ):
        scale = choose_scale(date(2019, 1, 1), date(2026, 7, 1), date(2024, 1, 1),
                             date(2024, 1, 1) + timedelta(days=days - 1))

        assert scale.per_day == pytest.approx(per_day)

    def test_Range_WhenBackToFrontOrHalfGivenOrNotADate_IsRefused(self):
        for start, end in (("2026-03-05", "2026-03-01"), ("2026-03-05", ""), ("x", "2026-03-01")):
            with pytest.raises(Exception, match=r"range|date"):
                parse_range(start, end)

    def test_Range_WhenNeitherIsGiven_MeansEverythingHeld(self):
        assert parse_range("", " ") == (None, None)


class TestTheValuesChart:
    def test_Values_WhenShown_DrawsOnePathPerLineAndNothingPerPoint(self):
        page = Parsed(shown(mixed()))

        series = [a["data-series"] for a in page.find("path") if "data-series" in a]
        assert sorted(series) == ["difference", "predicted", "stated"]

    def test_Values_WhenShown_StatedAndPredictedLinesDifferByDashPatternNotColourAlone(self):
        page = Parsed(shown(mixed()))

        (stated,) = page.find("path", data_series="stated")
        (predicted,) = page.find("path", data_series="predicted")
        assert stated["stroke"] == predicted["stroke"]
        assert "stroke-dasharray" not in stated
        assert predicted["stroke-dasharray"]

    def test_Values_WhenTwoSourcesState_EachHasItsOwnPairedLinesWithDistinctPatterns(self):
        page = Parsed(shown(mixed(second_source=True)))

        stated = page.find("path", data_series="stated")
        predicted = page.find("path", data_series="predicted")
        assert len(stated) == len(predicted) == 2
        assert stated[0]["stroke"] != stated[1]["stroke"]
        assert {p["stroke-dasharray"] for p in predicted} == {"6 5", "1 6"}
        assert "stroke-dasharray" not in stated[0] and stated[1]["stroke-dasharray"]
        assert {s["data-source"] for s in stated} == {corpus.CSV_SOURCE, corpus.SECOND_SOURCE}

    def test_Values_WhenShown_MarksEveryStepByKindWithAShapeAsWellAsAColour(self):
        page = Parsed(shown(mixed()))

        marks = [a for a in page.elements if a[1].get("class") == "mark"]
        kinds = sorted(a["data-kind"] for _, a in marks)
        assert kinds == ["recurring"] * 8 + ["transient"] * 2 + ["unexplained"]
        shapes = {kind: {tag for tag, a in marks if a["data-kind"] == kind} for kind in set(kinds)}
        assert shapes == {
            "recurring": {"polygon"},
            "transient": {"circle"},
            "unexplained": {"rect"},
        }

    def test_Values_WhenShown_TheStepsTableCarriesEachStepsFigureDayPartnerAndClass(self):
        page = Parsed(shown(mixed()))

        cells = page.pieces
        assert cells.count("-£45.00") == 1
        assert cells.count("+£9.99") == 8
        assert cells.count("-£25.00") == 1
        assert cells.count("+£25.00") == 1
        assert "The present difference is" in page.text
        assert "£34.92" in cells

    def test_Values_WhenShown_AxesCarryFiguresAndTheChartHasATitleAndDescription(self):
        page = Parsed(shown(mixed()))

        assert any(piece.startswith("£") or piece.startswith("-£") for piece in page.pieces)
        for prefix in ("bc-values", "bc-axis"):
            assert svg_named(page, prefix)["role"] == "img"
        titles = {a["id"] for a in page.find("title") if "id" in a}
        descs = {a["id"] for a in page.find("desc")}
        assert {"bc-values-t", "bc-axis-t"} <= titles
        assert {"bc-values-d", "bc-axis-d"} <= descs

    def test_Values_WhenShown_NamesTheWholeAccountInTheHeading(self):
        assert "The whole account: the main account and its Spaces together" in shown(mixed())

    def test_Values_WhenARangeIsGiven_IsDrawnAtTheLargerScaleWithOnlyItsRowsInTheTable(self):
        page = Parsed(shown(mixed(), start=date(2026, 3, 1), end=date(2026, 3, 31)))

        svg = svg_named(page, "bc-values")
        assert float(svg["width"]) == pytest.approx(EDGE + 31 * MAX_PIXELS_PER_DAY, abs=1)
        assert page.pieces.count("+£9.99") == 1
        assert "-£45.00" not in page.pieces


class TestTheOwnAccountView:
    def test_Heading_WhenTheChartIsOfTheAccountsOwnBalances_SaysSo(self):
        walk = corpus.walk(missing=True)
        chart = chart_of_walk(MAIN, walk)
        own = replace(chart, scope="own", spaces=())

        page = masked(own)

        assert "This account's own known balances" in page
        assert "main account and its Spaces together" not in page


class TestStatesWithNothingToDraw:
    def test_Page_WhenTheAccountIsUnknown_SaysSoAndDrawsNothing(self):
        page = Parsed(masked(empty_chart("nowhere", "", "unknown")))

        assert "Unknown account" in page.text
        assert page.find("svg") == []

    def test_Page_WhenNoBalanceIsStated_SaysThereIsNothingToCompare(self):
        page = Parsed(masked(empty_chart(MAIN, "", "no-balances")))

        assert "No balance is stated" in page.text
        assert page.find("svg") == []

    def test_Page_WhenNothingIsWrong_SaysOneLevelAndOneHundredPerCent(self):
        page = Parsed(masked(chart_of_walk(MAIN, corpus.walk())))

        assert "one level" in page.text and "100%" in page.text
        assert page.find("rect", class_="mark") == []


class TestNiceTicks:
    @pytest.mark.parametrize(
        ("low", "high"), [(0, 9_000), (-250_000, 1_300_000), (1_000, 1_001), (-5, 5)]
    )
    def test_Ticks_AlwaysCoverTheRangeInWholePoundSteps(self, low, high):
        ticks = nice_ticks(low, high)

        assert ticks[0] <= low and ticks[-1] >= high
        assert len(ticks) >= 2 and len(ticks) <= 12
        steps = {b - a for a, b in pairwise(ticks)}
        assert len(steps) == 1 and steps.pop() % 100 == 0


def readings_of(count: int, steps: int) -> FamilyWalk:
    """`count` stated balances a day, the difference changing on `steps` of them."""
    start = date(2019, 1, 1)
    every = max(count // steps, 1)
    level = 0
    readings = []
    for n in range(count):
        if n % every == 0 and n // every < steps:
            level += (n % 13 + 1) * (1 if (n // every) % 4 else -1)
        balance = 1_000_000 + n * 37 + level
        readings.append(
            FamilyReading(
                start + timedelta(days=n), balance, ("starling-csv",), n == 0,
                None if n == 0 else balance - level, None if n == 0 else level,
            )
        )
    return FamilyWalk(MAIN, (), tuple(readings))


class TestCostAndSizeAtScale:
    def test_Pages_WhenTwoThousandBalancesAndThreeHundredSteps_AreBuiltQuicklyAndStaySmall(self):
        walk = readings_of(2000, 300)

        began = time.perf_counter()
        chart = chart_of_walk(MAIN, walk)
        timeline = render_balance_chart(chart, unmasked=False)
        values = render_balance_chart(chart, unmasked=True)
        took = time.perf_counter() - began

        assert 280 <= len(chart.structure.whole.steps) <= 320
        assert took < 3.0, f"{took:.2f}s"
        # The shared stylesheet is most of the difference between this and a page of its own:
        # the coverage timeline's rules add about 1.6 kilobytes to every page (250,287 bytes
        # measured against the old bound of 250,000), and the chart itself did not change.
        assert len(timeline) < 252_000, len(timeline)
        assert len(values) < 500_000, len(values)
        page = Parsed(values.decode())
        assert len([a for a in page.find("path") if "data-series" in a]) == 3


@pytest.fixture
def served(tmp_path):
    charts = {
        MAIN: mixed(),
        "plain": chart_of_walk("plain", corpus.walk()),
        "nobalance": empty_chart("nobalance", "", "no-balances"),
    }

    def balance_chart_data(ref: str) -> BalanceChart:
        return charts.get(ref) or empty_chart(ref, "", "unknown")

    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        balance_chart_data=balance_chart_data,
    )
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


class TestOverHttp:
    def test_Get_WhenAskedForValuesInTheQuery_StillAnswersMaskedAndStoresNothingSpecial(
        self, served
    ):
        response = httpx.get(
            f"{served}/balance-chart", params={"ref": MAIN, "values": "yes", "unmask": "1"}
        )

        assert response.status_code == 200
        assert "£" not in response.text
        assert "VALUES ARE SHOWN" not in response.text
        assert "no-store" not in response.headers.get("cache-control", "")

    def test_Post_WhenConfirmed_AnswersWithValuesAndIsSentNoStoreWithoutARedirect(self, served):
        response = httpx.post(
            f"{served}/balance-chart", data={"ref": MAIN}, follow_redirects=False
        )

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert "VALUES ARE SHOWN" in response.text
        assert "£" in response.text

    def test_Post_WhenDrivenByAnotherSite_IsRefused(self, served):
        response = httpx.post(
            f"{served}/balance-chart",
            data={"ref": MAIN},
            headers={"Origin": "https://evil.example"},
        )

        assert response.status_code == 403
        assert "£" not in response.text

    def test_Post_WhenNoAccountIsNamed_IsRefusedWithoutValues(self, served):
        response = httpx.post(f"{served}/balance-chart", data={})

        assert response.status_code == 400
        assert "£" not in response.text

    def test_Get_WhenTheMaskedPageOffersValues_TheFormPostsToANewTabWithTheRange(self, served):
        text = httpx.get(
            f"{served}/balance-chart",
            params={"ref": MAIN, "from": "2026-03-01", "to": "2026-03-31"},
        ).text
        # The window's own control is the page's other form; the one that asks for values
        # is the one that opens a new tab.
        (opened,) = re.findall(r'<form[^>]*target="_blank".*?</form>', text, re.S)
        page = Parsed(opened)

        (form,) = page.find("form")
        assert (form["method"], form["action"], form["target"]) == (
            "post", "/balance-chart", "_blank",
        )
        hidden = {a["name"]: a["value"] for a in page.find("input")}
        assert hidden == {"ref": MAIN, "from": "2026-03-01", "to": "2026-03-31"}

    def test_Get_WhenTheRangeIsBackToFront_IsRefusedWithoutEchoingIt(self, served):
        response = httpx.get(
            f"{served}/balance-chart",
            params={"ref": MAIN, "from": "2026-03-31", "to": "2026-03-01"},
        )

        assert response.status_code == 400
        assert "2026-03-31" not in response.text

    def test_Get_WhenTheAccountIsUnknown_Answers404(self, served):
        assert httpx.get(f"{served}/balance-chart", params={"ref": "ghost"}).status_code == 404

    def test_Get_WhenNoAccountIsNamed_Answers400(self, served):
        assert httpx.get(f"{served}/balance-chart").status_code == 400

    def test_Get_WhenHookRaises_AnswersAFixedSentenceWithoutTheFault(self, tmp_path):
        def broken(ref: str) -> BalanceChart:
            raise RuntimeError("balance 123456.78 leaked")

        config = WebConfig(
            client_id="c",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            balance_chart_data=broken,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            response = httpx.post(
                f"http://127.0.0.1:{httpd.server_port}/balance-chart", data={"ref": MAIN}
            )
        finally:
            httpd.shutdown()

        assert response.status_code == 500
        assert "123456" not in response.text
        assert "no-store" in response.headers["cache-control"]
