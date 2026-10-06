"""How the coverage timeline treats statements, and where its gaps come from.

Known answers are worked out from `coverage_timeline_world`'s docstring and from the owner's own
account of statements. What a statement covers is `statement_span`'s to say (its own tests hold
the rules); what is held here is that the lanes draw exactly that:

  - a statement covers its period whole, and its closing day is a complete day;
  - an opening balance equal to the previous closing is "balances meet" only where the closings
    are a period apart, and never closes a hole: with the closings two periods apart it is a
    probable hole whose movements net to nil;
  - unequal balances prove a hole, whose end is inferred;
  - after the newest closing the next statement does not exist yet, which is not a gap.

THE CARD, by hand, with three statements closing 06-11, 07-11 and 09-11 (so a monthly cadence of
30 days, the lower median of 30 and 62): the first has nothing before it; the second opens on the
first's closing balance a period on, so it begins 06-12 as "balances meet"; the third opens on a
different balance, which proves a statement is missing: a hole from 07-12 whose end is inferred as
the closing day the missing statement is expected to have had, 08-11, and the third is taken to
begin the day after, 08-12, as inferred. The next closes 10-11.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
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
from obdi.account_names import AccountsShown
from obdi.bring_in import BALANCE_KINDS
from obdi.fetch_gaps import GapKind, gaps_for_account
from obdi.fetch_reasons import gap_lines
from obdi.statement_span import Known as SpanKnown
from obdi.statement_span import Span
from obdi.store import Store
from obdi.web_bring_in import BringInData, render_bring_in
from obdi.web_coverage_timeline import render_account_timeline


def d(text: str) -> date:
    return date.fromisoformat("2026-" + text)


def span(
    closing: str,
    *,
    first: str | None = "06-12",
    first_known: SpanKnown = SpanKnown.STATED,
    last: str | None = None,
    last_known: SpanKnown = SpanKnown.STATED,
    complete: bool = True,
) -> Span:
    return Span(
        "card", d(closing), "santander-cc-pdf", d(first) if first else None, first_known,
        d(last or closing), last_known, complete,
    )


class TestCaptureOf:
    """The lane makes no statement rule of its own: it draws what `statement_span` says."""

    def test_WholeStatement_IsACompleteLastDayWhateverKindOfFileItIs(self):
        capture = ct.capture_of(span("07-11"))
        assert (capture.first, capture.last, capture.last_state) == (
            d("06-12"), d("07-11"), ct.COMPLETE
        )
        assert capture.last_basis == ct.STATED

    def test_PartialStatement_IsDrawnWithALastDayThatMayBeCutAndAnInferredEnd(self):
        capture = ct.capture_of(
            span("07-11", last="07-08", last_known=SpanKnown.INFERRED, complete=False)
        )
        assert (capture.last, capture.last_state, capture.last_basis) == (
            d("07-08"), ct.POSSIBLY, ct.INFERRED
        )

    @pytest.mark.parametrize(
        ("known", "basis"),
        [
            (SpanKnown.STATED, ct.STATED),
            (SpanKnown.BALANCES_MEET, ct.MEETS),
            (SpanKnown.OBSERVED, ct.OBSERVED),
            (SpanKnown.INFERRED, ct.INFERRED),
        ],
    )
    def test_EachWayAStartIsKnown_IsItsOwnEdge(self, known, basis):
        assert ct.capture_of(span("07-11", first_known=known)).first_basis == basis

    def test_StatementNothingPlaces_IsDrawnOnItsClosingDayAloneAsInferred(self):
        capture = ct.capture_of(span("07-11", first=None))
        assert (capture.first, capture.last, capture.first_basis) == (
            d("07-11"), d("07-11"), ct.INFERRED
        )

    def test_Sections_OfAnAllAccountsStatement_ShareOneLaneNoParserNames(self):
        nameless = Span("card", d("07-11"), "", d("06-12"), SpanKnown.STATED, d("07-11"),
                        SpanKnown.STATED)
        assert ct.span_source(nameless) == "statement-pdf"
        assert ct.kind_of_source("statement-pdf") == ct.STATEMENT

    def test_Basis_FromStrongestToWeakest_IsStatedAskedMeetsObservedInferred(self):
        order = sorted(ct.WEAKNESS, key=ct.WEAKNESS.__getitem__, reverse=True)
        assert order == [ct.STATED, ct.ASKED, ct.MEETS, ct.OBSERVED, ct.INFERRED]


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
        # Closings 07-11 and 09-11 are two periods apart, so equal balances are not a meeting: the
        # third statement begins the day after the closing the missing one would have had.
        assert [(c.first, c.first_basis) for c in lane.captures][1:] == [
            (d("06-12"), ct.MEETS),
            (d("08-12"), ct.INFERRED),
        ]
        assert [(g.kind, g.first, g.last, g.reason, g.last_inferred) for g in card.gaps] == [
            ("hole-between", d("07-12"), d("08-11"), "balances-meet-net-nil", True)
        ]

    def test_Statement_WhenBalancesDiffer_StartsWhereTheMissingOneEndsAndTheHoleIsStated(
        self, household
    ):
        card = timeline_of(household, CARD, TODAY)
        assert card is not None
        third = statements_lane(card).captures[2]
        assert (third.first, third.first_basis) == (d("08-12"), ct.INFERRED)
        assert [(g.kind, g.stated, g.reason) for g in card.gaps] == [
            ("hole-between", True, "balances-differ")
        ]

    def test_Page_NamesBalancesMeetInItsKeyAndDrawsItsEdge(self, net_nil):
        view = timeline_of(net_nil, CARD, TODAY)
        assert view is not None
        page = render_account_timeline(
            view, fields={"window": "all", "window_held": "all"}
        ).decode()
        assert "Balances meet edge" in page
        assert "does not prove it" in page
        assert "cov-edge-meets" in page
        assert "Inferred edge" in page and "from how regularly statements arrive" in page

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

    def test_HoleSentence_WhenTheBalancesDiffer_SaysTheFactAndKeepsOnlyTheEndAsAGuess(self, page):
        assert "The statements either side do not meet" in page
        assert "Where the missing statement ends is inferred" in page
        assert "This is inferred from how regularly the statements held arrive." not in page

    def test_HoleSentence_WhenTheBalancesAreEqual_SaysProbableAndNeverThatNothingIsMissing(
        self, net_nil
    ):
        view = timeline_of(net_nil, CARD, TODAY)
        assert view is not None
        text = render_account_timeline(
            view, fields={"window": "all", "window_held": "all"}
        ).decode()
        assert "do not meet" not in text
        assert "a missing statement whose movements net to nil is probable" in text
        assert "nothing is missing" not in text.lower()

    def test_Hole_WhoseEndIsAGuess_IsDrawnWithAFirmStartAndAnOpenEnd(self, page):
        assert '<polyline class="cov-gap cov-focus"' in page
        assert 'class="cov-gap-firm"' in page

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


def _spans_in_reasons(html_text: str) -> list[str]:
    """The days each file gap is worded with, in Bring in's fold of why each is wanted."""
    return re.findall(r'<span class="mono bi-range">([^<]*)</span>', html_text)


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
            fetch_page = render_bring_in(
                BringInData(today=TODAY, report=world.report, unread="", names=AccountsShown())
            ).decode()
            for gap in outlook.gaps:
                span = (
                    gap.first_day.isoformat()
                    if gap.first_day == gap.last_day
                    else f"{gap.first_day.isoformat()} to {gap.last_day.isoformat()}"
                )
                assert gap_lines(gap)[0] == span, (ref, gap.kind)
                if gap.kind not in BALANCE_KINDS:
                    assert span in _spans_in_reasons(fetch_page), (ref, gap.kind)
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


class TestFollowingTheTimelinesLinkToBringIn:
    def test_Link_FromTheTimelinesGapEntry_NamesTheAccountsBlockOnBringIn(self, household):
        """The block's anchor is `account-` and the reference, which `test_bring_in_page` holds
        the page to; the timeline's entry for a file wanted leads there and not to `/gaps`."""
        with Store(household) as store:
            (gap,) = gaps_for_account(store, CARD, TODAY)
        assert gap.kind is GapKind.HOLE_BETWEEN
        with served(household, TODAY) as base:
            timeline = httpx.get(f"{base}/coverage-timeline?ref={CARD}", timeout=60).text

        assert f'href="/bring-in#account-{CARD}"' in timeline
        assert 'href="/gaps"' not in timeline
