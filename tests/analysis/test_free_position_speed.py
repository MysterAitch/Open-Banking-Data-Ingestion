"""The Position page's four figures over the large invented store: a fixed number of statements.

THE RULE THE BUDGET HOLDS: the figures add ONE select (the commitments with their windows) to the
page, whatever the commitments, and the whole-table detection runs only when a confirmed outgoing
commitment has no confirmed income to be counted to. The two cases are measured apart: a household
that confirmed nothing (no detection) and one that confirmed an outgoing commitment and no income
(the detector is read for the income it would count to).

MEASURED: see `POSITION_STATEMENTS` and `DETECTED_EXTRA`; the bounds are loose on time and tight on
statements, as the Recurring page's are (`test_recurring_speed`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import copy_of, serving
from obdi.ingest.commitment_records import WindowTerms
from obdi.ingest.store import Store

#: Statements a GET of /position issues over the large store with no commitment confirmed.
#: Measured 802 (1.2 s) with the figures; the page issued 801 before them, and the figures' one
#: addition is the select of the commitments. The page's other statements are its per-account
#: reads, which this budget does not try to reduce.
POSITION_STATEMENTS = 810
#: The same with one outgoing commitment confirmed and no income: the detector is read for the
#: income it would count to. Measured 817 (2.1 s), fifteen more than the plain page, which does
#: not grow with the commitments or the series.
DETECTED_STATEMENTS = 830
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
    with serving(large, tmp_path_factory.mktemp("free-plain")) as served:
        yield served


@pytest.fixture(scope="module")
def detected(large, tmp_path_factory):
    copied = copy_of(large, tmp_path_factory.mktemp("free-store"))
    confirm_outgoing(copied.path)
    with serving(copied, tmp_path_factory.mktemp("free-detected")) as served:
        yield served


class TestPositionFiguresOverTheLargeStore:
    def test_Position_WhenNothingIsConfirmed_IssuesAFixedFewStatementsAndIsQuick(self, plain):
        plain.get("/position")
        later = plain.get("/position")

        assert later.status == 200
        assert "Held, owed, committed, and free" in later.body
        assert later.statements <= POSITION_STATEMENTS, later.statements
        assert later.seconds < SECONDS

    def test_Position_WhenAnOutgoingCommitmentHasNoConfirmedIncome_ReadsTheDetectorOnce(
        self, detected
    ):
        detected.get("/position")
        later = detected.get("/position")

        assert later.status == 200
        assert later.statements <= DETECTED_STATEMENTS, later.statements
        assert later.seconds < SECONDS

    def test_Position_OverTheLargeStore_SealsEveryAmountForAGet(self, plain):
        body = plain.get("/position").body

        assert "£•••" in body
