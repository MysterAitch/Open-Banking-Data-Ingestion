"""The measurement tells a placement fault from a real hole, in counts and yes or no.

TWO ACCOUNTS, every answer decided before the first run. Each holds Santander statements built
through the doors a person's files use (see `statement_span_world`), every figure invented.

  early   April (prints its start after 2026-03-10) lists 03-20 10.00 and 04-09 7.00 and closes on
          04-10 at 117.00 owed; May (prints its start after 04-10) lists 04-08 3.00 and 05-02 2.00.
          May's first row is dated before the start it prints, so its opening is placed on the
          day before that row, 04-07, although it states the balance AT April's close. Nothing is
          missing.
            May's opening is not reproduced from the opening of April (03-10): 1 transaction lies
            after 03-10 up to 04-07 (April's 03-20). The difference is the size of the 17.00 April
            lists, which closes after 04-07 (previous_listed_after: yes, since a listed
            transaction counts on its statement's closing day); May lists nothing dated on or
            before 04-07 (own_listed_before: no); the transactions dated 04-07 or 04-08 are May's
            3.00 alone (off_by_a_day: no, 3.00 is not 7.00). May follows April directly (printed
            start meets the close), and as a second statement of April's closing it is
            reproduced: the figures are equal.
  hole    March (closes 03-10), then May printing a start after 04-10, April missing, whose 7.00
          is in May's opening. May's opening is placed on 04-10 by its printed start; 0
          transactions lie between 03-10 and 04-10. No set of transactions explains the
          difference (all three: no). May does not follow March directly (a hole lies between),
          and it is not reproduced as a second statement of anything.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest.family_anchors import Families
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.statement_opening_measure import OpeningDiagnosis, statement_opening_report
from statement_span_world import Spend, statement

D = date
NO_SPACES = Families({}, {}, {})


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    root = tmp_path_factory.mktemp("diagnosis")
    with Store(root / "store.sqlite3") as store:
        april = statement(
            store, root, "early", D(2026, 4, 10), 10000,
            [Spend(D(2026, 3, 20), "Aaa Shop", 1000), Spend(D(2026, 4, 9), "Bbb Shop", 700)],
            received=D(2026, 4, 10), previous_close=D(2026, 3, 10),
        )
        statement(
            store, root, "early", D(2026, 5, 10), april,
            [Spend(D(2026, 4, 8), "Ccc Shop", 300), Spend(D(2026, 5, 2), "Ddd Shop", 200)],
            received=D(2026, 5, 10), previous_close=D(2026, 4, 10),
        )
        march = statement(
            store, root, "hole", D(2026, 3, 10), 10000, [Spend(D(2026, 3, 4), "Eee Shop", 500)],
            received=D(2026, 3, 10), previous_close=D(2026, 2, 10),
        )
        april_hole = march + 700
        statement(
            store, root, "hole", D(2026, 5, 10), april_hole,
            [Spend(D(2026, 5, 2), "Fff Shop", 200)],
            received=D(2026, 5, 10), previous_close=D(2026, 4, 10),
        )
        keep_statement_readings(store)
        store.connection.commit()
        yield {f.account: f for f in statement_opening_report(store, NO_SPACES).accounts}


def diagnosis(report, ref: str) -> OpeningDiagnosis:
    (found,) = report[ref].diagnoses
    return found


class TestAPlacementFault:
    def test_Opening_WhenAStatementListsARowBeforeItsPrintedStart_IsExplainedByThePreviousStatement(
        self, report
    ):
        found = diagnosis(report, "early")

        assert found.placement.closing == D(2026, 5, 10)
        assert found.placement.day == D(2026, 4, 7)
        assert found.between == 1, "April's 03-20 purchase"
        assert found.previous_listed_after is True
        assert found.own_listed_before is False
        assert found.off_by_a_day is False

    def test_Opening_WhenTheStatementFollowsDirectly_IsReproducedAsASecondStatementOfTheClose(
        self, report
    ):
        found = diagnosis(report, "early")

        assert found.follows_directly is True
        assert found.reproduced_if_second_statement_of_previous is True


class TestARealHole:
    def test_Opening_AfterAMissingStatement_IsExplainedByNoSetOfTransactions(self, report):
        found = diagnosis(report, "hole")

        assert found.placement.day == D(2026, 4, 10)
        assert found.between == 0
        assert (found.own_listed_before, found.previous_listed_after, found.off_by_a_day) == (
            False,
            False,
            False,
        )

    def test_Opening_AfterAMissingStatement_DoesNotFollowDirectlyAndIsNotReproduced(self, report):
        found = diagnosis(report, "hole")

        assert found.follows_directly is False
        assert found.reproduced_if_second_statement_of_previous is False


class TestTheReportNamesNoFigure:
    def test_Sentences_ForBothAccounts_CarryCountsAndYesOrNoOnly(self, report):
        text = "\n".join(s for f in report.values() for s in f.sentences())

        assert "what the statement before it lists, which closes after its day: yes" in text
        for figure in ("17.00", "1700", "117.00", "11700", "100.00", "10000", "3.00"):
            assert figure not in text
