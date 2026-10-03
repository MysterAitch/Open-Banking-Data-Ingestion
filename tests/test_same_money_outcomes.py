"""The statement-periods page says what the same-money rule did at each closing.

The rule folded nothing on a real card, twice, and nothing said why. These tests
build invented cards with known answers (`card_chain_corpus`,
`card_variant_corpus`) and ask the page, in dates, counts, and source names only,
what the rule saw and where it stopped. The outcome is the rule's own
(`same_money_fold` builds it while it decides), so a verdict here is the rule's
verdict, not the page's guess.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

import obdi.same_money_fold as same_money_fold
from card_chain_corpus import (
    CARD,
    CHARGES,
    CLOSINGS,
    build_card,
    feed_row,
    first_day_of,
    land,
    pair_with_savings,
)
from card_variant_corpus import CLOSINGS as MINI_CLOSINGS
from card_variant_corpus import SIBLING_SCOPE, Variant, build_mini_card
from obdi.models import TransactionStatus
from obdi.period_reconciliation import (
    PeriodKind,
    dated_list,
    gather_evidence,
    period_reconciliation,
)
from obdi.rebuild import rebuild_from_raw
from obdi.same_money_fold import (
    _Attempt,
    _Candidate,
    _outcome_of,
    _proven,
    fold_same_money,
    plan_same_money,
)
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


def folded(store: Store) -> list[str]:
    return sorted(
        t.description
        for t in store.transactions_for_account(CARD)
        if t.status is TransactionStatus.FOLDED
    )


class TestWhereTheRuleFolds:
    def test_Page_WhenTheRuleFoldsEveryCharge_SaysEachClosingFoldedWithTheFeedRowsDate(
        self, store, tmp_path
    ):
        """The first period already agrees; each later closing folds the one feed
        row dated its period's first day, except the one whose period spans the
        missing statement, where it is dated 12 March."""
        build_card(store, tmp_path)
        fold_same_money(store)

        text = page(store)

        assert (
            f"Closing {CLOSINGS[0]}: the period 2025-09-19 to {CLOSINGS[0]} already agrees"
            in text
        )
        for position, closing in enumerate(CLOSINGS[1:], start=1):
            line = closing_line(text, closing)
            dated = date(2026, 3, 12) if position == 5 else first_day_of(position)
            assert f"the rule folds 1 feed row from truelayer (dated {dated})" in line
            assert f"the period {first_day_of(position)} to {closing} then agrees" in line
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
    def test_Page_WhenNoSubsetOfTheFeedRowsMakesThePeriodAgree_SaysSoWithBothDateLists(
        self, store, tmp_path
    ):
        """One statement's feed row is a penny off, so it matches nothing."""
        build_card(store, tmp_path, feed_charges={3: CHARGES[3] + 1})
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[3])

        assert line == (
            "Closing 2026-01-12: the period 2025-12-11 to 2026-01-12 holds 1 unmatched feed "
            "row from truelayer (dated 2025-12-11) and 3 statement-only rows (dated "
            "2026-01-12, 2026-01-12, 2026-01-12): no subset of the feed rows sums to the "
            "period's difference and to some subset of the statement-only rows."
        )

    def test_Page_WhenThePeriodAlreadyAgrees_SaysThereWasNothingToFold(self, store, tmp_path):
        build_card(store, tmp_path)
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[0])

        assert line == (
            "Closing 2025-10-10: the period 2025-09-19 to 2025-10-10 already agrees with the "
            "statement, so there is nothing to fold."
        )

    def test_Page_WhenTheOnlyFeedRowIsAConfirmedTransferLeg_SaysNoRowWasOpenToFolding(
        self, store, tmp_path
    ):
        build_card(store, tmp_path, transfer=False)
        pair_with_savings(store, "Plan Charge 3")
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[3])

        assert line == (
            "Closing 2026-01-12: the period 2025-12-11 to 2026-01-12 holds no unmatched feed "
            "row from truelayer that the rule may fold, so there is nothing to fold."
        )

    def test_Page_WhenThePeriodDiffersAndNoStatementOnlyRowExists_SaysNoneCanBeTheSameMoney(
        self, store, tmp_path
    ):
        """A purchase only the feed holds, dated in the first period, which prints
        nothing the feed never saw."""
        build_mini_card(store, tmp_path, Variant.IN_PERIOD)
        land(store, feed_row(777, date(2026, 1, 5), "Stray Purchase"))
        fold_same_money(store)

        line = closing_line(page(store), MINI_CLOSINGS[0])

        assert line == (
            "Closing 2026-01-10: the period 2025-12-29 to 2026-01-10 holds 1 unmatched feed "
            "row from truelayer (dated 2026-01-05) but no statement-only row, so none of "
            "them can be the same money as a statement row."
        )

    def test_Proof_WhenACandidateWouldLeaveItsOwnPeriodDiffering_RefusesItAndNamesThePeriod(
        self, store, tmp_path
    ):
        """The proof is a backstop: a candidate drawn from the rule's own search
        always makes its own period agree. This hands it a candidate that does
        not (a row of the next period, offered for the one before), which is the
        defect the backstop exists to catch, and reads what the page would say."""
        build_card(store, tmp_path)
        [item] = gather_evidence(store, sibling_accounts={})
        chain = sorted(
            (w for w in item.windows if w.kind is not PeriodKind.INSIDE), key=lambda w: w.last_day
        )
        [wrong] = [row for row in item.counted if row.description == "Plan Charge 3"]
        offered = _Candidate(chain[2], "truelayer", (wrong,))

        kept, refused = _proven(item, chain, [offered])
        outcome = _outcome_of(
            _Attempt(chain[2], "truelayer", False, [wrong], [], offered), refused, False
        )

        assert kept == []
        assert outcome.verdict is Verdict.REFUSED
        assert "the period 2025-11-13 to 2025-12-10 would still differ after folding" in (
            outcome.describe()
        )


class TestWhereTheSearchIsBounded:
    def test_Page_WhenTheStatementRowsExceedTheBound_SaysHowManyWereSearched(
        self, store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(same_money_fold, "MAX_STATEMENT_ROWS", 2)
        build_card(store, tmp_path, feed_charges={3: CHARGES[3] + 1})
        fold_same_money(store)

        line = closing_line(page(store), CLOSINGS[3])

        assert (
            "The search was bounded: only the nearest 2 of 3 statement-only rows were searched."
            in line
        )

    def test_Page_WhenTheFeedRowsExceedTheSetBound_SaysEachWasStillTriedAlone(
        self, store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(same_money_fold, "MAX_SET_ROWS", 1)
        build_mini_card(store, tmp_path, Variant.WITH_PURCHASE)
        fold_same_money(store)

        line = closing_line(page(store), MINI_CLOSINGS[2])

        assert (
            "The search was bounded: each unmatched feed row was tried alone, but only the "
            "latest 1 of 2 were combined into sets." in line
        )

    def test_Fold_WhenAPeriodHoldsFarMoreRowsThanTheSetBound_StillFindsTheSingleRowAnswer(
        self, store, tmp_path, monkeypatch
    ):
        """The period spanning the missing statement holds 31 feed rows, and its
        answer is one of them: the bound limits sets, never the rows tried alone."""
        monkeypatch.setattr(same_money_fold, "MAX_SET_ROWS", 3)
        build_card(store, tmp_path)

        report = fold_same_money(store)

        assert report.folded == 8
        assert "bounded" not in closing_line(page(store), CLOSINGS[5])

    def test_Fold_WhenTheAnswerIsAPairBeyondTheSetBound_FoldsLessNeverMore(
        self, store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(same_money_fold, "MAX_SET_ROWS", 1)
        build_mini_card(store, tmp_path, Variant.LISTED_TWICE)

        report = fold_same_money(store)

        assert report.folded == 2


class TestWhereTheRuleDoesNotRun:
    def test_Page_WhenOnlyOneStatementIsHeld_SaysTheRuleDidNotRunAndWhy(self, store, tmp_path):
        build_card(store, tmp_path, skip=frozenset(range(1, 9)))
        fold_same_money(store)

        text = page(store)

        assert "The same-money rule did not run: Only one statement is held" in text


class TestEachWayACardCanDifferFromTheNineStatementOne:
    """The variants in `card_variant_corpus`, each with the rule's measured answer.

    Every answer below was predicted first and then measured; the prediction
    that was wrong is recorded in the corpus module. The four closings are, in
    order, a first period that agrees and three that follow it.
    """

    @pytest.mark.parametrize(
        ("variant", "expected_folds", "expected_verdicts"),
        [
            (
                Variant.IN_PERIOD,
                3,
                [Verdict.AGREES, Verdict.FOLDED, Verdict.FOLDED, Verdict.FOLDED],
            ),
            (
                Variant.AFTER_CLOSING,
                0,
                [Verdict.AGREES, Verdict.NO_MATCH, Verdict.NO_MATCH, Verdict.NO_MATCH],
            ),
            (
                Variant.EQUAL_CHARGES,
                0,
                [Verdict.AGREES, Verdict.NO_MATCH, Verdict.AGREES, Verdict.AGREES],
            ),
            (
                Variant.WITH_PURCHASE,
                2,
                [Verdict.AGREES, Verdict.FOLDED, Verdict.NO_MATCH, Verdict.FOLDED],
            ),
            (
                Variant.FEED_TWICE,
                2,
                [Verdict.AGREES, Verdict.FOLDED, Verdict.NO_MATCH, Verdict.FOLDED],
            ),
            (
                Variant.LISTED_TWICE,
                4,
                [Verdict.AGREES, Verdict.FOLDED, Verdict.FOLDED, Verdict.FOLDED],
            ),
        ],
    )
    def test_Rule_OnEachVariantOfTheCard_FoldsAndStopsWhereTheCorpusSays(
        self, store, tmp_path, variant, expected_folds, expected_verdicts
    ):
        build_mini_card(store, tmp_path, variant)

        report = fold_same_money(store)

        assert report.folded == expected_folds
        assert verdicts(store) == expected_verdicts

    def test_Rule_WhenAPurchaseSharesThePeriod_LeavesTheChargeCountedBesideItAndSaysTheyDiffer(
        self, store, tmp_path
    ):
        """The period differs by the charge and the purchase together, so the
        charge alone is not folded (that would leave the period differing by a
        figure nobody has explained): both stay counted and the page says the
        period differs."""
        build_mini_card(store, tmp_path, Variant.WITH_PURCHASE)

        fold_same_money(store)

        assert "Plan Charge 2" not in folded(store)
        assert "Genuine Purchase" not in folded(store)
        text = page(store)
        assert "The 6 rows the store counts differ from the statement's movement." in text

    def test_Rule_WhenTheFeedPostsTheChargeTwiceAndTheStatementListsItOnce_FoldsNeither(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.FEED_TWICE)

        fold_same_money(store)

        assert "Plan Charge 2" not in folded(store)
        assert "Plan Charge Again 2" not in folded(store)

    def test_Rule_WhenTheStatementListsTheChargeTwiceToo_FoldsBothFeedRowsTogether(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.LISTED_TWICE)

        fold_same_money(store)

        assert {"Plan Charge 2", "Plan Charge Again 2"} <= set(folded(store))
        line = closing_line(page(store), MINI_CLOSINGS[2])
        assert "the rule folds 2 feed rows from truelayer (dated 2026-02-11, 2026-02-14)" in line

    def test_Page_WhenTheFeedDatesTheChargeTheDayAfterTheClosing_ShowsTheRowAsTheNextPeriodsOwn(
        self, store, tmp_path
    ):
        """The premise two earlier versions of the rule were built on. The row is
        listed under the NEXT period (its first day), where the statement lists a
        different charge, so nothing is folded."""
        build_mini_card(store, tmp_path, Variant.AFTER_CLOSING)
        fold_same_money(store)

        text = page(store)

        assert closing_line(text, MINI_CLOSINGS[1]) == (
            "Closing 2026-02-10: the period 2026-01-11 to 2026-02-10 holds 1 unmatched feed "
            "row from truelayer (dated 2026-01-11) and 3 statement-only rows (dated "
            "2026-02-10, 2026-02-10, 2026-02-10): no subset of the feed rows sums to the "
            "period's difference and to some subset of the statement-only rows."
        )
        assert "Feed-only rows are dated: 2026-01-11 (truelayer)." in text


class TestTheLeftoversAreListedByDateAndSource:
    def test_Page_ForAPeriodWithStatementOnlyRows_ListsTheirDatesAndTheStatementSource(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.AFTER_CLOSING)
        fold_same_money(store)

        text = page(store)

        assert (
            "Statement-only rows are dated: 2026-02-10 (santander-cc-pdf), "
            "2026-02-10 (santander-cc-pdf), 2026-02-10 (santander-cc-pdf)." in text
        )

    def test_Page_WhenAListIsLong_ShowsTwentyAndSaysHowManyMore(self, store, tmp_path):
        """The month the statement is missing holds thirty-one feed-only rows
        once the charge dated 12 March is folded."""
        build_card(store, tmp_path)
        fold_same_money(store)

        text = page(store)

        [listing] = [
            line
            for line in text.splitlines()
            if line.strip().startswith("Feed-only rows are dated: 2026-02-12")
        ]
        assert listing.endswith(", and 11 more.")
        assert listing.count("(truelayer)") == 20

    @pytest.mark.parametrize(("count", "tail"), [(20, "2026-01-20"), (21, ", and 1 more")])
    def test_DatedList_AtAndOverTheCap_StopsAtTwentyEntries(self, count, tail):
        days = [date(2026, 1, 1) + timedelta(days=index) for index in range(count)]

        listed = dated_list(days)

        assert listed.endswith(tail)
        assert listed.count("2026-") == min(count, 20)


class TestAnExcusedStatementRow:
    """The variants about excuses in `card_variant_corpus`, each with its known answer.

    Period 2 closes on 2026-03-10 and starts on 2026-02-11, where the feed dates
    its charge.
    """

    CLOSING = MINI_CLOSINGS[2]

    def test_Rule_WhenTheStatementRowThatIsTheChargeIsExcused_FoldsTheFeedRowAndSaysWhy(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.EXCUSED_CHARGE)

        report = fold_same_money(store)

        assert report.folded == 3
        assert "Plan Charge 2" in folded(store)
        assert closing_line(page(store), self.CLOSING) == (
            "Closing 2026-03-10: the rule folds 1 feed row from truelayer (dated 2026-02-11) "
            "as the same money as 1 statement-only row (dated 2026-03-10 [excused: a proven "
            "internal transfer]), and the period 2026-02-11 to 2026-03-10 then agrees."
        )
        [item] = period_reconciliation(store, sibling_accounts={}).accounts
        assert all(period.agrees for period in item.periods)

    def test_Page_WhenAStatementRowIsExcused_SaysWhyInThePeriodsLeftoverList(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.EXCUSED_CHARGE)
        fold_same_money(store)

        assert (
            "Statement-only rows are dated: 2026-03-10 (santander-cc-pdf), 2026-03-10 "
            "(santander-cc-pdf), 2026-03-10 (santander-cc-pdf, excused: a proven internal "
            "transfer)."
        ) in page(store)

    def test_Rule_WhenExcusedAndPlainStatementRowsShareTheChargesAmount_FoldsOnceUsingThePlainOne(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.EXCUSED_AND_PLAIN)

        fold_same_money(store)

        assert [name for name in folded(store) if name.endswith("2")] == ["Plan Charge 2"]
        assert closing_line(page(store), self.CLOSING) == (
            "Closing 2026-03-10: the rule folds 1 feed row from truelayer (dated 2026-02-11) "
            "as the same money as 1 statement-only row (dated 2026-03-10), and the period "
            "2026-02-11 to 2026-03-10 then agrees."
        )

    def test_Rule_WhenTheFeedRowEqualToTheDifferenceIsExcused_DoesNotFoldItAndThePeriodStaysOver(
        self, store, tmp_path
    ):
        """The cross-source page has explained that feed row as a transfer leg;
        folding it as well would explain it twice."""
        build_mini_card(store, tmp_path, Variant.EXCUSED_FEED)

        fold_same_money(store)

        assert "Plan Charge 2" not in folded(store)
        assert verdicts(store) == [
            Verdict.AGREES,
            Verdict.FOLDED,
            Verdict.NO_FEED_ROWS,
            Verdict.FOLDED,
        ]
        [item] = period_reconciliation(store, sibling_accounts={}).accounts
        assert [p.agrees for p in item.periods if p.last_day == self.CLOSING] == [False]

    def test_Rule_WhenTheStatementRowIsExcusedBySiblingAttribution_FoldsAndLeavesTheSiblingAlone(
        self, store, tmp_path
    ):
        """An equal row of the feed under another account says where a COPY was
        filed, not that the statement's row is not its own; the sibling's row is
        never used here."""
        build_mini_card(store, tmp_path, Variant.SIBLING_EXCUSED_CHARGE)

        plan = plan_same_money(gather_evidence(store, sibling_accounts=SIBLING_SCOPE))

        [outcome] = plan.outcomes
        [closing] = [c for c in outcome.closings if c.closing == self.CLOSING]
        assert closing.verdict is Verdict.FOLDED
        assert closing.matched_excuses == ("matched to a row filed under other-card",)
        assert closing.describe().count("excused: matched to a row filed under other-card") == 1
        [sibling] = store.transactions_for_account("other-card")
        assert sibling.entity_id not in plan.folds

    def test_Rule_WhenNothingIsExcused_FoldsExactlyAsBefore(self, store, tmp_path):
        build_mini_card(store, tmp_path, Variant.IN_PERIOD)

        fold_same_money(store)

        assert "excused" not in closing_line(page(store), self.CLOSING)

    def test_Outcome_WhenTheStoredRecordPredatesExcuses_StillReadsAndSaysNothingOfThem(
        self, store, tmp_path
    ):
        build_mini_card(store, tmp_path, Variant.IN_PERIOD)
        [outcome] = plan_same_money(gather_evidence(store, sibling_accounts={})).outcomes
        stored = json.loads(outcome.to_text())
        for closing in stored["closings"]:
            for key in ("statement_excuses", "matched_dates", "matched_excuses"):
                del closing[key]

        reread = AccountOutcome.from_text(json.dumps(stored))

        assert all(c.statement_excuses == () and c.matched_dates == () for c in reread.closings)
        assert all("excused" not in line for line in reread.describe())


class TestTheMaskedPageStillCarriesNoValues:
    @pytest.mark.parametrize("variant", list(Variant))
    def test_Page_OnEveryVariant_ContainsNoAmountOrDescription(self, store, tmp_path, variant):
        build_mini_card(store, tmp_path, variant)
        fold_same_money(store)

        text = page(store)

        assert MONEY_FIGURE.search(text) is None, MONEY_FIGURE.search(text)
        for private in ("6.95", "695", "Plan Charge", "Plan Part", "Ordinary", "Genuine"):
            assert private not in text, private
        assert re.search(r"Closing \d{4}-\d\d-\d\d:", text)


class TestTheRecordIsDerivedData:
    @pytest.mark.parametrize("variant", list(Variant))
    def test_Outcome_RoundTripsThroughItsStoredText_ForEachVariant(self, store, tmp_path, variant):
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

    def test_Page_WhenTheStoredRecordIsFromTheEarlierBandRule_SaysItCannotBeReadUntilThePassRuns(
        self, store, tmp_path
    ):
        """A record written by the rule that searched a band around each closing
        has no period in it. It is read as unreadable, not guessed at, and the
        next import, pull, or rebuild rewrites it."""
        build_card(store, tmp_path)
        earlier = (
            '{"account": "card", "feeds": ["truelayer"], "withheld": "", "unpaired": [], '
            '"closings": [{"closing": "2025-10-10", "feed": "truelayer", "verdict": '
            '"no-band-rows", "band": ["2025-10-08", "2025-10-12"], "band_dates": [], '
            '"band_searched": 8, "claimed_dates": [], "statement_dates": [], '
            '"statement_searched": 12, "taken_dates": [], "blocking": []}]}'
        )
        store.replace_same_money_outcomes({"card": earlier})
        store.connection.commit()

        assert "record for this account cannot be read" in page(store)

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
