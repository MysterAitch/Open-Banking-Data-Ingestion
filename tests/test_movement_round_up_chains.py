"""The chain check counts a round-up's arrival, which names no account, as the other side.

The deployed Identity health page, Movements section, said:

    2242 legs: 1630 verified against the account each names; 612 name no account that can be
    resolved, so their partner's account cannot be verified
    every leg has its partner
    684 account-pair days compared
    2019-01-21 starling-personal to starling-space-money: 1 leave, 0 arrive
    ... and 211 more

and the Overview raised the 231 days as "Data at risk". The round-up household
(`round_up_corpus`) is the invented stand-in: a round-up's OUT leg in the main account names the
Space, and the Space's own arriving item can name no account that resolves, so the chain check
filed the leaving leg and never counted the arrival. The corpus's own arrivals name the main
account, which is a shape the real feed was not shown to have, so these scenarios vary them to
name nothing.

KNOWN ANSWERS (four round-ups, on days 3, 5, 8, and 12, each paired with its arrival; the
ordinary transfer on day 2 names both sides):

    arrivals name no account, every pairing held         no chain fault, in every arrival order
                                                         every leg has its partner
                                                         the Overview raises no movement item
    the day-5 arrival missing from the Space
        one fault, 5 September, main to Bills: "1 leave, 0 arrive: 1 round-up leg, unpaired"
        and check 2 files the leg as having no partner
    the day-2 transfer's Space side missing
        one fault, 2 September, main to Bills: "1 leave, 0 arrive: 1 transfer leg, unpaired"

PLANTED, because no importer makes it (the pairing pass only pairs a leg that names an account
with a leg held in that account): a nameless arrival paired with a leg that names ANOTHER account,
and two named legs paired but stamped half an hour apart.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from obdi.movement_completeness import NO_PARTNER, check_chains, movement_completeness
from round_up_corpus import card_payment, main_feed, space_arrival
from test_movement_chains import NAMES, leg
from test_movement_pages import overview_of
from test_space_attribution import BILLS, HOLIDAY, MAIN, MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    corpus,
    order_id,
)

SEPTEMBER_5 = date(2026, 9, 5)


def canonical(ref: str) -> str:
    return str(MAP.resolve(*ref.split(":", 1))) if ":" in ref else ref


def space_feed_naming_no_account(without: tuple[str, ...] = ()):
    """The Space's feed as the corpus has it, with each round-up's arrival naming nothing."""
    arrivals = [
        ("s-topup", 10000, 2, {}),
        ("s-coffee", 50, 3, {"counterPartyUid": ""}),
        ("s-grocer", 90, 5, {"counterPartyUid": ""}),
        ("s-taxi", 25, 8, {"counterPartyUid": ""}),
        ("s-lunch", 20, 12, {"counterPartyUid": ""}),
    ]
    items = [space_arrival(uid, minor, day, **more) for uid, minor, day, more in arrivals]
    items.insert(4, card_payment("s-hotel", "Hotel", 3000, 10))
    return [item for item in items if item["feedItemUid"] not in without]


@pytest.fixture
def stores(tmp_path):
    opened = []

    def build(order, without: tuple[str, ...] = ()):
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = corpus(
            directory,
            order,
            main=main_feed(),
            space=space_feed_naming_no_account(without),
            export_rows=BASE_EXPORT,
            aggregator=[],
        )
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


CASES = [pytest.param(order, id=order_id(order)) for order in ORDERS]


class TestRoundUpsWhoseArrivalNamesNoAccount:
    @pytest.mark.parametrize("order", CASES)
    def test_Chain_WhenEveryRoundUpIsPairedWithItsArrival_NoFaultInAnyArrivalOrder(
        self, stores, order
    ):
        report = movement_completeness(stores(order), canonical)

        assert report.legs_unverifiable == 4, "the four arrivals name no account"
        assert report.leg_faults == []
        assert report.chain_faults == []
        assert "both sides agree every day" in report.describe()

    def test_Overview_WhenEveryRoundUpIsPaired_RaisesNoMovementItem(self, stores):
        items = [
            i
            for i in overview_of(stores(ORDERS[0])).items
            if i.kind == "movement-completeness"
        ]

        assert items == []

    def test_Chain_WhenARoundUpsArrivalIsMissing_TheLeavingLegIsStillAFault(self, stores):
        report = movement_completeness(stores(ORDERS[0], without=("s-grocer",)), canonical)

        assert [f.says() for f in report.chain_faults] == [
            f"2026-09-05 {MAIN} to {BILLS}: 1 leave, 0 arrive: 1 round-up leg, unpaired"
        ]
        assert [(f.day, f.kind, f.what) for f in report.leg_faults] == [
            (SEPTEMBER_5, NO_PARTNER, "round-up leg")
        ]

    def test_Chain_WhenAnOrdinaryTransfersSpaceSideIsMissing_SaysItIsATransferLeg(self, stores):
        report = movement_completeness(stores(ORDERS[0], without=("s-topup",)), canonical)

        assert [f.says() for f in report.chain_faults] == [
            f"2026-09-02 {MAIN} to {BILLS}: 1 leave, 0 arrive: 1 transfer leg, unpaired"
        ]


def round_up_out() -> object:
    out = leg("ru-out", MAIN, -50, "cat-bills", "2026-09-06T09:00:00Z")
    return replace(out, raw={**out.raw, "roundUpOf": "payment"})


def nameless_in(account: str, entity: str = "ru-in"):
    return leg(entity, account, 50, "", "2026-09-06T09:00:01Z")


class TestAPairedLegThatNamesNoAccount:
    def test_Chain_WhenTheNamelessLegIsPairedWithALegNamingItsAccount_IsTheOtherSide(self):
        rows = [round_up_out(), nameless_in(BILLS)]

        _, faults = check_chains(rows, NAMES, [("ru-out", "ru-in")])

        assert faults == []

    def test_Chain_WhenTheNamelessLegHasNoPair_TheLeavingLegIsStillAFault(self):
        rows = [round_up_out(), nameless_in(BILLS)]

        _, faults = check_chains(rows, NAMES, [])

        assert [f.says() for f in faults] == [
            f"2026-09-06 {MAIN} to {BILLS}: 1 leave, 0 arrive: 1 round-up leg, unpaired"
        ]

    def test_Chain_WhenTheNamelessLegSitsInAnotherAccountThanThePartnerNames_IsNotTheOtherSide(
        self,
    ):
        rows = [round_up_out(), nameless_in(HOLIDAY)]

        _, faults = check_chains(rows, NAMES, [("ru-out", "ru-in")])

        assert [f.says() for f in faults] == [
            f"2026-09-06 {MAIN} to {BILLS}: 1 leave, 0 arrive: "
            f"1 round-up leg, paired, its partner in {HOLIDAY}"
        ]

    def test_Chain_WhenTheNamelessLegsPartnerNamesNothingEither_NeitherIsCompared(self):
        rows = [
            leg("a", MAIN, -50, "", "2026-09-06T09:00:00Z"),
            nameless_in(BILLS, "b"),
        ]

        assert check_chains(rows, NAMES, [("a", "b")]) == (0, [])


class TestAFaultExplainsItself:
    def test_Says_WhenBothSidesAreNamedPairedAndStampedFarApart_NamesWhereThePartnersSit(self):
        rows = [
            leg("out", MAIN, -100, "cat-bills", "2026-09-06T09:00:00Z"),
            leg("in", BILLS, 100, "cat-main", "2026-09-06T09:30:00Z"),
        ]

        _, faults = check_chains(rows, NAMES, [("out", "in")])

        assert [f.says() for f in faults] == [
            f"2026-09-06 {MAIN} to {BILLS}: 1 leave, 1 arrive "
            "- the same number, but not of the same sizes: "
            f"2 transfer legs, both paired, their partners in {MAIN} and {BILLS}"
        ]

    def test_Says_WhenRoundUpAndTransferLegsAreMixed_CountsEachKind(self):
        rows = [
            round_up_out(),
            leg("t-out", MAIN, -200, "cat-bills", "2026-09-06T11:00:00Z"),
        ]

        _, faults = check_chains(rows, NAMES, [])

        assert [f.says() for f in faults] == [
            f"2026-09-06 {MAIN} to {BILLS}: 2 leave, 0 arrive: "
            "1 round-up leg and 1 transfer leg, neither paired"
        ]
