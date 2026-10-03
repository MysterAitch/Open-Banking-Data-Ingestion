"""The same money, itemised differently by two sources, is counted once.

A card's statements print three small charges on the last day of each period
(an interest charge, a fee, a levy) where the aggregator's feed carries one row
for their total, posted on the statement date, which by date is the first days
of the NEXT period. The store holds all four rows, so each period after the
first is over by the feed's row and the account's total is over by every feed
row, while the statements' own arithmetic is exact.

The card, built from the constructed data below with every answer decided
before the first run. Statements close 11 February, 11 March, 11 April 2026 and
chain from 100.00 owed (spending raises what is owed; the store's sign is the
reverse). Per statement, an ordinary purchase the feed also reports, and three
itemised charges the statement prints on the 10th that the feed reports as ONE
row for their total on the 12th:

    S1  20 Jan Alpha Grocer 12.37    10 Feb  2.11 + 2.50 + 3.16 = 7.77   feed 12 Feb 7.77
    S2  14 Feb Charlie Cafe  5.43    10 Mar  1.11 + 2.22 + 4.44 = 7.77   feed 12 Mar 7.77
    S3  12 Mar Echo Rail    40.73    10 Apr  1.20 + 1.30 + 2.50 = 5.00   feed 12 Apr 5.00

S1 and S2 itemise the SAME total on purpose: a rule that took the neighbouring
month's feed row for its own would be satisfied by either. Closings (owed):
120.14, 133.34, 179.07; the statements' spending sums to 79.07.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from obdi.accounts import AccountBinding, AccountMap
from obdi.balance_anchors import effective_opening
from obdi.identity import content_key
from obdi.ingest import import_file, reconcile_batch
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.period_reconciliation import (
    PeriodKind,
    gather_evidence,
    period_reconciliation,
)
from obdi.rebuild import rebuild_from_raw
from obdi.same_money_fold import fold_same_money, plan_same_money
from obdi.space_attribution import fold_space_copies
from obdi.statement_membership import Membership
from obdi.store import Store
from test_period_reconciliation import (
    ACCOUNT,
    MONEY_FIGURE,
    Row,
    _held_statement,
    _statement,
)
from test_space_attribution import BILLS, MAIN, Household, bill_from_the_space

FEED_SOURCE = "truelayer"

#: (the ordinary purchase, its statement date text, the itemised charges, closing date)
ORDINARY: list[tuple[str, str, int, date]] = [
    ("20th Jan", "Alpha Grocer", 1237, date(2026, 1, 20)),
    ("14th Feb", "Charlie Cafe", 543, date(2026, 2, 14)),
    ("12th Mar", "Echo Rail", 4073, date(2026, 3, 12)),
]
ITEMISED: list[list[int]] = [[211, 250, 316], [111, 222, 444], [120, 130, 250]]
CLOSINGS = ["11th Feb 2026", "11th Mar 2026", "11th Apr 2026"]
CHARGE_DAYS = ["10th Feb", "10th Mar", "10th Apr"]
FEED_DAYS = [date(2026, 2, 12), date(2026, 3, 12), date(2026, 4, 12)]
CHARGE_NAMES = ["Interest", "Late Fee", "Levy"]

FIRST_PERIOD = "2026-01-20 to 2026-02-11"
SECOND_PERIOD = "2026-02-12 to 2026-03-11"
THIRD_PERIOD = "2026-03-12 to 2026-04-11"

#: The statements' own spending, in the store's sign.
STATEMENT_TOTAL = -(1237 + 777 + 543 + 777 + 4073 + 500)


def _row(
    minor: int,
    day: date,
    description: str,
    *,
    source: str = FEED_SOURCE,
    source_id: str | None = None,
    account: str = ACCOUNT,
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=minor,
        currency="GBP",
        value_date=day,
        booking_date=day,
        description=description,
        source=source,
        source_id=source_id or f"{source}-{description}-{day}",
        tier=SourceTier.AUTHORITATIVE,
        content_key=content_key(amount_minor=minor, value_date=day, description=description),
    )


def land_feed(store: Store, *rows: Transaction, digest: str = "feed") -> None:
    reconcile_batch(store, list(rows), digest=digest)


def feed_rows(
    totals: list[int | None] | None = None, *, days: list[date] | None = None
) -> list[Transaction]:
    """The feed's rows: each ordinary purchase, and one row per statement for the
    itemised total (None leaves a statement's total out of the feed)."""
    wanted = totals if totals is not None else [sum(items) for items in ITEMISED]
    chosen = days if days is not None else FEED_DAYS
    rows = [
        _row(-minor, when, description)
        for (_, description, minor, when) in ORDINARY
    ]
    rows += [
        _row(-total, day, f"Combined Fees {position}")
        for position, (total, day) in enumerate(zip(wanted, chosen, strict=True))
        if total is not None
    ]
    return rows


def hold_statements(
    store: Store, root: Path, *, itemised: list[list[int]] | None = None
) -> None:
    owed = 10000
    for position, closing in enumerate(CLOSINGS):
        when, description, minor, _ = ORDINARY[position]
        items = (itemised if itemised is not None else ITEMISED)[position]
        rows: list[Row] = [(when, description, minor)]
        rows += [
            (CHARGE_DAYS[position], f"{CHARGE_NAMES[position]} {n}", amount)
            for n, amount in enumerate(items)
        ]
        _held_statement(store, root, f"card-{position}", closing, owed, rows)
        owed += sum(minor for _, _, minor in rows)


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "fold.sqlite3") as opened:
        yield opened


def card_a(store: Store, root: Path, **options) -> None:
    hold_statements(store, root, itemised=options.pop("itemised", None))
    land_feed(store, *feed_rows(**options))


def periods(store: Store) -> dict[str, object]:
    [item] = period_reconciliation(store, sibling_accounts={}).accounts
    return {
        f"{p.first_day} to {p.last_day}": p
        for p in item.periods
        if p.kind is not PeriodKind.INSIDE
    }


def total(store: Store) -> int:
    return sum(
        t.amount_minor
        for t in store.transactions_for_account(ACCOUNT)
        if not t.status.is_history
    )


def folded_descriptions(store: Store) -> list[str]:
    return sorted(
        t.description
        for t in store.transactions_for_account(ACCOUNT)
        if t.status is TransactionStatus.FOLDED
    )


class TestTheCardAShape:
    def test_Before_EveryPeriodAfterTheFirstIsOverAndTheTotalIsOverByEveryFeedRow(
        self, store, tmp_path
    ):
        """Not the fold: the fault it exists for. Each feed row dated after a
        closing lands in the next period: S2's and S3's periods are over by 7.77
        each, and the account's total is over the statements' by all three feed
        rows (7.77 + 7.77 + 5.00)."""
        card_a(store, tmp_path)

        held = periods(store)

        assert held[FIRST_PERIOD].agrees
        assert held[SECOND_PERIOD].surplus_minor == -777
        assert held[THIRD_PERIOD].surplus_minor == -777
        assert total(store) == STATEMENT_TOTAL - 777 - 777 - 500

    def test_Fold_OverThreeConsecutiveMonths_FoldsEachFeedRowAndEveryPeriodAgrees(
        self, store, tmp_path
    ):
        card_a(store, tmp_path)

        report = fold_same_money(store)

        assert (report.folded, report.newly_folded, report.released) == (3, 3, 0)
        assert folded_descriptions(store) == [
            "Combined Fees 0",
            "Combined Fees 1",
            "Combined Fees 2",
        ]
        held = periods(store)
        assert all(p.agrees for p in held.values())
        assert total(store) == STATEMENT_TOTAL

    def test_Fold_KeepsTheStatementsRowsCountedAndTheFeedRowsSightings(self, store, tmp_path):
        card_a(store, tmp_path)

        fold_same_money(store)

        rows = store.transactions_for_account(ACCOUNT)
        charges = {"Interest", "Late", "Levy"}
        statement_rows = [t for t in rows if t.description.split()[0] in charges]
        assert len(statement_rows) == 9
        assert all(t.status is TransactionStatus.BOOKED for t in statement_rows)
        for folded in (t for t in rows if t.status is TransactionStatus.FOLDED):
            [(count,)] = store.connection.execute(
                "SELECT COUNT(*) FROM transaction_sources WHERE entity_id = ?",
                (folded.entity_id,),
            ).fetchall()
            assert count >= 1

    def test_Anchors_AfterTheFold_AllAgree(self, store, tmp_path):
        card_a(store, tmp_path)
        assert effective_opening(store, ACCOUNT).differing

        fold_same_money(store)

        assert effective_opening(store, ACCOUNT).differing == []

    def test_Report_AfterTheFold_SaysHowManyFeedRowsWereFoldedAsHowManyStatementRows(
        self, store, tmp_path
    ):
        card_a(store, tmp_path)
        fold_same_money(store)

        text = period_reconciliation(store, sibling_accounts={}).describe(masked=True)

        assert text.count("1 feed row was folded as the same money as 3 statement rows") == 3
        assert "withheld from the push" in text
        assert "differ from" not in text
        assert MONEY_FIGURE.search(text) is None, MONEY_FIGURE.search(text)

    def test_Report_AfterTheFold_StatesTheFoldedSumOnlyWhenUnmasked(self, store, tmp_path):
        card_a(store, tmp_path)
        fold_same_money(store)
        report = period_reconciliation(store, sibling_accounts={})

        assert "The folded rows sum to -£7.77." in report.describe(masked=False)
        assert "The folded rows sum" not in report.describe(masked=True)

    def test_Fold_RunTwice_ChangesNothingTheSecondTime(self, store, tmp_path):
        card_a(store, tmp_path)
        fold_same_money(store)

        again = fold_same_money(store)

        assert (again.folded, again.newly_folded, again.released) == (3, 0, 0)


class TestTheImportSummarySaysSo:
    def test_Import_WhenAStatementCompletesTheEvidence_SummaryCountsTheFoldedFeedRows(
        self, store, tmp_path
    ):
        """The feed is held first; the three statements then arrive one file at a
        time through the door a person uses. The fold runs after each, so the
        rows are folded as the evidence arrives and every summary says how many
        that file's arrival folded: three in all, and the wording names what it
        means for the push."""
        land_feed(store, *feed_rows())
        summaries = []
        owed = 10000
        for position, closing in enumerate(CLOSINGS):
            when, description, minor, _ = ORDINARY[position]
            rows: list[Row] = [(when, description, minor)]
            rows += [
                (CHARGE_DAYS[position], f"{CHARGE_NAMES[position]} {n}", amount)
                for n, amount in enumerate(ITEMISED[position])
            ]
            payload, owed_after = _statement(closing, owed, rows)
            path = tmp_path / f"arrival-{position}.pdf"
            path.write_bytes(payload)
            summaries.append(import_file(store, path, account_id=ACCOUNT))
            owed = owed_after

        assert sum(s.same_money_folded for s in summaries) == 3
        described = " ".join(s.describe() for s in summaries)
        assert "feed row(s) as the same money a statement itemises" in described
        assert "(withheld from the push)" in described
        assert folded_descriptions(store) == [
            "Combined Fees 0",
            "Combined Fees 1",
            "Combined Fees 2",
        ]


class TestWhatIsNotTheSameMoney:
    def test_Fold_WhenTheSumsDifferByAPenny_FoldsNothing(self, store, tmp_path):
        """S2's feed row is 7.78 against the statement's 7.77. A near miss is a
        different payment until the penny is explained; folding it would hide
        7.78 of real spending. Only S2 itemises here, so nothing else is in play."""
        card_a(store, tmp_path, itemised=[[], [111, 222, 444], []], totals=[None, 778, None])

        report = fold_same_money(store)

        assert report.folded == 0
        assert folded_descriptions(store) == []

    def test_Fold_WhenTheSumsAreEqualButThePeriodStillDiffers_FoldsNothing(
        self, store, tmp_path
    ):
        """The sums are equal (7.77 and 7.77), but a second feed (a CSV export)
        also holds a 3.33 charge on 20 February that neither the statement nor
        the aggregator has. Folding the aggregator's row leaves S2's period over
        by 3.33, so the statement's arithmetic does not vouch for the fold."""
        card_a(store, tmp_path, itemised=[[], [111, 222, 444], []], totals=[None, 777, None])
        land_feed(
            store,
            _row(-333, date(2026, 2, 20), "Stray Charge", source="csv", source_id="csv-1"),
            digest="csv",
        )

        report = fold_same_money(store)

        assert report.folded == 0
        assert periods(store)[SECOND_PERIOD].surplus_minor == -333

    def test_Fold_WhenAnUnrelatedFeedRowSharesThePeriod_FoldsNothingAndSaysTheyAreNotEqual(
        self, store, tmp_path
    ):
        """A 3.33 charge only the feed holds, on 20 February, in S2's period: the
        feed-only rows there sum to 3.33 against the statement's 7.77. The
        aggregator's 7.77 row for S2 is dated 12 March, so the rows the fold may
        take for S2 are 3.33 and 7.77: not equal to the statement's, and none is
        taken."""
        card_a(store, tmp_path, itemised=[[], [111, 222, 444], []], totals=[None, 777, None])
        land_feed(store, _row(-333, date(2026, 2, 20), "Surprise Charge"))

        report = fold_same_money(store)

        assert report.folded == 0
        text = period_reconciliation(store, sibling_accounts={}).describe(masked=True)
        second = text.split(f"Period {SECOND_PERIOD}")[1].split("  Period")[0]
        assert "sum to different figures: the leftovers are not the same money" in second
        assert "folded as the same money" not in text

    def test_Fold_WhenOnlyOneSideHasLeftovers_FoldsNothing(self, store, tmp_path):
        """Statement charges with no feed row for them, and a feed row for a
        statement that itemises nothing: leftovers on one side only each time."""
        card_a(store, tmp_path, itemised=[[211, 250, 316], [], []], totals=[None, 777, None])

        report = fold_same_money(store)

        assert report.folded == 0

    def test_Fold_WhenTheFeedRowIsDatedBeyondTheBoundaryWindow_FoldsNothing(
        self, store, tmp_path
    ):
        """The aggregator dates S2's combined row 15 March, four days after the
        closing: outside the two days a posting on the statement date can lag."""
        card_a(
            store,
            tmp_path,
            itemised=[[], [111, 222, 444], []],
            totals=[None, 777, None],
            days=[FEED_DAYS[0], date(2026, 3, 15), FEED_DAYS[2]],
        )

        report = fold_same_money(store)

        assert report.folded == 0

    def test_Fold_WhenTheFeedRowIsDatedInsideTheBoundaryWindow_Folds(self, store, tmp_path):
        card_a(
            store,
            tmp_path,
            itemised=[[], [111, 222, 444], []],
            totals=[None, 777, None],
            days=[FEED_DAYS[0], date(2026, 3, 13), FEED_DAYS[2]],
        )

        report = fold_same_money(store)

        assert report.folded == 1


class TestWhatIsNeverFolded:
    def _with_pair(self, store: Store, tmp_path: Path, which: int) -> None:
        card_a(store, tmp_path)
        [leg] = [
            t
            for t in store.transactions_for_account(ACCOUNT)
            if t.description == f"Combined Fees {which}"
        ]
        land_feed(store, _row(777, date(2026, 2, 12), "Other Side", account="savings"))
        [other] = store.transactions_for_account("savings")
        store.replace_transfer_pairs([(leg.entity_id, other.entity_id)])
        store.connection.commit()

    @pytest.mark.parametrize(
        ("which", "expected"),
        [
            (0, []),
            (1, ["Combined Fees 0"]),
            (2, ["Combined Fees 0", "Combined Fees 1"]),
        ],
        ids=["first-statements-row", "middle-statements-row", "beyond-the-span"],
    )
    def test_Fold_WhenTheFeedRowIsAConfirmedTransferLeg_LeavesItCountedAndFoldsOnlyWhatStillProves(
        self, store, tmp_path, which, expected
    ):
        """The leg stays counted, so the period it is dated in stays over by it,
        and the fold that would need that period to agree is refused with it:
        the first statement's leg blocks the second period and so everything
        after; the middle one blocks the third; the last one blocks nothing."""
        self._with_pair(store, tmp_path, which)

        fold_same_money(store)

        folded = folded_descriptions(store)
        assert f"Combined Fees {which}" not in folded
        assert folded == expected

    def test_Fold_WhenAStatementListsTheFeedRow_LeavesItCounted(self, store, tmp_path):
        """A feed-only leftover that a statement also lists (here simulated by
        placing it with S2's membership) is the statement's own row, whatever
        the pairing made of its dates."""
        card_a(store, tmp_path)
        evidence = gather_evidence(store, sibling_accounts={})
        [item] = evidence
        [listed] = [t for t in item.counted if t.description == "Combined Fees 1"]
        placed = {**item.membership.placed, listed.entity_id: date(2026, 3, 11)}
        listed_item = replace(item, membership=Membership(item.membership.statements, placed))

        plan = plan_same_money([listed_item])

        assert listed.entity_id not in plan.folds

    def test_Fold_NeverUsesAnotherAccountsFeedRow(self, store, tmp_path):
        """The statement itemises 7.77 and the only 7.77 row in the store is another
        account's, on the same day: nothing is folded in either account."""
        card_a(store, tmp_path, itemised=[[], [111, 222, 444], []], totals=[None, None, None])
        land_feed(store, _row(-777, date(2026, 3, 12), "Combined Fees 1", account="other-card"))

        report = fold_same_money(store)

        assert report.folded == 0
        assert [t.status for t in store.transactions_for_account("other-card")] == [
            TransactionStatus.BOOKED
        ]


class TestTheFoldIsDerivedAndReversible:
    def test_Fold_WhenTheLastStatementIsNoLongerHeld_ReleasesItsFeedRow(self, store, tmp_path):
        card_a(store, tmp_path)
        fold_same_money(store)
        assert "Combined Fees 2" in folded_descriptions(store)
        [(artefact_id,)] = store.connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE origin LIKE ?", ("%card-2.pdf",)
        ).fetchall()
        # Filed under another account, the statement is no longer this card's.
        store.refile_artefact(artefact_id, "elsewhere")

        report = fold_same_money(store)

        assert "Combined Fees 2" not in folded_descriptions(store)
        assert report.released == 1
        assert report.folded == 2

    def test_Fold_WhenAStatementLaterListsTheFeedRow_ReleasesIt(self, store, tmp_path):
        """A later statement file whose rows include a 7.77 charge on 12 March
        merges into the aggregator's row, so a statement now lists it: it is no
        longer 'the same money, described differently', it is the statement's own."""
        card_a(store, tmp_path, itemised=[[], [111, 222, 444], []], totals=[None, 777, None])
        fold_same_money(store)
        assert folded_descriptions(store) == ["Combined Fees 1"]
        _held_statement(
            store, tmp_path, "later", "11th Apr 2026", 13334,
            [("12th Mar", "Combined Fees 1", 777)],
        )  # fmt: skip

        fold_same_money(store)

        assert "Combined Fees 1" not in folded_descriptions(store)

    @pytest.mark.parametrize("feed_first", [True, False], ids=["feed-first", "statements-first"])
    def test_Fold_InEitherArrivalOrder_FoldsTheSameRows(self, store, tmp_path, feed_first):
        if feed_first:
            land_feed(store, *feed_rows())
            hold_statements(store, tmp_path)
        else:
            card_a(store, tmp_path)

        fold_same_money(store)

        assert folded_descriptions(store) == [
            "Combined Fees 0",
            "Combined Fees 1",
            "Combined Fees 2",
        ]
        assert total(store) == STATEMENT_TOTAL

    def test_Fold_WhenReplayedFromRawInARebuild_FoldsTheSameRows(self, store, tmp_path):
        """The statements and the feed both as raw artefacts, replayed by a
        rebuild: the same three rows are folded as by arrival."""
        import json

        from obdi.providers import truelayer

        hold_statements(store, tmp_path)
        records = [
            {
                "transaction_id": f"id-{row.description}",
                "normalised_provider_transaction_id": f"norm-{row.description}",
                "timestamp": f"{row.value_date.isoformat()}T09:00:00Z",
                "description": row.description,
                "amount": row.amount_minor / 100,
                "currency": "GBP",
                "transaction_type": "DEBIT",
            }
            for row in feed_rows()
        ]
        body = json.dumps({"results": records, "status": "Succeeded"}).encode()
        store.land_artefact(truelayer.artefact_for(body, account_id="tl-card", kind="booked"))
        account_map = AccountMap([AccountBinding(ACCOUNT, "truelayer", "tl-card")])

        report = rebuild_from_raw(store, account_map=account_map)

        assert folded_descriptions(store) == [
            "Combined Fees 0",
            "Combined Fees 1",
            "Combined Fees 2",
        ]
        assert report.same_money_folded == 3
        assert total(store) == STATEMENT_TOTAL


class TestTheSpaceFoldAndThisOneDoNotInterfere:
    def test_Folds_WhenAStarlingFamilySitsBesideACard_EachPassKeepsItsOwn(self, store, tmp_path):
        """A bill paid from a Starling Space (main row folded into the Space's) in
        the same store as the card. Space fold: 1 row. Same-money fold: 3 rows.
        Running either pass again releases neither's rows, and no row is folded
        by both."""
        from test_space_attribution import MAP

        home = Household(store, MAP)
        home.arrive(*bill_from_the_space())
        card_a(store, tmp_path)
        fold_same_money(store)
        space_folded = {
            t.entity_id for t in store.transactions_for_account(MAIN) if t.status.is_history
        }
        card_folded = {
            t.entity_id for t in store.transactions_for_account(ACCOUNT) if t.status.is_history
        }
        assert len(space_folded) == 1 and len(card_folded) == 3

        space_report = fold_space_copies(store, MAP)
        same_report = fold_same_money(store)

        assert (space_report.folded, space_report.newly_folded, space_report.released) == (1, 0, 0)
        assert (same_report.folded, same_report.newly_folded, same_report.released) == (3, 0, 0)
        assert store.space_folded_ids() == space_folded
        assert store.statement_folded_ids() == card_folded
        assert store.space_folded_ids().isdisjoint(store.statement_folded_ids())
        assert home.counted(BILLS) == 1
