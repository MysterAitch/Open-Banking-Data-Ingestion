"""The bank's own feed clears the rows it lists, measured on rows the provider really makes.

The deployed Overview showed 4,104 cleared and 1,174 not for the main account, with half of some
months uncleared, and the sightings read "seen by starling, truelayer". The registry named the
feed's ARTEFACT source (`starling-feed`), and a row and its sighting carry the source the PROVIDER
gives them (`starling`), so no feed-only row was ever cleared. The earlier tests passed because
they planted rows under an invented source name that no door produces.

Here every row is made by `providers.starling.to_transactions` from an invented feed payload,
landed as the artefact a pull lands, and read back through `rebuild_from_raw`.

KNOWN ANSWERS (September 2026, `round_up_corpus` amounts; no export is imported, so the feed is the
only lister):

    f-deposit   booked IN                           cleared
    f-coffee    booked payment, round-up 50         cleared, and its leg (the leg is derived
                                                    from the same listed item) is cleared
    f-parcel    PENDING payment, round-up 30        neither the payment nor its leg is cleared
    f-topup     the ordinary transfer, booked       cleared
"""

from __future__ import annotations

import json

import pytest

from obdi.clearing import cleared_entity_ids
from obdi.core.namespaces import API_SOURCES, CLEARING_SOURCES, FILE_SOURCES
from obdi.ingest.parsers import pdf_statements, qif, uk_banks
from obdi.ingest.parsers.base import StatementParser
from obdi.ingest.rebuild import parse_artefact_transactions, rebuild_from_raw
from obdi.ingest.store import Store
from obdi.ledger import build_ledger
from round_up_corpus import (
    card_payment,
    deposit_item,
    land_feed,
    round_up_of,
)
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP

PARSER_MODULES = (pdf_statements, qif, uk_banks)


@pytest.fixture
def feed_only(tmp_path):
    with Store(tmp_path / "feed-only.sqlite3") as store:
        land_evidence(store)
        land_feed(
            store,
            [
                deposit_item(),
                card_payment("f-coffee", "Coffee", 350, 3, round_up=round_up_of(50)),
                card_payment(
                    "f-parcel", "Parcel", 1200, 6, round_up=round_up_of(30), status="PENDING"
                ),
            ],
            origin=FEED_ORIGIN,
        )
        assert rebuild_from_raw(store, account_map=MAP).problems == []
        yield store


def by_description(store: Store) -> dict[str, str]:
    return {t.description: t.entity_id for t in store.transactions_for_account(MAIN)}


def legs_of(store: Store, source_id: str):
    return [t for t in store.transactions_for_account(MAIN) if t.source_id == source_id]


class TestTheBanksOwnFeed:
    def test_FeedOnlyRow_WhenBooked_IsCleared(self, feed_only):
        cleared = cleared_entity_ids(feed_only)
        rows = by_description(feed_only)

        assert rows["Coffee"] in cleared
        assert rows["Opening deposit"] in cleared

    def test_FeedOnlyRow_WhenPending_IsNotCleared(self, feed_only):
        assert by_description(feed_only)["Parcel"] not in cleared_entity_ids(feed_only)

    def test_RoundUpLeg_WhenItsPaymentIsBooked_IsClearedAsTheListedItemItCameFrom(self, feed_only):
        cleared = cleared_entity_ids(feed_only)

        legs = legs_of(feed_only, "f-coffee:round-up")

        assert [t.entity_id in cleared for t in legs] == [True]

    def test_RoundUpLeg_WhenItsPaymentIsPending_IsNotCleared(self, feed_only):
        cleared = cleared_entity_ids(feed_only)

        legs = legs_of(feed_only, "f-parcel:round-up")

        assert [t.entity_id in cleared for t in legs] == [False]

    def test_Ledger_WhenOnlyTheFeedListsTheRows_CountsThemClearedByTheProvidersName(
        self, feed_only
    ):
        ledger = build_ledger(feed_only, MAIN, "2026-09", bound=False)

        assert ledger.clearing is not None
        assert ledger.clearing.cleared > 0
        coffee = [r for r in ledger.rows if r.description == "Coffee"]
        assert [r.cleared_by for r in coffee] == [("starling",)]


def invented_row_sources() -> set[str]:
    """Every `source` a row can carry, taken from what the parsers and the providers do.

    A parser declares its own; a provider's rows are made by running its door on an invented
    payload, because a provider names no source until it builds a row.
    """
    assert all(PARSER_MODULES), "each parser module is imported, so its classes are subclasses"
    found: set[str] = set()
    pending = list(StatementParser.__subclasses__())
    while pending:
        parser = pending.pop()
        pending.extend(parser.__subclasses__())
        declared = parser.__dict__.get("source")
        if isinstance(declared, str):
            found.add(declared)
    feed = json.dumps({"feedItems": [card_payment("g-1", "Cafe", 100, 3)]}).encode()
    rows = parse_artefact_transactions("starling-feed", feed, MAIN, "digest-feed")
    found.update(row.source for row in rows)
    for kind, amount in (("truelayer-booked", -1.0), ("truelayer-card-booked", 1.0)):
        record = {
            "transaction_id": "tl-1",
            "normalised_provider_transaction_id": "tl-n1",
            "timestamp": "2026-09-03T10:00:00Z",
            "amount": amount,
            "currency": "GBP",
            "transaction_type": "DEBIT",
            "description": "Cafe",
        }
        rows = parse_artefact_transactions(
            kind, json.dumps({"results": [record]}).encode(), MAIN, "digest-tl"
        )
        found.update(row.source for row in rows)
    return found


class TestTheRegistry:
    def test_ClearingSources_EveryNameIsASourceSomeParserOrProviderGivesARow(self):
        given = invented_row_sources()

        unreachable = sorted(CLEARING_SOURCES - given)
        assert unreachable == [], (
            "a name here that no parser or provider gives a row clears nothing: "
            "the registry once named the feed's artefact source, which no row carries"
        )

    def test_ClearingSources_TheFeedArtefactSourceIsNotAMemberBecauseNoRowCarriesIt(self):
        assert "starling-feed" in API_SOURCES
        assert "starling-feed" not in CLEARING_SOURCES
        assert "starling" in CLEARING_SOURCES

    def test_ClearingSources_TheAggregatorsRowSourceStillDoesNotClear(self):
        assert "truelayer" in invented_row_sources()
        assert "truelayer" not in CLEARING_SOURCES

    def test_ClearingSources_EveryFileFormatThatGivesRowsClears(self):
        listers = FILE_SOURCES - {"statement"}

        assert listers <= CLEARING_SOURCES
        assert listers <= invented_row_sources()
