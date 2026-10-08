"""The listing rule's second round (13b2a32) against inputs built to make its answers false.

Every account is invented and built through the doors a person's files use; every answer was
written here before the first run; every account is read through `app_reading`, the app's own
composition. EVERY TEST IN THIS FILE FAILED when it was written, against 13b2a32.

A FOLD INTO A SPACE IS CALLED A FAULT (decision 6). Two complete households, folded by the fold
pass itself (`fold_space_copies`) and read with `families_of` over the same account map. In each
a main account has two Spaces, Bills and Holiday; its Starling statement cannot see them and
lists an energy bill the bank's own feed filed under Bills, which the pass folds into the Bills
transaction.

  w-unkept    The statement's reading is not kept (the state `unkept` is elsewhere). Without the
              rule the account adds up through 31 Jan and protection is offered.
              Expected: cannot say, no fault, the verdict unmoved.
              Got: "does not add up", adding up through nothing: "a transaction it lists is held
              under another account". With no kept reading the statement's source is not known,
              so it is not recognised as blind to the Spaces.
  w-twospace  The account map also binds the statement's source to Holiday (Holiday's own
              statements are held under it). The fold pass asks blindness Space by Space and
              folds the bill into Bills; the listing asks whether the source is bound to ANY of
              the account's Spaces, finds Holiday, and does not test with the Spaces.
              Expected: no fault. Got: the same fault.

THE DAY-AFTER HYPOTHESIS SAYS "ADDS UP" OVER BALANCES THE APP DOES NOT SHOW (decision 4). A card
opens at 100.00 owed; its statement closes 10 Feb at 110.48 (7.17 and 3.31); a purchase of 7.77
is dated 11 Feb; a typed balance for 10 Feb is 118.25 owed. The rule takes the typed balance to
have been taken the day after, and says the account adds up. But the typed balance, stated for
10 Feb, is the one the opening is worked out from, and nothing moves it:

  w-next-day     Expected, if it adds up: 110.48 owed at the end of 10 Feb and 118.25 at the end
                 of 11 Feb, from an opening of 100.00 (the statement's own).
                 Got: 118.25 and 126.02, from an opening of 107.77.
  w-next-listed  The same, the purchase listed by the next statement (closing 10 Mar at 118.25).
                 Said: "adds up to every known balance from 10 Feb to 10 Mar".
                 Expected: the balance shown for 10 Mar is that statement's, 118.25.
                 Got: 126.02.
  w-contradicted The same as w-next-day, and a typed balance for 11 Feb of 110.48: the person's
                 own figure for the day after says the purchase had not happened.
                 Expected: the hypothesis that the 10 Feb figure is the day after's is refused,
                 and the day stays a conflict. Got: taken, and 10 Feb offered to protection.

PROTECTION RECORDS A BALANCE THE SPAN DOES NOT REPRODUCE (decision 9).

  w-same-day     A purchase of 7.77 dated 10 Feb, the typed balance 118.25: the statement is
                 taken to have closed before it, rightly, and the span through 10 Feb reaches
                 118.25. Expected: protection pressed through 10 Feb records 118.25.
                 Got: the statement's 110.48 ("verified against" a figure the span is 7.77 from).

A DISREGARDED STATEMENT'S TRANSACTIONS STILL COUNT AS LISTED INSIDE ANOTHER'S DAYS (decision 7).

  w-reissue      Two documents close 10 Jan: one lists two purchases (117.56 owed); the reissue
                 lists the same two and a third of 4.13 dated 7 Jan (121.69 owed). The person
                 disregards the reissue's closing, which the page offers. The third purchase is
                 still held and counts, inside the first statement's days, and no statement in
                 use lists it. Expected: as `r-lone-unlisted`, nothing to check against, no day
                 offered to protection. Got: adds up through 10 Jan, "tested by the 2
                 transactions its statement lists", 10 Jan offered, from an opening of 95.87
                 where the statement states 100.00.

THE MEMO'S KEY CAN REPEAT (the cost fix). `standing_data._CHECKS` is keyed by the store's path,
its standing epoch, and the Spaces. Two stores whose key is the same and whose statements
conclude differently:

  a rolled-back write   the checks are read inside a write that is then rolled back (the epoch
                        returns to what it was), and a different write follows.
  a restored file       another copy of the store is put at the same path, having reached the
                        same epoch by a different write.
  Expected for both: the checks of the store as it stands. Got: the other state's.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from listing_rule_reading import app_reading
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.accounts import AccountBinding, AccountMap, AccountRecord, AccountRef
from obdi.ingest.family_anchors import Families, families_of
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.space_attribution import fold_space_copies
from obdi.ingest.store import Store
from obdi.verify.agreement import HELD_CONFLICT
from obdi.verify.balance_anchors import (
    STATEMENT,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
)
from obdi.verify.protection import press, running_balance
from obdi.verify.protection import tested_days as days_offered
from obdi.verify.standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    statement_checks_for,
)
from obdi.verify.statement_listing_measure import statement_checks_all
from statement_span_world import Spend, feed, statement
from test_statement_listing_measure import FAMILIES, OPENING, starling_statement

D = date
OUR_DAY = D(2026, 9, 1)
JAN, FEB, MAR = D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10)
NEXT = D(2026, 2, 11)
STATEMENT_SOURCE = "starling-statement-pdf"


def _typed_balance(store: Store, ref: str, day: date, minor: int) -> None:
    record_stated_anchor(store, ref, day.isoformat(), f"{minor / 100:.2f}", today=OUR_DAY)


def _household(store: Store, root: Path, tag: str) -> None:
    """A main account whose Starling statement lists two of its own payments and an energy bill
    that the bank's own feed files under its Bills Space."""
    starling_statement(
        store, root, f"{tag}-main", D(2026, 1, 1), D(2026, 1, 31), 500000,
        [
            (D(2026, 1, 5), f"SHOP A {tag}", 4001),
            (D(2026, 1, 9), f"SHOP B {tag}", 2503),
            (D(2026, 1, 12), f"ENERGY BILL {tag}", 1507),
        ],
    )
    day = D(2026, 1, 12)
    text = f"ENERGY BILL {tag}"
    reconcile_batch(
        store,
        [
            Transaction(
                account_id=f"{tag}-bills",
                amount_minor=-1507,
                currency="GBP",
                value_date=day,
                booking_date=day,
                description=text,
                source="starling",
                source_id=f"feed-item-{tag}",
                tier=SourceTier.AUTHORITATIVE,
                content_key=content_key(amount_minor=-1507, value_date=day, description=text),
            )
        ],
        digest=f"feed-{tag}",
    )


def _map() -> AccountMap:
    bindings, records = [], []
    for tag in ("w-unkept", "w-twospace"):
        bindings += [
            AccountBinding(f"{tag}-main", "starling", f"acc-{tag}"),
            AccountBinding(f"{tag}-bills", "starling", f"cat-bills-{tag}"),
            AccountBinding(f"{tag}-holiday", "starling", f"cat-holiday-{tag}"),
        ]
        records += [
            AccountRecord(
                ref=AccountRef(f"{tag}-{space}"),
                kind="starling-space",
                parent=AccountRef(f"{tag}-main"),
            )
            for space in ("bills", "holiday")
        ]
    # Holiday's own statements are held under the statement's source.
    bindings.append(AccountBinding("w-twospace-holiday", STATEMENT_SOURCE, "pdf-holiday"))
    return AccountMap(bindings, records=records)


@pytest.fixture(scope="module")
def spaces(tmp_path_factory):
    root = tmp_path_factory.mktemp("review2-spaces")
    with Store(root / "store.sqlite3") as store:
        _household(store, root, "w-unkept")
        _household(store, root, "w-twospace")
        store.connection.commit()
        account_map = _map()
        folded = fold_space_copies(store, account_map)
        store.connection.execute(
            "DELETE FROM statement_readings WHERE digest IN "
            "(SELECT digest FROM raw_artefacts WHERE account_ref = 'w-unkept-main')"
        )
        store.connection.commit()
        yield store, families_of(store, account_map), folded


def _card(store: Store, root: Path, ref: str) -> int:
    return statement(
        store, root, ref, FEB, OPENING,
        [Spend(D(2026, 1, 20), f"Quiet {ref}", 717), Spend(D(2026, 2, 5), f"Calm {ref}", 331)],
        received=FEB, previous_close=JAN,
    )


@pytest.fixture(scope="module")
def cards(tmp_path_factory):
    root = tmp_path_factory.mktemp("review2-cards")
    with Store(root / "store.sqlite3") as store:
        closing = _card(store, root, "w-next-day")
        feed(store, "w-next-day", [Spend(NEXT, "Late w-next-day", 777)], digest="nd")
        _typed_balance(store, "w-next-day", FEB, -closing - 777)

        closing = _card(store, root, "w-next-listed")
        statement(
            store, root, "w-next-listed", MAR, closing,
            [Spend(NEXT, "Late w-next-listed", 777)], received=MAR, previous_close=FEB,
        )
        _typed_balance(store, "w-next-listed", FEB, -closing - 777)

        closing = _card(store, root, "w-contradicted")
        feed(store, "w-contradicted", [Spend(NEXT, "Late w-contradicted", 777)], digest="co")
        _typed_balance(store, "w-contradicted", FEB, -closing - 777)
        _typed_balance(store, "w-contradicted", NEXT, -closing)
        store.connection.commit()
        yield store


def _shown(store: Store, ref: str, day: date) -> int:
    """The balance the account page shows for the end of `day`: the opening and the rows."""
    opening = effective_opening(store, ref, families=FAMILIES)
    assert opening.opening_minor is not None
    return running_balance(opening.opening_minor, store.transactions_for_account(ref), day)


class TestAStatementLineFoldedIntoTheAccountsOwnSpace:
    def test_Account_WhenItsBlindStatementsReadingIsNotKept_IsNotFaultedAndStillAddsUp(
        self, spaces
    ):
        store, families, folded = spaces
        before, before_verdict = app_reading(store, "w-unkept-main", families, rule=False)
        now, verdict = app_reading(store, "w-unkept-main", families)

        # The fold pass folded each household's bill into its Bills Space: complete households.
        assert folded.folded == 2
        assert (before_verdict, before.own.through) == (ADDS_UP, D(2026, 1, 31))
        assert now.own.statement_faults == ()
        assert (verdict, now.own.through) == (ADDS_UP, D(2026, 1, 31))

    def test_Account_WhenTheStatementsSourceIsBoundToAnotherOfItsSpaces_IsNotFaulted(
        self, spaces
    ):
        store, families, _ = spaces
        now, _ = app_reading(store, "w-twospace-main", families)

        # The source is bound to Holiday and is blind to Bills, where the bill was folded.
        assert families.spaces_of("w-twospace-main") == ("w-twospace-bills", "w-twospace-holiday")
        assert now.own.statement_faults == ()
        assert now.own.held is None


class TestTheOtherBalanceTakenToBeTheDayAfters:
    def test_Position_WhenTheAccountIsSaidToAddUp_ShowsTheBalancesTheHypothesisStates(
        self, cards
    ):
        # Round three, decision 2: the second hypothesis (the other balance was really taken the
        # day after) is withdrawn. The reviewer's expectation was the balances it would have had
        # to show; with it gone the day is the conflict it is without the rule.
        now, verdict = app_reading(cards, "w-next-day", FAMILIES)
        opening = effective_opening(cards, "w-next-day", families=FAMILIES)

        assert verdict == DOES_NOT_ADD_UP
        assert (now.own.state, now.own.closed_before) == (HELD_CONFLICT, ())
        assert days_offered(opening, now) == ()

    def test_Position_WhenTheNextStatementListsThatPurchase_ShowsItsClosingBalanceOnItsDay(
        self, cards
    ):
        # Round three, decision 2: stays the conflict it is (see the test above).
        now, verdict = app_reading(cards, "w-next-listed", FAMILIES)

        assert (verdict, now.own.state, now.own.closed_before) == (
            DOES_NOT_ADD_UP, HELD_CONFLICT, ()
        )

    def test_Day_WhenABalanceStatedForTheDayAfterSaysOtherwise_StaysAConflict(self, cards):
        now, _ = app_reading(cards, "w-contradicted", FAMILIES)
        opening = effective_opening(cards, "w-contradicted", families=FAMILIES)

        assert now.own.closed_before == ()
        assert now.own.state == HELD_CONFLICT
        assert days_offered(opening, now) == ()


class TestProtectionRecordsTheBalanceReproduced:
    def test_Protection_WhenPressedOnADayAStatementClosedBefore_RecordsTheBalanceTheSpanReaches(
        self, tmp_path
    ):
        with Store(tmp_path / "store.sqlite3") as store:
            closing = _card(store, tmp_path, "w-same-day")
            # Round four: a day tested only by its statement's own listing is not offered to
            # protection, so the day is made one the ordinary chain tests, by an earlier typed
            # balance (the opening, 100.00 owed, before the first purchase).
            _typed_balance(store, "w-same-day", D(2026, 1, 5), -OPENING)
            feed(store, "w-same-day", [Spend(FEB, "Late w-same-day", 777)], digest="sd")
            _typed_balance(store, "w-same-day", FEB, -closing - 777)
            now, verdict = app_reading(store, "w-same-day", FAMILIES)
            opening = effective_opening(store, "w-same-day", families=FAMILIES)
            press(store, "w-same-day", FEB.isoformat(), opening=opening, standing=now)
            record = store.protection_record("w-same-day")
            reached = _shown(store, "w-same-day", FEB)

        assert verdict == ADDS_UP
        assert record is not None
        # 100.00 owed, then 7.17, 3.31, and the 7.77 the statement closed before: 118.25.
        assert reached == -11825
        assert int(str(record["verified_minor"])) == reached


class TestADisregardedStatementsTransactionInsideAnothersDays:
    def test_Account_WhenAReissueIsDisregardedAndItsExtraTransactionIsStillHeld_NothingIsTested(
        self, tmp_path
    ):
        mid, jan = D(2025, 12, 10), JAN
        two = [
            Spend(D(2025, 12, 20), "Alpha Reissue", 1237),
            Spend(D(2026, 1, 5), "Bravo Reissue", 519),
        ]
        again = tmp_path / "again"
        again.mkdir()
        with Store(tmp_path / "store.sqlite3") as store:
            statement(store, tmp_path, "w-reissue", jan, OPENING, two, received=jan,
                      previous_close=mid)
            statement(
                store, again, "w-reissue", jan, OPENING,
                [*two, Spend(D(2026, 1, 7), "Charlie Reissue", 413)],
                received=jan, previous_close=mid,
            )
            closings = sorted(
                (
                    r.anchor
                    for r in effective_opening(store, "w-reissue", families=FAMILIES).readings
                    if r.anchor.basis == STATEMENT
                ),
                key=lambda a: a.balance_minor,
            )
            # The reissue's closing, 121.69 owed, is the first by place of the two for the day.
            disregard_balance(
                store, "w-reissue", jan.isoformat(), closings[0].stating, STATEMENT,
                families=FAMILIES, which=1,
            )
            opening = effective_opening(store, "w-reissue", families=FAMILIES)
            in_use = [r.anchor.balance_minor for r in opening.readings]
            counting = sorted(t.amount_minor for t in store.transactions_for_account("w-reissue"))
            now, verdict = app_reading(store, "w-reissue", FAMILIES)

        assert in_use == [-11756]
        assert counting == [-1237, -519, -413]
        assert verdict == NOTHING_TO_CHECK_AGAINST
        assert days_offered(opening, now) == ()


def _faulted_store(path: Path, root: Path) -> None:
    """One account whose statement lists a purchase the store holds as reversed (a fault)."""
    with Store(path) as store:
        statement(
            store, root, "w-memo", FEB, OPENING, [Spend(D(2026, 1, 20), "Quiet w-memo", 717)],
            received=FEB, previous_close=JAN,
        )
        statement(
            store, root, "w-other", FEB, OPENING, [Spend(D(2026, 1, 21), "Quiet w-other", 419)],
            received=FEB, previous_close=JAN,
        )
        store.connection.execute(
            "UPDATE transactions SET status = 'reversed' WHERE account_id = 'w-memo'"
        )


def _faults(store: Store, families: Families) -> tuple[list[str], list[str]]:
    served = statement_checks_for(store, "w-memo", families)
    fresh = statement_checks_all(store, families)["w-memo"]
    assert served is not None
    return [s.fault for s in served.statements], [s.fault for s in fresh.statements]


class TestTheHeldChecksAreTheStoresOwn:
    def test_Checks_WhenAWriteWasRolledBackAndAnotherFollowed_AreOfTheStoreAsItStands(
        self, tmp_path
    ):
        path = tmp_path / "store.sqlite3"
        _faulted_store(path, tmp_path)
        with Store(path) as store:
            # A write that mends the fault, read inside the write, then rolled back.
            store.connection.execute(
                "UPDATE transactions SET status = 'booked' WHERE account_id = 'w-memo'"
            )
            assert _faults(store, FAMILIES)[0] == [""]
            store.connection.rollback()
            # A different write: the store's epoch is what it was inside the rolled-back one.
            store.connection.execute(
                "UPDATE transactions SET description = description WHERE account_id = 'w-other'"
            )
            store.connection.commit()
            served, fresh = _faults(store, FAMILIES)

        assert fresh == ["no-longer-counts"]
        assert served == fresh

    def test_Checks_WhenAnotherCopyOfTheStoreIsPutAtThePath_AreOfTheStoreAsItStands(
        self, tmp_path
    ):
        path, copy = tmp_path / "store.sqlite3", tmp_path / "copy.sqlite3"
        _faulted_store(path, tmp_path)
        shutil.copy(path, copy)
        with Store(path) as live:
            live.connection.execute(
                "UPDATE transactions SET status = 'booked' WHERE account_id = 'w-memo'"
            )
            live.connection.commit()
            assert _faults(live, FAMILIES)[0] == [""]
        with Store(copy) as other:
            other.connection.execute(
                "UPDATE transactions SET description = description WHERE account_id = 'w-other'"
            )
        shutil.copy(copy, path)
        with Store(path) as restored:
            served, fresh = _faults(restored, FAMILIES)

        assert fresh == ["no-longer-counts"]
        assert served == fresh
