"""The structure of the differences over the invented household of `test_export_dating`.

That household opened at nil, is fed by the bank's feed, and is stated by an
export blind to the Spaces. Each variant plants one defect with a known
consequence, so the chart's structure has a KNOWN ANSWER here as it has over the
walk-only corpus. The export dates five payments a day or two either side of the
feed, which sighting placement absorbs; with placement defeated on purpose they
become five timing pairs, which is the shape the structure exists to name.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

import pytest

import obdi.balance_anchors as balance_anchors
from obdi.balance_anchors import STATED, Anchor, derive_opening
from obdi.balance_chart import build_balance_chart
from obdi.core.models import Transaction, TransactionStatus
from obdi.family_anchors import families_of
from obdi.fault_structure import (
    EXPLAINED,
    TRANSIENT,
    UNHELD,
    account_report,
)
from obdi.sighting_placement import SightingPlacement
from test_balance_chart_pages import Parsed
from test_export_dating import (
    MAIN,
    MAP,
    PAYMENTS,
    Payment,
    build,
    render,
    set_status,
    surplus,
)
from test_family_anchors import leg


@pytest.fixture
def make(tmp_path):
    opened = []

    def made(**kwargs):
        store = build(tmp_path, **kwargs)
        opened.append(store)
        return store

    yield made
    for store in opened:
        store.close()


def chart_of(store):
    return build_balance_chart(store, MAIN, families=families_of(store, MAP))


@pytest.fixture
def placement_defeated(monkeypatch):
    """Every row counts on its stored date, as if no source's dating were known."""
    monkeypatch.setattr(balance_anchors, "sighting_placement", lambda *a, **k: SightingPlacement())


#: Payments the export and the feed date alike, and one the store dates three
#: days after the export does.
AGREED = [p for p in PAYMENTS if p.feed_day == p.export_day]
LATE = Payment("Late", -2500, 10, 13)


class TestOneRowTheExportListsButTheStoreVoided:
    def test_Chart_WhenARowIsVoided_HasOneStepThatTheLedgersArithmeticExplains(self, make):
        store = make()
        set_status(store, "Garage", TransactionStatus.VOID)

        chart = chart_of(store)

        assert (chart.state, chart.scope) == ("ok", "whole")
        (step,) = chart.structure.whole.steps
        assert (step.day, step.size_minor) == (date(2026, 9, 14), -9000)
        assert (step.kind, step.explained, step.unheld) == (EXPLAINED, True, False)
        assert chart.structure.whole.present_minor == -9000

    def test_Chart_WhenNothingIsWrong_HasNoSteps(self, make):
        chart = chart_of(make())

        assert chart.structure.whole.steps == ()
        assert chart.structure.whole.share_agreeing == 1.0


class TestARowTheStoreDatesLaterThanTheExport:
    def test_Chart_WhenPlacementIsDefeatedAndOneRowIsThreeDaysLate_IsOnePairWithGapThree(
        self, make, placement_defeated
    ):
        found = chart_of(make(payments=[*AGREED, LATE])).structure.whole

        first, second = found.steps
        assert (first.day, first.size_minor) == (date(2026, 9, 10), 2500)
        assert (second.day, second.size_minor) == (date(2026, 9, 13), -2500)
        assert (first.kind, second.kind) == (TRANSIENT, TRANSIENT)
        assert found.pairs == 1 and found.gap_counts == (0, 0, 1, 0, 0)
        assert found.permanent == () and found.present_minor == 0

    def test_Chart_WhenPlacementIsLeftOn_TheSameRowIsNotAFaultAtAll(self, make):
        found = chart_of(make(payments=[*AGREED, LATE])).structure.whole

        assert found.steps == ()

    def test_Chart_WhenPlacementIsDefeatedOverTheNaturalCorpus_IsFiveTimingPairsNettingToNil(
        self, make, placement_defeated
    ):
        # The five payments the export dates a day either side of the feed: Cafe,
        # Refund, Water, and Rent with Grocer, which share a day and so one pair.
        found = chart_of(make(payments=[p for p in PAYMENTS if p.name != "Gym"])).structure.whole

        assert [s.day.day for s in found.steps] == [4, 5, 7, 8, 14, 15, 20, 21]
        assert found.pairs == 4
        assert found.gap_counts == (4, 0, 0, 0, 0)
        assert found.permanent == ()
        assert found.present_minor == 0 == found.permanent_sum_minor


class TestATransferToASpaceWhoseRowsAreNotHeld:
    def test_Chart_WhenALegGoesToAnUnheldSpace_ItsStepIsPermanentAndNamedForIt(self, make):
        chart = chart_of(make(feed_only=[leg(MAIN, -4700, 12, "cat-closed", "f-gone")]))

        found = chart.structure.whole
        (step,) = found.steps
        assert (step.day, step.kind, step.unheld) == (date(2026, 9, 12), UNHELD, True)
        assert found.permanent_unheld == (step,)
        assert found.exact


class TestTheLedgerPageSummarisesTheStructure:
    def test_Page_WhenARowIsVoided_SaysHowManyBalancesMoveAsTheRowsDoAndLinksTheTimeline(
        self, make
    ):
        store = make()
        set_status(store, "Garage", TransactionStatus.VOID)

        page = Parsed(render(store))

        assert "of the 29 known balances" in page.text
        assert "move from the one before by exactly what the rows move by" in page.text
        links = [a for a in page.find("a") if a.get("href", "").startswith("/balance-chart")]
        assert [(a["href"], a["target"], a["rel"]) for a in links] == [
            (f"/balance-chart?ref={MAIN}", "_blank", "noopener")
        ]
        assert "With the pairs cancelled, 1 permanent change remains" in page.text

    def test_Page_WhenARowIsVoided_ItsStructureSectionCarriesNoFigure(self, make):
        store = make()
        set_status(store, "Garage", TransactionStatus.VOID)

        page = Parsed(render(store))
        summary = page.text[
            page.text.index("The structure of the whole account") : page.text.index(
                "Timeline of the differences"
            )
        ]

        for figure in ("90.00", "9,000", "9000"):
            assert figure not in summary
        assert "£" not in summary

    def test_Page_WhenNothingDiffers_HasNoStructureSection(self, make):
        assert "The structure of the" not in render(make())

    def test_Page_WhenManyDaysChange_StillSummarisesInsteadOfListingThemAll(self, tmp_path):
        phantoms = [surplus(f"Phantom{d}", -(7 + d), d) for d in range(3, 28)]
        store = build(tmp_path, feed_only=phantoms)
        try:
            page = Parsed(render(store))
        finally:
            store.close()

        assert "The structure of the whole account" in page.text
        assert not re.search(r"\b25 of the \d+ known balances", page.text)
        assert "the other 25 are the 25 changes in the difference" in page.text


class TestTheAccountsOwnBalances:
    def rows(self) -> list[Transaction]:
        from obdi.core.models import SourceTier
        from obdi.identity import content_key

        def row(day: int, minor: int) -> Transaction:
            when = date(2026, 3, day)
            return Transaction(
                account_id="acc", amount_minor=minor, value_date=when, booking_date=when,
                description=f"r{day}", source="s", source_id=f"r{day}",
                content_key=content_key(amount_minor=minor, value_date=when, description=f"r{day}"),
                tier=SourceTier.AUTHORITATIVE, status=TransactionStatus.BOOKED,
            )

        return [row(2, -500), row(5, 300)]

    def test_Report_WhenALaterStatedBalanceDiffers_HasOneStepAtItsDayNamedForItsBasis(self):
        anchors = [
            Anchor(date(2026, 3, 1), 1000, STATED),
            Anchor(date(2026, 3, 3), 600, STATED),
            Anchor(date(2026, 3, 6), 900, STATED),
        ]

        opening = derive_opening("acc", anchors, self.rows())
        report = account_report(opening)

        (step,) = report.whole.steps
        assert (step.day, step.size_minor, step.source) == (date(2026, 3, 3), 100, STATED)
        assert report.whole.present_minor == 100
        assert replace(step, size_minor=0).kind == "unexplained"
        assert report.by_source == ()
