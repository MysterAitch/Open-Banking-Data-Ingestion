"""The listing rule (`agreement`, R1 to R5) against inputs built to make each claim false.

A review by construction: every account below is invented, built through the doors a person's
files use, and its answer was written here before the first run. EVERY TEST IN THIS FILE FAILED
when it was written, against the rule as it stood at 6e8b707: each is a finding, and the answer
the rule gave is beside the answer expected.

Each account is read AS THE RUNNING APP READS IT: the whole store's movement report is laid on
the standing beside the statements' own listings (`standings_for`, the account page). Read
without it, as `test_statement_listing_rule_accounts` reads, `v-cancel` and `v-twice-uploaded`
say "adds up" outright; with it a movement fault holds both, so what is left of those two is the
statement's own check passing and the sentences that follow from it.

Each card account opens at 100.00 owed (Santander's layout) and its payees are its own, so that
no two documents share their bytes.

  v-cancel            A lone statement lists two purchases (12.37, 5.19). The store holds ONE
                      transaction: the second is not held, and the first is held 5.19 larger.
                      The sum of what is held still reaches the closing balance.
                      Expected: the statement does not "add up by what it lists" (R1 asks that
                      every listed transaction is held as listed).
                      Got: adds up, its days tested; the account page says "1 of 1 statement adds
                      up by what it lists" and that the known balance is "tested by the 2
                      transactions its statement lists" beside a movement fault for the same day.
  v-twice-uploaded    The same statement held as two documents whose payee text differs, so the
                      two readings did not merge: the store holds every purchase twice.
                      Expected: neither is said to add up with its days tested; they overlap (R4)
                      and nothing is concluded.
                      Got: both add up, days tested ("2 of 2 statements add up by what they
                      list").
  v-nextday-subset    A statement closing 10 Feb; a typed balance for 10 Feb 7.77 away; a feed
                      holds a purchase of 7.77 dated 11 Feb AND one of 1.23 dated 10 Feb, neither
                      listed. Taking the next day's purchase and leaving the same day's is a
                      subset fitted to the difference (R2: all or nothing).
                      Expected: still a conflict, no "closed before" claim.
                      Got: "taken to have closed before" 1 transaction dated the day after; the
                      conflict is gone (the account is held as unmet instead).
  v-cannot-say        A statement whose reading was never kept (cannot say what it lists), a typed
                      balance for its closing day 7.77 away, a feed purchase of 7.77 that day, and
                      a typed balance on 1 Mar the transactions reproduce.
                      Expected: "cannot say" verifies nothing, so the day stays the conflict it
                      is without the rule.
                      Got: adds up through 1 Mar, 1 Mar offered to protection.
  v-misread           A COMPLETE, CORRECT account: a feed holds its two transactions (a purchase
                      and a monthly fee), and typed balances either side of them are true. Its
                      statement prints the fee without a date, which the reader does not take as
                      a transaction, so the import refuses the document and keeps the file.
                      Expected: adds up through 1 Mar, as without the rule; and the person can
                      set the statement aside. Got: does not add up, adding up through nothing,
                      and the statement cannot be disregarded (it is no known balance).
  v-masked-earlier    A kept statement that does not read whole (10 Feb), and an earlier typed
                      balance the transactions do not reproduce (15 Jan).
  v-masked-conflict   The same statement fault, and an earlier statement whose closing day
                      (10 Jan) another source states a different balance for, unexplained.
                      Expected for both (R3): Today raises `statement-fault` for the account, and
                      the checks index does not say the statements are in order.
                      Got: no such item; `/period-reconciliation` says "No statement fails to add
                      up by the transactions it lists."
  v-disregarded-first Statements closing 10 Jan and 10 Feb, both adding up; a feed holds a
                      purchase in November that no statement lists; the person disregards the
                      10 Jan closing. Expected: as `r-lone-earlier`, nothing is offered to
                      protection, whose span runs from the first held transaction.
                      Got: 10 Feb offered.
  folded              (the measurement's own household) A statement lists three purchases; one is
                      held as history, folded into a transaction of ANOTHER account.
                      Expected: the account's own two counting transactions do not carry the
                      statement's opening balance to its closing, so its own reading does not say
                      "adds up". Got: adds up, "tested by the 3 transactions its statement lists",
                      10 Feb offered to protection.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from obdi.agreement import HELD_CONFLICT, Standing, standing_of
from obdi.balance_anchors import (
    STATEMENT,
    AnchorRefused,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
)
from obdi.checks_index import CHECKS, IN_ORDER, result_of
from obdi.identity import content_key
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.movement_completeness import MovementCompleteness, movement_completeness
from obdi.overview import Overview, standing_items_from
from obdi.protection import tested_days as days_offered
from obdi.standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    AccountStanding,
    standings_for,
    verification_of,
)
from obdi.statement_checks import StatementChecks
from obdi.statement_listing_measure import statement_checks
from obdi.statement_terms import keep_statement_readings
from obdi.store import Store
from statement_span_world import Spend, feed, statement
from test_statement_listing_measure import (
    FAMILIES,
    OPENING,
    chain,
    landed_only,
    santander_lines,
)
from test_statement_listing_measure import world as listing_world  # noqa: F401 - the fixture

D = date
OUR_DAY = D(2026, 9, 1)
MID, JAN, FEB = D(2025, 12, 10), D(2026, 1, 10), D(2026, 2, 10)


def _typed_balance(store: Store, ref: str, day: date, minor: int) -> None:
    record_stated_anchor(store, ref, day.isoformat(), f"{minor / 100:.2f}", today=OUR_DAY)


def _forget(store: Store, ref: str, payee: str) -> None:
    """Take a transaction out of the store, sightings and all."""
    store.connection.execute(
        "DELETE FROM transaction_sources WHERE entity_id IN (SELECT entity_id FROM transactions "
        "WHERE account_id = ? AND description = ?)",
        (ref, payee),
    )
    store.connection.execute(
        "DELETE FROM transactions WHERE account_id = ? AND description = ?", (ref, payee)
    )


def _fee(store: Store, ref: str, day: date, text: str, minor: int) -> None:
    """One feed transaction whose text is not a payee the statement's reader would produce."""
    reconcile_batch(
        store,
        [
            Transaction(
                account_id=ref,
                amount_minor=-minor,
                currency="GBP",
                value_date=day,
                booking_date=day,
                description=text,
                source="truelayer-booked",
                source_id=f"{ref}-fee",
                tier=SourceTier.AUTHORITATIVE,
                content_key=content_key(amount_minor=-minor, value_date=day, description=text),
            )
        ],
        digest=f"{ref}-fee",
    )


def _unread_statement(store: Store, root: Path, ref: str, payee: str) -> None:
    """A statement closing 10 Feb that prints a purchase of 5.23 and an UNDATED fee of 1.00, which
    the reader does not take as a transaction: its opening and the one amount it reads do not
    reach its closing balance. The document is kept and nothing is read from it into the account,
    as the import door leaves a statement it refuses."""
    landed_only(
        store, root, ref, f"{ref}-feb",
        [
            *santander_lines(
                FEB, OPENING, [Spend(D(2026, 1, 20), payee, 523)], OPENING + 623, JAN
            )[:-1],
            "Monthly account fee                                      1.00",
            f"Your new balance:                                        {(OPENING + 623) / 100:.2f}",
        ],
        FEB,
    )


def build(store: Store, root: Path) -> None:
    statement(
        store, root, "v-cancel", JAN, OPENING,
        [Spend(D(2025, 12, 20), "Alpha Cancel", 1237), Spend(D(2026, 1, 5), "Bravo Cancel", 519)],
        received=JAN, previous_close=MID,
    )
    _forget(store, "v-cancel", "Bravo Cancel")
    store.connection.execute(
        "UPDATE transactions SET amount_minor = amount_minor - 519 "
        "WHERE account_id = 'v-cancel' AND description = 'Alpha Cancel'"
    )

    statement(
        store, root, "v-twice-uploaded", JAN, OPENING,
        [Spend(D(2025, 12, 20), "Alpha Twice", 1237), Spend(D(2026, 1, 5), "Bravo Twice", 519)],
        received=JAN, previous_close=MID,
    )
    again = root / "again"
    again.mkdir()
    statement(
        store, again, "v-twice-uploaded", JAN, OPENING,
        [
            Spend(D(2025, 12, 20), "ALPHA TWICE STORES", 1237),
            Spend(D(2026, 1, 5), "BRAVO TWICE STORES", 519),
        ],
        received=JAN, previous_close=MID,
    )

    closing = statement(
        store, root, "v-nextday-subset", FEB, OPENING,
        [Spend(D(2026, 1, 20), "Quiet Subset", 717), Spend(D(2026, 2, 5), "Calm Subset", 331)],
        received=FEB, previous_close=JAN,
    )
    feed(
        store, "v-nextday-subset",
        [Spend(D(2026, 2, 11), "Next Subset", 777), Spend(FEB, "Same Subset", 123)],
        digest="subset",
    )
    _typed_balance(store, "v-nextday-subset", FEB, -closing - 777)

    _unread_statement(store, root, "v-misread", "Fennel Misread")
    feed(store, "v-misread", [Spend(D(2026, 1, 20), "Fennel Misread", 523)], digest="misread")
    _fee(store, "v-misread", D(2026, 2, 9), "Monthly account fee", 100)
    _typed_balance(store, "v-misread", D(2026, 1, 1), -OPENING)
    _typed_balance(store, "v-misread", D(2026, 3, 1), -OPENING - 623)

    _unread_statement(store, root, "v-masked-earlier", "Fennel Earlier")
    feed(store, "v-masked-earlier", [Spend(D(2026, 1, 20), "Fennel Earlier", 523)], digest="me")
    _typed_balance(store, "v-masked-earlier", D(2026, 1, 1), -OPENING)
    _typed_balance(store, "v-masked-earlier", D(2026, 1, 15), -OPENING - 3)

    closing = statement(
        store, root, "v-masked-conflict", JAN, OPENING,
        [Spend(D(2026, 1, 5), "Ash Conflict", 419)], received=JAN, previous_close=MID,
    )
    _typed_balance(store, "v-masked-conflict", JAN, -closing - 500)
    _unread_statement(store, root, "v-masked-conflict", "Fennel Conflict")

    feed(
        store, "v-disregarded-first", [Spend(D(2025, 11, 20), "Early First", 413)], digest="early"
    )
    chain(
        store, root, "v-disregarded-first", [JAN, FEB],
        [[Spend(D(2026, 1, 5), "One First", 419)], [Spend(D(2026, 1, 20), "Two First", 717)]],
    )
    source = next(
        r.anchor.stating
        for r in effective_opening(store, "v-disregarded-first", families=FAMILIES).readings
        if r.anchor.basis == STATEMENT
    )
    disregard_balance(
        store, "v-disregarded-first", JAN.isoformat(), source, STATEMENT, families=FAMILIES
    )
    store.connection.commit()
    keep_statement_readings(store)
    store.connection.commit()

    # Built last: every later import keeps the readings of the documents still without one.
    closing = statement(
        store, root, "v-cannot-say", FEB, OPENING,
        [Spend(D(2026, 1, 20), "Quiet Unkept", 717), Spend(D(2026, 2, 5), "Calm Unkept", 331)],
        received=FEB, previous_close=JAN,
    )
    feed(store, "v-cannot-say", [Spend(FEB, "Late Unkept", 777)], digest="unkept")
    _typed_balance(store, "v-cannot-say", FEB, -closing - 777)
    _typed_balance(store, "v-cannot-say", D(2026, 3, 1), -closing - 777)
    store.connection.execute(
        "DELETE FROM statement_readings WHERE digest IN "
        "(SELECT digest FROM raw_artefacts WHERE account_ref = 'v-cannot-say')"
    )
    store.connection.commit()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("review")
    with Store(root / "store.sqlite3") as store:
        build(store, root)
        yield store, movement_completeness(store, lambda ref: ref)


def checks_of(store: Store, ref: str) -> StatementChecks:
    opening = effective_opening(store, ref, families=FAMILIES)
    return statement_checks(store, FAMILIES, {ref: opening})[ref]


def read(
    store: Store, movement: MovementCompleteness, ref: str, *, rule: bool
) -> tuple[Standing, str]:
    """The account's standing and verdict as the account page reaches them: the movement report
    laid on, and the statements' own listings where `rule`."""
    rows = store.transactions_for_account(ref)
    opening = effective_opening(store, ref, rows, families=FAMILIES)
    checks = statement_checks(store, FAMILIES, {ref: opening}).get(ref) if rule else None
    standing = standing_of(opening, [ref], movement, checks)
    return standing, verification_of(AccountStanding(standing, None, False))


def offered(store: Store, ref: str, standing: Standing) -> tuple[date, ...]:
    return days_offered(effective_opening(store, ref, families=FAMILIES), standing)


class TestAStatementThatSumsOnlyBecauseTwoErrorsCancel:
    def test_Statement_WhenOneListedTransactionIsNotHeldAndAnotherIsHeldLargerBySoMuch_DoesNotAddUp(
        self, world
    ):
        store, _ = world
        held = [t.amount_minor for t in store.transactions_for_account("v-cancel")]
        (check,) = checks_of(store, "v-cancel").statements

        # The statement lists 12.37 and 5.19; the store holds one transaction, of 17.56.
        assert held == [-1756]
        assert check.adds_up is not True
        assert check.days_tested is False


class TestTheSameStatementHeldAsTwoDocumentsThatDidNotMerge:
    def test_Statements_WhenEveryListedTransactionIsHeldTwice_NeitherTestsItsDays(self, world):
        store, _ = world
        held = sorted(t.amount_minor for t in store.transactions_for_account("v-twice-uploaded"))
        found = checks_of(store, "v-twice-uploaded").statements

        # One statement's two purchases, each held under both documents' payee text.
        assert held == [-1237, -1237, -519, -519]
        assert [c.adds_up is True and c.days_tested for c in found] == [False, False]


class TestAStatementClosedBeforeTheNextDaysTransactionOnly:
    def test_Account_WhenTheSameDayAlsoHoldsAnUnlistedTransaction_StaysAConflict(self, world):
        now, verdict = read(*world, "v-nextday-subset", rule=True)

        assert verdict == DOES_NOT_ADD_UP
        assert now.own.closed_before == ()
        assert now.own.state == HELD_CONFLICT


class TestAStatementThatCannotSayWhatItLists:
    def test_Account_WhenItsClosingDisagreesWithAnotherSource_StaysTheConflictItIsWithoutTheRule(
        self, world
    ):
        store, _ = world
        today, today_verdict = read(*world, "v-cannot-say", rule=False)
        now, verdict = read(*world, "v-cannot-say", rule=True)

        assert [c.adds_up for c in checks_of(store, "v-cannot-say").statements] == [None]
        assert (today.own.state, today_verdict) == (HELD_CONFLICT, DOES_NOT_ADD_UP)
        assert (now.own.state, verdict) == (HELD_CONFLICT, DOES_NOT_ADD_UP)
        assert offered(store, "v-cannot-say", now) == ()


class TestACompleteAccountWhoseStatementTheReaderDidNotReadWhole:
    def test_Account_WhenItsTransactionsReproduceTheBalancesEitherSide_StillAddsUp(self, world):
        today, today_verdict = read(*world, "v-misread", rule=False)
        now, verdict = read(*world, "v-misread", rule=True)

        # Ground truth: 100.00 owed on 1 Jan, a purchase of 5.23 and a fee of 1.00, 106.23 owed
        # on 1 Mar. Every transaction is held once.
        assert (today_verdict, today.own.through) == (ADDS_UP, D(2026, 3, 1))
        assert (verdict, now.own.through) == (ADDS_UP, D(2026, 3, 1))

    def test_Statement_WhenItDoesNotReadWholeAndIsAFault_CanBeSetAsideByThePerson(self, world):
        store, _ = world
        before = read(*world, "v-misread", rule=False)[1]
        try:
            disregard_balance(
                store, "v-misread", FEB.isoformat(), "santander-cc-pdf", STATEMENT,
                families=FAMILIES,
            )
        except AnchorRefused as refused:
            pytest.fail(
                "a statement that is a fault and is no known balance cannot be disregarded, so "
                f"nothing but removing the document clears it: {refused}"
            )

        assert read(*world, "v-misread", rule=True)[1] == before


class TestAStatementFaultBehindAnotherHold:
    @pytest.mark.parametrize("ref", ["v-masked-earlier", "v-masked-conflict"])
    def test_Today_WhenAnotherHoldBeginsBeforeTheStatementsClosing_StillRaisesTheStatementFault(
        self, world, ref
    ):
        store, movement = world
        standings = standings_for(store, [ref], families=FAMILIES, movement=movement)
        items = standing_items_from(standings, lambda r: r, lambda r: False, D(2026, 3, 1))
        row = result_of(
            next(spec for spec in CHECKS if spec.route == "/period-reconciliation"),
            Overview(datetime(2026, 3, 1, 12, tzinfo=UTC), 1, 1, tuple(items), ()),
        )

        # The statement's lines were found and do not sum: the measurement says so.
        assert "not-read-whole" in [s.fault for s in checks_of(store, ref).statements]
        assert [i.accounts for i in items if i.kind == "statement-fault"] == [(ref,)]
        assert row.state != IN_ORDER


class TestTheEarliestKnownBalanceIsNotTheFirstStatementHeld:
    def test_Protection_WhenAnUnlistedTransactionPrecedesADisregardedFirstStatement_IsOfferedNoDay(
        self, world
    ):
        store, _ = world
        today, _ = read(*world, "v-disregarded-first", rule=False)
        now, _ = read(*world, "v-disregarded-first", rule=True)
        first_held = min(
            t.value_date for t in store.transactions_for_account("v-disregarded-first")
        )

        # Protection spans from the first held transaction: the November purchase nothing lists.
        assert first_held == D(2025, 11, 20)
        assert offered(store, "v-disregarded-first", today) == ()
        assert offered(store, "v-disregarded-first", now) == ()


class TestAListedTransactionFoldedIntoAnotherAccounts:
    def test_Account_WhenItsOwnTransactionsDoNotReachTheClosing_ItsOwnReadingDoesNotSayAddsUp(
        self, listing_world  # noqa: F811
    ):
        store = listing_world[0]
        movement = movement_completeness(store, lambda ref: ref)
        own = sum(
            t.amount_minor
            for t in store.transactions_for_account("folded")
            if not t.status.is_history
        )
        now, verdict = read(store, movement, "folded", rule=True)

        # The statement: 100.00 owed, three purchases (7.17, 3.31, 4.57), 115.05 owed. The
        # account's own counting transactions are two of them.
        assert own == -(331 + 457)
        assert -OPENING + own != -(OPENING + 717 + 331 + 457)
        assert verdict != ADDS_UP
        assert now.own.listing_tested == ()
