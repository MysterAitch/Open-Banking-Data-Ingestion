"""How a named row's feed item differs from the usual item like it, over invented feeds.

Two payments the bank's feed, the aggregator, and the bank's app all show as ordinary
settled card payments are absent from a statement and an export, so the status does not
tell them from money. This measures what else in the item might, in field names,
closed-set values, currencies, and times, and never a figure, a name, or free text.

The unlisted payment is `feed_morning_corpus.cafe_payment`, the one row the export omits,
so it is the row the change names. Its comparable items are the household's own card
payments (SETTLED, out, MASTER_CARD: six of them) plus a crowd the export lists, added
where a scenario needs a field or a value to be usual.

KNOWN ANSWERS, decided before the first run:

    the item identical in shape to the household's           "is like the usual one"
    80 crowd items carry settlementTime, the payment has none   lacks settlementTime
    94 crowd items carry country (91 GB, 3 DE), payment says DE country DE, carried by 3%
    payment's amount in GBP and its sourceAmount in EUR         names both currencies
    transaction time on day 9, settlement time on day 11        both times, 2 days apart
    landed pending with no settlementTime, then settled with    status changed from PENDING
        one                                                     to SETTLED; settlementTime appeared
    a source no other item has                                  too few comparable items
    a rare value in a field the registry withholds (a payee in  never on the page
        capitals, a reference, an unclassified field)
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from feed_morning_corpus import TOP_UP_LISTED, cafe_payment, top_up
from obdi.family_anchors import families_of
from obdi.feed_item_shape import MIN_COMPARABLE, THRESHOLDS
from obdi.rebuild import rebuild_from_raw
from round_up_corpus import card_payment, household_store, main_feed, space_feed
from test_export_cuts import Row
from test_feed_times import plain
from test_space_attribution import MAIN, MAP

LIKE = "Its feed item is like the usual one."
DIFFERS = "Its feed item differs from the usual SETTLED out MASTER_CARD item: "


@pytest.fixture
def make(tmp_path):
    opened = []

    def build(main=(), export_extra=(), **more):
        directory = tmp_path / f"store-{len(opened)}"
        directory.mkdir()
        store = household_store(
            directory,
            [*main_feed(), *main],
            space_feed(),
            export_extra=(TOP_UP_LISTED, *export_extra),
            **more,
        )
        opened.append(store)
        assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def crowd(count: int, **fields_for) -> tuple[list[dict[str, Any]], tuple[Row, ...]]:
    """`count` comparable payments the export lists, so only the unlisted one is named.

    `fields_for(index)` gives the extra fields of the item at that index.
    """
    items, rows = [], []
    for index in range(count):
        day = 1 + index % 8
        extra = fields_for["extra"](index) if "extra" in fields_for else {}
        items.append(card_payment(f"f-crowd-{index}", f"Crowd{index}", 100 + index, day, **extra))
        rows.append(Row(f"Crowd{index}", -(100 + index), day, day))
    return items, tuple(rows)


def page_of(store) -> str:
    from obdi.ledger import build_ledger
    from obdi.web_ledger import render_ledger

    ledger = build_ledger(store, MAIN, "2026-09", bound=False, families=families_of(store, MAP))
    return plain(render_ledger(ledger, unmasked=False).decode("utf-8"))


def sentence(page: str) -> str:
    """The shape sentence of the named row, or "" where the page has none."""
    found = re.search(r"Its feed item (?:is like|differs from|has \d+ other)[^<]*?\.(?= |$)", page)
    return found.group(0) if found else ""


class TestAnItemLikeTheUsualOne:
    def test_Row_WhenItsItemMatchesTheHouseholdsCardPayments_IsLikeTheUsualOne(self, make):
        page = page_of(make([cafe_payment(), top_up()]))

        assert LIKE in page
        assert "Its feed item differs" not in page

    def test_Page_WhenARowIsNamed_StatesTheThresholdsOnce(self, make):
        page = page_of(make([cafe_payment(), top_up()]))

        assert page.count(THRESHOLDS) == 1


class TestAnItemLackingAUsualField:
    def test_Row_WhenEveryComparableItemStatesASettlementTime_SaysItLacksIt(self, make):
        items, rows = crowd(80, extra=lambda i: {"settlementTime": "2026-09-02T10:00:00.000Z"})
        page = page_of(make([*items, cafe_payment(), top_up()], rows))

        assert DIFFERS + "lacks settlementTime." in page

    def test_Row_WhenHalfTheComparableItemsStateIt_ItIsNotUsualSoNothingIsLacked(self, make):
        items, rows = crowd(
            12, extra=lambda i: {"settlementTime": "2026-09-02T10:00:00.000Z"} if i % 2 else {}
        )
        page = page_of(make([*items, cafe_payment(), top_up()], rows))

        assert "lacks settlementTime" not in page


class TestAnItemWithARareClosedSetValue:
    def test_Row_WhenItCarriesACountryFewCarry_SaysTheValueAndHowRareItIs(self, make):
        items, rows = crowd(94, extra=lambda i: {"country": "DE" if i < 3 else "GB"})
        page = page_of(make([*items, cafe_payment(country="DE"), top_up()], rows))

        assert "country DE, carried by 3% of comparable items" in page

    def test_Row_WhenItCarriesTheCountryMostCarry_SaysNothingOfIt(self, make):
        items, rows = crowd(94, extra=lambda i: {"country": "GB"})
        page = page_of(make([*items, cafe_payment(country="GB"), top_up()], rows))

        assert "country" not in sentence(page)
        assert LIKE in page


class TestAForeignCurrencyItemAmongDomesticOnes:
    def foreign(self, currency: str) -> dict[str, Any]:
        return cafe_payment(sourceAmount={"currency": currency, "minorUnits": 4001})

    def test_Row_WhenTheSourceAmountIsInAnotherCurrency_NamesBothCurrencies(self, make):
        page = page_of(make([self.foreign("EUR"), top_up()]))

        assert "amount and sourceAmount name different currencies (GBP and EUR)" in page

    def test_Row_WhenTheSourceAmountIsInTheSameCurrency_SaysNothingOfCurrencies(self, make):
        page = page_of(make([self.foreign("GBP"), top_up()]))

        assert "different currencies" not in page


class TestAnItemStatingBothTimes:
    def test_Row_WhenSettlementIsDaysAfterTheTransaction_SaysHowManyWholeDays(self, make):
        late = cafe_payment(settlementTime="2026-09-11T05:11:00.000Z")
        page = page_of(make([late, top_up()]))

        assert "states both times, 2 days apart, as under 1% of comparable items do" in page

    def test_Row_WhenSettlementIsTheSameDay_SaysNothingOfTheGap(self, make):
        same = cafe_payment(settlementTime="2026-09-09T09:11:00.000Z")
        page = page_of(make([same, top_up()]))

        assert "days apart" not in page


class TestAnItemLandedInSeveralFetches:
    def test_Row_WhenItChangedBetweenLandings_SaysWhichFieldAndValueChanged(self, make):
        store = make(
            [cafe_payment(status="PENDING"), top_up()],
            main_refetches=(
                [cafe_payment(settlementTime="2026-09-09T06:00:00.000Z"), top_up()],
            ),
        )
        page = page_of(store)

        assert "status changed from PENDING to SETTLED" in page
        assert "field settlementTime appeared" in page

    def test_Row_WhenItLandedTwiceUnchanged_SaysNothingChanged(self, make):
        store = make([cafe_payment(), top_up()], main_refetches=([cafe_payment(), top_up()],))

        assert "between its first and latest landing" not in page_of(store)


class TestAnItemWithNothingComparable:
    def test_Row_WhenNoOtherItemHasItsSource_SaysThereAreTooFewToCompare(self, make):
        page = page_of(make([cafe_payment(source="OPEN_BANKING"), top_up()]))

        found = sentence(page)
        assert found.startswith("Its feed item has 0 other comparable items, too few")
        assert str(MIN_COMPARABLE) in THRESHOLDS


class TestNothingPrivateReachesThePage:
    def planted(self, **more: Any) -> dict[str, Any]:
        return cafe_payment(
            counterPartyName="PLANTEDPAYEE",
            reference="PLANTEDREFERENCE",
            userNote="planted free text note",
            mysteryField="PLANTEDUNKNOWN",
            sourceAmount={"currency": "EUR", "minorUnits": 876543},
            amount={"currency": "GBP", "minorUnits": 987654},
            spendingCategory="PLANTEDCATEGORY",
            **more,
        )

    def test_Page_WhenEveryFreeTextAndAmountFieldIsPlanted_NoneOfItIsOnThePage(self, make):
        items, rows = crowd(
            80,
            extra=lambda i: {
                "counterPartyName": "COMMONPAYEE",
                "reference": "COMMONREFERENCE",
                "mysteryField": "COMMONUNKNOWN",
                "spendingCategory": "EATING_OUT",
            },
        )
        page = page_of(make([*items, self.planted(), top_up()], rows))

        for secret in (
            "PLANTEDPAYEE",
            "PLANTEDREFERENCE",
            "planted free text note",
            "PLANTEDUNKNOWN",
            "987654",
            "9,876.54",
            "876543",
            "8,765.43",
            "COMMONPAYEE",
            "COMMONREFERENCE",
            "COMMONUNKNOWN",
        ):
            assert secret not in page, secret

    def test_Page_WhenARareValueIsInAFieldTheRegistryApproves_ItIsSaidAsTheContrast(self, make):
        items, rows = crowd(80, extra=lambda i: {"spendingCategory": "EATING_OUT"})
        page = page_of(make([*items, self.planted(), top_up()], rows))

        assert "spendingCategory PLANTEDCATEGORY, carried by under 1% of comparable items" in page
