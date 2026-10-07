"""The balance chart draws what an account did, and each known balance as a mark on it.

The scenario is a loan the owner described: opened with a stated balance, repaid by one regular
payment a month for three years, then one final payment of double the regular size, then one
small clearing payment a few weeks later that brings the balance to exactly nil; a stated nil
at the close, and one statement closing a year at 1 January. The owner's complaints of the old
drawing: the lines "jump in January each year", the chart "showed the loan still owing after it
was paid off", and "the convergence on nil should happen at the same time - declared nil and the
running total summing to nil".

Every answer below was worked out from the construction BEFORE the first run (amounts in pence):

    regular payment            30,000 on the 1st of each month, 2021-02-01 to 2024-01-01 (36)
    final payment              60,000 on 2024-02-01
    clearing payment            4,800 on 2024-02-19
    owed at the start       1,144,800 (36 x 30,000 + 60,000 + 4,800), held as a minus figure
    after the 1 Jan payment:  2022-01-01 -784,800 (12 paid)   2023-01-01 -424,800 (24 paid)
                              2024-01-01 -64,800 (36 paid)
    after 2024-02-01          -4,800
    after 2024-02-19                0

So the running line has 38 steps: 36 of one size, one of twice that, one of 4,800/30,000 of it.
The chart is archived on 2024-02-19; statements for 2025-01-01 and 2026-08-10 (a section period
that ends long after the close) are also held and must not be drawn.

The alternative: the same loan with the clearing payment missing from the rows. The running line
ends at -4,800 while the stated nil sits at nil: the known balance is 4,800 above the rows.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from itertools import pairwise

import pytest

from obdi.balance_anchors import (
    STATED,
    STATEMENT,
    Anchor,
    derive_opening,
    record_stated_anchor,
)
from obdi.balance_chart import BalanceChart, build_balance_chart, chart_of_opening
from obdi.core.models import Transaction
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.web_balance_chart import EDGE, PIXELS_PER_DAY, render_balance_chart
from page_dom import Node, elements, parse
from test_ledger import land, txn

REF = "invented-loan"
BANK = "Invented Loan Co statement"
OPENED = date(2021, 1, 1)
CLOSE = date(2024, 2, 19)
REGULAR = 30_000
FINAL = 2 * REGULAR
CLEARING = 4_800
OWED = 36 * REGULAR + FINAL + CLEARING
JANUARIES = {
    date(2022, 1, 1): -OWED + 12 * REGULAR,
    date(2023, 1, 1): -OWED + 24 * REGULAR,
    date(2024, 1, 1): -OWED + 36 * REGULAR,
}
STRAY = [(date(2025, 1, 1), 0), (date(2026, 8, 10), 0)]


def payment(day: date, minor: int) -> Transaction:
    return Transaction(REF, minor, day, day, "LOAN PAYMENT", "invented-csv")


def months(count: int) -> list[date]:
    found = []
    year, month = 2021, 2
    for _ in range(count):
        found.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found


def rows(*, clearing: bool = True) -> list[Transaction]:
    found = [payment(day, REGULAR) for day in months(36)]
    found.append(payment(date(2024, 2, 1), FINAL))
    if clearing:
        found.append(payment(CLOSE, CLEARING))
    return found


def anchors() -> list[Anchor]:
    found = [Anchor(OPENED, -OWED, STATED), Anchor(CLOSE, 0, STATED)]
    found += [Anchor(day, minor, STATEMENT, BANK) for day, minor in JANUARIES.items()]
    found += [Anchor(day, minor, STATEMENT, BANK) for day, minor in STRAY]
    return found


def loan(*, clearing: bool = True, closed: date | None = CLOSE) -> BalanceChart:
    held = rows(clearing=clearing)
    opening = derive_opening(REF, anchors(), held)
    return chart_of_opening(REF, opening, label="Invented loan", rows=held, closed=closed)


def drawn(chart: BalanceChart) -> str:
    return render_balance_chart(chart, unmasked=True).decode()


def x_of(day: date, first: date = OPENED) -> float:
    return EDGE + (day - first).days * PIXELS_PER_DAY


def svg_of(root: Node, prefix: str) -> Node:
    (found,) = [
        n for n in elements(root, "svg") if n.attrs.get("aria-labelledby", "").startswith(prefix)
    ]
    return found


def vertices(path: Node) -> list[tuple[float, float]]:
    found: list[tuple[float, float]] = []
    for command, first, second in re.findall(
        r"([MHV])(-?[\d.]+)(?:,(-?[\d.]+))?", path.attrs["d"]
    ):
        if command == "M":
            found.append((float(first), float(second)))
        elif command == "H":
            found.append((float(first), found[-1][1]))
        else:
            found.append((found[-1][0], float(first)))
    return found


def running_line(root: Node) -> list[tuple[float, float]]:
    (path,) = [n for n in elements(svg_of(root, "bc-values"), "path")
               if n.attrs.get("data-series") == "running"]
    return vertices(path)


def line_y_at(line: list[tuple[float, float]], x: float) -> float:
    """The running line's height at x: after any step that falls on x."""
    return [y for vx, y in line if vx <= x + 0.06][-1]


def steps_of(line: list[tuple[float, float]]) -> list[float]:
    return [
        later[1] - earlier[1]
        for earlier, later in pairwise(line)
        if abs(later[0] - earlier[0]) < 0.01 and abs(later[1] - earlier[1]) > 0.01
    ]


def in_group(circle: Node, name: str) -> str:
    """A mark's look is its group's, so thousands of marks stay small on the page."""
    return circle.attrs.get(name) or (circle.parent.attrs[name] if circle.parent else "")


def marks(root: Node) -> list[Node]:
    return [n for n in elements(svg_of(root, "bc-values"), "circle")
            if n.parent is not None and n.parent.attrs.get("class") == "known"]


def ticks(root: Node) -> list[Node]:
    return [n for n in elements(svg_of(root, "bc-values"), "line")
            if n.attrs.get("class") == "gap"]


def difference_marks(root: Node) -> list[Node]:
    return [n for n in elements(svg_of(root, "bc-values"), "circle")
            if n.parent is not None and n.parent.attrs.get("class") == "difference"]


def mark_on(root: Node, day: date) -> Node:
    (found,) = [m for m in marks(root) if m.attrs["data-day"] == day.isoformat()]
    return found


def label_scale(root: Node) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
    """(y, pence) of the figure column's labels, one list per panel."""
    found = []
    for node in elements(svg_of(root, "bc-axis"), "text"):
        text = node.text()
        if re.fullmatch(r"-?£[\d,]+(\.\d\d)?", text):
            minor = round(float(text.replace("£", "").replace(",", "")) * 100)
            found.append((float(node.attrs["y"]), minor))
    split = next(i for i in range(1, len(found)) if found[i][0] > found[i - 1][0])
    return found[:split], found[split:]


def pence_at(panel: list[tuple[float, int]], y: float) -> float:
    """The figure at height y, from the first two labels (each is set 4 px below its line)."""
    if len(panel) == 1:
        return 0.0 if abs(y - (panel[0][0] - 4)) < 0.06 else float("nan")
    (y0, v0), (y1, v1) = panel[0], panel[1]
    return v0 + (y - (y0 - 4)) * (v1 - v0) / (y1 - y0)


def pence_per_pixel(panel: list[tuple[float, int]]) -> float:
    if len(panel) == 1:
        return 0.06
    (y0, v0), (y1, v1) = panel[0], panel[1]
    return abs((v1 - v0) / (y1 - y0))


@pytest.fixture(scope="module")
def paid() -> tuple[BalanceChart, Node, str]:
    chart = loan()
    html = drawn(chart)
    return chart, parse(html), html


@pytest.fixture(scope="module")
def short() -> tuple[BalanceChart, Node, str]:
    chart = loan(clearing=False)
    html = drawn(chart)
    return chart, parse(html), html


class TestARepaidLoanIsDrawnAsWhatItDid:
    def test_Loan_WhenRepaidByRegularPayments_RunningLineStepsOncePerPayment(self, paid):
        steps = steps_of(running_line(paid[1]))

        assert len(steps) == 38
        regular = steps[0]
        assert all(step == pytest.approx(regular, abs=0.25) for step in steps[:36])
        assert steps[36] == pytest.approx(2 * regular, abs=0.3)
        assert steps[37] == pytest.approx(regular * CLEARING / REGULAR, abs=0.3)

    def test_Loan_WhenClearingPaymentLands_RunningLineReachesNilOnThatDay(self, paid):
        _, root, _ = paid
        line = running_line(root)
        nil = mark_on(root, CLOSE)

        assert line_y_at(line, x_of(CLOSE)) == pytest.approx(float(nil.attrs["cy"]), abs=0.06)
        assert line_y_at(line, x_of(CLOSE - timedelta(days=1))) > float(nil.attrs["cy"]) + 0.5
        upper, _ = label_scale(root)
        assert pence_at(upper, line_y_at(line, x_of(CLOSE))) == pytest.approx(
            0, abs=pence_per_pixel(upper) * 1.5
        )

    def test_Loan_WhenNilIsStatedAtTheClose_TheMarkSitsOnTheLineAtThatDay(self, paid):
        _, root, _ = paid
        nil = mark_on(root, CLOSE)

        assert float(nil.attrs["cx"]) == pytest.approx(x_of(CLOSE), abs=0.06)
        assert in_group(nil, "data-kind") == "stated"
        assert in_group(nil, "fill") != "none"
        assert ticks(root) == []

    def test_Loan_WhenStatementsCloseEachJanuary_HollowMarksSitOnTheLineThatDay(self, paid):
        _, root, _ = paid
        line = running_line(root)
        upper, _ = label_scale(root)

        for day, minor in JANUARIES.items():
            mark = mark_on(root, day)
            assert in_group(mark, "data-kind") == "statement"
            assert in_group(mark, "fill") == "none"
            assert float(mark.attrs["cx"]) == pytest.approx(x_of(day), abs=0.06)
            assert float(mark.attrs["cy"]) == pytest.approx(
                line_y_at(line, x_of(day)), abs=0.06
            )
            assert pence_at(upper, float(mark.attrs["cy"])) == pytest.approx(
                minor, abs=pence_per_pixel(upper) * 1.5
            )

    def test_Loan_WhenAStatementClosesInJanuary_NothingIsHeldAtItsValueThroughTheYear(self, paid):
        _, root, _ = paid
        paths = [n for n in elements(svg_of(root, "bc-values"), "path") if "d" in n.attrs]
        longest_kept = 40 * PIXELS_PER_DAY

        for day in JANUARIES:
            y = float(mark_on(root, day).attrs["cy"])
            for path in paths:
                points = vertices(path)
                for earlier, later in pairwise(points):
                    held = abs(later[1] - y) < 0.3 and abs(earlier[1] - y) < 0.3
                    assert not (held and later[0] - earlier[0] > longest_kept), (
                        f"a line is held at the {day} closing for {later[0] - earlier[0]:.0f}"
                    )

    def test_Loan_WhenDrawn_NoKnownBalanceIsDrawnAsALineHeldToTheNextOne(self, paid):
        _, root, _ = paid
        series = {n.attrs["data-series"] for n in elements(svg_of(root, "bc-values"), "path")
                  if "data-series" in n.attrs}

        assert series == {"running"}

    def test_Loan_WhenDrawn_HasOneMarkPerKnownBalanceAtItsOwnDay(self, paid):
        _, root, _ = paid

        assert sorted(m.attrs["data-day"] for m in marks(root)) == sorted(
            d.isoformat() for d in [OPENED, *JANUARIES, CLOSE]
        )

    def test_Loan_WhenDrawn_DifferenceMarksStandOnlyAtKnownBalanceDaysAndAreNotJoined(self, paid):
        _, root, _ = paid
        chart_svg = svg_of(root, "bc-values")

        assert sorted(m.attrs["data-day"] for m in difference_marks(root)) == sorted(
            m.attrs["data-day"] for m in marks(root)
        )
        assert not [n for n in elements(chart_svg, "path")
                    if n.attrs.get("data-series") == "difference"]
        _, lower = label_scale(root)
        for mark in difference_marks(root):
            assert pence_at(lower, float(mark.attrs["cy"])) == pytest.approx(
                0, abs=pence_per_pixel(lower) * 1.5
            )

    def test_Loan_WhenAgreeing_SaysNothingOfAnyDifference(self, paid):
        assert "The running balance differs from" not in paid[1].text()


class TestAnArchivedAccountEndsAtItsClose:
    def test_Loan_WhenArchived_ChartEndsOnTheCloseWhateverAStatementPeriodSays(self, paid):
        chart, root, _ = paid

        assert chart.last_day == CLOSE
        text = root.text()
        assert f"Drawn from {OPENED} to {CLOSE}" in text
        assert f"closed on {CLOSE}" in text
        assert {m.attrs["data-day"] for m in marks(root)}.isdisjoint(
            {d.isoformat() for d, _ in STRAY}
        )
        width = float(svg_of(root, "bc-values").attrs["width"])
        days = (CLOSE - OPENED).days + 1
        assert width == pytest.approx(2 * EDGE + days * PIXELS_PER_DAY, abs=1)

    def test_Loan_WhenNotArchived_ChartRunsToTheLastKnownBalance(self):
        chart = loan(closed=None)
        root = parse(drawn(chart))

        assert chart.last_day == date(2026, 8, 10)
        assert {d.isoformat() for d, _ in STRAY} <= {m.attrs["data-day"] for m in marks(root)}
        assert "closed on" not in root.text()

    def test_Loan_WhenClosingDateIsBeforeEveryKnownBalance_IsNotUsedToCutTheChart(self):
        chart = loan(closed=date(2020, 1, 1))

        assert chart.last_day == date(2026, 8, 10)


class TestTheStoreFeedsTheSameChart:
    """The chart built from a store takes its rows, and the account's closing day, from it.

    Rows (value day, pence): 03-02 -1,250; 03-05 +10,000; 03-10 -2,000; 03-15 -3,300;
    03-20 +500. Stated: 03-10 at 100,000 (so the account stood at 93,250 before) and 03-31 at
    97,200 (93,250 + 3,950, which agrees). The account closed on 03-20, so the chart ends there
    and the 03-31 balance is not drawn; the line starts at the first known balance, 100,000,
    then 03-15 96,700 and 03-20 97,200.
    """

    def test_Chart_WhenBuiltFromAStoreOfAClosedAccount_EndsAtTheCloseWithOneStepPerRow(
        self, tmp_path
    ):
        with Store(tmp_path / "chart.sqlite3") as store:
            store.declare_account(
                AccountRecord(
                    ref=AccountRef("everyday"), label="Everyday", closed=date(2026, 3, 20)
                )
            )
            land(store, "digest", *(
                txn("everyday", "src-a", f"r{n}", day, amount, "ROW")
                for n, (day, amount) in enumerate(
                    [(date(2026, 3, 2), -1250), (date(2026, 3, 5), 10000),
                     (date(2026, 3, 10), -2000), (date(2026, 3, 15), -3300),
                     (date(2026, 3, 20), 500)]
                )
            ))
            record_stated_anchor(store, "everyday", "2026-03-10", "1000.00")
            record_stated_anchor(store, "everyday", "2026-03-31", "972.00")

            chart = build_balance_chart(store, "everyday")

        assert chart.closed == date(2026, 3, 20)
        assert chart.last_day == date(2026, 3, 20)
        assert dict(chart.running) == {
            date(2026, 3, 10): 100000,
            date(2026, 3, 15): 96700,
            date(2026, 3, 20): 97200,
        }


class TestTheLegendNamesWhatIsDrawn:
    def test_Legend_WhenAKnownBalanceDiffers_NamesTheLineTheTwoMarksTheTickAndTheDifferenceMarks(
        self, short
    ):
        text = short[1].text()

        for phrase in (
            "running balance",
            "filled mark",
            "hollow mark",
            "short vertical bar",
            "lower panel",
        ):
            assert phrase in text, phrase

    def test_Legend_WhenDrawn_NeverSaysABalanceIsHeldUntilTheNextOne(self, paid):
        assert "held until" not in paid[2]


class TestAClearingPaymentMissingFromTheRows:
    def test_Loan_WhenClearingPaymentIsMissing_LineEndsShortOfTheStatedNil(self, short):
        _, root, _ = short
        line = running_line(root)
        nil = mark_on(root, CLOSE)
        upper, _ = label_scale(root)

        assert len(steps_of(line)) == 37
        end = line_y_at(line, x_of(CLOSE))
        assert end > float(nil.attrs["cy"]) + 0.5
        assert pence_at(upper, end) == pytest.approx(-CLEARING, abs=pence_per_pixel(upper) * 1.5)

    def test_Loan_WhenClearingPaymentIsMissing_ATickJoinsTheNilMarkToTheLine(self, short):
        _, root, _ = short
        (tick,) = ticks(root)
        nil = mark_on(root, CLOSE)
        end = line_y_at(running_line(root), x_of(CLOSE))

        assert tick.attrs["data-day"] == CLOSE.isoformat()
        assert float(tick.attrs["x1"]) == float(tick.attrs["x2"]) == pytest.approx(
            x_of(CLOSE), abs=0.06
        )
        assert sorted([float(tick.attrs["y1"]), float(tick.attrs["y2"])]) == pytest.approx(
            sorted([float(nil.attrs["cy"]), end]), abs=0.06
        )

    def test_Loan_WhenClearingPaymentIsMissing_TheDifferenceMarkAtTheCloseShowsIt(self, short):
        _, root, _ = short
        _, lower = label_scale(root)
        (close,) = [m for m in difference_marks(root) if m.attrs["data-day"] == CLOSE.isoformat()]

        assert pence_at(lower, float(close.attrs["cy"])) == pytest.approx(
            CLEARING, abs=pence_per_pixel(lower) * 1.5
        )

    def test_Loan_WhenClearingPaymentIsMissing_PageSaysTheKnownBalanceDiffersFromTheTransactions(
        self, short
    ):
        text = short[1].text()

        assert "The running balance differs from 1 known balance" in text
        assert f"The latest, on {CLOSE}" in text
        assert "£48.00" in text
