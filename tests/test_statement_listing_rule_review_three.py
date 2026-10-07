"""The listing rule's third round (19fc7ad): "what adds up is what is shown", asked of EVERY
known balance of an account said to add up, and not only of a day a statement closed before
something (`listing_rule_reading.shown_balances_are_the_stated_ones`, which holds on those days).

The balance SHOWN for the end of a day is `ledger.running_balance` over the account's opening
figure and its rows by their stored dates: the function the ledger's running position and the
position page both call. The balance STATED is the known balance's own figure. Every account is
invented, built through the file doors, read through `app_reading`, and its answer was written
here before the first run. EVERY TEST IN THIS FILE FAILED when it was written, against 19fc7ad.

A statement is tested by what it LISTS, whatever the dates; the position is drawn by DATE. Where
the two part, the rule says "adds up" of a balance the app does not show. Cards open at 100.00
owed; the opening figure the app holds is the statement's own in every case below.

NEW WITH THE RULE (each account was "nothing to check against" at f2440eb):

  y-late      A lone statement closing 10 Jan at 117.56 lists purchases made 20 Dec and 9 Jan.
              The feed then reports the 9 Jan purchase, posted 11 Jan: one transaction, held
              under the feed's date. Said: adds up through 10 Jan, tested by the 2 transactions
              its statement lists. Expected: 117.56 owed shown for 10 Jan. Got: 112.37.
              (Arriving in the other order, the statement's date is kept and it is the same.)
  y-pending   A lone statement closing 10 Jan at 117.56, and a feed transaction of 4.13 still
              pending, dated inside its days: not counted towards a statement's balance, counted
              in the position. Said: adds up through 10 Jan. Expected: 117.56 shown for 10 Jan.
              Got: 121.69.

THE DAY NEWLY TESTED AND OFFERED TO PROTECTION (the gap itself is older than the rule):

  y-chain     January's statement (104.19 at 10 Jan) and February's, which lists a purchase of
              3.07 made 8 Jan: pending at January's close, the owner's own case. Without the rule
              10 Jan sets the opening, is not tested, and is not offered to protection; with it
              10 Jan is "tested by the 1 transaction its statement lists" and offered.
              Expected: protection pressed through 10 Jan records a balance its span reaches.
              Got: records 104.19; the span reaches 107.26.

A DISREGARD MAKES A CONFLICT OF A DAY THAT WAS EXPLAINED (the reach of "a disregarded statement
lists nothing"):

  y-set-aside Statements closing 10 Jan, 10 Feb, and 10 Mar; March's lists a purchase of 7.77 made
              10 Feb (pending at February's close), and the balance typed for 10 Feb includes it.
              The day is explained and the account adds up through 10 Mar. The person then
              disregards March's closing. The measurement now calls the purchase listed by no
              statement and tells the rule the chain counts it at February's closing; the chain
              still places it by March's statement, so the claim is not borne out.
              Expected: 10 Feb is still explained (the two balances differ by exactly that
              purchase, as before) and the account adds up through 10 Feb.
              Got: a conflict on 10 Feb, adding up through 10 Jan only. (13b2a32 answered this
              input "adds up"; f2440eb, without the rule, a conflict.)

OLDER THAN THE RULE, and stated here because it is the invariant as asked (it fails at f2440eb
too, where these accounts already "add up"): over the measurement's own household, the accounts
`december`, `december-current`, `pending`, and `settled` are shown a balance other than the
stated one on ten statement closing days, by the purchase a later statement lists.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from listing_rule_reading import app_reading, shown_balances_are_stated_or_named
from obdi.agreement import date_difference_sentence
from obdi.balance_anchors import (
    STATEMENT,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
)
from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.identity import content_key
from obdi.ingest import reconcile_batch
from obdi.ledger import running_balance
from obdi.protection import ProtectionRefused, press
from obdi.protection import tested_days as days_offered
from obdi.standing_data import ADDS_UP, statement_checks_for
from obdi.store import Store
from statement_span_world import Spend, statement
from test_statement_listing_measure import FAMILIES, OPENING, chain
from test_statement_listing_measure import world as listing_world  # noqa: F401 - the fixture

D = date
MID, JAN, FEB = D(2025, 12, 10), D(2026, 1, 10), D(2026, 2, 10)


def _feed(
    store: Store,
    ref: str,
    day: date,
    payee: str,
    minor: int,
    *,
    source: str = "truelayer-booked",
    status: TransactionStatus = TransactionStatus.BOOKED,
) -> None:
    reconcile_batch(
        store,
        [
            Transaction(
                account_id=ref,
                amount_minor=-minor,
                currency="GBP",
                value_date=day,
                booking_date=day,
                description=payee,
                source=source,
                source_id=f"{ref}-{payee}",
                status=status,
                tier=SourceTier.AUTHORITATIVE,
                content_key=content_key(amount_minor=-minor, value_date=day, description=payee),
            )
        ],
        digest=f"{ref}-{payee}",
    )


def _lone(store: Store, root: Path, ref: str, second: date) -> int:
    return statement(
        store, root, ref, JAN, OPENING,
        [Spend(D(2025, 12, 20), f"Alpha {ref}", 1237), Spend(second, f"Bravo {ref}", 519)],
        received=JAN, previous_close=MID,
    )


def _chain(store: Store, root: Path, ref: str) -> None:
    chain(
        store, root, ref, [JAN, FEB],
        [
            [Spend(D(2026, 1, 5), f"One {ref}", 419)],
            [Spend(D(2026, 1, 8), f"Pending {ref}", 307), Spend(D(2026, 2, 2), f"Two {ref}", 709)],
        ],
    )


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("review3")
    with Store(root / "store.sqlite3") as store:
        _lone(store, root, "y-late", D(2026, 1, 9))
        _feed(store, "y-late", D(2026, 1, 11), "Bravo y-late", 519)
        _lone(store, root, "y-pending", D(2026, 1, 5))
        _feed(
            store, "y-pending", D(2025, 12, 30), "Hold y-pending", 413,
            source="truelayer-pending", status=TransactionStatus.PENDING,
        )
        store.connection.commit()
        yield store


def _shown(store: Store, ref: str, day: date) -> int:
    """The balance the position shows for the end of `day`."""
    opening = effective_opening(store, ref, families=FAMILIES)
    assert opening.opening_minor is not None
    return running_balance(opening.opening_minor, store.transactions_for_account(ref), day)


def _out_of_step(store: Store, ref: str) -> list[tuple[str, int]]:
    """Each known balance on or before the day the account adds up through whose stated figure is
    not the balance shown for its day (bar what a statement is taken to have closed before), as
    (day, shown less stated)."""
    standing, verdict = app_reading(store, ref, FAMILIES)
    if verdict != ADDS_UP or standing.own.through is None:
        return []
    closed = {c.day: sum(c.that_amounts) for c in standing.own.closed_before}
    found = []
    for reading in effective_opening(store, ref, families=FAMILIES).readings:
        known = reading.anchor
        if known.at is not None or known.day > standing.own.through:
            continue
        stated = known.balance_minor
        if known.basis == "statement" and known.day in closed:
            stated += closed[known.day]
        shown = _shown(store, ref, known.day)
        if shown != stated:
            found.append((known.day.isoformat(), shown - stated))
    return found


class TestALoneStatementSaidToAddUp:
    def test_Position_WhenAListedPurchaseIsHeldUnderTheFeedsLaterDate_ShowsTheClosingBalance(
        self, world
    ):
        # Round four, finding 1: ACCEPTED and said. A statement is tested by what it lists and
        # the position is drawn by stored date, so the shown balance for 10 Jan is not the stated
        # one; it differs by exactly the purchase the statement lists that is held under the
        # feed's later date, which the page names in counts (one listed, dated after).
        now, verdict = app_reading(world, "y-late", FAMILIES)
        opening = effective_opening(world, "y-late", families=FAMILIES)
        dated = sorted(t.value_date for t in world.transactions_for_account("y-late"))
        (check,) = statement_checks_for(world, "y-late", FAMILIES).statements

        assert dated == [D(2025, 12, 20), D(2026, 1, 11)]
        assert (verdict, now.own.through, opening.opening_minor) == (ADDS_UP, JAN, -10000)
        assert _shown(world, "y-late", JAN) == -11756 + 519
        assert (check.by_date.pending, check.by_date.later, check.by_date.listed_after) == (0, 0, 1)
        assert check.date_gap_minor == 519
        assert shown_balances_are_stated_or_named(world, "y-late", FAMILIES, now) == 1
        assert "1 transaction it lists is dated after it" in date_difference_sentence(
            JAN, check.by_date
        )

    def test_Position_WhenAPendingTransactionIsHeldInsideItsDays_ShowsTheClosingBalance(
        self, world
    ):
        # Round four, finding 1: accepted and said, in counts (one pending, dated before).
        now, verdict = app_reading(world, "y-pending", FAMILIES)
        opening = effective_opening(world, "y-pending", families=FAMILIES)
        (check,) = statement_checks_for(world, "y-pending", FAMILIES).statements

        assert (verdict, now.own.through, opening.opening_minor) == (ADDS_UP, JAN, -10000)
        assert (check.by_date.pending, check.by_date.later, check.by_date.listed_after) == (1, 0, 0)
        assert check.date_gap_minor == -413
        assert shown_balances_are_stated_or_named(world, "y-pending", FAMILIES, now) == 1


class TestAFirstStatementNewlyOfferedToProtection:
    def test_Protection_WhenTheNextStatementListsAPurchaseMadeBeforeTheClose_RecordsWhatItReaches(
        self, tmp_path
    ):
        with Store(tmp_path / "store.sqlite3") as store:
            _chain(store, tmp_path, "y-chain")
            before, _ = app_reading(store, "y-chain", FAMILIES, rule=False)
            now, verdict = app_reading(store, "y-chain", FAMILIES)
            opening = effective_opening(store, "y-chain", families=FAMILIES)
            offered_before = days_offered(opening, before)
            offered = days_offered(opening, now)
            with pytest.raises(ProtectionRefused):
                press(store, "y-chain", JAN.isoformat(), opening=opening, standing=now)
            record = store.protection_record("y-chain")

        # Round four, finding 2: protection is NOT widened. 10 Jan is tested only by its
        # statement's own listing, so it is not offered or pressable, and what is offered is
        # what it was before the rule.
        assert verdict == ADDS_UP
        assert offered_before == offered == (FEB,)
        assert record is None


class TestALaterStatementSetAside:
    def test_Day_WhenTheStatementListingItsPendingPurchaseIsDisregarded_IsStillExplained(
        self, tmp_path
    ):
        mar = D(2026, 3, 10)
        with Store(tmp_path / "store.sqlite3") as store:
            owed = chain(
                store, tmp_path, "y-set-aside", [JAN, FEB, mar],
                [
                    [Spend(D(2026, 1, 5), "One y-set-aside", 419)],
                    [Spend(D(2026, 1, 20), "Two y-set-aside", 717)],
                    [
                        Spend(FEB, "Pending y-set-aside", 777),
                        Spend(D(2026, 2, 20), "Later y-set-aside", 331),
                    ],
                ],
            )
            record_stated_anchor(
                store, "y-set-aside", FEB.isoformat(), f"{-(owed[1] + 777) / 100:.2f}",
                today=D(2026, 9, 1),
            )
            before, before_verdict = app_reading(store, "y-set-aside", FAMILIES)
            march = next(
                r.anchor
                for r in effective_opening(store, "y-set-aside", families=FAMILIES).readings
                if r.anchor.basis == STATEMENT and r.anchor.day == mar
            )
            disregard_balance(
                store, "y-set-aside", mar.isoformat(), march.stating, STATEMENT, families=FAMILIES
            )
            now, verdict = app_reading(store, "y-set-aside", FAMILIES)

        assert (before_verdict, before.own.through) == (ADDS_UP, mar)
        assert [c.day for c in before.own.closed_before] == [FEB]
        assert now.own.conflicts == ()
        assert (verdict, now.own.through) == (ADDS_UP, FEB)


class TestEveryKnownBalanceOfAnAccountSaidToAddUp:
    def test_Position_OverTheMeasurementsHousehold_ShowsEachStatedBalanceOrWhatThePageNames(
        self, listing_world  # noqa: F811
    ):
        """Round four, finding 1: the gap between a listing and its dates is older than the rule
        (it fails at f2440eb too) and is ACCEPTED, so the invariant is the true one: every known
        balance on or before `through` of an account that adds up is shown as stated, or differs
        by exactly the transactions the page names."""
        store = listing_world[0]
        refs = sorted(
            str(row[0])
            for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
        )

        checked = 0
        for ref in refs:
            standing, _ = app_reading(store, ref, FAMILIES)
            checked += shown_balances_are_stated_or_named(store, ref, FAMILIES, standing)

        assert checked > 20
