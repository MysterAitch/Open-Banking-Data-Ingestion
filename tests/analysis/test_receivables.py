"""Money owed back on one transaction (plan.md worked example E), met by the leg matcher.

KNOWN ANSWERS, decided before the first run (pence; every name and figure invented):

  A coffee of 4.20 on 2026-10-03 is declared owed by an organisation, labelled "volunteering",
  expected within the default 28 days (2026-10-31).
    - 4.20 from that organisation on 10-15 meets it: reimbursed on 10-15, by that transaction.
    - the same amount from anyone else, an amount 11% out, or a transfer before the expense meets
      nothing.
    - two such coffees and one transfer: the older is met, the younger is not.
    - written off by hand, it takes no transfer: the transfer meets the younger instead.
  Label totals this year, for 4.20 + 4.20 + 3.00 declared as volunteering (one reimbursed, one
  owed, one written off): 11.40 declared, 4.20 reimbursed, 4.20 owed, 3.00 written off.
"""

from __future__ import annotations

from datetime import date

from obdi.analysis.flows import (
    OPEN,
    REIMBURSED,
    label_lines,
    label_sentence,
    read_flows,
    settle_receivables,
)
from obdi.analysis.recurring import Mover
from obdi.ingest.receivable_records import RECEIVED_ELSEWHERE, WRITTEN_OFF, Receivable

CHARITY, OTHER, PARTNER = 21, 22, 11
NAMES = {CHARITY: "Brindlewick Volunteers", OTHER: "Fenwick Club", PARTNER: "Casey Wintermute"}
TODAY = date(2026, 10, 20)


def owed(ident: int, day: date, minor: int = 420, **kw) -> Receivable:
    fields = {
        "id": ident, "account": "current-main", "row_ref": f"row-{ident}", "day": day,
        "debtor": CHARITY, "amount_minor": minor, "label": "volunteering",
        "expected_day": date(2026, 10, 31), "declared_at": "2026-10-03T12:00:00+00:00",
    }  # fmt: skip
    fields.update(kw)
    return Receivable(**fields)


def paid(row: str, day: date, minor: int = 420, party: int = CHARITY) -> Mover:
    return Mover(row, "current-main", day, minor, party)


def states(receivables, movers, today=TODAY):
    return {s.id: s for s in settle_receivables(receivables, movers, NAMES, today)}


class TestAReceivableIsMetByATransferFromItsDebtor:
    def test_Receivable_WhenTheDebtorPaysTheAmount_IsReimbursedAndNamesTheTransaction(self):
        found = states([owed(1, date(2026, 10, 3))], [paid("t1", date(2026, 10, 15))])[1]

        assert found.state == REIMBURSED
        assert (found.closed_by, found.closed_on) == ("t1", "2026-10-15")
        assert found.overdue is False

    def test_Receivable_WhenAnyoneElsePaysTheSameAmount_IsNotClosed(self):
        found = states(
            [owed(1, date(2026, 10, 3))], [paid("t1", date(2026, 10, 15), party=OTHER)]
        )[1]

        assert found.state == OPEN

    def test_Receivable_WhenTheAmountIsOutsideTheWindow_IsNotClosed(self):
        found = states([owed(1, date(2026, 10, 3))], [paid("t1", date(2026, 10, 15), 375)])[1]

        assert found.state == OPEN

    def test_Receivable_WhenTheAmountIsAPennyOut_IsStillMet(self):
        found = states([owed(1, date(2026, 10, 3), 1000)], [paid("t1", date(2026, 10, 15), 990)])[1]

        assert found.state == REIMBURSED

    def test_Receivable_WhenTheTransferIsBeforeTheExpense_IsNotClosedByIt(self):
        found = states([owed(1, date(2026, 10, 3))], [paid("t1", date(2026, 10, 2))])[1]

        assert found.state == OPEN

    def test_Receivable_WhenMoneyWentOutToTheDebtor_IsNotClosedByIt(self):
        found = states([owed(1, date(2026, 10, 3))], [paid("t1", date(2026, 10, 15), -420)])[1]

        assert found.state == OPEN

    def test_Receivables_WhenTwoAreOwedAndOneTransferArrives_OnlyTheOlderIsClosed(self):
        both = [owed(2, date(2026, 10, 5)), owed(1, date(2026, 10, 3))]

        found = states(both, [paid("t1", date(2026, 10, 15))])

        assert found[1].state == REIMBURSED
        assert found[2].state == OPEN

    def test_Receivables_WhenTwoAreOwedAndTwoTransfersArrive_BothAreClosedByDifferentOnes(self):
        both = [owed(1, date(2026, 10, 3)), owed(2, date(2026, 10, 5))]

        found = states(both, [paid("t1", date(2026, 10, 15)), paid("t2", date(2026, 10, 16))])

        assert {found[1].closed_by, found[2].closed_by} == {"t1", "t2"}

    def test_Receivables_WhenTheOlderWasClosedByHand_TheTransferMeetsTheYounger(self):
        older = owed(1, date(2026, 10, 3), closed_how=WRITTEN_OFF, closed_reason="goodwill",
                     closed_at="2026-10-10T09:00:00+00:00")  # fmt: skip
        younger = owed(2, date(2026, 10, 5))

        found = states([older, younger], [paid("t1", date(2026, 10, 15))])

        assert found[1].state == WRITTEN_OFF and found[1].reason == "goodwill"
        assert found[2].state == REIMBURSED

    def test_Receivable_WhenPastItsExpectedDay_IsOverdueAndOtherwiseNot(self):
        item = [owed(1, date(2026, 10, 3))]

        assert states(item, [], date(2026, 10, 31))[1].overdue is False
        assert states(item, [], date(2026, 11, 1))[1].overdue is True

    def test_Receivable_WhenTheDebtorHasNoPaymentsAttributed_SaysWhyNothingCanMeetIt(self):
        found = states([owed(1, date(2026, 10, 3))], [paid("t1", date(2026, 10, 15), party=0)])[1]

        assert found.state == OPEN
        assert "No payment is attributed to that entity yet" in found.why

    def test_Receivable_WhenTheDebtorEntityIsGone_SaysSoAndDoesNotFail(self):
        found = states([owed(1, date(2026, 10, 3), debtor=999)], [])[1]

        assert found.state == OPEN
        assert "no longer exists" in found.why


class TestWhatIsOwedAndWhatALabelComesTo:
    def test_ReadFlows_WhenOneIsOpenAndOneReimbursed_OwesOnlyTheOpenOneAndNamesTheDebtor(self):
        items = [owed(1, date(2026, 10, 3)), owed(2, date(2026, 10, 5), 700)]

        reading = read_flows([], [paid("t1", date(2026, 10, 15))], NAMES, {}, TODAY, items)

        assert [(o.who, o.amount.minor, o.source, o.receivable) for o in reading.owed] == [
            ("Brindlewick Volunteers", 700, "transaction", 2)
        ]
        assert reading.owed_total.minor == 700

    def test_ReadFlows_WhenAReceivableIsClosedByHand_OwesNothingForIt(self):
        items = [owed(1, date(2026, 10, 3), closed_how=RECEIVED_ELSEWHERE, closed_reason="cash",
                      closed_at="2026-10-10T09:00:00+00:00")]  # fmt: skip

        reading = read_flows([], [], NAMES, {}, TODAY, items)

        assert reading.owed == ()

    def test_ReadFlows_WhenNothingIsDeclared_HasNoReceivableNorLabel(self):
        reading = read_flows([], [], NAMES, {}, TODAY)

        assert reading.receivables == () and reading.labels == ()

    def test_Labels_WhenOneIsReimbursedOneOwedAndOneWrittenOff_SumsEachAndSaysTheSentence(self):
        items = [
            owed(1, date(2026, 10, 3)),
            owed(2, date(2026, 10, 5)),
            owed(3, date(2026, 10, 6), 300, label="Volunteering", closed_how=WRITTEN_OFF,
                 closed_reason="goodwill", closed_at="2026-10-10T09:00:00+00:00"),
        ]  # fmt: skip
        found = settle_receivables(items, [paid("t1", date(2026, 10, 15))], NAMES, TODAY)

        (line,) = label_lines(items, found, TODAY)

        assert line.label == "volunteering"
        figures = (line.total, line.reimbursed, line.owed, line.written_off)
        assert tuple(m.minor for m in figures) == (1140, 420, 420, 300)
        assert line.has_written_off is True
        assert (
            label_sentence(line.label, "£11.40", "£4.20", "£4.20")
            == "volunteering this year: £11.40, of which £4.20 reimbursed, £4.20 owed"
        )

    def test_Labels_WhenSpentInAnotherYearOrUnlabelled_AreNotInThisYearsReport(self):
        items = [
            owed(1, date(2025, 12, 30)),
            owed(2, date(2026, 10, 5), label=""),
        ]
        found = settle_receivables(items, [], NAMES, TODAY)

        assert label_lines(items, found, TODAY) == []

    def test_ReadFlows_WhenAPartnersHalfAndAReceivableWantTheSameTransfer_TheLegHasItFirst(self):
        # One 450.00 transfer from the partner: the leg for the rent has it, so a receivable of the
        # same amount from the partner is not closed by the very payment that met the leg.
        from obdi.ingest.commitment_records import Commitment, Leg, Window

        leg = Leg(
            1, 1, 1, "", "current-main", PARTNER, None, 45000, None, 30, 1, 0, ""
        )
        rent = Commitment(
            1, "Rent", None, "rent", "scheduled", "current-main", "out", "2026-09-01",
            (Window(1, 1, date(2026, 10, 1), None, 90000, "GBP", "monthly", 1, 0, 4, "x"),),
            (leg,),
        )  # fmt: skip
        transfer = Mover("t1", "current-main", date(2026, 9, 30), 45000, PARTNER)
        mine = owed(5, date(2026, 9, 1), 45000, debtor=PARTNER, expected_day=date(2026, 10, 1))

        reading = read_flows([rent], [transfer], NAMES, {}, date(2026, 10, 2), [mine])

        assert [s.state for s in reading.receivables] == [OPEN]
