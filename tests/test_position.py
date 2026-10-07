"""The financial position: what each account holds, what is observed, and the months behind it.

Every figure below was worked out by hand from the household's construction
BEFORE the first run, and the working sits beside the fixture. All amounts are
minor units (pence), and today is 2026-10-02.

    everyday   stated 2026-03-31: 5,555.55 (555,555)
        rows   03-05 +3,000.00   03-20 -450.00   04-10 -123.45
               04-15 -9.99 VOID   05-02 +77.77 PENDING
        opening  555,555 - (300,000 - 45,000)                 = 300,555
        now      300,555 + 300,000 - 45,000 - 12,345 + 7,777  = 550,987
        month-ends  03: 555,555   04: 543,210   05 onwards: 550,987
        applies from 03-04 (the day before its first row)

    card       stated 2026-04-30: owes 900.00 (-90,000)
        rows   02-10 -500.00   03-12 -234.56   04-01 +100.00
        opening  -90,000 - (-50,000 - 23,456 + 10,000)        = -26,544
        now      -90,000 (every row is on or before the anchor)
        month-ends  02: -76,544   03: -100,000   04 onwards: -90,000
        applies from 02-09

    drifter    stated 2026-03-31: 1,000.00 and 2026-04-30: 1,111.11
        rows   03-10 +500.00   04-10 +50.00
        opening  100,000 - 50,000                             = 50,000
        later anchor expects 50,000 + 55,000 = 105,000, states 111,111: DIFFERS
        now      105,000 (the rows' own figure; the check never edits it)
        month-ends  03: 100,000   04 onwards: 105,000       applies from 03-09

    oldsaver   (archived, closed 2026-02-01) stated 2025-12-31: 1,500.00
        rows   2026-01-10 +200.00
        now      170,000   month-ends  2025-12: 150,000   2026-01 onwards: 170,000
        applies from 2025-12-31 (its anchor precedes its first row)

    unanchored one row, 2026-03-01 +888.88, and nothing states a balance: UNKNOWN.

        MOVED       +88,888 since 2026-03-01 (its one row). Nothing before it.

    work-pension  defined contribution: 2026-02-28 10,000.00; 2026-05-31 12,000.00
    house         property: 2026-04-15 250,000.00
    state-pension-forecast  state pension, 11,500.00 a year       (income, not wealth)
    teachers-scheme         defined benefit, 6,123.45 a year      (income, not wealth)

NET WORTH NOW   735,987 (accounts) + 26,200,000 (assets) = 26,935,987
    in credit (not archived)  everyday + drifter = 655,987
    overdrawn or owed         card               = -90,000
    archived                  oldsaver           = 170,000
    observed assets           1,200,000 + 25,000,000 = 26,200,000
    counted: 4 of 5 accounts and 2 assets; 2 income entitlements, not counted

HISTORY (six counted items: four accounts and two assets)
    2025-12  150,000                                       1 of 6
    2026-01  170,000                                       1 of 6
    2026-02  170,000 - 76,544 + 1,000,000      = 1,093,456  3 of 6
    2026-03  170,000 - 100,000 + 1,000,000
             + 555,555 + 100,000               = 1,725,555  5 of 6  (no house yet)
    2026-04  170,000 - 90,000 + 1,000,000
             + 543,210 + 105,000 + 25,000,000  = 26,728,210 6 of 6  COMPLETE FROM HERE
    2026-05 .. 2026-10  26,935,987                        6 of 6
    eleven points, 2025-12 to 2026-10

PROVISIONAL (each unknown opening taken as nil; the known figures above stay as they are)
    total now  26,935,987 + 88,888                          = 27,024,875
    month-ends  2025-12 .. 2026-02  as known (unanchored has no row yet)
                2026-03  1,725,555 + 88,888                 = 1,814,443
                2026-04  26,728,210 + 88,888                = 26,817,098
                2026-10  27,024,875

    plus `late` (uncounted): rows 2026-04-01 -999.00 VOID, 2026-05-10 -200.00,
    2026-06-01 +50.00 PENDING. Void is never counted, pending is.
        moved   -20,000 + 5,000                             = -15,000, first row 2026-05-10
        total now  27,024,875 - 15,000                      = 27,009,875
        2026-04  26,817,098 (void row before its first live row changes nothing)
        2026-05  26,935,987 + 88,888 - 20,000               = 27,004,875
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.balance_anchors import STATED, Anchor, derive_opening, record_stated_anchor
from obdi.core.models import TransactionStatus
from obdi.ledger import build_ledger
from obdi.position import (
    AccountInput,
    AssetInput,
    Observation,
    Position,
    build_position,
    read_position,
)
from obdi.store import Store
from obdi.valuations import Asset, AssetKind, record_observation
from test_ledger import land, txn

D = date
TODAY = D(2026, 10, 2)


def household(store: Store) -> None:
    land(
        store,
        "d-everyday",
        txn("everyday", "src-a", "e1", D(2026, 3, 5), 300000, "PAY IN"),
        txn("everyday", "src-a", "e2", D(2026, 3, 20), -45000, "BIG BILL"),
        txn("everyday", "src-a", "e3", D(2026, 4, 10), -12345, "SHOP"),
        txn("everyday", "src-a", "e4", D(2026, 4, 15), -999, "VANISHED",
            status=TransactionStatus.VOID),
        txn("everyday", "src-a", "e5", D(2026, 5, 2), 7777, "SETTLING",
            status=TransactionStatus.PENDING),
    )
    land(
        store,
        "d-card",
        txn("card", "src-a", "c1", D(2026, 2, 10), -50000, "HOLIDAY"),
        txn("card", "src-a", "c2", D(2026, 3, 12), -23456, "SHOES"),
        txn("card", "src-a", "c3", D(2026, 4, 1), 10000, "PAYMENT"),
    )
    land(
        store,
        "d-drifter",
        txn("drifter", "src-a", "d1", D(2026, 3, 10), 50000, "IN ONE"),
        txn("drifter", "src-a", "d2", D(2026, 4, 10), 5000, "IN TWO"),
    )
    land(store, "d-old", txn("oldsaver", "src-a", "o1", D(2026, 1, 10), 20000, "INTEREST"))
    land(store, "d-un", txn("unanchored", "src-a", "u1", D(2026, 3, 1), 88888, "UNKNOWN IN"))
    store.declare_account(
        AccountRecord(
            ref=AccountRef("oldsaver"), label="Old saver", kind="savings", closed=D(2026, 2, 1)
        )
    )
    record_stated_anchor(store, "everyday", "2026-03-31", "5555.55")
    record_stated_anchor(store, "card", "2026-04-30", "-900.00")
    record_stated_anchor(store, "drifter", "2026-03-31", "1000.00")
    record_stated_anchor(store, "drifter", "2026-04-30", "1111.11")
    record_stated_anchor(store, "oldsaver", "2025-12-31", "1500.00")
    work = Asset("work-pension", AssetKind.DEFINED_CONTRIBUTION)
    for when, minor in ((D(2026, 2, 28), 1000000), (D(2026, 5, 31), 1200000)):
        record_observation(store, work, observed_at=when, source="statement", value_minor=minor)
    record_observation(
        store, Asset("house", AssetKind.PROPERTY),
        observed_at=D(2026, 4, 15), source="valuation", value_minor=25000000,
    )
    record_observation(
        store, Asset("state-pension-forecast", AssetKind.STATE_PENSION),
        observed_at=D(2026, 6, 1), source="forecast", annual_income_minor=1150000,
    )
    record_observation(
        store, Asset("teachers-scheme", AssetKind.DEFINED_BENEFIT),
        observed_at=D(2026, 6, 1), source="statement", annual_income_minor=612345,
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "position.sqlite3") as opened:
        yield opened


@pytest.fixture
def position(store) -> Position:
    household(store)
    return read_position(store, today=TODAY)


def group(position: Position, key: str):
    return next(g for g in position.groups if g.key == key)


def account(position: Position, ref: str):
    every = [a for g in position.groups for a in g.accounts] + list(position.uncounted)
    return next(a for a in every if a.ref == ref)


def month(position: Position, label: str):
    return next(p for p in position.history if p.month == label)


class TestTheHouseholdsNetWorth:
    def test_NetWorth_IsTheCountedAccountsPlusTheCountedAssets(self, position):
        assert position.net_worth is not None
        assert position.net_worth.minor == 26935987

    def test_Subtotals_AreWhatEachGroupHolds(self, position):
        assert group(position, "in").subtotal.minor == 655987
        assert group(position, "out").subtotal.minor == -90000
        assert group(position, "archived").subtotal.minor == 170000
        assert position.assets_subtotal.minor == 26200000

    def test_Headline_CountsWhatIsCountedAndWhatIsNot(self, position):
        assert (position.accounts_counted, position.accounts_total) == (4, 5)
        assert position.accounts_uncounted == 1
        assert position.assets_counted == 2

    def test_Groups_SeparateByWhatTheBalanceSaysAndFoldArchivedApart(self, position):
        assert {a.ref for a in group(position, "in").accounts} == {"everyday", "drifter"}
        assert {a.ref for a in group(position, "out").accounts} == {"card"}
        assert {a.ref for a in group(position, "archived").accounts} == {"oldsaver"}
        assert all(a.archived for a in group(position, "archived").accounts)

    def test_ADeclaredKindAndLabel_AreCarriedAndAnUndeclaredAccountUsesItsReference(self, position):
        assert account(position, "oldsaver").kind == "savings"
        assert account(position, "oldsaver").label == "Old saver"
        assert account(position, "everyday").kind == ""
        assert account(position, "everyday").label == "everyday"


class TestAnAccountWithNoOpeningBalance:
    def test_Account_IsListedAsNotCountedAndHasNoBalanceAtAll(self, position):
        [unknown] = position.uncounted
        assert unknown.ref == "unanchored"
        assert unknown.state == "no-opening"
        assert unknown.balance is None
        assert unknown.direction == ""

    def test_Account_IsInNoTotal(self, position):
        # Were its +88,888 counted as a balance the net worth would differ.
        assert position.net_worth is not None
        assert position.net_worth.minor == 26935987
        assert "unanchored" not in {a.ref for g in position.groups for a in g.accounts}

    def test_StatingABalanceForIt_MovesItIntoTheTotal(self, store):
        household(store)
        record_stated_anchor(store, "unanchored", "2026-03-31", "1000.00")

        after = read_position(store, today=TODAY)

        # opening 100,000 - 88,888 = 11,112; now 11,112 + 88,888 = 100,000
        assert after.uncounted == ()
        assert (after.accounts_counted, after.accounts_total) == (5, 5)
        assert account(after, "unanchored").balance is not None
        assert after.net_worth is not None
        assert after.net_worth.minor == 26935987 + 100000

    def test_AnOpeningThatIsWithheld_IsNotCountedEitherAndSaysWhy(self, store):
        euros = replace(txn("euro", "src-a", "x1", D(2026, 3, 1), 5000, "EUROS"), currency="EUR")
        land(store, "d-eur", euros)
        record_stated_anchor(store, "euro", "2026-03-31", "10.00")

        shown = read_position(store, today=TODAY)

        [withheld] = shown.uncounted
        assert withheld.state == "withheld"
        assert withheld.withheld == "the rows are not all in GBP"
        assert withheld.balance is None
        assert shown.net_worth is None


class TestADifferingLaterAnchor:
    def test_Account_IsFlaggedButStillCountedAtWhatItsRowsGive(self, position):
        drifter = account(position, "drifter")

        assert drifter.state == "counted"
        assert drifter.checks_differ == 1
        assert drifter.checks_agree == 0
        assert drifter.balance is not None
        assert drifter.balance.minor == 105000

    def test_AnAccountWhoseChecksAgreeOrThatHasNone_IsNotFlagged(self, position):
        assert account(position, "everyday").checks_differ == 0
        assert account(position, "card").checks_differ == 0


class TestIncomeEntitlements:
    def test_StatePensionAndDefinedBenefit_AreListedWithTheirIncomeAndNeverTotalled(self, position):
        entitlements = {e.asset_id: e for e in position.entitlements}

        assert set(entitlements) == {"state-pension-forecast", "teachers-scheme"}
        assert entitlements["state-pension-forecast"].annual_income.minor == 1150000
        assert entitlements["teachers-scheme"].annual_income.minor == 612345
        assert {a.asset_id for a in position.assets} == {"work-pension", "house"}
        assert position.assets_subtotal.minor == 26200000

    def test_AccountBalanceAnchors_AreNotAssets(self, position):
        # Five stated anchors share the valuations table with the assets.
        assert len(position.assets) + len(position.entitlements) == 4


class TestAssets:
    def test_AnAsset_IsCountedAtItsLatestObservationWithItsDateAndAge(self, position):
        pension = next(a for a in position.assets if a.asset_id == "work-pension")

        assert pension.value.minor == 1200000
        assert pension.observed_on == "2026-05-31"
        assert pension.age_days == (TODAY - D(2026, 5, 31)).days
        assert pension.observations == 2
        assert pension.source == "statement"


class TestMonthEndHistory:
    @pytest.mark.parametrize(
        ("label", "net_worth", "included"),
        [
            ("2025-12", 150000, 1),
            ("2026-01", 170000, 1),
            ("2026-02", 1093456, 3),
            ("2026-03", 1725555, 5),
            ("2026-04", 26728210, 6),
            ("2026-05", 26935987, 6),
            ("2026-10", 26935987, 6),
        ],
    )
    def test_MonthEnd_IsWhatWasKnownThen(self, position, label, net_worth, included):
        point = month(position, label)

        assert point.net_worth.minor == net_worth
        assert (point.included, point.of) == (included, 6)

    def test_History_RunsFromTheEarliestFigureToTheCurrentMonth(self, position):
        months = [p.month for p in position.history]
        assert (months[0], months[-1], len(months)) == ("2025-12", "2026-10", 11)

    def test_AnAccount_ContributesNothingBeforeItsOpeningApplies(self, position):
        # everyday applies from 03-04, so February holds neither it nor the
        # drifter (03-09): included is 3 (oldsaver, card, pension), not 5.
        assert month(position, "2026-02").included == 3

    def test_AnAsset_IsCarriedForwardUntilItIsObservedAgain(self, position):
        # March and April carry the February observation; May takes the new one.
        assert month(position, "2026-03").net_worth.minor == 1725555
        may, april = month(position, "2026-05"), month(position, "2026-04")
        assert may.net_worth.minor - april.net_worth.minor == (550987 - 543210) + 200000

    def test_CompleteFrom_IsTheFirstMonthThatHoldsEveryCountedItem(self, position):
        assert position.complete_from == "2026-04"
        assert [p.month for p in position.history if p.partial] == [
            "2025-12", "2026-01", "2026-02", "2026-03",
        ]

    def test_TheNewestPoint_IsTheHeadline(self, position):
        assert position.net_worth is not None
        assert position.history[-1].net_worth.minor == position.net_worth.minor


class TestTheBalanceIsTheLedgersBalance:
    @pytest.mark.parametrize("ref", ["everyday", "card", "drifter", "oldsaver"])
    def test_Account_BalanceEqualsTheLedgersRunningPosition(self, store, position, ref):
        ledger = build_ledger(store, ref, None, bound=False)

        assert ledger.position is not None
        assert account(position, ref).balance is not None
        assert account(position, ref).balance.minor == ledger.position.store_balance.minor


class TestDegenerateHistories:
    def test_NothingHeld_IsNoNetWorthAndNoHistory(self, store):
        empty = read_position(store, today=TODAY)

        assert empty.net_worth is None
        assert empty.net_direction == ""
        assert (empty.accounts_total, empty.accounts_counted, empty.assets_counted) == (0, 0, 0)
        assert empty.history == ()
        assert empty.complete_from == ""

    def test_EveryAccountUncounted_IsNoNetWorthEvenThoughRowsExist(self, store):
        land(store, "d", txn("only", "src-a", "o1", D(2026, 3, 1), 12345, "ROW"))

        shown = read_position(store, today=TODAY)

        assert shown.net_worth is None
        assert (shown.accounts_counted, shown.accounts_total) == (0, 1)
        assert shown.history == ()

    def test_OneObservationInTheCurrentMonth_IsOnePointNotADivisionByZero(self, store):
        record_observation(
            store, Asset("sole", AssetKind.OTHER),
            observed_at=D(2026, 10, 1), source="note", value_minor=777777,
        )

        shown = read_position(store, today=TODAY)

        assert [(p.month, p.net_worth.minor) for p in shown.history] == [("2026-10", 777777)]
        assert shown.complete_from == "2026-10"

    def test_ForeignCurrencyObservations_AreLeftOutAndCounted(self, store):
        # A raw row, since the recording door itself refuses another currency.
        store.record_valuation_row(
            asset_id="villa", kind="property", observed_at=D(2026, 4, 1),
            source="note", value_minor=999900, currency="EUR",
        )

        shown = read_position(store, today=TODAY)

        assert shown.assets == ()
        assert shown.foreign_observations == 1
        assert shown.net_worth is None

    def test_AnAssetOverdrawnInNetWorth_IsADirectionNotAnAbsoluteValue(self, store):
        record_observation(
            store, Asset("loan", AssetKind.OTHER),
            observed_at=D(2026, 9, 1), source="note", value_minor=-5000,
        )

        shown = read_position(store, today=TODAY)

        assert shown.net_worth is not None
        assert shown.net_worth.minor == -5000
        assert shown.net_direction == "out"


def provisional(position: Position, label: str) -> int:
    return next(p for p in position.provisional_history if p.month == label).total.minor


def with_late_account(store: Store) -> None:
    household(store)
    land(
        store,
        "d-late",
        txn("late", "src-a", "l0", D(2026, 4, 1), -999, "VANISHED", status=TransactionStatus.VOID),
        txn("late", "src-a", "l1", D(2026, 5, 10), -20000, "OUT"),
        txn("late", "src-a", "l2", D(2026, 6, 1), 5000, "SETTLING",
            status=TransactionStatus.PENDING),
    )


class TestWhatAnUncountedAccountHasMoved:
    def test_Account_ShowsItsMovementAndTheDateOfItsFirstRow(self, position):
        [unknown] = position.uncounted

        assert unknown.moved is not None
        assert unknown.moved.minor == 88888
        assert unknown.moved_direction == "in"
        assert unknown.first_row == "2026-03-01"
        assert unknown.balance is None, "a movement is never a balance"

    def test_AccountWithNoRows_HasNoMovementAndNoFirstRow(self, store):
        household(store)
        store.declare_account(AccountRecord(ref=AccountRef("dormant"), label="Dormant"))

        shown = read_position(store, today=TODAY)

        dormant = account(shown, "dormant")
        assert dormant.moved is None
        assert dormant.first_row == ""
        assert dormant.moved_direction == ""

    def test_VoidRowsNeverMoveItAndPendingRowsDo(self, store):
        with_late_account(store)

        shown = read_position(store, today=TODAY)

        late = account(shown, "late")
        assert late.moved is not None
        assert late.moved.minor == -15000
        assert late.moved_direction == "out"
        assert late.first_row == "2026-05-10", "the void row of 04-01 is not its first row"

    def test_ACountedAccount_HasNoMovementFigure(self, position):
        assert account(position, "everyday").moved is None


class TestTheProvisionalTotal:
    def test_Total_IsTheKnownNetWorthPlusTheUncountedMovement(self, position):
        assert position.provisional_total is not None
        assert position.provisional_total.minor == 27024875
        assert position.provisional_direction == "in"

    def test_TheKnownFiguresAreUntouchedByIt(self, position):
        assert position.net_worth is not None
        assert position.net_worth.minor == 26935987
        assert group(position, "in").subtotal.minor == 655987
        assert group(position, "out").subtotal.minor == -90000
        assert group(position, "archived").subtotal.minor == 170000
        assert position.assets_subtotal.minor == 26200000
        assert position.history[-1].net_worth.minor == 26935987
        assert (position.accounts_counted, position.accounts_uncounted) == (4, 1)

    def test_TwoUncountedAccounts_AddTheirMovementsAndLeaveTheKnownFiguresAlone(self, store):
        with_late_account(store)

        shown = read_position(store, today=TODAY)

        assert shown.accounts_uncounted == 2
        assert shown.provisional_total is not None
        assert shown.provisional_total.minor == 27009875
        assert shown.net_worth is not None
        assert shown.net_worth.minor == 26935987
        assert group(shown, "in").subtotal.minor == 655987

    def test_WhenEveryAccountIsCounted_NothingIsProvisional(self, store):
        household(store)
        record_stated_anchor(store, "unanchored", "2026-03-31", "1000.00")

        shown = read_position(store, today=TODAY)

        assert shown.provisional_total is None
        assert shown.provisional_direction == ""
        assert shown.provisional_history == ()

    def test_WhenNothingIsCounted_ThereIsNoNetWorthButThereIsAProvisionalTotal(self, store):
        land(store, "d", txn("only", "src-a", "o1", D(2026, 3, 1), 12345, "ROW"))

        shown = read_position(store, today=TODAY)

        assert shown.net_worth is None
        assert shown.history == ()
        assert shown.provisional_total is not None
        assert shown.provisional_total.minor == 12345

    def test_AnUncountedAccountWithNoRows_AddsNothingButIsStillAnUnknownOpening(self, store):
        household(store)
        store.declare_account(AccountRecord(ref=AccountRef("dormant"), label="Dormant"))

        shown = read_position(store, today=TODAY)

        assert shown.accounts_uncounted == 2
        assert shown.provisional_total is not None
        assert shown.provisional_total.minor == 27024875


class TestTheProvisionalHistory:
    @pytest.mark.parametrize(
        ("label", "total"),
        [
            ("2025-12", 150000),
            ("2026-02", 1093456),
            ("2026-03", 1814443),
            ("2026-04", 26817098),
            ("2026-10", 27024875),
        ],
    )
    def test_MonthEnd_IsTheKnownFigureAndTheMovementUpToThen(self, position, label, total):
        assert provisional(position, label) == total

    def test_AnUncountedAccount_ContributesNothingBeforeItsFirstRow(self, position):
        # Its first row is 2026-03-01, so February's figure is the known one.
        assert provisional(position, "2026-02") == month(position, "2026-02").net_worth.minor

    def test_TwoUncountedAccounts_EachContributeFromTheirOwnFirstRow(self, store):
        with_late_account(store)

        shown = read_position(store, today=TODAY)

        assert provisional(shown, "2026-04") == 26817098
        assert provisional(shown, "2026-05") == 27004875
        assert provisional(shown, "2026-10") == 27009875

    def test_TheKnownSeries_IsExactlyAsItWas(self, position):
        assert [p.net_worth.minor for p in position.history] == [
            150000, 170000, 1093456, 1725555, 26728210, *[26935987] * 6,
        ]
        assert position.complete_from == "2026-04"

    def test_TheNewestPoint_IsTheProvisionalTotal(self, position):
        assert position.provisional_total is not None
        assert position.provisional_history[-1].total.minor == position.provisional_total.minor

    def test_WhenNothingIsCounted_TheHistoryRunsFromTheFirstRow(self, store):
        land(store, "d", txn("only", "src-a", "o1", D(2026, 3, 1), 12345, "ROW"))

        shown = read_position(store, today=TODAY)

        months = [p.month for p in shown.provisional_history]
        assert (months[0], months[-1], len(months)) == ("2026-03", "2026-10", 8)
        assert {p.total.minor for p in shown.provisional_history} == {12345}


class TestThePureCore:
    def test_BuildPosition_GivenData_NeedsNoStore(self):
        rows = (txn("a", "s", "1", D(2026, 1, 5), 1000, "IN"),)
        opening = derive_opening("a", [Anchor(D(2026, 1, 31), 5000, STATED)], rows)
        item = AccountInput("a", "A", "", False, opening, rows)
        obs = Observation(D(2026, 1, 1), "other", 300, None, "n")

        built = build_position([item], [AssetInput("x", (obs,))], today=D(2026, 1, 31))

        # opening 5,000 - 1,000 = 4,000; now 5,000; plus 300
        assert built.net_worth is not None
        assert built.net_worth.minor == 5300
        assert built.history[0].month == "2026-01"
