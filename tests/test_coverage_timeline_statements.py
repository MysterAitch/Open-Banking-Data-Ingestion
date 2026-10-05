"""How the coverage timeline treats statements, and where its gaps come from.

Known answers are worked out from `coverage_timeline_world`'s docstring and from the owner's own
account of statements before the first run:

  - a statement covers its period whole, and its closing day is a complete day;
  - it is partial only where its stated end is later than the day it was received;
  - an opening balance equal to the previous closing is "balances meet" and proves nothing more,
    because a missing statement whose movements net to nil leaves them equal;
  - after the newest closing the next statement does not exist yet, which is not a gap.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from coverage_timeline_serve import served, timeline_of
from coverage_timeline_world import (
    CARD,
    CARD_NET_NIL_STATEMENTS,
    MAIN,
    TODAY,
    build_household,
)
from fetch_gaps_world import load_household
from obdi import coverage_timeline as ct
from obdi.fetch_gaps import GapKind, gaps_for_account
from obdi.store import Store
from obdi.web_coverage_timeline import render_account_timeline
from obdi.web_gaps import render_gaps


def d(text: str) -> date:
    return date.fromisoformat("2026-" + text)


def statement(
    closing: str,
    *,
    opening: int | None = 1000,
    closed: int | None = 2000,
    opens: str | None = None,
    received: datetime | None = None,
    first_row: str | None = None,
) -> ct._Statement:
    return ct._Statement(
        "digest", d(closing), opening, closed,
        opens=d(opens) if opens else None, received=received,
        first_row=d(first_row) if first_row else None,
    )


class TestStatementCover:
    def test_Closing_WhenReceivedAfterItsClose_IsACompleteDay(self):
        received = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
        cover = ct.statement_cover(statement("07-11", received=received), None)
        assert (cover.last, cover.last_state, cover.fraction) == (d("07-11"), ct.COMPLETE, None)
        assert cover.last_basis == ct.STATED

    def test_Closing_WhenReceivedOnItsCloseDay_IsStillACompleteDay(self):
        received = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)
        assert ct.statement_cover(statement("07-11", received=received), None).last_state == (
            ct.COMPLETE
        )

    def test_Closing_WhenItsStatedEndIsLaterThanTheDayItWasReceived_IsPartialToThatDay(self):
        # Received at 10:30 UTC on 07-08, which is 11:30 London time: 11.5 of 24 hours.
        received = datetime(2026, 7, 8, 10, 30, tzinfo=UTC)
        cover = ct.statement_cover(statement("07-11", first_row="07-01", received=received), None)
        assert (cover.first, cover.last, cover.last_state) == (d("07-01"), d("07-08"), ct.PARTIAL)
        assert cover.fraction == pytest.approx(11.5 / 24)

    def test_Closing_WhenNoReceiptIsKnown_IsACompleteDay(self):
        assert ct.statement_cover(statement("07-11"), None).last_state == ct.COMPLETE

    def test_Start_WhenTheFormatStatesAPeriod_IsStatedWhateverTheBalances(self):
        previous = statement("06-11", closed=5)
        cover = ct.statement_cover(
            statement("07-11", opening=5, opens="06-20", first_row="06-25"), previous
        )
        assert (cover.first, cover.first_basis) == (d("06-20"), ct.STATED)

    def test_Start_WhenBalancesMeet_IsTheDayAfterButOnlyBalancesMeet(self):
        previous = statement("06-11", closed=5)
        cover = ct.statement_cover(statement("07-11", opening=5, first_row="06-25"), previous)
        assert (cover.first, cover.first_basis) == (d("06-12"), ct.MEETS)

    def test_Start_WhenBalancesDiffer_IsTheFirstRowAndOnlyObserved(self):
        previous = statement("06-11", closed=5)
        cover = ct.statement_cover(statement("07-11", opening=6, first_row="06-25"), previous)
        assert (cover.first, cover.first_basis) == (d("06-25"), ct.OBSERVED)

    def test_Start_WhenThereIsNoPreviousStatement_IsTheFirstRowObserved(self):
        cover = ct.statement_cover(statement("07-11", first_row="06-20"), None)
        assert (cover.first, cover.first_basis) == (d("06-20"), ct.OBSERVED)

    def test_Basis_BalancesMeetIsWeakerThanAskedAndStrongerThanObserved(self):
        assert ct.WEAKNESS[ct.OBSERVED] < ct.WEAKNESS[ct.MEETS] < ct.WEAKNESS[ct.ASKED]


@pytest.fixture(scope="module")
def household(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_household(tmp_path_factory.mktemp("statements"))


@pytest.fixture(scope="module")
def net_nil(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_household(
        tmp_path_factory.mktemp("net-nil"), card_statements=CARD_NET_NIL_STATEMENTS
    )


def statements_lane(view: ct.AccountTimeline) -> ct.Lane:
    return next(item for item in view.lanes if item.kind == ct.STATEMENT)


class TestStatementsAreWhole:
    def test_Statements_NoClosingDayIsPartialAndNoStatementCarriesASeam(self, household):
        card = timeline_of(household, CARD, TODAY)
        assert card is not None
        lane = statements_lane(card)
        assert {c.last_state for c in lane.captures} == {ct.COMPLETE}
        assert not [s for s in card.seams if s.source == lane.source]

    def test_Export_WhenItIsNotAStatement_StillHasItsPossiblyCutLastDay(self, household):
        main = timeline_of(household, MAIN, TODAY)
        assert main is not None
        export = next(item for item in main.lanes if item.kind == ct.EXPORT)
        assert {c.last_state for c in export.captures} == {ct.POSSIBLY}


class TestBalancesMeet:
    def test_Statement_WhenTheMissingOneNetsToNil_StartsAsBalancesMeetAndTheGapIsStillNamed(
        self, net_nil
    ):
        card = timeline_of(net_nil, CARD, TODAY)
        assert card is not None
        lane = statements_lane(card)
        assert [(c.first, c.first_basis) for c in lane.captures] == [
            (d("05-15"), ct.OBSERVED),
            (d("06-12"), ct.MEETS),
            (d("07-12"), ct.MEETS),
        ]
        # The three join into one run, and the gap the fetch page names is not closed by it.
        assert [(r.first, r.last) for r in lane.runs] == [(d("05-15"), d("09-11"))]
        assert [(g.kind, g.first, g.last) for g in card.gaps] == [
            ("hole-between", d("07-12"), d("09-10"))
        ]

    def test_Statement_WhenBalancesDiffer_StartsAtItsFirstRowAndTheGapIsNamed(self, household):
        card = timeline_of(household, CARD, TODAY)
        assert card is not None
        fourth = statements_lane(card).captures[2]
        assert (fourth.first, fourth.first_basis) == (d("08-14"), ct.OBSERVED)
        assert [g.kind for g in card.gaps] == ["hole-between"]

    def test_Page_NamesBalancesMeetInItsKeyAndDrawsItsEdge(self, net_nil):
        view = timeline_of(net_nil, CARD, TODAY)
        assert view is not None
        page = render_account_timeline(
            view, fields={"window": "all", "window_held": "all"}
        ).decode()
        assert "Balances meet edge" in page
        assert "does not prove it" in page
        assert "cov-edge-meets" in page

    def test_Page_WhenNoStatementStartIsOnlyBalancesMeet_DoesNotMentionIt(self, household):
        view = timeline_of(household, MAIN, TODAY)
        assert view is not None
        page = render_account_timeline(
            view, fields={"window": "all", "window_held": "all"}
        ).decode()
        assert "Balances meet" not in page


class TestNotYetAvailable:
    @pytest.fixture(scope="class")
    def page(self, household: Path) -> str:
        view = timeline_of(household, CARD, TODAY)
        assert view is not None
        return render_account_timeline(
            view, fields={"window": "all", "window_held": "all"}
        ).decode()

    def test_Stretch_AfterTheNewestClose_IsDrawnQuietAndNotAsAGap(self, page):
        assert "cov-unavailable" in page
        assert "not available yet" in page
        # The one gap outline is the missing statement's; the quiet stretch is not a second.
        assert page.count('class="cov-gap cov-focus"') == 1

    def test_ExpectedClosing_IsADatedQuietMarkLabelledAsExpected(self, page):
        assert "expected 2026-10-11" in page
        assert "cov-expected-mark" in page

    def test_Verdict_SaysWhenTheNextStatementIsExpectedAndCountsOnlyTheRealGap(self, page):
        verdict = re.search(r'<p class="cov-verdict" data-verdict>([^<]*)</p>', page)
        assert verdict is not None
        assert "1 gap to fill" in verdict.group(1)
        assert "Next statement expected about 2026-10-11." in verdict.group(1)

    def test_Key_ExplainsTheQuietStretchAndTheExpectedMark(self, page):
        assert "Not available yet: the next statement does not exist until its period ends" in page
        assert "When the next statement is expected to close" in page

    def test_Verdict_WhenAStatementIsAlreadyWaiting_DoesNotSayExpected(self, tmp_path):
        view = timeline_of(build_household(tmp_path), CARD, TODAY)
        assert view is not None
        waiting = replace(
            view,
            gaps=(
                ct.Gap(
                    d("09-12"), TODAY, "newer-statement", "santander-cc-pdf", True, account=CARD
                ),
            ),
        )
        page = render_account_timeline(
            waiting, fields={"window": "all", "window_held": "all"}
        ).decode()
        assert "Next statement expected" not in page


def _spans_on_fetch_page(html_text: str) -> list[str]:
    return re.findall(r'<p class="gaps-range mono">([^<]*)</p>', html_text)


class TestOnePlaceForGaps:
    """The fetch page's household (every kind of gap, eleven accounts): for each account the two
    pages name the same gaps, with the same dates."""

    @pytest.fixture(scope="class")
    def world(self, tmp_path_factory: pytest.TempPathFactory):
        return load_household(tmp_path_factory.mktemp("one-place"))

    def test_Pages_ForEveryAccount_NameTheSameGapsWithTheSameDates(self, world):
        refs = sorted(
            {o.account for o in world.report.accounts if o.gaps}
        )
        assert len(refs) >= 8
        checked = 0
        for ref in refs:
            view = timeline_of(world.db, ref, TODAY)
            assert view is not None, ref
            timeline_page = render_account_timeline(
                view, fields={"window": "between", "window_held": "between",
                              "window_from": "2025-01-01", "window_to": TODAY.isoformat()},
            ).decode()
            outlook = world.outlook(ref)
            assert outlook is not None
            fetch_page = render_gaps(world.report, {}).decode()
            for gap in outlook.gaps:
                span = (
                    gap.first_day.isoformat()
                    if gap.first_day == gap.last_day
                    else f"{gap.first_day.isoformat()} to {gap.last_day.isoformat()}"
                )
                assert span in _spans_on_fetch_page(fetch_page), (ref, gap.kind)
                assert f"{ct.gap_anchor(ref, gap.kind, gap.first_day)}" in timeline_page, (
                    ref, gap.kind
                )
                assert f"for {span} (" in timeline_page, (ref, gap.kind, span)
                checked += 1
            named = re.findall(r'id="e-(gap-[^"]*)"', timeline_page)
            assert len(named) == len(outlook.gaps), (ref, named)
        assert checked == len(world.report.gaps)

    def test_Timeline_NamesNoFetchGapForAnAccountTheFetchPageSaysNeedsNothing(self, world):
        quiet = [o.account for o in world.report.accounts if not o.gaps and not o.balance_only]
        assert quiet
        for ref in quiet:
            view = timeline_of(world.db, ref, TODAY)
            assert view is not None
            assert not [g for g in view.gaps if g.kind != ct.ASK_HOLE], ref

    def test_GapAnchor_DependsOnAccountKindAndFirstDayAlone(self):
        assert ct.gap_anchor("card one", "hole-between", d("07-12")) == (
            "gap-card-one-hole-between-2026-07-12"
        )
        assert ct.gap_anchor("card one", "hole-between", d("07-12")) == ct.gap_anchor(
            "card one", "hole-between", d("07-12")
        )
        assert ct.gap_anchor("card one", "hole-between", d("07-13")) != ct.gap_anchor(
            "card one", "hole-between", d("07-12")
        )


class TestFollowingTheFetchPagesLink:
    def test_Link_FromTheFetchPage_LandsOnTheGapsOwnSentenceAndMark(self, household):
        from obdi.web_gaps import _timeline_link

        with Store(household) as store:
            (gap,) = gaps_for_account(store, CARD, TODAY)
        assert gap.kind is GapKind.HOLE_BETWEEN
        link = _timeline_link(CARD, gap, TODAY)
        href = re.search(r'href="([^"]*)"', link)
        assert href is not None
        address = href.group(1).replace("&amp;", "&")
        path, _, fragment = address.partition("#")
        assert fragment == f"e-{ct.gap_anchor(CARD, str(gap.kind), gap.first_day)}"
        with served(household, TODAY) as base:
            text = httpx.get(f"{base}{path}", timeout=60).text
        assert f'id="{fragment}"' in text
        assert f'id="m-{fragment[2:]}"' in text
        assert f'<a href="#{fragment}" id="m-{fragment[2:]}"' in text
