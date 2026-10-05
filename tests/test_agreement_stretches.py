"""Each stretch between known balances is judged by its own arithmetic, said in days and no figure.

THE ACCOUNT. A current account whose statements close on the 10th of each month, January to June
2026, with one transaction on the 5th of each, so it falls in the statement that closes on the
10th after it. In pounds, opening at 1,000.00:

    statement  closes   row on the 5th   closing balance
    Jan        01-10      -10.00             990.00
    Feb        02-10      -20.00             970.00
    Mar        03-10      -30.00             940.00
    Apr        04-10      -40.00             900.00
    May        05-10      -50.00             850.00
    Jun        06-10      -60.00             790.00

Every answer was worked out from this table before the first run. "Distance" is how far a balance
is from what the rows predict from the first closing; a stretch is reproduced exactly when the
distance did not change across it.

  complete       every statement and every row held: five stretches, all reproduced, nothing
                 failing, through 06-10.
  one hole       April's statement and row missing. May's closing is 40.00 from what the rows
                 predict: ONE stretch fails, 03-10 to 05-10, and 05-10 to 06-10 is reproduced.
                 Held at 05-10 and through 03-10, as the rule alone says.
  two holes      February and April missing, with their rows. Distances 0, -20.00, -60.00,
                 -60.00: failing 01-10 to 03-10 and 03-10 to 05-10; 05-10 to 06-10 reproduced.
  lone bad       March's closing is mis-stated by 5.00 and every row is held. Both stretches it
                 borders fail (02-10 to 03-10 and 03-10 to 04-10): nothing here chooses WHICH of
                 the balance or the rows is wrong, and the page says so.
  conflict       February is also stated by a second source, 5.00 different. A conflict between
                 sources at 02-10, never a failing stretch; the stretch beginning on 02-10 is
                 untested, and the one after it adds up.
  twins          February is also stated by a second source with the SAME figure: nothing fails
                 and nothing is untested.
"""

from __future__ import annotations

from datetime import date

from obdi.agreement import (
    AGREES,
    HELD_CONFLICT,
    HELD_UNMET,
    STRETCH_MEANINGS,
    Agreement,
    derive_agreement,
    known_of_opening,
    stretch_sentences,
)
from obdi.balance_anchors import STATED, STATEMENT, Anchor, derive_opening
from test_ledger import txn

D = date
ACCOUNT = "current"
CLOSINGS = [D(2026, m, 10) for m in range(1, 7)]
BALANCE = [99000, 97000, 94000, 90000, 85000, 79000]
SPEND = [-1000, -2000, -3000, -4000, -5000, -6000]


def read(
    statements: tuple[int, ...] = (0, 1, 2, 3, 4, 5),
    *,
    missing_rows: tuple[int, ...] = (),
    closing_figure: dict[int, int] | None = None,
    also_stated: dict[int, int] | None = None,
) -> Agreement:
    """The agreement of the account above, with the statements and rows asked for. `also_stated`
    is a second source's balance (a typed one) for the closing day of the statement named."""
    closing_figure = closing_figure or {}
    anchors = [
        Anchor(
            CLOSINGS[index],
            closing_figure.get(index, BALANCE[index]),
            STATEMENT,
            stated_by="invented-pdf",
        )
        for index in statements
    ]
    anchors += [
        Anchor(CLOSINGS[index], figure, STATED) for index, figure in (also_stated or {}).items()
    ]
    rows = [
        txn(ACCOUNT, "feed", f"r{index}", D(2026, index + 1, 5), SPEND[index], f"Shop {index}")
        for index in range(6)
        if index not in missing_rows
    ]
    return derive_agreement(known_of_opening(derive_opening(ACCOUNT, anchors, rows)), ())


def failing(agreement: Agreement) -> list[tuple[str, str]]:
    return [(s.start.isoformat(), s.end.isoformat()) for s in agreement.failing]


def untested(agreement: Agreement) -> list[tuple[str, str]]:
    return [(s.start.isoformat(), s.end.isoformat()) for s in agreement.stretches if s.untested]


class TestEveryStatementHeld:
    def test_Account_WhenNothingIsMissing_EveryStretchIsReproduced(self):
        found = read()

        assert len(found.stretches) == 5
        assert all(s.reproduced for s in found.stretches)
        assert failing(found) == []
        assert found.state == AGREES and found.through == CLOSINGS[5]
        assert stretch_sentences(found) == []

    def test_Account_WithASingleKnownBalance_HasNoStretchToJudge(self):
        assert read((2,)).stretches == ()


class TestOneMissingStatement:
    def test_Account_WhenAprilIsMissing_OnlyTheStretchContainingItFails(self):
        found = read((0, 1, 2, 4, 5), missing_rows=(3,))

        assert failing(found) == [("2026-03-10", "2026-05-10")]
        assert [s.reproduced for s in found.stretches] == [True, True, False, True]

    def test_Account_WhenAprilIsMissing_TheRuleIsUnchangedByTheStretches(self):
        found = read((0, 1, 2, 4, 5), missing_rows=(3,))

        assert found.state == HELD_UNMET
        assert found.through == CLOSINGS[2]
        assert found.held is not None and found.held.day == CLOSINGS[4]

    def test_Account_WhenAprilIsMissing_TheSentenceNamesTheDaysAndNoFigure(self):
        found = read((0, 1, 2, 4, 5), missing_rows=(3,))

        assert stretch_sentences(found) == [
            "The transactions held between 2026-03-10 and 2026-05-10 do not add up to the "
            "change between the two known balances."
        ]

    def test_Account_WhenAprilsStatementIsMissingButItsRowIsHeld_NothingFails(self):
        found = read((0, 1, 2, 4, 5))

        assert failing(found) == []
        assert found.state == AGREES


class TestTwoMissingStatements:
    def test_Account_WhenFebruaryAndAprilAreMissing_EachHoleFailsItsOwnStretchOnly(self):
        found = read((0, 2, 4, 5), missing_rows=(1, 3))

        assert failing(found) == [("2026-01-10", "2026-03-10"), ("2026-03-10", "2026-05-10")]
        assert found.stretches[-1].reproduced


class TestALoneBadBalance:
    def test_Account_WhenOneClosingIsMisStated_BothStretchesItBordersFailAndNoneIsBlamed(self):
        found = read(closing_figure={2: BALANCE[2] + 500})

        assert failing(found) == [("2026-02-10", "2026-03-10"), ("2026-03-10", "2026-04-10")]
        said = " ".join(stretch_sentences(found))
        assert "in doubt" not in said and "mis-stated" not in said

    def test_Account_WhenTheMisStatedClosingIsTheLastOne_OnlyItsOneStretchFails(self):
        found = read(closing_figure={5: BALANCE[5] + 500})

        assert failing(found) == [("2026-05-10", "2026-06-10")]


class TestSourcesThatDisagreeOnOneDay:
    def test_Account_WhenTwoSourcesDifferForFebruary_ItIsAConflictAndNotAFailingStretch(self):
        found = read(also_stated={1: BALANCE[1] + 500})

        assert found.state == HELD_CONFLICT
        assert [c.day for c in found.conflicts] == [CLOSINGS[1]]
        assert failing(found) == []
        assert [s.conflict for s in found.stretches] == [True, False, False, False, False], (
            "the stretch ENDING on 02-10 is the first, 01-10 to 02-10"
        )

    def test_Account_WhileAConflictStands_TheStretchBeginningThereIsUntestedNotAddingUp(self):
        found = read(also_stated={1: BALANCE[1] + 500})

        assert untested(found) == [("2026-02-10", "2026-03-10")]
        assert [s.reproduced for s in found.stretches if s.untested] == [False]
        assert stretch_sentences(found) == [
            "Nothing is tested from 2026-02-10 to 2026-03-10: two known balances differ for "
            "2026-02-10, so the transactions cannot be said to add up from either."
        ]

    def test_Account_WhileAConflictStands_TheStretchesAfterItAreStillJudged(self):
        found = read(also_stated={1: BALANCE[1] + 500})

        assert [s.reproduced for s in found.stretches][2:] == [True, True, True]

    def test_Account_WhenTwoSourcesStateTheSameFigure_NothingFailsAndNothingIsUntested(self):
        found = read(also_stated={1: BALANCE[1]})

        assert failing(found) == [] and untested(found) == []
        assert found.state == AGREES


class TestWhatAFailingStretchCanMean:
    def test_Meanings_AreFiveAndNameNoCauseAsTheOne(self):
        assert len(STRETCH_MEANINGS) == 5
        assert all("is why" not in item for item in STRETCH_MEANINGS)
