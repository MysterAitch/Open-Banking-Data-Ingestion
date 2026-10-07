"""What treating a statement's opening balance as a known balance would change, measured first.

THE HOUSEHOLD. Santander statements built through the doors a person's files use (see
`statement_span_world`), every figure invented. Each statement lists one spend; amounts are owed,
in pounds. "dated" statements print the day before their start (so their opening is placed on the
previous statement's closing day); "undated" ones print none (so it is placed on the day before the
first listed row). Every answer below was decided here before the first run.

  five      dated; closing 01-10 to 05-10 (S1 opens 2025-12-10), one spend each on 01-04, 02-03,
            03-04, 04-03, 05-05, all held.
              5 held, 5 state an opening, 5 placed, all dated by the statement; 4 redundant (each
              opens on the day the one before closed, on the same figure), 1 new (the first, with
              no known balance before it); 0 conflicting.
              Today: known 01-10 to 05-10, in agreement through 05-10. Rule: in agreement from
              2025-12-10 to 05-10. Newly in agreement: 2025-12-10 to 01-10. Newly not: none.
              1 row (01-04) is tested that is not today. The opening figure does not change.
  april     dated; closings 01-10, 02-10, 03-10, 05-10, 06-10: APRIL IS MISSING, and its 7.00 of
            movement is in May's opening.
              5 held and placed; redundant 3 (Feb, Mar, Jun), new 2 (Jan the first, and May on
              04-10, where no known balance is); of the new, 1 has nothing before it and 1 would
              NOT be reproduced, from 03-10 to 04-10 (the April rows are not held).
              Today: in agreement through 03-10, held back by the 05-10 balance. Rule: in
              agreement from 2025-12-10 to 03-10 and from 04-10 to 06-10; not proven 03-10 to
              04-10. Newly in agreement: 2025-12-10 to 01-10 and 04-10 to 06-10. Newly not: none.
  may-row   dated; closings 01-10 to 06-10 held as documents only (no rows read from them), the
            bank's feed holding every spend EXCEPT the 12.00 on 05-05, in the middle of May.
              6 held and placed; 5 redundant, 1 new; today: in agreement through 04-10, held back
              at 05-10. Rule: in agreement from 2025-12-10 to 04-10 and from 05-10 to 06-10; not
              proven 04-10 to 05-10 (the failure is pinned to May). Newly in agreement:
              2025-12-10 to 01-10 and 05-10 to 06-10. 1 row tested that is not today.
  single    dated; one statement closing 09-10 (opens 08-10), one spend on 09-02.
              1 held, placed, new, with nothing before it. Today: a lone known balance, no
              agreement date. Rule: in agreement from 08-10 to 09-10, which is all of it newly.
  undated   closings 07-10, 08-10, 09-10 printing no start, spends on 07-05, 08-05, 09-05.
              3 placed, none dated by the statement, all 3 on the day before the first listed row
              (07-04, 08-04, 09-04); redundant 0 (none is on a closing day); new 3, of which 2
              repeat the closing figure of the statement before; 1 has nothing before it, 2 are
              reproduced. Rule: in agreement from 07-04 to 09-10; newly 07-04 to 07-10.
  spaces    dated; three statements for an account with a Space the statements cannot see.
              3 state an opening, all blind; 0 placed in the account's own reading. (How many of
              the 3 the family's reading already holds was not decided first: see the test.)
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest.family_anchors import Families
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.statement_opening_measure import OpeningFigures, statement_opening_report
from obdi.statement_openings import PlacedBy
from statement_span_world import Spend, feed, statement

D = date

NO_SPACES = Families({}, {}, {})
WITH_A_SPACE = Families({"spaces-space": "spaces"}, {}, {})


def _chain(store, root, ref, closings, spends, *, start, received=True, import_rows=True):
    """Statements closing on `closings`, each opening on the one before it; returns each's close."""
    owed = start
    previous = None
    for index, closing in enumerate(closings):
        owed = statement(
            store,
            root,
            ref,
            closing,
            owed,
            spends[index],
            received=D(closing.year, closing.month, closing.day) if received else None,
            previous_close=previous,
            import_rows=import_rows,
        )
        previous = closing
    return owed


def build(store: Store, root) -> None:
    dec = D(2025, 12, 10)
    jan, feb, mar, apr, may, jun = (D(2026, m, 10) for m in range(1, 7))

    def chain_dated(ref, closings, spends, opening_from, **kw):
        owed = 10000
        previous = opening_from
        for index, closing in enumerate(closings):
            owed = statement(
                store, root, ref, closing, owed, spends[index], received=closing,
                previous_close=previous, **kw,
            )
            previous = closing

    chain_dated(
        "five",
        [jan, feb, mar, apr, may],
        [
            [Spend(D(2026, 1, 4), "Aaa Shop", 2000)],
            [Spend(D(2026, 2, 3), "Bbb Shop", 3000)],
            [Spend(D(2026, 3, 4), "Ccc Shop", 1500)],
            [Spend(D(2026, 4, 3), "Ddd Shop", 2500)],
            [Spend(D(2026, 5, 5), "Eee Shop", 1000)],
        ],
        dec,
    )

    s_jan = statement(store, root, "april", jan, 10000, [Spend(D(2026, 1, 4), "Aaa Shop", 2000)],
                      received=jan, previous_close=dec)
    s_feb = statement(store, root, "april", feb, s_jan, [Spend(D(2026, 2, 3), "Bbb Shop", 3000)],
                      received=feb, previous_close=jan)
    s_mar = statement(store, root, "april", mar, s_feb, [Spend(D(2026, 3, 4), "Ccc Shop", 1500)],
                      received=mar, previous_close=feb)
    s_apr = s_mar + 700  # April's movement, never landed
    s_may = statement(store, root, "april", may, s_apr, [Spend(D(2026, 5, 5), "Eee Shop", 1000)],
                      received=may, previous_close=apr)
    statement(store, root, "april", jun, s_may, [Spend(D(2026, 6, 4), "Fff Shop", 500)],
              received=jun, previous_close=may)

    held_only = {"import_rows": False}
    may_spends = [
        Spend(D(2026, 5, 2), "Eee Shop", 400),
        Spend(D(2026, 5, 5), "Mid Shop", 1200),
        Spend(D(2026, 5, 8), "Ggg Shop", 600),
    ]
    spends = [
        [Spend(D(2026, 1, 4), "Aaa Shop", 2000)],
        [Spend(D(2026, 2, 3), "Bbb Shop", 3000)],
        [Spend(D(2026, 3, 4), "Ccc Shop", 1500)],
        [Spend(D(2026, 4, 3), "Ddd Shop", 2500)],
        may_spends,
        [Spend(D(2026, 6, 4), "Fff Shop", 500)],
    ]
    chain_dated("may-row", [jan, feb, mar, apr, may, jun], spends, dec, **held_only)
    feed(
        store,
        "may-row",
        [row for rows in spends for row in rows if row.payee != "Mid Shop"],
        digest="may-row-feed",
    )

    statement(store, root, "single", D(2026, 9, 10), 5000, [Spend(D(2026, 9, 2), "Hhh Shop", 1000)],
              received=D(2026, 9, 10), previous_close=D(2026, 8, 10))

    u = 3000
    for closing, day in ((D(2026, 7, 10), D(2026, 7, 5)), (D(2026, 8, 10), D(2026, 8, 5)),
                         (D(2026, 9, 10), D(2026, 9, 5))):
        u = statement(store, root, "undated", closing, u, [Spend(day, "Iii Shop", 100)],
                      received=closing)

    owed = 4000
    previous = D(2026, 6, 10)
    for closing in (D(2026, 7, 10), D(2026, 8, 10), D(2026, 9, 10)):
        owed = statement(store, root, "spaces", closing, owed,
                         [Spend(D(closing.year, closing.month, 4), "Jjj Shop", 100)],
                         received=closing, previous_close=previous)
        previous = closing
    keep_statement_readings(store)
    store.connection.commit()


def figures(store: Store) -> dict[str, OpeningFigures]:
    plain = statement_opening_report(store, NO_SPACES).accounts
    spaced = statement_opening_report(store, WITH_A_SPACE).accounts
    found = {f.account: f for f in plain if f.account != "spaces"}
    found["spaces"] = next(f for f in spaced if f.account == "spaces")
    return found


@pytest.fixture(scope="module", params=["live", "rebuilt"])
def measured(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("openings")
    with Store(root / "store.sqlite3") as store:
        build(store, root)
        if request.param == "rebuilt":
            rebuild_from_raw(store)
            keep_statement_readings(store)
            store.connection.commit()
        return figures(store), statement_opening_report(store, NO_SPACES), request.param


def days(spans):
    return [(a.isoformat(), b.isoformat()) for a, b in spans]


class TestFiveMonthlyStatementsThatAllMeet:
    def test_Openings_WhenEveryStatementMeetsTheOneBefore_FourAreRedundantAndTheFirstIsNew(
        self, measured
    ):
        five = measured[0]["five"]
        assert (five.statements, five.stating, len(five.placed)) == (5, 5, 5)
        assert {p.how for p in five.placed} == {PlacedBy.DATED_BY_STATEMENT}
        assert (len(five.redundant), len(five.new), len(five.conflicting)) == (4, 1, 0)
        assert (len(five.first), len(five.reproduced), len(five.unreproduced)) == (1, 0, 0)

    def test_Standing_WhenEveryStatementMeets_OnlyTheFirstStatementsDaysAreNewlyInAgreement(
        self, measured
    ):
        five = measured[0]["five"]
        assert "add up to every known balance from" in five.today_sentence
        assert "to 2026-05-10" in five.today_sentence
        assert five.rule_sentence == "The transactions add up from 2025-12-10 to 2026-05-10."
        assert days(five.newly_agreeing) == [("2025-12-10", "2026-01-10")]
        assert five.newly_not_agreeing == []
        assert five.rows_now_tested == 1
        assert not five.opening_changes


class TestACardWithAprilMissing:
    def test_Openings_WhenAprilIsMissing_MayOpeningIsNewAndTheRowsHeldDoNotReproduceIt(
        self, measured
    ):
        april = measured[0]["april"]
        assert (april.statements, len(april.placed)) == (5, 5)
        assert (len(april.redundant), len(april.new), len(april.conflicting)) == (3, 2, 0)
        assert len(april.first) == 1 and len(april.reproduced) == 0
        assert [(p.closing.isoformat(), b.isoformat(), p.day.isoformat())
                for p, b in april.unreproduced] == [("2026-05-10", "2026-03-10", "2026-04-10")]
        assert april.repeating == 0

    def test_Standing_WhenAprilIsMissing_AgreementResumesFromTheMayOpening(self, measured):
        april = measured[0]["april"]
        assert "add up to every known balance from" in april.today_sentence
        assert "to 2026-03-10" in april.today_sentence
        assert "2026-05-10" in april.today_sentence
        assert april.rule_sentence == (
            "The transactions add up from 2025-12-10 to 2026-03-10 and from 2026-04-10 to "
            "2026-06-10. The transactions do not add up from 2026-03-10 to 2026-04-10."
        )
        assert days(april.newly_agreeing) == [
            ("2025-12-10", "2026-01-10"),
            ("2026-04-10", "2026-06-10"),
        ]
        assert april.newly_not_agreeing == []
        assert not april.opening_changes


class TestARowMissingFromTheMiddleOfMay:
    def test_Standing_WhenAStatementsRowsAreAllReadIn_NothingIsMissingFromMay(self, measured):
        """A rebuild replays every held document through its parser, so the statement's own
        row comes back and May reproduces: the missing row is a state only the live store,
        before a rebuild, can be in."""
        if measured[2] != "rebuilt":
            pytest.skip("the row is missing only before a rebuild")
        may = measured[0]["may-row"]
        assert "add up to every known balance from" in may.today_sentence
        assert "to 2026-06-10" in may.today_sentence
        assert may.rule_sentence == "The transactions add up from 2025-12-10 to 2026-06-10."
        assert may.newly_not_agreeing == []

    def test_Standing_WhenARowIsMissingInsideMay_TheFailureIsPinnedToMay(self, measured):
        if measured[2] != "live":
            pytest.skip("a rebuild reads the missing row back from the statement")
        may = measured[0]["may-row"]
        assert (may.statements, len(may.placed)) == (6, 6)
        assert (len(may.redundant), len(may.new), len(may.conflicting)) == (5, 1, 0)
        assert "add up to every known balance from" in may.today_sentence
        assert "to 2026-04-10" in may.today_sentence
        assert may.rule_sentence == (
            "The transactions add up from 2025-12-10 to 2026-04-10 and from 2026-05-10 to "
            "2026-06-10. The transactions do not add up from 2026-04-10 to 2026-05-10."
        )
        assert days(may.newly_agreeing) == [
            ("2025-12-10", "2026-01-10"),
            ("2026-05-10", "2026-06-10"),
        ]
        assert may.newly_not_agreeing == []
        assert may.rows_now_tested == 1
        assert not may.opening_changes


class TestAnAccountWithOneStatementOnly:
    def test_Standing_WhenOnlyOneStatementIsHeld_ItsOwnRowsBecomeTestable(self, measured):
        single = measured[0]["single"]
        assert (single.statements, len(single.placed), len(single.new)) == (1, 1, 1)
        assert len(single.first) == 1
        assert "do not yet add up to any known balance" in single.today_sentence
        assert single.rule_sentence == "The transactions add up from 2026-08-10 to 2026-09-10."
        assert days(single.newly_agreeing) == [("2026-08-10", "2026-09-10")]
        assert single.rows_now_tested == 1


class TestStatementsThatPrintNoStart:
    def test_Openings_WhenNoStartIsPrinted_EachIsPlacedTheDayBeforeItsFirstRow(self, measured):
        undated = measured[0]["undated"]
        assert {p.how for p in undated.placed} == {PlacedBy.DAY_BEFORE_FIRST_ROW}
        assert [p.day.isoformat() for p in undated.placed] == [
            "2026-07-04", "2026-08-04", "2026-09-04",
        ]
        assert (len(undated.redundant), len(undated.new), undated.repeating) == (0, 3, 2)
        assert (len(undated.first), len(undated.reproduced), len(undated.unreproduced)) == (1, 2, 0)
        assert undated.rule_sentence == "The transactions add up from 2026-07-04 to 2026-09-10."
        assert days(undated.newly_agreeing) == [("2026-07-04", "2026-07-10")]


class TestAnAccountWithSpaces:
    def test_Openings_WhenStatementsCannotSeeSpaces_TheyAreTheFamilysFigureNotTheAccounts(
        self, measured
    ):
        spaces = measured[0]["spaces"]
        assert spaces.with_spaces
        assert (spaces.stating, spaces.blind, len(spaces.placed)) == (3, 3, 0)
        assert "No opening balance would be added to this account's own reading." in (
            spaces.sentences()
        )

    def test_Openings_WhenTheAccountHasNoSpaces_TheSameStatementsAreItsOwn(self, measured):
        plain = {f.account: f for f in measured[1].accounts}["spaces"]
        assert (plain.blind, len(plain.placed)) == (0, 3)


class TestTheReport:
    def test_Report_ForAStoreWithNoStatements_SaysSoInAWord(self, tmp_path):
        with Store(tmp_path / "empty.sqlite3") as store:
            assert statement_opening_report(store, NO_SPACES).sentences() == [
                "No account holds a statement, so there is no opening balance to place."
            ]

    def test_Report_ForTheHousehold_NamesNoFigure(self, measured):
        text = "\n".join(measured[1].sentences())
        assert "five:" in text and "april:" in text
        for figure in ("100.00", "120.00", "7.00", "12.00", "10000", "-1200"):
            assert figure not in text


def _text(store) -> str:
    """The measurement of every account whose rows a rebuild leaves as they were."""
    accounts = [f for f in statement_opening_report(store, NO_SPACES).accounts
                if f.account != "may-row"]
    return "\n".join(s for f in accounts for s in f.sentences())


def test_Measurement_WhenTheStoreIsRebuiltFromItsArtefacts_IsTheSame(tmp_path):
    with Store(tmp_path / "store.sqlite3") as store:
        build(store, tmp_path)
        live = _text(store)
        rebuild_from_raw(store)
        keep_statement_readings(store)
        store.connection.commit()
        assert _text(store) == live
