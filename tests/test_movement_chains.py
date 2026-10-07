"""The two sides of a chain of transfers agree, movement for movement.

The owner's scenario: "100 in / 100 out / 100 in / 100 out. This scenario could
collapse into just one out and one in and the daily totals would stay the same.
It could also collapse into zero movements. While it would be effectively the
same in the end, it would be a mismatch." Every balance agrees whether the Space
side holds four movements, two, or none, so the legs leaving each account for
another are set against the legs arriving there, per day.

THE HOUSEHOLD is the round-up corpus with four transfers between the main
account and the Bills Space on day 6, from the main account's side in, out, in,
out, each of 100, at nine, ten, eleven, and twelve o'clock. The Space's feed
stamps its side one second after the main account's. Every household lands the
sources in EVERY order and rebuilds.

KNOWN ANSWERS, worked by hand before the first run:

    (i)   all four movements on both sides
          no fault
    (ii)  the Space holds one in and one out only
          two faults on day 6: main to Bills (2 leave, 1 arrive) and Bills to
          main (1 leaves, 2 arrive)
    (iii) the Space holds none
          two faults on day 6: main to Bills (2 leave, 0 arrive) and Bills to
          main (0 leave, 2 arrive)

THE CHAIN, on bare legs: main to Space A to main to Space B, then a payment to
a third party that is no leg. Complete it is no fault in any order of the legs;
with the hop from Space A back to main missing on the main side, the one fault
names Space A, main, and the day.

MIDNIGHT: a movement out at 23:59:58 and arriving at 00:00:01 the next day is
one movement. An arrival two hours after the departure is not, and neither is
the same size arriving the next day when another arrival is the one missing.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date

import pytest

from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.store import Store
from obdi.movement_completeness import check_chains, movement_completeness
from round_up_corpus import main_feed, space_arrival, space_feed
from test_space_attribution import BILLS, HOLIDAY, MAIN, MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    corpus,
    order_id,
    transfer_to_the_space,
)

DAY = 6
AMOUNT = 100


def canonical(ref: str) -> str:
    return str(MAP.resolve(*ref.split(":", 1))) if ":" in ref else ref


def at(hour: int, seconds: int = 0) -> str:
    return f"2026-09-{DAY:02}T{hour:02}:00:{seconds:02}.000Z"


def main_out(hour: int) -> dict:
    return transfer_to_the_space(f"m-out-{hour}", AMOUNT, DAY) | {"transactionTime": at(hour)}


def main_in(hour: int) -> dict:
    return space_arrival(
        f"m-in-{hour}",
        AMOUNT,
        DAY,
        counterPartyUid="cat-bills",
        counterPartyName="Bills",
        transactionTime=at(hour),
    )


def space_in(hour: int) -> dict:
    return space_arrival(f"s-in-{hour}", AMOUNT, DAY, transactionTime=at(hour, 1))


def space_out(hour: int) -> dict:
    return space_arrival(f"s-out-{hour}", AMOUNT, DAY, direction="OUT", transactionTime=at(hour, 1))


# From the main account's side: in at nine, out at ten, in at eleven, out at twelve.
MAIN_SIDE = [main_in(9), main_out(10), main_in(11), main_out(12)]
SPACE_SIDE = [space_out(9), space_in(10), space_out(11), space_in(12)]


def household(directory, order, space_side) -> Store:
    return corpus(
        directory,
        order,
        main=[*main_feed(), *MAIN_SIDE],
        space=[*space_feed(), *space_side],
        export_rows=BASE_EXPORT,
        aggregator=[],
    )


@pytest.fixture
def stores(tmp_path):
    opened: list[Store] = []

    def build(order, space_side) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = household(directory, order, space_side)
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


CASES = [pytest.param(order, id=order_id(order)) for order in ORDERS]


def on_the_day(report):
    return [
        (f.from_account, f.to_account, f.day.day, f.leaving, f.arriving)
        for f in report.chain_faults
        if f.day.day == DAY
    ]


class TestTheOwnersFourMovements:
    @pytest.mark.parametrize("order", CASES)
    def test_Chain_WhenBothSidesHoldAllFourMovements_NoFaultInAnyArrivalOrder(self, stores, order):
        report = movement_completeness(stores(order, SPACE_SIDE), canonical)

        assert on_the_day(report) == []
        assert report.chain_faults == []

    @pytest.mark.parametrize("order", CASES)
    def test_Chain_WhenTheSpaceHoldsOnlyOneInAndOneOut_NamesBothDirectionsAndTheDay(
        self, stores, order
    ):
        report = movement_completeness(stores(order, [SPACE_SIDE[0], SPACE_SIDE[1]]), canonical)

        assert set(on_the_day(report)) == {
            (BILLS, MAIN, DAY, 1, 2),
            (MAIN, BILLS, DAY, 2, 1),
        }
        said = [f.says() for f in report.chain_faults if f.day.day == DAY]
        assert (
            f"2026-09-06 {MAIN} to {BILLS}: 2 leave, 1 arrive: 1 transfer leg, unpaired" in said
        )

    @pytest.mark.parametrize("order", CASES)
    def test_Chain_WhenTheSpaceHoldsNone_NamesBothDirectionsAndTheDay(self, stores, order):
        report = movement_completeness(stores(order, []), canonical)

        assert set(on_the_day(report)) == {
            (BILLS, MAIN, DAY, 0, 2),
            (MAIN, BILLS, DAY, 2, 0),
        }


def leg(
    entity: str,
    account: str,
    minor: int,
    names: str,
    when: str | None,
    day: date = date(2026, 9, 6),
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description="x",
        source="starling",
        source_id=entity,
        content_key=entity,
        tier=SourceTier.AUTHORITATIVE,
        status=TransactionStatus.BOOKED,
        is_internal_transfer=True,
        entity_id=entity,
        raw={"counterPartyUid": names} | ({} if when is None else {"transactionTime": when}),
    )


NAMES = {"cat-main": MAIN, "cat-bills": BILLS, "cat-holiday": HOLIDAY}.get


def chain() -> list[Transaction]:
    """Main to Bills, Bills to main, main to Holiday; the payment on from Holiday is no leg."""
    return [
        leg("1-out", MAIN, -500, "cat-bills", "2026-09-06T09:00:00Z"),
        leg("1-in", BILLS, 500, "cat-main", "2026-09-06T09:00:01Z"),
        leg("2-out", BILLS, -300, "cat-main", "2026-09-06T10:00:00Z"),
        leg("2-in", MAIN, 300, "cat-bills", "2026-09-06T10:00:01Z"),
        leg("3-out", MAIN, -200, "cat-holiday", "2026-09-06T11:00:00Z"),
        leg("3-in", HOLIDAY, 200, "cat-main", "2026-09-06T11:00:01Z"),
    ]


class TestAChainOfTransfers:
    def test_Chain_WhenEveryHopIsOnBothSides_NoFaultWhateverOrderTheLegsArriveIn(self):
        rows = chain()
        for order in itertools.islice(itertools.permutations(rows), 0, None, 7):
            compared, faults = check_chains(order, NAMES)
            assert faults == []
        # Three hops, each on one day.
        assert compared == 3

    def test_Chain_WhenOneHopIsMissingOnOneSide_NamesTheTwoAccountsAndTheDay(self):
        rows = [r for r in chain() if r.entity_id != "2-in"]
        for order in itertools.islice(itertools.permutations(rows), 0, None, 5):
            _, faults = check_chains(order, NAMES)

            (fault,) = faults
            assert (fault.from_account, fault.to_account, fault.day) == (
                BILLS,
                MAIN,
                date(2026, 9, 6),
            )
            assert (fault.leaving, fault.arriving) == (1, 0)

    def test_Chain_WhenTheSidesAreTheSameNumberButNotTheSameSizes_SaysSo(self):
        rows = [
            leg("a", MAIN, -100, "cat-bills", "2026-09-06T09:00:00Z"),
            leg("b", BILLS, 101, "cat-main", "2026-09-06T09:00:01Z"),
        ]

        _, faults = check_chains(rows, NAMES)

        assert [f.says() for f in faults] == [
            f"2026-09-06 {MAIN} to {BILLS}: 1 leave, 1 arrive "
            "- the same number, but not of the same sizes: 2 transfer legs, neither paired"
        ]


class TestAMovementCrossingMidnight:
    def test_Chain_WhenTheArrivalIsStampedSecondsAfterMidnight_IsOneMovement(self):
        rows = [
            leg("out", MAIN, -100, "cat-bills", "2026-09-06T23:59:58Z", day=date(2026, 9, 6)),
            leg("in", BILLS, 100, "cat-main", "2026-09-07T00:00:01Z", day=date(2026, 9, 7)),
        ]

        _, faults = check_chains(rows, NAMES)

        assert faults == []

    def test_Chain_WhenTheArrivalComesHoursLater_IsNotTheSameMovement(self):
        rows = [
            leg("out", MAIN, -100, "cat-bills", "2026-09-06T23:00:00Z", day=date(2026, 9, 6)),
            leg("in", BILLS, 100, "cat-main", "2026-09-07T01:00:00Z", day=date(2026, 9, 7)),
        ]

        _, faults = check_chains(rows, NAMES)

        assert {(f.from_account, f.day.day, f.leaving, f.arriving) for f in faults} == {
            (MAIN, 6, 1, 0),
            (MAIN, 7, 0, 1),
        }

    def test_Chain_WhenAnotherArrivalOfTheSameSizeFollowsTheNextDay_ItDoesNotHideAMissingOne(self):
        rows = [
            leg("out-1", MAIN, -100, "cat-bills", "2026-09-06T09:00:00Z", day=date(2026, 9, 6)),
            leg("out-2", MAIN, -100, "cat-bills", "2026-09-07T09:00:00Z", day=date(2026, 9, 7)),
            leg("in-2", BILLS, 100, "cat-main", "2026-09-07T09:00:01Z", day=date(2026, 9, 7)),
        ]

        _, faults = check_chains(rows, NAMES)

        assert [(f.day.day, f.leaving, f.arriving) for f in faults] == [(6, 1, 0)]

    def test_Chain_WhenLegsCarryNoTimeAndFallOnNeighbouringDays_AreJudgedByThePairingWindow(self):
        rows = [
            leg("out", MAIN, -100, "cat-bills", None, day=date(2026, 9, 6)),
            leg("in", BILLS, 100, "cat-main", None, day=date(2026, 9, 7)),
        ]

        _, faults = check_chains(rows, NAMES)

        assert faults == []


def test_Chain_WhenALegNamesNoAccountThatCanBeResolved_IsNotCompared():
    rows = [leg("out", MAIN, -100, "cat-unknown", None)]

    assert check_chains(rows, NAMES) == (0, [])


def test_Chain_WhenAPaymentToAThirdPartyFollowsTheHops_IsNoLegAndIsNotCompared():
    leg_like = leg("pay", HOLIDAY, -200, "cat-main", None)
    payment = replace(leg_like, is_internal_transfer=False, raw={})

    assert check_chains([*chain(), payment], NAMES) == check_chains(chain(), NAMES)
