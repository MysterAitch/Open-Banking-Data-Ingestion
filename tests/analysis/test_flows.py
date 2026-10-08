"""The three-leg rent (plan.md worked example C) and the space's sum, matched leg by leg.

KNOWN ANSWERS, decided before the first run (pence; every name and figure invented):

  Rent 900.00 monthly on the 1st from 2026-10-01, three legs for the October payment:
    1. current account -> the bills space, half (450.00), by 2026-09-28
    2. the partner (an entity) -> current account, half (450.00), by 2026-09-30
    3. current account -> the landlord (an entity), the whole 900.00, on 2026-10-01
    4. the partner -> the landlord, external: neither end is held
  Seen: 450.00 to the space on 09-27 (a transfer pair), 450.00 from the partner on 09-30, 900.00 to
  the landlord on 10-01. So on 10-02 all three checked legs are matched and nothing is said.

  The space's need on 09-25 for October: rent 450.00 by 10-01, electric 79.00 by 10-25, gas 34.50
  by 10-27, which is 563.50 by 10-01. From 09-10 for September it is electric and gas alone, 113.50
  by 09-25, because the rent was drawn down on 09-01.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.analysis.flows import (
    INCOMING,
    MATCHED,
    MISSING,
    OUTGOING,
    PENDING,
    STASH,
    evaluate_legs,
    missing_sentence,
    read_flows,
    space_needs,
)
from obdi.analysis.free_position import AccountFigures
from obdi.analysis.recurring import Mover
from obdi.ingest.commitment_records import Commitment, Leg, Window
from obdi.read.ledger import Money

CURRENT = "current-main"
SPACE = "bills-space"
PARTNER, LANDLORD = 11, 12
NAMES = {PARTNER: "Casey Wintermute", LANDLORD: "Landlord Ltd"}


def window(minor: int, day: int, since: date = date(2026, 10, 1)) -> Window:
    return Window(1, 1, since, None, minor, "GBP", "monthly", day, 0, 4, "invented")


def leg(position: int, **kw) -> Leg:
    fields = {
        "id": position, "commitment_id": 1, "position": position, "from_account": "",
        "to_account": "", "from_entity": None, "to_entity": None, "amount_minor": None,
        "share_percent": None, "day": 1, "months_before": 0, "tolerance_days": 0, "label": "",
    }  # fmt: skip
    fields.update(kw)
    return Leg(**fields)


def rent(*, drop: int | None = None, extra: Leg | None = None) -> Commitment:
    legs = [
        leg(1, from_account=CURRENT, to_account=SPACE, share_percent=50, day=28, months_before=1),
        leg(2, from_entity=PARTNER, to_account=CURRENT, share_percent=50, day=30, months_before=1),
        leg(3, from_account=CURRENT, to_entity=LANDLORD, share_percent=100, day=1),
        leg(4, from_entity=PARTNER, to_entity=LANDLORD, share_percent=100, day=1),
    ]
    if extra is not None:
        legs.append(extra)
    return Commitment(
        1, "Rent", LANDLORD, "landlord", "scheduled", CURRENT, "out", "2026-09-01",
        (window(90000, 1),), tuple(leg_ for leg_ in legs if leg_.position != drop),
    )  # fmt: skip


def mover(row: str, day: date, minor: int, *, party: int = 0, other: str = "", account=CURRENT):
    return Mover(row, account, day, minor, party, other)


ALL_THREE = [
    mover("r1", date(2026, 9, 27), -45000, other=SPACE),
    mover("r2", date(2026, 9, 30), 45000, party=PARTNER),
    mover("r3", date(2026, 10, 1), -90000, party=LANDLORD),
]
BAKERY = mover("rb", date(2026, 10, 2), -320)


def october(found):
    return [i for i in found if i.month == "October"]


def by_kind(found):
    return {i.kind: i for i in october(found)}


class TestTheThreeLegRent:
    def test_Rent_WhenAllThreeLegsHappened_EachIsMatchedAndNothingIsMissing(self):
        found = evaluate_legs([rent()], ALL_THREE, NAMES, date(2026, 10, 2))

        legs = by_kind(found)

        assert {k: v.state for k, v in legs.items()} == {
            STASH: MATCHED,
            INCOMING: MATCHED,
            OUTGOING: MATCHED,
        }
        assert legs[STASH].matched_on == "2026-09-27"
        assert all(i.state != MISSING for i in found)

    def test_Rent_WhenTheExternalLegIsDeclared_ItNeverAppearsAmongTheInstances(self):
        found = evaluate_legs([rent()], ALL_THREE, NAMES, date(2026, 10, 2))

        assert len(october(found)) == 3

    def test_Rent_WhenThePartnersHalfHasNotArrivedByItsDay_ThatLegIsMissingWithTheSentence(self):
        movers = [ALL_THREE[0], ALL_THREE[2], BAKERY]

        legs = by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 2)))

        assert legs[INCOMING].state == MISSING
        assert legs[STASH].state == MATCHED
        said = missing_sentence(
            INCOMING, "Rent", NAMES[PARTNER], legs[INCOMING].share_word, "October",
            legs[INCOMING].due, "",
        )  # fmt: skip
        assert said == (
            "Casey Wintermute's half for October has not arrived (expected by 2026-09-30)."
        )

    def test_Rent_WhenThePartnersHalfIsDueTodayAndNotThereYet_ItIsPendingNotMissing(self):
        movers = [ALL_THREE[0]]

        legs = by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 9, 30)))

        assert legs[INCOMING].state == PENDING

    def test_Rent_WhenTheLandlordWasNotPaidOnTheFirst_TheStoppedSentenceIsSaidOnThe2nd(self):
        movers = [ALL_THREE[0], ALL_THREE[1], BAKERY]

        legs = by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 2)))

        assert legs[OUTGOING].state == MISSING
        assert (
            missing_sentence(OUTGOING, "Rent", "", "", "October", legs[OUTGOING].due, "")
            == "October's Rent did not go out on 2026-10-01."
        )

    def test_Rent_WhenTheAccountsTransactionsStopBeforeTheDay_AbsenceIsNotCalledMissing(self):
        movers = [ALL_THREE[0], ALL_THREE[1]]

        legs = by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 2)))

        assert legs[OUTGOING].state == PENDING
        assert "run to 2026-09-30" in legs[OUTGOING].said

    def test_Rent_WhenTheMoneyCameFromTheWrongEntity_TheLegIsNotClosedByIt(self):
        movers = [
            ALL_THREE[0],
            mover("r2", date(2026, 9, 30), 45000, party=99),
            ALL_THREE[2],
            BAKERY,
        ]

        legs = by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 2)))

        assert legs[INCOMING].state == MISSING

    def test_Rent_WhenTheAmountIsOutsideTheWindow_TheLegIsNotMet(self):
        movers = [
            ALL_THREE[0],
            mover("r2", date(2026, 9, 30), 40000, party=PARTNER),
            ALL_THREE[2],
            BAKERY,
        ]

        assert by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 2)))[
            INCOMING
        ].state == MISSING

    def test_Rent_WhenTheAmountIsAPennyOrTwoOut_TheLegIsStillMet(self):
        movers = [ALL_THREE[0], mover("r2", date(2026, 9, 30), 44800, party=PARTNER), ALL_THREE[2]]

        assert by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 2)))[
            INCOMING
        ].state == MATCHED

    def test_Rent_WhenTheLegsEntityIsNotKnown_ItIsReportedUnmatchableAndNothingCrashes(self):
        unknown = leg(5, from_entity=777, to_account=CURRENT, amount_minor=1000, day=15)
        # Position 2 dropped so the only incoming leg is the unknown one.
        later = mover("rl", date(2026, 10, 16), -320)
        found = evaluate_legs(
            [rent(drop=2, extra=unknown)], [*ALL_THREE, later], NAMES, date(2026, 10, 20)
        )

        incoming = [i for i in october(found) if i.kind == INCOMING]

        assert [i.state for i in incoming] == [MISSING]
        assert "no longer exists" in incoming[0].said

    def test_Rent_WhenNoPaymentIsAttributedToTheEntityYet_TheLegSaysSo(self):
        movers = [m for m in ALL_THREE if m.party != PARTNER] + [BAKERY]

        incoming = by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 2)))[INCOMING]

        assert "No payment is attributed to that entity yet" in incoming.said

    def test_Rent_WhenOneTransferCouldMeetTwoLegs_ItMeetsOneOnly(self):
        twin = Commitment(
            2, "Rent again", LANDLORD, "landlord", "scheduled", CURRENT, "out", "2026-09-01",
            (window(90000, 1),),
            (leg(1, commitment_id=2, id=9, from_entity=PARTNER, to_account=CURRENT,
                 share_percent=50, day=30, months_before=1),),
        )  # fmt: skip
        movers = [ALL_THREE[0], ALL_THREE[1], ALL_THREE[2], BAKERY]

        found = evaluate_legs([rent(), twin], movers, NAMES, date(2026, 10, 2))
        partner_legs = [i for i in october(found) if i.kind == INCOMING]

        assert sorted(i.state for i in partner_legs) == [MATCHED, MISSING]

    def test_Rent_WhenThePartnersHalfArrivesAfterBeingReportedMissing_ItClosesTheLeg(self):
        movers = [ALL_THREE[0], mover("r2", date(2026, 10, 3), 45000, party=PARTNER), ALL_THREE[2]]

        legs = by_kind(evaluate_legs([rent()], movers, NAMES, date(2026, 10, 4)))

        assert legs[INCOMING].state == MATCHED
        assert legs[INCOMING].matched_on == "2026-10-03"

    def test_ReadFlows_WhenThePartnersHalfIsMissing_OwesYouThatHalfAndCountsOneMissing(self):
        movers = [ALL_THREE[0], ALL_THREE[2], BAKERY]

        reading = read_flows([rent()], movers, NAMES, {}, date(2026, 10, 2))

        assert reading.missing == 1
        assert [(o.who, o.amount.minor, o.overdue) for o in reading.owed] == [
            ("Casey Wintermute", 45000, True)
        ]
        assert reading.owed_total.minor == 45000

    def test_ReadFlows_WhenEveryLegHappened_OwesNothingAndSaysNothingMissing(self):
        reading = read_flows([rent()], ALL_THREE, NAMES, {}, date(2026, 10, 2))

        assert reading.owed == ()
        assert reading.owed_total.minor == 0
        assert reading.missing == 0


def figures(ref: str, held_minor: int | None) -> AccountFigures:
    known = held_minor is not None
    return AccountFigures(
        ref=ref, label="Bills", is_card=False, held_basis="", held_known=known,
        held=Money(abs(held_minor), "GBP") if held_minor is not None else None,
        held_direction=("out" if (held_minor or 0) < 0 else "in") if known else "",
        owed=None, income_from="none", income_on="", income_said="", committed=None,
        committed_said="", due=(), unplaced=0, free=None, free_short=False, free_said="",
    )  # fmt: skip


def stash_commitment(ident: int, name: str, minor: int, day: int) -> Commitment:
    stash = leg(1, id=ident * 10, commitment_id=ident, from_account=CURRENT, to_account=SPACE,
                share_percent=100, day=day)  # fmt: skip
    return Commitment(
        ident, name, None, name.casefold(), "pulled", CURRENT, "out", "2026-01-01",
        (Window(ident, ident, date(2026, 1, 1), None, minor, "GBP", "monthly", day, 0, 4, "x"),),
        (stash,),
    )  # fmt: skip


def household() -> list[Commitment]:
    rent_stash = Commitment(
        1, "Rent", None, "rent", "scheduled", CURRENT, "out", "2026-01-01",
        (Window(1, 1, date(2026, 1, 1), None, 90000, "GBP", "monthly", 1, 0, 4, "x"),),
        (leg(1, from_account=CURRENT, to_account=SPACE, share_percent=50, day=28,
             months_before=1),),
    )  # fmt: skip
    return [
        rent_stash,
        stash_commitment(2, "Electric", 7900, 25),
        stash_commitment(3, "Gas", 3450, 27),
    ]


class TestTheSpacesNeed:
    def test_Need_WhenFromThe25thOfTheMonthBefore_IsTheSumOfEveryStashByTheEarliestDay(self):
        (need,) = space_needs(household(), {SPACE: figures(SPACE, 45000)}, date(2026, 9, 25))

        assert need.needed.minor == 56350
        assert need.by == "2026-10-01"
        assert [(line.commitment, line.amount.minor) for line in need.lines] == [
            ("Rent", 45000),
            ("Electric", 7900),
            ("Gas", 3450),
        ]
        assert need.funded is False

    def test_Need_WhenEarlyInTheMonth_CountsOnlyTheDrawdownsStillToCome(self):
        (need,) = space_needs(household(), {SPACE: figures(SPACE, 11350)}, date(2026, 9, 10))

        assert need.needed.minor == 11350
        assert need.by == "2026-09-25"
        assert need.funded is True

    def test_Need_WhenTheSpaceHoldsExactlyWhatIsNeeded_IsFundedWithNoSurplus(self):
        (need,) = space_needs(household(), {SPACE: figures(SPACE, 56350)}, date(2026, 9, 25))

        assert need.funded is True
        assert need.surplus.minor == 0

    def test_Need_WhenTheSpaceHoldsMoreThanNeeded_ShowsTheSurplus(self):
        (need,) = space_needs(household(), {SPACE: figures(SPACE, 60000)}, date(2026, 9, 25))

        assert need.funded is True
        assert need.surplus.minor == 3650

    def test_Need_WhenTheSpaceIsOverdrawn_IsNotFundedAndSaysSoInItsHeldFigure(self):
        (need,) = space_needs(household(), {SPACE: figures(SPACE, -500)}, date(2026, 9, 25))

        assert need.funded is False
        assert need.held is not None and need.held.minor == -500

    def test_Need_WhenTheSpacesBalanceIsNotKnown_IsNotFundedAndNeverGuessedAsNil(self):
        (need,) = space_needs(household(), {SPACE: figures(SPACE, None)}, date(2026, 9, 25))

        assert need.funded is False
        assert need.held is None
        assert need.held_known is False

    def test_Need_WhenNoCommitmentStashesInASpace_ThereIsNoNeedAtAll(self):
        assert space_needs([rent(drop=1)], {}, date(2026, 9, 25)) == []

    def test_Need_WhenTheCommitmentHasEnded_ItIsNotCounted(self):
        ended = household()
        window_ = ended[0].windows[0]
        closed = Window(*(*(getattr(window_, f) for f in ("id", "commitment_id", "from_day")),
                          date(2026, 6, 1),
                          *(getattr(window_, f) for f in ("amount_minor", "currency", "cadence",
                                                          "usual_day", "usual_month",
                                                          "tolerance_days", "basis"))))  # fmt: skip
        ended[0] = Commitment(
            1, "Rent", None, "rent", "scheduled", CURRENT, "out", "2026-01-01", (closed,),
            ended[0].legs,
        )  # fmt: skip

        (need,) = space_needs(ended, {SPACE: figures(SPACE, 0)}, date(2026, 9, 25))

        assert need.needed.minor == 11350


@pytest.mark.parametrize(("today", "expected_by"), [("2026-09-24", "2026-09-25"),
                                                    ("2026-09-25", "2026-10-01")])
def test_Need_WhenTheDayCrossesThe25th_TheMonthLooked_AtMovesToTheNext(today, expected_by):
    (need,) = space_needs(household(), {SPACE: figures(SPACE, 0)}, date.fromisoformat(today))

    assert need.by == expected_by
