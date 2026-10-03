"""The statement-periods page says what the same-money rule did at each closing.

The rule folded nothing on a real card and nothing said why. These tests build
invented cards with known answers (`card_chain_corpus`, `card_variant_corpus`)
and ask the page, in dates, counts, and source names only, what the rule saw and
where it stopped. The outcome is the rule's own (`same_money_fold` builds it
while it decides), so a verdict here is the rule's verdict, not the page's guess.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

import obdi.same_money_fold as same_money_fold
from card_chain_corpus import CHARGE, CLOSINGS, build_card, feed_row, land, statement_day
from card_variant_corpus import CLOSINGS as MINI_CLOSINGS
from card_variant_corpus import Variant, build_mini_card
from obdi.period_reconciliation import dated_list, gather_evidence, period_reconciliation
from obdi.rebuild import rebuild_from_raw
from obdi.same_money_fold import fold_same_money, plan_same_money
from obdi.same_money_outcome import AccountOutcome, Verdict
from obdi.store import SCHEMA_VERSION, Store
from test_period_reconciliation import MONEY_FIGURE, _held_statement


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "outcomes.sqlite3") as opened:
        yield opened


def page(store: Store) -> str:
    return period_reconciliation(store, sibling_accounts={}).describe(masked=True)


def closing_line(text: str, closing: date) -> str:
    [found] = [line.strip() for line in text.splitlines() if f"Closing {closing}:" in line]
    return found


def verdicts(store: Store) -> list[Verdict]:
    """The verdicts the last pass recorded, which is what the page shows."""
    kept = store.same_money_outcome("card")
    assert kept is not None
    return [closing.verdict for closing in AccountOutcome.from_text(kept).closings]


class TestWhereTheRuleFolds:
    def test_Page_WhenTheRuleFoldsEveryCharge_SaysEachClosingFoldedWithTheFeedRowsDate(
        self, store, tmp_path
    ):
        build_card(store, tmp_path)
        fold_same_money(store)

        text = page(store)

        for closing in CLOSINGS:
            line = closing_line(text, closing)
            assert "the rule folds 1 feed row from truelayer" in line
            assert f"(dated {closing + timedelta(days=1)})" in line
        assert "refused" not in text

    def test_Page_BeforeAnyPassHasRun_SaysNothingWasRecordedRatherThanGuessing(
        self, store, tmp_path
    ):
        build_card(store, tmp_path)
        store.clear_same_money_outcomes()
        store.connection.commit()

        text = page(store)

        assert "has recorded nothing for this account yet" in text
        assert "Closing 2025-10-10" not in text

    def test_Page_WhenTheFeedLandedAfterTheLastPass_SaysThePassPredatesItUntilOneRuns(
        self, store, tmp_path
    ):
        _held_statement(
            store, tmp_path, "s0", "10th Jun 2026", 10000, [("5th Jun", "Plain One", 1000)]
        )
        _held_statement(
            store, tmp_path, "s1", "10th Jul 2026", 11000, [("5th Jul", "Plain Two", 1100)]
        )
        land(store, feed_row(1234, date(2026, 6, 20), "Late Feed Row"))

        before = page(store)
        fold_same_money(store)
        after = page(store)

        assert "last ran when no feed held rows for this account, and truelayer does now" in before
        assert "last ran when" not in after
        assert "Closing 2026-07-10:" in after

    def test_Rebuild_AfterWipingTheRecord_RewritesItWithItsClosingPass(self, store, tmp_path):
        """The feed rows of this corpus are not raw artefacts, so the rebuilt
        store holds only the statements; the record is nonetheless rewritten."""
        build_card(store, tmp_path)
        store.replace_same_money_outcomes({"card": "stale"})
        store.connection.commit()

        rebuild_from_raw(store)

        kept = store.same_money_outcome("card")
        assert kept is not None
        assert AccountOutcome.from_text(kept).account == "card"


class TestWhereTheRuleStops:
    def test_Page_WhenNoSubsetOfTheFeedRowsSumsToAnyOfTheStatementRows_SaysSoWithBothDateLists(
        self, store, tmp_path
    ):
        """One statement's feed row is a penny off, so it matches nothing."""
        build_card(store, tmp_path, feed_charges={3: CHARGE + 1})
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[3])

        assert line == (
            "Closing 2026-01-12: 1 unmatched feed row from truelayer in the band "
            "2026-01-10 to 2026-01-14 (dated 2026-01-13), and 3 statement-only rows in the "
            "period (dated 2026-01-12, 2026-01-12, 2026-01-12): no subset of the one sums "
            "to any subset of the other."
        )

    def test_Page_WhenNoFeedRowIsDatedNearTheClosing_SaysTheBandAndThatNothingWasToFold(
        self, store, tmp_path
    ):
        build_card(store, tmp_path, charge_offset=3)
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[1])

        assert line == (
            "Closing 2025-11-12: no unmatched feed row from truelayer within the band "
            "2025-11-10 to 2025-11-14, so there is nothing to fold."
        )

    def test_Page_WhenACandidateIsRefused_NamesThePeriodsThatWouldStillDiffer(
        self, store, tmp_path
    ):
        """A stray purchase dated 1 June sits in the period closing 10 June, so
        the folds that touch that period are refused and the page names it."""
        build_card(
            store,
            tmp_path,
            extra_feed=[feed_row(333, date(2026, 6, 1), "Stray Purchase")],
        )
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[6])

        assert "a candidate was found (1 feed row from truelayer dated 2026-05-13" in line
        assert "the period 2026-05-13 to 2026-06-10 would still differ" in line
        assert "would still differ after folding" in line

    def test_Page_WhenAnEarlierClosingClaimedTheBandsRow_SaysWhichRowWasClaimed(
        self, store, tmp_path
    ):
        _held_statement(
            store, tmp_path, "s0", "10th May 2026", 10000, [("1st May", "Plain Purchase", 2231)]
        )
        _held_statement(
            store,
            tmp_path,
            "s1",
            statement_day(date(2026, 6, 10)),
            12231,
            [("10th Jun", "Part A", 389), ("10th Jun", "Part B", 111)],
        )
        _held_statement(
            store,
            tmp_path,
            "s2",
            statement_day(date(2026, 6, 11)),
            12731,
            [("11th Jun", "Part C", 678), ("11th Jun", "Part D", 222)],
        )
        land(
            store,
            feed_row(2231, date(2026, 5, 1), "Plain Purchase"),
            feed_row(500, date(2026, 6, 11), "Combined One"),
            feed_row(900, date(2026, 6, 12), "Combined Two"),
        )
        fold_same_money(store)

        line = closing_line(page(store), date(2026, 6, 11))

        assert "the rule folds 1 feed row from truelayer (dated 2026-06-12)" in line
        assert (
            "1 feed row dated 2026-06-11 in the band had been claimed by an earlier closing"
            in line
        )


class TestWhereTheSearchIsBounded:
    def test_Page_WhenTheStatementRowsExceedTheBound_SaysHowManyWereSearched(
        self, store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(same_money_fold, "MAX_STATEMENT_ROWS", 2)
        build_card(store, tmp_path)
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[2])

        assert (
            "The search was bounded: only the nearest 2 of 3 statement-only rows were searched."
            in line
        )
        assert "no subset of the one sums to any subset of the other" in line

    def test_Page_WhenTheBandRowsExceedTheBound_SaysHowManyWereSearched(
        self, store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(same_money_fold, "MAX_BAND_ROWS", 1)
        build_mini_card(store, tmp_path, Variant.ITEMISED_TWICE)
        fold_same_money(store)

        line = closing_line(page(store), MINI_CLOSINGS[1])

        assert (
            "The search was bounded: only the nearest 1 of 2 unmatched feed rows in the "
            "band were searched." in line
        )


class TestWhereTheRuleDoesNotRun:
    def test_Page_WhenOnlyOneStatementIsHeld_SaysTheRuleDidNotRunAndWhy(self, store, tmp_path):
        build_card(store, tmp_path, skip=frozenset(range(1, 9)))
        fold_same_money(store)

        text = page(store)

        assert "The same-money rule did not run: Only one statement is held" in text


class TestEachWayACardCanDifferFromTheNineStatementOne:
    """The variants in `card_variant_corpus`, each with the rule's measured answer.

    Every answer below was predicted first and then measured; two predictions
    were wrong and are recorded in the corpus module.
    """

    @pytest.mark.parametrize(
        ("variant", "expected_folds", "expected_verdicts"),
        [
            (Variant.ITEMISED, 4, [Verdict.FOLDED] * 4),
            (Variant.FEED_TWICE, 0, [Verdict.NO_MATCH] * 4),
            (Variant.ITEMISED_TWICE, 0, [Verdict.REFUSED] * 4),
            (Variant.SAME_AMOUNT_BOTH, 0, [Verdict.NO_BAND_ROWS] * 4),
            (Variant.TWIN_LATE, 0, [Verdict.NO_BAND_ROWS] * 4),
        ],
    )
    def test_Rule_OnEachVariantOfTheCard_FoldsAndStopsWhereTheCorpusSays(
        self, store, tmp_path, variant, expected_folds, expected_verdicts
    ):
        build_mini_card(store, tmp_path, variant)

        report = fold_same_money(store)

        assert report.folded == expected_folds
        assert verdicts(store) == expected_verdicts

    def test_Page_WhenTheFeedPostsTheChargeTwice_ShowsTheShapeTheRealCardReported(
        self, store, tmp_path
    ):
        """One feed row dated the period's first day AND one row dated its
        closing day, both of the difference's size: the closing-day row is held
        by both sources, so it is not a leftover, and no statement-only row exists
        for the rule to match the feed row to."""
        build_mini_card(store, tmp_path, Variant.FEED_TWICE)
        fold_same_money(store)

        text = page(store)

        assert closing_line(text, MINI_CLOSINGS[1]) == (
            "Closing 2026-02-10: 1 unmatched feed row from truelayer in the band "
            "2026-02-08 to 2026-02-12 (dated 2026-02-11), and no statement-only row in the "
            "period: no subset of the one sums to any subset of the other."
        )
        assert "Feed-only rows are dated: 2026-01-11 (truelayer)." in text
        assert (
            "The difference equals a single row held in this period: dated 2026-01-11, "
            "held by truelayer (one of this period's feed-only leftovers); dated 2026-02-10, "
            "held by santander-cc-pdf and truelayer (a row both sources hold, so not a "
            "leftover)." in text
        )

    def test_Page_WhenTheFeedPostsTheChargeTwiceAndTheStatementItemisesIt_NamesTheBlockingPeriod(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.ITEMISED_TWICE)
        fold_same_money(store)

        line = closing_line(page(store), MINI_CLOSINGS[0])

        assert "a candidate was found (1 feed row from truelayer dated 2026-01-11" in line
        assert "the period 2026-01-11 to 2026-02-10 would still differ after folding" in line


class TestTheLeftoversAreListedByDateAndSource:
    def test_Page_ForEachDifferingPeriod_ListsTheDatesAndSourcesOfBothLeftoverSides(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.FEED_TWICE)
        fold_same_money(store)

        text = page(store)

        assert "Feed-only rows are dated: 2026-02-11 (truelayer)." in text

    def test_Page_ForAPeriodWithStatementOnlyRows_ListsTheirDatesAndTheStatementSource(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.ITEMISED_TWICE)
        fold_same_money(store)

        text = page(store)

        assert (
            "Statement-only rows are dated: 2026-02-10 (santander-cc-pdf), "
            "2026-02-10 (santander-cc-pdf), 2026-02-10 (santander-cc-pdf)." in text
        )

    def test_Page_WhenAListIsLong_ShowsTwentyAndSaysHowManyMore(self, store, tmp_path):
        """The month the statement is missing holds thirty-one feed-only rows
        once the charge dated 12 February is folded."""
        build_card(store, tmp_path)
        fold_same_money(store)

        text = page(store)

        [listing] = [
            line
            for line in text.splitlines()
            if line.strip().startswith("Feed-only rows are dated: 2026-02-13")
        ]
        assert listing.endswith(", and 11 more.")
        assert listing.count("(truelayer)") == 20

    @pytest.mark.parametrize(("count", "tail"), [(20, "2026-01-20"), (21, ", and 1 more")])
    def test_DatedList_AtAndOverTheCap_StopsAtTwentyEntries(self, count, tail):
        days = [date(2026, 1, 1) + timedelta(days=index) for index in range(count)]

        listed = dated_list(days)

        assert listed.endswith(tail)
        assert listed.count("2026-") == min(count, 20)


class TestTheMaskedPageStillCarriesNoValues:
    @pytest.mark.parametrize("variant", list(Variant))
    def test_Page_OnEveryVariant_ContainsNoAmountOrDescription(self, store, tmp_path, variant):
        build_mini_card(store, tmp_path, variant)
        fold_same_money(store)

        text = page(store)

        assert MONEY_FIGURE.search(text) is None, MONEY_FIGURE.search(text)
        for private in ("6.95", "695", "Plan Charge", "Plan Part", "Ordinary", "Late Twin"):
            assert private not in text, private
        assert re.search(r"Closing \d{4}-\d\d-\d\d:", text)


class TestTheRecordIsDerivedData:
    @pytest.mark.parametrize(
        "variant", [Variant.ITEMISED, Variant.FEED_TWICE, Variant.ITEMISED_TWICE, Variant.TWIN_LATE]
    )
    def test_Outcome_RoundTripsThroughItsStoredText_ForEachVerdict(
        self, store, tmp_path, variant
    ):
        build_mini_card(store, tmp_path, variant)
        [outcome] = plan_same_money(gather_evidence(store, sibling_accounts={})).outcomes

        assert AccountOutcome.from_text(outcome.to_text()) == outcome

    def test_Page_WhenTheStoredRecordIsDamaged_SaysSoRatherThanShowingAGuess(
        self, store, tmp_path
    ):
        build_card(store, tmp_path)
        store.replace_same_money_outcomes({"card": "not json"})
        store.connection.commit()

        text = page(store)

        assert "record for this account cannot be read" in text

    def test_Store_OpenedAtTheVersionBeforeTheTable_GrowsItOnOpen(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path):
            pass
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE same_money_outcomes")
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            assert reopened.same_money_outcome("card") is None
