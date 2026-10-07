"""The listing rule (`agreement`, R1 to R5) over households built through the doors a person's
files use, every figure invented, every answer written before the first run.

Each account is a card (Santander's layout, opening at 100.00 owed) or a current account (a
Starling certified statement) unless its name says otherwise. "Today" is the agreement rule
without the statements' own listings laid on it; "now" is with them.

  r-lone              One card statement closing 10 Jan, two purchases in its own days, held.
                      Today: nothing to check against. Now: adds up, through 10 Jan, tested by
                      its 2 listed transactions.
  r-lone-unlisted     The same, and a feed holds a third purchase inside its days that no
                      statement lists. Verified, but its days are not: nothing to check against.
  r-lone-earlier      The same, and a feed holds a purchase before the statement's first day.
                      Nothing to check against: its days stop short of the earlier purchase.
  r-lone-current      A lone Starling statement, one payment: adds up through its closing.
  r-lone-missing      Lists 3 transactions, the store holds 2: does not add up, "not held".
  r-lone-merged       A listed transaction held with another amount: does not add up.
  r-lone-unread       A statement whose reading was never kept: cannot say. Nothing verified,
                      nothing faulted.
  r-unsummed          Jan statement adds up; a Feb statement whose own amounts do not reach its
                      closing balance, landed unread. Round one called that a fault; it is a fact
                      about the reading (the statement is never a known balance), so the account
                      adds up through 10 Jan and nothing is faulted.
  r-first             Two consecutive statements (10 Feb, 10 Mar). Today tested on 10 Mar only;
                      now on 10 Feb as well. Adds up through 10 Mar either way.
  r-first-unlisted    As r-first and a feed purchase inside the first statement's days that no
                      statement lists: today and now tested on 10 Mar only.
  r-gap-differs       10 Jan, 10 Feb, April whose opening is not February's closing. Does not add
                      up (held at the April closing) today and now; the gap is arithmetic.
  r-gap-meets         The same but April opens at February's closing: adds up either way.
  r-overlap           A short statement (10 Jan) and a longer one (10 Mar) listing the same two
                      purchases: adds up either way; the later one overlaps, sharing 2.
  sd-*                A statement closing 10 Feb (two purchases) and a typed balance for 10 Feb,
                      with a feed purchase of 7.77:
    sd-explained        dated 10 Feb, the typed balance 7.77 away: today a conflict, now adds
                        up through 10 Feb, the statement taken to have closed before it.
    sd-unexplained      the typed balance 5.00 away: a conflict today and now.
    sd-two              TWO feed purchases dated 10 Feb (7.77 and 1.23), the typed balance 7.77
                        away: only one explains it, so it stays a conflict (all or nothing).
    sd-nextday          the purchase dated 11 Feb: a conflict today and now (round three withdrew
                        the day-after hypothesis that rounds one and two had in turn).
    sd-later-ok         explained, and a typed balance on 1 Mar that the transactions reproduce:
                        adds up through 1 Mar.
    sd-later-bad        explained, and a typed 1 Mar balance 0.03 out: adds up through 10 Feb,
                        held unmet at 1 Mar.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from listing_rule_reading import app_reading, shown_balances_are_stated_or_named
from obdi.ingest.store import Store
from obdi.verify.agreement import (
    HELD_CONFLICT,
    HELD_UNMET,
    Standing,
)
from obdi.verify.balance_anchors import (
    STATEMENT,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
)
from obdi.verify.protection import tested_days as days_offered
from obdi.verify.standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
)
from obdi.verify.statement_listing_measure import Link, statement_listing_report
from statement_span_world import Spend, feed, statement
from test_statement_listing_measure import (
    FAMILIES,
    OPENING,
    chain,
    landed_only,
    santander_lines,
    starling_statement,
)

D = date
OUR_DAY = D(2026, 9, 1)


def build(store: Store, root: Path) -> None:
    jan = D(2026, 1, 10)
    mid = D(2025, 12, 10)

    statement(
        store, root, "r-lone", jan, OPENING,
        [Spend(D(2025, 12, 20), "Alpha Shop", 1237), Spend(D(2026, 1, 5), "Bravo Shop", 519)],
        received=jan, previous_close=mid,
    )
    statement(
        store, root, "r-lone-unlisted", jan, OPENING,
        [Spend(D(2025, 12, 20), "Alpha Shop", 1237), Spend(D(2026, 1, 5), "Bravo Shop", 519)],
        received=jan, previous_close=mid,
    )
    feed(store, "r-lone-unlisted", [Spend(D(2025, 12, 30), "Extra Shop", 413)], digest="u1")
    statement(
        store, root, "r-lone-earlier", jan, OPENING,
        [Spend(D(2025, 12, 20), "Alpha Shop", 1237), Spend(D(2026, 1, 5), "Bravo Shop", 519)],
        received=jan, previous_close=mid,
    )
    feed(store, "r-lone-earlier", [Spend(D(2025, 11, 20), "Early Shop", 413)], digest="e1")
    starling_statement(
        store, root, "r-lone-current", D(2026, 1, 1), D(2026, 1, 31), 500000,
        [(D(2026, 1, 5), "RENT", 91103)],
    )
    statement(
        store, root, "r-lone-missing", D(2026, 2, 10), OPENING,
        [
            Spend(D(2026, 1, 20), "Ash Shop", 717),
            Spend(D(2026, 1, 25), "Birch Shop", 331),
            Spend(D(2026, 2, 5), "Cedar Shop", 457),
        ],
        received=D(2026, 2, 10), previous_close=jan,
    )
    store.connection.execute(
        "DELETE FROM transaction_sources WHERE entity_id IN (SELECT entity_id FROM transactions "
        "WHERE account_id = 'r-lone-missing' AND description = 'Birch Shop')"
    )
    store.connection.execute(
        "DELETE FROM transactions WHERE account_id = 'r-lone-missing' "
        "AND description = 'Birch Shop'"
    )
    statement(
        store, root, "r-lone-merged", D(2026, 2, 10), OPENING,
        [Spend(D(2026, 1, 20), "Basil Shop", 717), Spend(D(2026, 2, 5), "Sage Shop", 331)],
        received=D(2026, 2, 10), previous_close=jan,
    )
    store.connection.execute(
        "UPDATE transactions SET amount_minor = amount_minor - 100 "
        "WHERE account_id = 'r-lone-merged' AND description = 'Sage Shop'"
    )
    statement(
        store, root, "r-unsummed", jan, OPENING, [Spend(D(2026, 1, 5), "Dill Shop", 311)],
        received=jan, previous_close=mid,
    )
    landed_only(
        store, root, "r-unsummed", "r-unsummed-feb",
        santander_lines(
            D(2026, 2, 10), OPENING + 311, [Spend(D(2026, 1, 20), "Fennel Shop", 523)],
            OPENING + 311 + 523 + 100, jan,
        ),
        D(2026, 2, 10),
    )
    chain(
        store, root, "r-first", [D(2026, 2, 10), D(2026, 3, 10)],
        [[Spend(D(2026, 1, 20), "Zinc Shop", 717)], [Spend(D(2026, 2, 20), "Lead Shop", 331)]],
    )
    chain(
        store, root, "r-first-unlisted", [D(2026, 2, 10), D(2026, 3, 10)],
        [[Spend(D(2026, 1, 20), "Zinc Shop", 717)], [Spend(D(2026, 2, 20), "Lead Shop", 331)]],
    )
    feed(store, "r-first-unlisted", [Spend(D(2026, 1, 25), "Extra Shop", 413)], digest="f1")
    feb = chain(
        store, root, "r-gap-differs", [jan, D(2026, 2, 10)],
        [[Spend(D(2026, 1, 5), "Bronze Shop", 413)], [Spend(D(2026, 1, 20), "Copper Shop", 717)]],
    )[-1]
    statement(
        store, root, "r-gap-differs", D(2026, 4, 10), feb + 1500,
        [Spend(D(2026, 3, 20), "Iron Shop", 221)],
        received=D(2026, 4, 10), previous_close=D(2026, 3, 10),
    )
    feb = chain(
        store, root, "r-gap-meets", [jan, D(2026, 2, 10)],
        [[Spend(D(2026, 1, 5), "Silver Shop", 413)], [Spend(D(2026, 1, 20), "Gold Shop", 717)]],
    )[-1]
    statement(
        store, root, "r-gap-meets", D(2026, 4, 10), feb,
        [Spend(D(2026, 3, 20), "Tin Shop", 221)],
        received=D(2026, 4, 10), previous_close=D(2026, 3, 10),
    )
    statement(
        store, root, "r-overlap", jan, OPENING,
        [Spend(D(2025, 12, 20), "Oak Shop", 717), Spend(D(2026, 1, 5), "Elm Shop", 331)],
        received=jan, previous_close=mid,
    )
    statement(
        store, root, "r-overlap", D(2026, 3, 10), OPENING,
        [
            Spend(D(2025, 12, 20), "Oak Shop", 717),
            Spend(D(2026, 1, 5), "Elm Shop", 331),
            Spend(D(2026, 2, 12), "Ivy Shop", 457),
        ],
        received=D(2026, 3, 10), previous_close=mid,
    )
    feb10, feb20 = D(2026, 2, 10), D(2026, 2, 20)
    for ref, rows, stated_away in (
        ("sd-explained", [Spend(feb10, "Late Shop", 777)], 777),
        ("sd-unexplained", [Spend(feb10, "Late Shop", 777)], 500),
        ("sd-two", [Spend(feb10, "Late Shop", 777), Spend(feb10, "Later Shop", 123)], 777),
        ("sd-nextday", [Spend(D(2026, 2, 11), "Late Shop", 777)], 777),
        ("sd-later-ok", [Spend(feb10, "Late Shop", 777), Spend(feb20, "After", 100)], 777),
        ("sd-later-bad", [Spend(feb10, "Late Shop", 777), Spend(feb20, "After", 100)], 777),
    ):
        closing = statement(
            store, root, ref, feb10, OPENING,
            [Spend(D(2026, 1, 20), "Quiet Shop", 717), Spend(D(2026, 2, 5), "Calm Shop", 331)],
            received=feb10, previous_close=jan,
        )
        feed(store, ref, rows, digest=f"late-{ref}")
        stated = -closing - stated_away
        record_stated_anchor(store, ref, "2026-02-10", f"{stated / 100:.2f}", today=OUR_DAY)
        if ref.startswith("sd-later"):
            later = stated - 100 + (3 if ref == "sd-later-bad" else 0)
            record_stated_anchor(store, ref, "2026-03-01", f"{later / 100:.2f}", today=OUR_DAY)
    store.connection.commit()
    # Built last: every later import keeps the readings of the documents still without one.
    statement(
        store, root, "r-lone-unread", D(2026, 2, 10), OPENING,
        [Spend(D(2026, 1, 20), "Hazel Shop", 717), Spend(D(2026, 2, 5), "Holly Shop", 331)],
        received=D(2026, 2, 10), previous_close=jan,
    )
    store.connection.execute(
        "DELETE FROM statement_readings WHERE digest IN "
        "(SELECT digest FROM raw_artefacts WHERE account_ref = 'r-lone-unread')"
    )
    store.connection.commit()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("rule")
    with Store(root / "store.sqlite3") as store:
        build(store, root)
        yield store


def read(store: Store, ref: str, *, rule: bool) -> tuple[Standing, str]:
    return app_reading(store, ref, FAMILIES, rule=rule)


class TestALoneStatement:
    def test_Account_WhenItsOnlyStatementAddsUpByWhatItLists_AddsUpThroughItsClosing(self, world):
        _, today_verdict = read(world, "r-lone", rule=False)
        now, verdict = read(world, "r-lone", rule=True)

        assert today_verdict == NOTHING_TO_CHECK_AGAINST
        assert verdict == ADDS_UP
        assert (now.own.through, now.own.tested) == (D(2026, 1, 10), (D(2026, 1, 10),))
        assert [(t.day, t.listed) for t in now.own.listing_tested] == [(D(2026, 1, 10), 2)]

    def test_Account_WhenItsCurrentAccountStatementAddsUp_AddsUpThroughItsClosing(self, world):
        _, today_verdict = read(world, "r-lone-current", rule=False)
        now, verdict = read(world, "r-lone-current", rule=True)

        assert (today_verdict, verdict) == (NOTHING_TO_CHECK_AGAINST, ADDS_UP)
        assert now.own.through == D(2026, 1, 31)

    def test_Account_WhenAFeedHoldsATransactionInItsDaysNoStatementLists_NothingIsToCheck(
        self, world
    ):
        now, verdict = read(world, "r-lone-unlisted", rule=True)

        assert verdict == NOTHING_TO_CHECK_AGAINST
        assert now.own.through is None

    def test_Account_WhenAFeedHoldsATransactionBeforeItsFirstDay_NothingIsToCheck(self, world):
        _, verdict = read(world, "r-lone-earlier", rule=True)

        assert verdict == NOTHING_TO_CHECK_AGAINST

    def test_Account_WhenTheStatementsReadingWasNeverKept_CannotSayAndNothingIsFaulted(
        self, world
    ):
        now, verdict = read(world, "r-lone-unread", rule=True)

        assert verdict == NOTHING_TO_CHECK_AGAINST
        assert now.own.held is None


class TestAStatementThatDoesNotAddUpByWhatItLists:
    def test_Account_WhenAListedTransactionIsNotHeld_DoesNotAddUpAndSaysWhichCheckFailed(
        self, world
    ):
        # With the movement report laid on (as the app does) an earlier movement fault is the
        # account's hold, and the statement fault is still its own finding.
        now, verdict = read(world, "r-lone-missing", rule=True)

        assert verdict == DOES_NOT_ADD_UP
        (fault,) = now.own.statement_faults
        assert fault.day == D(2026, 2, 10)
        assert "is not held" in fault.says

    def test_Account_WhenAListedTransactionIsHeldWithAnotherAmount_DoesNotAddUp(self, world):
        now, verdict = read(world, "r-lone-merged", rule=True)

        assert verdict == DOES_NOT_ADD_UP
        (fault,) = now.own.statement_faults
        assert "different amount from the one the statement prints" in fault.says

    def test_Account_WhenALaterStatementDidNotReadWhole_ItIsTheReadingAndNotTheTransactions(
        self, world
    ):
        # Round two (decision 1): the reader refused the February document, so it is no known
        # balance, verifies nothing, and faults nothing. January's statement still adds up.
        _, today_verdict = read(world, "r-unsummed", rule=False)
        now, verdict = read(world, "r-unsummed", rule=True)

        assert today_verdict == NOTHING_TO_CHECK_AGAINST
        assert (verdict, now.own.through, now.own.statement_faults) == (
            ADDS_UP, D(2026, 1, 10), ()
        )


class TestTheFirstOfSeveralStatements:
    def test_Protection_WhenTheFirstStatementIsClean_IsOfferedExactlyWhatItWasOffered(
        self, world
    ):
        # Round four: a day tested ONLY by its statement's own listing is not offered to
        # protection, which records a balance by date span that a listing does not reach.
        today, _ = read(world, "r-first", rule=False)
        now, _ = read(world, "r-first", rule=True)
        opening = effective_opening(
            world, "r-first", world.transactions_for_account("r-first"), families=FAMILIES
        )

        assert today.own.through == now.own.through == D(2026, 3, 10)
        assert now.own.tested == (D(2026, 2, 10), D(2026, 3, 10))
        assert days_offered(opening, today) == days_offered(opening, now) == (D(2026, 3, 10),)

    def test_Protection_ForEveryAccountTheRuleDoesNotExplainADayFor_IsTheSameWithAndWithoutIt(
        self, world
    ):
        refs = sorted(
            str(row[0])
            for row in world.connection.execute("SELECT DISTINCT account_id FROM transactions")
        )
        compared = 0
        for ref in refs:
            today, _ = read(world, ref, rule=False)
            now, _ = read(world, ref, rule=True)
            if now.own.closed_before:
                continue
            opening = effective_opening(
                world, ref, world.transactions_for_account(ref), families=FAMILIES
            )
            assert days_offered(opening, today) == days_offered(opening, now), ref
            compared += 1
        # Every account the world builds, less those with a day a statement closed before.
        assert compared == 16

    def test_Protection_WhenOnlyItsOwnStatementTestsTheDay_SaysWhyNothingIsOfferedYet(
        self, world
    ):
        from obdi.verify.protection import protection_view

        now, _ = read(world, "r-lone", rule=True)
        opening = effective_opening(
            world, "r-lone", world.transactions_for_account("r-lone"), families=FAMILIES
        )
        view = protection_view(
            world, "r-lone", opening, world.transactions_for_account("r-lone"), now
        )

        assert view.offer == ()
        assert "tests by what it lists are not offered yet" in view.not_offered

    def test_Account_WhenAFeedHoldsAnUnlistedTransactionInTheFirstDays_NothingIsNewlyTested(
        self, world
    ):
        today, _ = read(world, "r-first-unlisted", rule=False)
        now, _ = read(world, "r-first-unlisted", rule=True)

        assert today.own.tested == now.own.tested == (D(2026, 3, 10),)


class TestLinkingStatements:
    def test_Link_WhenOpeningDiffersAndTheStatementsDoNotOverlap_TheGapIsProven(self, world):
        listing = {a.account: a for a in statement_listing_report(world, FAMILIES).accounts}

        assert [s.link for s in listing["r-gap-differs"].statements] == [
            Link.FIRST, Link.MEETS, Link.DIFFERS,
        ]
        assert [s.link for s in listing["r-gap-meets"].statements] == [
            Link.FIRST, Link.MEETS, Link.MEETS,
        ]

    def test_Account_WhenAMissingStatementLeavesAGap_DoesNotAddUpAsTodayAndMeetingAddsUp(
        self, world
    ):
        assert read(world, "r-gap-differs", rule=False)[1] == DOES_NOT_ADD_UP
        assert read(world, "r-gap-differs", rule=True)[1] == DOES_NOT_ADD_UP
        assert read(world, "r-gap-meets", rule=False)[1] == ADDS_UP
        assert read(world, "r-gap-meets", rule=True)[1] == ADDS_UP

    def test_Link_WhenALongerStatementListsWhatAShorterOneLists_TheyOverlapAndConcludeNothing(
        self, world
    ):
        listing = {a.account: a for a in statement_listing_report(world, FAMILIES).accounts}
        longer = listing["r-overlap"].statements[1]

        assert (longer.link, longer.shared) == (Link.OVERLAPS, 2)
        assert read(world, "r-overlap", rule=True)[1] == ADDS_UP


class TestAStatementClosedBeforeTheDaysLastTransaction:
    def test_Account_WhenTheTypedBalanceDiffersByExactlyTheUnlistedPurchase_NoConflictAndAddsUp(
        self, world
    ):
        today, today_verdict = read(world, "sd-explained", rule=False)
        now, verdict = read(world, "sd-explained", rule=True)

        assert (today.own.state, today_verdict) == (HELD_CONFLICT, DOES_NOT_ADD_UP)
        assert (now.own.state, verdict) == ("agrees", ADDS_UP)
        assert now.own.through == D(2026, 2, 10)
        assert [(c.day, c.transactions) for c in now.own.closed_before] == [(D(2026, 2, 10), 1)]
        assert shown_balances_are_stated_or_named(world, "sd-explained", FAMILIES, now) == 2

    def test_Account_WhenNothingExplainsTheDifference_StaysAConflict(self, world):
        assert read(world, "sd-unexplained", rule=True)[0].own.state == HELD_CONFLICT

    def test_Account_WhenTwoArePurchasedThatDayAndOnlyOneExplainsIt_StaysAConflict(self, world):
        assert read(world, "sd-two", rule=True)[0].own.state == HELD_CONFLICT

    def test_Account_WhenTheUnlistedPurchaseIsDatedTheNextDay_StaysTheConflictItIs(self, world):
        # Round three, decision 2: the typed balance for 10 Feb differs from the statement's by
        # exactly the purchase dated 11 Feb, and that is no hypothesis the rule takes (round
        # two took it and showed balances out by the purchase).
        today, today_verdict = read(world, "sd-nextday", rule=False)
        now, verdict = read(world, "sd-nextday", rule=True)

        assert (today.own.state, today_verdict) == (HELD_CONFLICT, DOES_NOT_ADD_UP)
        assert (now.own.state, verdict, now.own.closed_before) == (
            HELD_CONFLICT, DOES_NOT_ADD_UP, ()
        )

    def test_Account_WhenALaterTypedBalanceIsReproducedAfterTheExplainedDay_ChainContinues(
        self, world
    ):
        now, verdict = read(world, "sd-later-ok", rule=True)

        assert (verdict, now.own.through) == (ADDS_UP, D(2026, 3, 1))

    def test_Account_WhenALaterTypedBalanceIsNotReproduced_AddsUpOnlyThroughTheExplainedDay(
        self, world
    ):
        now, verdict = read(world, "sd-later-bad", rule=True)

        assert (verdict, now.own.through, now.own.state) == (
            DOES_NOT_ADD_UP, D(2026, 2, 10), HELD_UNMET,
        )
        assert now.own.held is not None and now.own.held.day == D(2026, 3, 1)


class TestAStatementClosingThatWasDisregarded:
    def test_Account_WhenTheOnlyStatementsClosingIsDisregarded_NothingIsVerifiedOrFaulted(
        self, tmp_path
    ):
        with Store(tmp_path / "d.sqlite3") as store:
            statement(
                store, tmp_path, "d-lone", D(2026, 1, 10), OPENING,
                [Spend(D(2026, 1, 5), "Bravo Shop", 519)],
                received=D(2026, 1, 10), previous_close=D(2025, 12, 10),
            )
            before = read(store, "d-lone", rule=True)[1]
            source = next(
                r.anchor.stating
                for r in effective_opening(store, "d-lone", families=FAMILIES).readings
                if r.anchor.basis == STATEMENT
            )
            disregard_balance(store, "d-lone", "2026-01-10", source, STATEMENT, families=FAMILIES)
            after = read(store, "d-lone", rule=True)

        assert before == ADDS_UP
        assert after[1] == NOTHING_TO_CHECK_AGAINST
        assert after[0].own.held is None
