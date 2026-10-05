"""The listing rule over the household `test_statement_listing_measure` builds: what the
measurement says it would conclude is what the rule concludes, and what it does not touch is
unmoved (`agreement`, R1 to R5).

Decided before the first run, from the household's own docstring:

  * the statements that fail are exactly `missing`, `merged`, and `history` (which lists a
    reversed and a void transaction), and each is a fault in the rule too. A statement whose
    lines were not found (`unkept`, `folded-lost`), that the reader refused (`unsummed`), or one
    of whose lines is held under another account (`folded`: only the store's own Space fold puts
    one there) is none of them: it cannot say;
  * a statement's days are verified by the measurement exactly where the rule tests them;
  * an account that adds up without the listing checks still adds up with them, unless one of
    its statements is a fault;
  * the statement blind to its Spaces (`blind-main`, `blind2-main`) is read as it was.

The lines for accounts that moved are printed by `-s`, for the report.
"""

from __future__ import annotations

import pytest

from listing_rule_reading import movement_of
from obdi.agreement import standing_of
from obdi.balance_anchors import effective_opening
from obdi.standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    AccountStanding,
    verification_of,
)
from obdi.statement_listing_measure import statement_checks
from test_statement_listing_measure import FAMILIES
from test_statement_listing_measure import world as listing_world  # noqa: F401 - the fixture

EXPECTED_FAULTS = {"missing", "merged", "history"}


def verdicts(store, ref: str):
    rows = store.transactions_for_account(ref)
    opening = effective_opening(store, ref, rows, families=FAMILIES)
    checks = statement_checks(store, FAMILIES, {ref: opening}).get(ref)
    members = [ref, *FAMILIES.spaces_of(ref)]
    today = standing_of(opening, members, movement_of(store))
    now = standing_of(opening, members, movement_of(store), checks)
    return (
        checks,
        verification_of(AccountStanding(today, None, False)),
        verification_of(AccountStanding(now, None, False)),
        now,
    )


@pytest.fixture(scope="module")
def read(listing_world):  # noqa: F811
    store, accounts, _ = listing_world
    return store, accounts, {ref: verdicts(store, ref) for ref in accounts}


class TestTheMeasurementAndTheRuleAgree:
    def test_Faults_WhenAStatementFailsItsListing_AreExactlyTheOnesTheRuleReports(self, read):
        _, accounts, found = read

        measured = {ref for ref, a in accounts.items() if a.failing}
        ruled = {
            ref
            for ref, (checks, _, _, _) in found.items()
            if checks is not None and any(s.fault for s in checks.statements)
        }
        assert measured == ruled == EXPECTED_FAULTS

    def test_Days_WhenTheMeasurementVerifiesAStatementsDays_TheRuleTestsThem(self, read):
        _, accounts, found = read

        for ref, listing in accounts.items():
            checks = found[ref][0]
            if checks is None:
                continue
            measured = [s.stretch is not None for s in listing.statements]
            ruled = [c.days_tested for c in checks.statements]
            assert measured == ruled, ref

    def test_Accounts_WhenTheyAddedUpBefore_StillDoUnlessAStatementIsAFault(self, read, capsys):
        _, _, found = read
        moved = {}
        for ref, (checks, before, after, _) in found.items():
            if before != after:
                moved[ref] = (before, after)
            if before == ADDS_UP:
                faulted = checks is not None and any(s.fault for s in checks.statements)
                assert after == ADDS_UP or faulted, ref
        with capsys.disabled():
            print("\nmoved by the listing rule:", dict(sorted(moved.items())))
        assert {ref for ref, (_, after) in moved.items() if after == DOES_NOT_ADD_UP} <= (
            EXPECTED_FAULTS
        )

    def test_BlindStatements_WhenTheStatementCannotSeeTheSpaces_AreReadAsToday(self, read):
        _, _, found = read

        for ref in ("blind-main", "blind2-main"):
            _, before, after, now = found[ref]
            assert before == after, ref
            assert now.own.held is None or now.own.held.kind != "statement", ref

    def test_NoStatementLinesFound_WhenTheyWereNotFound_NothingIsVerifiedOrFaulted(self, read):
        _, _, found = read

        for ref in ("unkept", "folded-lost"):
            _, before, after, _ = found[ref]
            assert (before, after) == (NOTHING_TO_CHECK_AGAINST, NOTHING_TO_CHECK_AGAINST), ref
