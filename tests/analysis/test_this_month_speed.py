"""The This month page, and Today's line about it, over the large invented store: a fixed number of
statements.

THE RULE THE BUDGET HOLDS: the page reads the home page's held position (`home_position`), the
commitments (one select), and, only when a commitment is confirmed, the detector once for the
payments each was met by. A household that has confirmed nothing pays one select more than the
position. Today's line adds the same and no more, since both read one held value (`month_memo`).

MEASURED: see the constants; the bounds are loose on time and tight on statements, as the
Recurring page's are (`test_recurring_speed`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import copy_of, serving
from obdi.ingest.commitment_records import WindowTerms
from obdi.ingest.store import Store

#: Statements a first GET of /this-month issues over the large store with no commitment
#: confirmed: the held position is read, and nothing else is. Measured 824 (1.2 s), nearly all of
#: it the position's per-account reads, which this budget does not try to reduce.
PLAIN_FIRST_STATEMENTS = 835
#: The same GET a second time, the position held: the memo's key (the standings' key and the
#: commitments) and no more. Measured 14.
PLAIN_HELD_STATEMENTS = 20
#: A first GET with one outgoing commitment confirmed: the position, the commitments, and the
#: detector once. Measured 839 (1.4 s), fifteen more than the plain page, which does not grow
#: with the commitments or the series.
DETECTED_FIRST_STATEMENTS = 850
#: The same GET again, everything held. Measured 14; the month ahead is the same 14, since it is
#: built from the held inputs.
DETECTED_HELD_STATEMENTS = 20
#: Statements Today gains once a commitment is confirmed and the month is held: the memo's key
#: and the commitments' select. Measured 14 (57 before, 71 after).
TODAY_HELD_EXTRA_STATEMENTS = 20
SECONDS = 60.0


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


def confirm_outgoing(path) -> None:
    with Store(path) as store:
        ref = str(
            store.connection.execute(
                "SELECT account_id FROM transactions GROUP BY account_id "
                "ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()[0]
        )
        store.declare_commitment(
            "Invented Standing Charge",
            kind="pulled",
            account=ref,
            direction="out",
            entity_id=None,
            name_key="invented standing charge",
            from_day=datetime.now(UTC).date() - timedelta(days=300),
            to_day=None,
            terms=WindowTerms(1500, "GBP", "monthly", 12, 0, 4, "invented for a test"),
        )


@pytest.fixture(scope="module")
def plain(large, tmp_path_factory):
    with serving(large, tmp_path_factory.mktemp("month-plain")) as served:
        yield served


@pytest.fixture(scope="module")
def detected(large, tmp_path_factory):
    copied = copy_of(large, tmp_path_factory.mktemp("month-store"))
    confirm_outgoing(copied.path)
    with serving(copied, tmp_path_factory.mktemp("month-detected")) as served:
        yield served


class TestThisMonthOverTheLargeStore:
    def test_ThisMonth_WhenNothingIsConfirmed_IssuesAFixedFewStatementsAndIsQuick(self, plain):
        first = plain.get("/this-month")
        later = plain.get("/this-month")

        assert first.status == 200
        assert first.statements <= PLAIN_FIRST_STATEMENTS, first.statements
        assert later.statements <= PLAIN_HELD_STATEMENTS, later.statements
        assert first.seconds < SECONDS

    def test_ThisMonth_WhenACommitmentIsConfirmed_ReadsTheDetectorOnceAndHoldsTheResult(
        self, detected
    ):
        first = detected.get("/this-month")
        later = detected.get("/this-month")

        assert first.status == 200
        assert first.statements <= DETECTED_FIRST_STATEMENTS, first.statements
        assert later.statements <= DETECTED_HELD_STATEMENTS, later.statements
        assert first.seconds < SECONDS

    def test_ThisMonth_MonthAhead_IsAsCheapAsTheMonthOnceHeld(self, detected):
        detected.get("/this-month")
        ahead = detected.get("/this-month?month=next")

        assert ahead.status == 200
        assert ahead.statements <= DETECTED_HELD_STATEMENTS, ahead.statements

    def test_Today_WhenACommitmentIsConfirmed_AddsFewStatementsOnceTheMonthIsHeld(
        self, large, tmp_path_factory
    ):
        copied = copy_of(large, tmp_path_factory.mktemp("month-today"))
        with serving(copied, tmp_path_factory.mktemp("month-today-pages")) as served:
            served.get("/")
            before = served.get("/")
            confirm_outgoing(copied.path)
            served.get("/")
            after = served.get("/")

        assert after.status == 200
        extra = after.statements - before.statements
        assert extra <= TODAY_HELD_EXTRA_STATEMENTS, (before.statements, after.statements)

    def test_ThisMonth_OverTheLargeStore_SealsEveryAmountForAGet(self, detected):
        body = detected.get("/this-month").body

        assert "£•••" in body
        assert "15.00" not in body
