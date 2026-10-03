"""A card payment's round-up is money leaving the main account for a Space.

Starling reports a card payment once, in the main category, at the payment's own
amount, with a `roundUp` naming the Space and the spare change. The Space's feed
reports the money arriving. Nothing in the main category reports it leaving, so
a main account built from the feed alone was over by every round-up ever made,
and the whole-account balances stated by a Space-blind export disagreed with the
family's rows by the same sum.

The corpus and its hand-worked answer are `round_up_corpus`.
"""

from __future__ import annotations

import pytest

from obdi.balance_anchors import effective_opening
from obdi.family_anchors import (
    families_of,
    feed_round_ups,
    round_up_tally,
    unheld_space_legs,
)
from obdi.ingest import pair_transfers_across_store
from obdi.models import TransactionStatus
from obdi.providers import starling
from obdi.rebuild import rebuild_from_raw
from round_up_corpus import (
    MAIN_BALANCE,
    ROUND_UPS,
    SPACE_BALANCE,
    card_payment,
    counted,
    household_store,
    land_feed,
    main_feed,
    round_up_of,
    space_arrival,
)
from round_up_corpus import balance as balance_of
from test_export_dating import render
from test_family_anchors import FEED_ORIGIN
from test_space_attribution import BILLS, MAIN, MAP


@pytest.fixture
def rebuilt(tmp_path):
    store = household_store(tmp_path)
    report = rebuild_from_raw(store, account_map=MAP)
    assert report.problems == []
    yield store
    store.close()


def walk_of(store):
    opening = effective_opening(store, MAIN, families=families_of(store, MAP))
    assert opening.family is not None
    return opening.family


class TestAFamilyWhoseCardPaymentsCarryRoundUps:
    def test_StatedBalances_WhenEveryPaymentCarriesItsRoundUp_AllAgreeWithTheFamilysRows(
        self, rebuilt
    ):
        walk = walk_of(rebuilt)

        assert walk.readings
        assert walk.differing == []
        assert {reading.agrees for reading in walk.readings} == {True}

    def test_MainBalance_WhenPaymentsCarryRoundUps_IsThePaymentsPlusTheRoundUpsOut(self, rebuilt):
        assert balance_of(rebuilt, MAIN) == MAIN_BALANCE == 84080

    def test_SpaceBalance_WhenPaymentsCarryRoundUps_IsTheRoundUpsIn(self, rebuilt):
        assert balance_of(rebuilt, BILLS) == SPACE_BALANCE == 7185
        assert ROUND_UPS == 185

    def test_RoundUps_WhenBothFeedsAreHeld_AreOneConfirmedTransferPairEach(self, rebuilt):
        pairs = rebuilt.confirmed_transfer_pairs()
        by_entity = {t.entity_id: t for ref in (MAIN, BILLS) for t in counted(rebuilt, ref)}

        # Four round-ups and the ordinary transfer, which keeps exactly one pair.
        assert len(pairs) == 5
        for debit, credit in pairs:
            assert by_entity[debit].account_id == MAIN
            assert by_entity[credit].account_id == BILLS
            assert by_entity[debit].amount_minor == -by_entity[credit].amount_minor

    def test_Payments_WhenTheyCarryRoundUps_KeepTheirOwnAmountForTheExportToMatch(self, rebuilt):
        amounts = sorted(
            t.amount_minor for t in counted(rebuilt, MAIN) if not t.is_internal_transfer
        )

        assert amounts == [-2310, -1275, -1000, -800, -350, 100000]

    def test_Payments_WhenTheExportAlsoListsThem_AreSightedByBothSources(self, rebuilt):
        (coffee,) = [t for t in counted(rebuilt, MAIN) if t.amount_minor == -350]

        assert set(rebuilt.sources_for(coffee.entity_id)) == {"starling", "starling-csv"}

    def test_Rebuild_WhenRunAgain_HoldsTheSameLegsWithTheSameIdentities(self, rebuilt):
        before = sorted(
            (t.account_id, str(t.source_id), t.amount_minor, t.occurrence)
            for ref in (MAIN, BILLS)
            for t in counted(rebuilt, ref)
        )

        rebuild_from_raw(rebuilt, account_map=MAP)
        after = sorted(
            (t.account_id, str(t.source_id), t.amount_minor, t.occurrence)
            for ref in (MAIN, BILLS)
            for t in counted(rebuilt, ref)
        )

        assert before == after


class TestTheLegAFeedItemYields:
    def legs(self, item):
        rows = starling.to_transactions(item, account_id=MAIN)
        return rows[0], rows[1:]

    def test_Payment_WhenItCarriesAReadableRoundUp_YieldsAnOutLegToTheSpaceBesideIt(self):
        payment, legs = self.legs(card_payment("u-1", "Cafe", 450, 4, round_up=round_up_of(50)))

        (leg,) = legs
        assert payment.amount_minor == -450
        assert (leg.amount_minor, leg.value_date, leg.account_id) == (
            -50,
            payment.value_date,
            MAIN,
        )
        assert leg.is_internal_transfer
        assert leg.raw["counterPartyType"] == "CATEGORY"
        assert leg.raw["counterPartyUid"] == "cat-bills"
        assert leg.source == "starling"
        assert leg.status is payment.status

    def test_Leg_WhenTheItemIsReadTwice_HasTheSameIdentityDerivedFromTheItemsUid(self):
        item = card_payment("u-1", "Cafe", 450, 4, round_up=round_up_of(50))

        first = self.legs(item)[1][0]
        second = self.legs(item)[1][0]

        assert first.source_id == second.source_id
        assert first.source_id is not None
        assert "u-1" in first.source_id
        assert first.source_id != "u-1"

    @pytest.mark.parametrize(
        "round_up",
        [
            pytest.param(round_up_of(0), id="zero"),
            pytest.param({"goalCategoryUid": "cat-bills"}, id="no-amount"),
            pytest.param({"goalCategoryUid": "cat-bills", "amount": None}, id="null-amount"),
        ],
    )
    def test_Payment_WhenTheRoundUpIsNothing_YieldsNoLeg(self, round_up):
        _, legs = self.legs(card_payment("u-1", "Cafe", 450, 4, round_up=round_up))

        assert legs == []

    @pytest.mark.parametrize(
        "round_up",
        [
            pytest.param("fifty pence", id="not-an-object"),
            pytest.param(
                {"goalCategoryUid": "cat-bills", "amount": {"minorUnits": "x"}}, id="text"
            ),
            pytest.param(
                {"goalCategoryUid": "cat-bills", "amount": {"currency": "GBP", "minorUnits": 5.5}},
                id="fractional",
            ),
            pytest.param(
                {"goalCategoryUid": "cat-bills", "amount": {"currency": "EUR", "minorUnits": 50}},
                id="foreign",
            ),
            pytest.param(
                {"goalCategoryUid": "cat-bills", "amount": {"currency": "GBP", "minorUnits": -50}},
                id="negative",
            ),
            pytest.param(
                {"amount": {"currency": "GBP", "minorUnits": 50}}, id="no-space-named"
            ),
        ],
    )
    def test_Payment_WhenTheRoundUpCannotBeRead_YieldsNoLegAndIsCountedUnreadable(self, round_up):
        payment, legs = self.legs(card_payment("u-1", "Cafe", 450, 4, round_up=round_up))

        assert legs == []
        assert payment.amount_minor == -450
        assert starling.round_up_of(payment.raw).unreadable

    def test_Payment_WhenItCarriesNoRoundUp_IsNotCountedAsCarryingOne(self):
        payment, legs = self.legs(card_payment("u-1", "Cafe", 450, 4))

        assert legs == []
        assert not starling.round_up_of(payment.raw).carried

    def test_Refund_WhenItCarriesARoundUp_YieldsNoLeg(self):
        # What Starling does with the round-up of a refunded purchase is not
        # documented where the schema could be found, so no leg is invented.
        refund = card_payment(
            "u-1", "Cafe", 450, 4, round_up=round_up_of(50), direction="IN", status="REFUNDED"
        )

        _, legs = self.legs(refund)

        assert legs == []

    def test_Payment_WhenDeclined_YieldsNoPaymentRowAndOnlyTheBookedLeg(self):
        declined = card_payment("u-1", "Cafe", 450, 4, round_up=round_up_of(50), status="DECLINED")

        (leg,) = starling.to_transactions(declined, account_id=MAIN)

        assert (leg.source_id, leg.amount_minor) == ("u-1:round-up", -50)
        assert leg.status is TransactionStatus.BOOKED

    def test_Payment_WhenDeclinedAndCarryingNoRoundUp_YieldsNothingAtAll(self):
        declined = card_payment("u-1", "Cafe", 450, 4, status="DECLINED")

        assert starling.to_transactions(declined, account_id=MAIN) == []

    def test_TransferItem_WhenItAlreadyIsTheMainSideOfATransfer_GainsNoSecondLeg(self):
        transfer = card_payment(
            "u-1",
            "Holiday",
            5000,
            4,
            source="INTERNAL_TRANSFER",
            counterPartyType="CATEGORY",
            counterPartyUid="cat-bills",
        )

        assert len(starling.to_transactions(transfer, account_id=MAIN)) == 1

    def test_SpaceArrival_WhenItIsAnOrdinaryTransferIn_YieldsOnlyItself(self):
        arrival = space_arrival("s-1", 5000, 4)

        assert len(starling.to_transactions(arrival, account_id=BILLS)) == 1

    def test_SpaceItem_WhenItCarriesARoundUpKey_YieldsNoLeg(self):
        arrival = space_arrival("s-1", 50, 4, roundUp=round_up_of(50))

        assert len(starling.to_transactions(arrival, account_id=BILLS)) == 1

    def test_Leg_WhenThePaymentIsPending_IsPendingAndSettlesIntoOneLeg(self, tmp_path):
        store = household_store(
            tmp_path,
            [
                card_payment("f-hold", "Cafe", 450, 4, round_up=round_up_of(50), status="PENDING"),
            ],
        )
        land_feed(
            store,
            [card_payment("f-hold", "Cafe", 450, 4, round_up=round_up_of(50))],
            origin=FEED_ORIGIN,
            asked="2026-09-06T00:00:00Z",
        )
        try:
            rebuild_from_raw(store, account_map=MAP)

            legs = [t for t in counted(store, MAIN) if t.amount_minor == -50]
            assert len(legs) == 1
            assert legs[0].status is TransactionStatus.BOOKED
        finally:
            store.close()

    def test_Leg_WhenTheSameItemIsFetchedAgain_IsNotDuplicated(self, tmp_path):
        store = household_store(tmp_path)
        land_feed(store, main_feed(), origin=FEED_ORIGIN, asked="2026-09-09T00:00:00Z")
        try:
            rebuild_from_raw(store, account_map=MAP)

            assert len([t for t in counted(store, MAIN) if t.amount_minor == -50]) == 1
            assert balance_of(store, MAIN) == MAIN_BALANCE
        finally:
            store.close()


class TestARoundUpToASpaceTheStoreDoesNotHold:
    def test_Leg_WhenTheSpaceIsNotHeld_IsReportedAsALegToAnUnheldSpace(self):
        gone = starling.to_transactions(
            card_payment("u-1", "Cafe", 450, 4, round_up=round_up_of(50, "cat-gone")),
            account_id=MAIN,
        )

        found = unheld_space_legs(gone, frozenset({"cat-main", "cat-bills"}))

        assert found.legs == 1
        assert found.uids == ("cat-gone",)

    def test_Leg_WhenTheSpaceIsHeld_IsNotReportedAsUnheld(self):
        held = starling.to_transactions(
            card_payment("u-1", "Cafe", 450, 4, round_up=round_up_of(50)), account_id=MAIN
        )

        assert unheld_space_legs(held, frozenset({"cat-main", "cat-bills"})).legs == 0


class TestWhatTheLedgerSaysOfRoundUps:
    def test_Tally_WhenEveryRoundUpIsPaired_CountsCarriedHeldPairedAndNoneUnreadable(
        self, rebuilt
    ):
        tally = walk_of(rebuilt).round_ups

        # Five payments carry a round-up key; one of them is of nothing.
        assert (tally.carried, tally.legs, tally.paired, tally.unreadable) == (5, 4, 4, 0)

    def test_Tally_WhenTheExportSightsEveryPaymentAfterTheFeed_StillCountsTheirRoundUps(
        self, tmp_path
    ):
        store = household_store(tmp_path, export_last=True)
        try:
            rebuild_from_raw(store, account_map=MAP)

            tally = walk_of(store).round_ups

            assert (tally.carried, tally.legs, tally.paired, tally.unreadable) == (5, 4, 4, 0)
        finally:
            store.close()

    @pytest.mark.parametrize("export_last", [False, True])
    def test_FeedCount_WhicheverSourceArrivedFirst_CountsEachRoundUpTheFeedCarriesOnce(
        self, tmp_path, export_last
    ):
        store = household_store(tmp_path, export_last=export_last)
        try:
            rebuild_from_raw(store, account_map=MAP)
            # The same feed fetched again must not count its round-ups twice.
            land_feed(store, main_feed(), origin=FEED_ORIGIN, asked="2026-09-03T00:00:00Z")
            rebuild_from_raw(store, account_map=MAP)

            assert feed_round_ups(store, MAIN) == (5, 0)
        finally:
            store.close()

    def test_Tally_WhenNoStoredRowKeepsItsFeedItem_StillSaysWhatTheFeedCarries(self):
        # A payment an export reported first keeps the export's raw, so nothing
        # about its round-up can be read from the row.
        legs = [
            leg
            for item in main_feed()
            for leg in starling.to_transactions(item, account_id=MAIN)
            if "roundUpOf" in leg.raw
        ]

        tally = round_up_tally(legs, frozenset(), feed=(5, 1))

        assert (tally.carried, tally.legs, tally.paired, tally.unreadable) == (5, 4, 0, 1)

    def test_Tally_WhenTheFeedCarriesNoRoundUps_IsNilAcrossTheBoard(self, tmp_path):
        plain = [replace_item(item) for item in main_feed()]
        store = household_store(tmp_path, plain)
        try:
            rebuild_from_raw(store, account_map=MAP)

            tally = walk_of(store).round_ups

            assert (tally.carried, tally.legs, tally.paired, tally.unreadable) == (0, 0, 0, 0)
        finally:
            store.close()

    def test_Tally_WhenARoundUpCannotBeRead_CountsItUnreadableAndHoldsNoLeg(self, tmp_path):
        items = main_feed()
        (position,) = [i for i, item in enumerate(items) if item["feedItemUid"] == "f-coffee"]
        items[position] = card_payment("f-coffee", "Coffee", 350, 3, round_up="fifty pence")
        store = household_store(tmp_path, items)
        try:
            rebuild_from_raw(store, account_map=MAP)

            tally = walk_of(store).round_ups

            assert (tally.carried, tally.legs, tally.unreadable) == (5, 3, 1)
        finally:
            store.close()

    def test_Tally_WhenTheSpaceFeedIsNotHeld_CountsLegsHeldButNoneAsPaired(self, tmp_path):
        store = household_store(tmp_path, space=[])
        try:
            rebuild_from_raw(store, account_map=MAP)
            pair_transfers_across_store(store)

            tally = walk_of(store).round_ups

            assert (tally.legs, tally.paired) == (4, 0)
        finally:
            store.close()


class TestTheRoundUpSentenceOnTheMaskedLedgerPage:
    def test_Page_WhenEveryRoundUpIsPaired_SaysHowManyAreCarriedHeldPairedAndUnreadable(
        self, rebuilt
    ):
        page = render(rebuilt)

        assert (
            "5 feed row(s) carry a round-up. 4 round-up leg(s) to a Space are held, "
            "and 4 of them are paired with a row in that Space. "
            "0 could not be read and hold no leg."
        ) in page

    def test_Page_WhenTheFeedCarriesNoRoundUps_SaysSoInsteadOfStayingSilent(self, tmp_path):
        store = household_store(tmp_path, [replace_item(item) for item in main_feed()])
        try:
            rebuild_from_raw(store, account_map=MAP)

            page = render(store)

            assert "No row of the feed carries a round-up, so no round-up leg to a Space" in page
            assert "the feed does not report it in the shape this page reads" in page
        finally:
            store.close()

    def test_Page_WhenARoundUpCannotBeRead_CountsItWithoutAFigure(self, tmp_path):
        items = main_feed()
        (position,) = [i for i, item in enumerate(items) if item["feedItemUid"] == "f-coffee"]
        items[position] = card_payment("f-coffee", "Coffee", 350, 3, round_up="fifty pence")
        store = household_store(tmp_path, items)
        try:
            rebuild_from_raw(store, account_map=MAP)

            assert "1 could not be read and hold no leg." in render(store)
        finally:
            store.close()

    def test_MaskedPage_WhenRoundUpsAreHeld_ShowsNoFigure(self, rebuilt):
        page = render(rebuilt)

        for figure in ("84080", "840.80", "91265", "912.65", "7185", "71.85", "10000", "100.00"):
            assert figure not in page, figure


def replace_item(item):
    stripped = dict(item)
    stripped.pop("roundUp", None)
    return stripped


def test_Leg_WhenReplayedByTheShippedPushBuilder_TravelsAsALinkedTransfer(rebuilt):
    from obdi.actual_push import transactions_to_push
    from obdi.replay import ActualAccountBinding, build_transfer_pairs

    bindings = [
        ActualAccountBinding(MAIN, "act-main"),
        ActualAccountBinding(BILLS, "act-bills"),
    ]

    listed = build_transfer_pairs(
        transactions_to_push(rebuilt), bindings, rebuilt.confirmed_transfer_pairs()
    )

    assert len(listed) == 5
    debits = [entry["debit"] for entry in listed]
    credits = [entry["credit"] for entry in listed]
    assert sorted(leg["amount"] for leg in debits) == [-10000, -90, -50, -25, -20]
    assert {leg["account"] for leg in debits} == {"act-main"}
    assert {leg["account"] for leg in credits} == {"act-bills"}
