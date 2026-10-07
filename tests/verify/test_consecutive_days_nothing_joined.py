"""A week of one payee paid one amount on consecutive days is never joined, and never lost.

The commonest pattern in real spending, and the one the join of two stored rows
(`ingest._absorb_second_row`) must leave alone: payment one's settlement day IS payment two's
transaction day, so the settlement rule names the OTHER payment's row for every payment
(`consecutive_days_corpus` has the week and its answer).

WHICH GUARD STOPS EACH TEMPTING PAIR (`matching.second_row_verdicts`), read by
`exact_rule_measure.pair_figures` and asserted in `test_exact_rule_pairs`: for every pair of
neighbouring payments the settlement day and the payee both name the neighbour's row, and
  - with an aggregator that states the feed's uids, the neighbour's row is one an id contradicts:
    the record's uid is not among the uids the neighbour's row was sighted under;
  - without uids, the neighbour's row is one the record's own source has already sighted.
Neither depends on the order the sources arrived in.

KNOWN ANSWERS, decided before the first run, in every arrival order, live and rebuilt, with the
feed fetched again and a second overlapping export:
    seven stored rows, one per payment, each sighted by all three sources
    each payment's feed uid on its own row, and the aggregator's item for it on that row
    nothing lost: seven feed uids, seven aggregator ids, seven export rows sighted
    rows listed equal rows held, and every stated balance agrees
THE EXPORT'S ROW ON ITS OWN PAYMENT is the answer in every order but one. An export row dated D
used to reach the payment MADE on D by content key before the payment SETTLED on D was looked for,
whenever the feed was already stored, so each export row sat on the next payment's row, and a
second overlapping export sighted another payment's row than the first did. A record listed on its
settlement day is now planned onto the row its day names (`matching.plan_settlement`), and a file
listing a row again finds the row its first listing joined.
THE ORDER STILL PINNED (`STILL_ON_THE_NEXT_PAYMENT`, measured): the aggregator, then the export,
then the feed, with an aggregator that states the feed's uids. The export arrives when no stored
row has a settlement day, so nothing can name its payment, and it joins the aggregator's row of
its own date by content key; the feed then finds each aggregator row by id and the export's
sighting stays where it was put. Nothing later moves a sighting.
"""

from __future__ import annotations

import itertools
import json
import pathlib
from collections.abc import Callable, Iterator
from datetime import date

import pytest

from consecutive_days_corpus import AMOUNT, consecutive_payments
from late_settlement_corpus import Payment, household
from obdi.ingest.providers import starling
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.verify.movement_completeness import check_rows
from round_up_corpus import main_feed
from test_absorbed_rows import ALL_THREE, arrive, second_export
from test_family_anchors import FEED_ORIGIN
from test_movement_chains import canonical
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import order_id, sources, walk_differences

ORDERS = list(itertools.permutations(("feed", "export", "aggregator")))
#: Measured, not reasoned (see the module docstring): where an export row still sits on the next
#: payment's row, as (aggregator states uids, order), in every shape.
STILL_ON_THE_NEXT_PAYMENT = {(True, ("aggregator", "export", "feed"))}
EXPORT_ON_THE_NEXT_PAYMENT = (
    "the export arrived before any stored row had a settlement day, so it joined the "
    "aggregator's row of its own date by content key, and the feed's later arrival joins by "
    "id and moves no sighting"
)
SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]
WEEK = consecutive_payments(7)


def repeat_the_feed(store: Store, payments: list[Payment]) -> None:
    """The feed fetched again: the same items in the other order, so the bytes are their own."""
    items = [*main_feed(), *(p.feed_item() for p in payments)][::-1]
    arrive(
        store,
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id="starling:cat-main",
            kind="feed",
            origin=f"{FEED_ORIGIN}?changesSince=2026-09-02T00:00:00Z",
        ),
    )


@pytest.fixture
def joins(monkeypatch) -> list[tuple[str, str]]:
    """Every join of two stored rows made while a test runs, as (kept, absorbed)."""
    made: list[tuple[str, str]] = []
    real = Store.absorb_entity

    def spy(self: Store, kept: str, absorbed: str) -> None:
        made.append((kept, absorbed))
        real(self, kept, absorbed)

    monkeypatch.setattr(Store, "absorb_entity", spy)
    return made


@pytest.fixture
def stores(tmp_path, joins) -> Iterator[Callable[..., Store]]:
    opened: list[Store] = []

    def build(order, payments, *, rebuild=False, again=False, linked=True) -> Store:
        here: pathlib.Path = tmp_path / f"d{len(opened)}"
        here.mkdir()
        store = household(here, order, payments, linked=linked)
        opened.append(store)
        if again:
            repeat_the_feed(store, payments)
            second_export(store, here, payments)
        if rebuild:
            assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def week_rows(store: Store):
    return [
        t
        for t in store.transactions_for_account(MAIN)
        if t.amount_minor == -AMOUNT and not t.status.is_history
    ]


def feed_uid_of(store: Store, row) -> str:
    (uid,) = store.stated_ids_by_entity()[1][row.entity_id]
    return uid


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
@pytest.mark.parametrize("linked", [True, False], ids=["uids-stated", "no-uids"])
@pytest.mark.parametrize("order", ORDERS, ids=order_id)
class TestAWeekOfEqualPaymentsOnConsecutiveDays:
    def test_Rows_WhenEachPaymentSettlesTheDayAfter_IsOneRowPerPaymentSeenByAllThree(
        self, stores, order, linked, rebuild, again
    ):
        store = stores(order, WEEK, rebuild=rebuild, again=again, linked=linked)

        rows = week_rows(store)

        assert len(rows) == 7
        assert [sources(store, t) for t in rows] == [ALL_THREE] * 7

    def test_Rows_WhenEachPaymentSettlesTheDayAfter_NoPaymentIsJoinedToAnotherOrLost(
        self, stores, joins, order, linked, rebuild, again
    ):
        store = stores(order, WEEK, rebuild=rebuild, again=again, linked=linked)

        assert joins == []
        rows = week_rows(store)

        assert sorted(feed_uid_of(store, t) for t in rows) == sorted(p.uid for p in WEEK)
        aggregator_ids = {
            sid
            for entity in (t.entity_id for t in rows)
            for source, sid in store.connection.execute(
                "SELECT source, source_id FROM transaction_sources WHERE entity_id = ?", (entity,)
            )
            if source == "truelayer"
        }
        assert len(aggregator_ids) == 7
        assert walk_differences(store) == {}

    def test_Rows_WhenEachPaymentSettlesTheDayAfter_RowsListedEqualRowsHeld(
        self, stores, order, linked, rebuild, again
    ):
        store = stores(order, WEEK, rebuild=rebuild, again=again, linked=linked)

        assert check_rows(store, canonical).row_faults == []

    def test_Aggregator_WhenItStatesTheDaysUids_SitsOnItsOwnPaymentsRow(
        self, stores, order, linked, rebuild, again
    ):
        if not linked:
            pytest.skip("with no uid the aggregator's pairing is the heuristic's, measured apart")
        store = stores(order, WEEK, rebuild=rebuild, again=again, linked=linked)

        days = store.sighting_days([MAIN], ["truelayer"])["truelayer"]

        for row in week_rows(store):
            made = int(feed_uid_of(store, row).rsplit("-", 1)[1])
            assert date.fromisoformat(days[row.entity_id][:10]) == date(2026, 9, made)

    def test_Export_WhenEachRowIsListedOnItsSettlementDay_SitsOnItsOwnPaymentsRow(
        self, request, stores, order, linked, rebuild, again
    ):
        if (linked, order) in STILL_ON_THE_NEXT_PAYMENT:
            request.applymarker(pytest.mark.xfail(strict=True, reason=EXPORT_ON_THE_NEXT_PAYMENT))
        store = stores(order, WEEK, rebuild=rebuild, again=again, linked=linked)

        days = store.sighting_days([MAIN], ["starling-csv"])["starling-csv"]

        for row in week_rows(store):
            made = int(feed_uid_of(store, row).rsplit("-", 1)[1])
            assert date.fromisoformat(days[row.entity_id][:10]) == date(2026, 9, made + 1)
