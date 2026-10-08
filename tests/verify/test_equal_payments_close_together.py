"""Equal payments close together are matched to their partners as a set.

The deployed page showed one pair of changes in an account's balance difference
that undid itself three days later: "The change at the end of 2021-03-22 (...,
undone by an opposite change at the end of 2021-03-25 ...)", with the earlier
payment "seen by starling, truelayer" and "the export lists a row of the same
size and direction, 2 days away, sighted on another stored row", and the later
payment as "out row dated starling-csv 2021-03-25; seen by starling-csv; not
held" with "The export lists 5 rows in the window and the store holds 4
sightings of it there".

THE SHAPE. The feed and the aggregator hold TWO outgoing payments of one size,
dated days 5 and 8. The export lists two rows of that size, dated days 7 and 8.
Taking the export's rows one at a time, the row of day 7 is nearer the feed's
payment of day 8 (one day) than day 5 (two days), so it took that one and left
the export's row of day 8 with nothing to sit beside, and the feed's payment of
day 5 with no export sighting. The right assignment is the other: day 7 with
day 5, day 8 with day 8, which is also the lower total distance.

An earlier attempt to reproduce this used ONE feed payment and two export rows,
which does not break.

Every scenario is the `round_up_corpus` household (September 2026, amounts in
pence) with the three sources landed in EVERY order, the export's file in both
orders, and rebuilt. Known answers, decided before the first run:

    (i) two feed payments of 2000 on days 5 and 8, the aggregator listing both,
        the export listing rows on days 7 and 8
        two stored rows, each seen by all three sources
        the export's sighting on the day-5 row is dated day 7, the day-8 row's day 8
        every stated balance agrees; no export row is unheld
    (ii) the feed and the aggregator hold payments on days 2 and 3, the export
         lists ONE row, on day 4
         two rows: the day-3 payment seen by all three sources and the day-2
         payment by the feed and the aggregator alone; the family is over by
         one payment from day 2 on, which is what the store held before the rule
    (iii) three feed payments on days 5, 8, and 9, the export listing days 7, 8,
          and 12 (every export row within the window of every feed row)
          the minimum total distance pairs 5-7, 8-8, 9-12; in this shape the
          set is solved exactly
    (iv) feed payments on days 5 and 8, the export rows on days 12 and 13
         only the row of day 12 reaches the payment of day 5 (seven days), so
         12 takes 5 and 13 takes 8; taking 12 with 8 first strands 13
    (v) the feed and the aggregator on days 2 and 3, the export on days 4 and 20
        the row of day 20 reaches nothing and is held as its own row, seen by
        the export alone
    (vi) a group past `matching.ASSIGNMENT_SET_LIMIT` keeps its same-date pairs
         and pairs nothing else as a set; every export row is still held
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import pytest

from landing import import_file, rebuild_from_raw
from obdi.ingest.family_anchors import families_of
from obdi.ingest.matching import ASSIGNMENT_SET_LIMIT, assign_as_a_set
from obdi.ingest.providers import truelayer
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import effective_opening
from round_up_corpus import (
    SPACE_FEED_ORIGIN,
    card_payment,
    land_feed,
    main_feed,
    space_feed,
)
from test_export_cuts import Row, export_lines
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    aggregator_record,
    order_id,
    rows,
)

AMOUNT = 2000
FILE_ORDERS = ("ascending", "descending")


def build(
    directory: pathlib.Path,
    order: tuple[str, ...],
    file_order: str,
    *,
    feed_days: list[int],
    export_days: list[int],
    aggregator_days: list[int] | None = None,
) -> Store:
    """The raw artefacts landed in `order` and rebuilt: nothing derived is trusted."""
    store = Store(directory / "household.sqlite3")
    land_evidence(store)
    ascending = sorted(
        [*BASE_EXPORT, *(Row("Bill", -AMOUNT, day, day) for day in export_days)],
        key=lambda row: row.export_day,
    )
    listed = ascending if file_order == "ascending" else list(reversed(ascending))
    path = directory / "export.csv"
    path.write_text("\n".join(export_lines(listed, balanced_as=ascending)) + "\n", encoding="utf-8")
    aggregated = feed_days if aggregator_days is None else aggregator_days

    def feed() -> None:
        land_feed(
            store,
            [
                *main_feed(),
                *(card_payment(f"f-p{day}", "Water Co", AMOUNT, day) for day in feed_days),
            ],
            origin=FEED_ORIGIN,
            asked="2026-09-02T00:00:00Z",
        )
        land_feed(store, space_feed(), origin=SPACE_FEED_ORIGIN)

    def export() -> None:
        import_file(store, path, account_id=MAIN, account_map=MAP)

    def aggregate() -> None:
        records = [aggregator_record(f"tl-p{day}", "-20.00", day, "BILL DD") for day in aggregated]
        store.land_artefact(
            truelayer.artefact_for(
                json.dumps({"results": records}).encode(), account_id="tl-main", kind="booked"
            )
        )

    steps = {"feed": feed, "export": export, "aggregator": aggregate}
    for name in order:
        steps[name]()
    assert rebuild_from_raw(store, account_map=MAP).problems == []
    return store


@pytest.fixture
def stores(tmp_path):
    opened: list[Store] = []

    def make(order, file_order, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = build(directory, order, file_order, **kwargs)
        opened.append(store)
        return store

    yield make
    for store in opened:
        store.close()


def sighted(store: Store, row) -> tuple[tuple[str, int], ...]:
    """Each source that observed this row, with the day it gave the payment."""
    found = store.connection.execute(
        "SELECT DISTINCT source, observed_date FROM transaction_sources WHERE entity_id = ?",
        (row.entity_id,),
    ).fetchall()
    return tuple(sorted((item[0], date.fromisoformat(item[1]).day) for item in found))


def payments(store: Store) -> list[tuple[tuple[str, int], ...]]:
    """The stored payments of the amount, each as who saw it on which day, earliest first.

    Sorted by what the sources said, never by the stored row's own date, which
    is the last writer's and so depends on arrival order.
    """
    return sorted(
        (sighted(store, row) for row in rows(store, MAIN, minor=-AMOUNT)),
        key=lambda seen: min(day for _, day in seen),
    )


def walk_differences(store: Store) -> dict[date, int]:
    opening = effective_opening(store, MAIN, families=families_of(store, MAP))
    assert opening.family is not None
    return {r.day: r.difference_minor for r in opening.family.readings if r.difference_minor}


class TestTwoFeedPaymentsAndTwoExportRows:
    @pytest.mark.parametrize("file_order", FILE_ORDERS)
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_EqualPayments_WhenExportDatesBothBetweenTheFeedDates_EachExportRowTakesItsOwnPayment(
        self, stores, order, file_order
    ):
        store = stores(order, file_order, feed_days=[5, 8], export_days=[7, 8])

        assert payments(store) == [
            (("starling", 5), ("starling-csv", 7), ("truelayer", 5)),
            (("starling", 8), ("starling-csv", 8), ("truelayer", 8)),
        ]
        assert walk_differences(store) == {}


class TestTheExportListsOneRowOfTwoPayments:
    @pytest.mark.parametrize("file_order", FILE_ORDERS)
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_EqualPayments_WhenExportListsOneRowAfterBoth_TheNearerPaymentTakesIt(
        self, stores, order, file_order
    ):
        store = stores(order, file_order, feed_days=[2, 3], export_days=[4])

        assert payments(store) == [
            (("starling", 2), ("truelayer", 2)),
            (("starling", 3), ("starling-csv", 4), ("truelayer", 3)),
        ]
        assert set(walk_differences(store).values()) == {AMOUNT}


class TestThreePaymentsAndThreeExportRows:
    @pytest.mark.parametrize("file_order", FILE_ORDERS)
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_EqualPayments_WhenEveryRowIsInWindowOfEveryOther_TotalDistanceIsMinimal(
        self, stores, order, file_order
    ):
        store = stores(order, file_order, feed_days=[5, 8, 9], export_days=[7, 8, 12])

        assert payments(store) == [
            (("starling", 5), ("starling-csv", 7), ("truelayer", 5)),
            (("starling", 8), ("starling-csv", 8), ("truelayer", 8)),
            (("starling", 9), ("starling-csv", 12), ("truelayer", 9)),
        ]
        assert walk_differences(store) == {}


class TestTheEarlierExportRowIsTheOnlyOneInWindowOfTheEarlierPayment:
    @pytest.mark.parametrize("file_order", FILE_ORDERS)
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_EqualPayments_WhenOnlyOneExportRowReachesTheEarlierPayment_NoRowIsStranded(
        self, stores, order, file_order
    ):
        store = stores(order, file_order, feed_days=[5, 8], export_days=[12, 13])

        assert payments(store) == [
            (("starling", 5), ("starling-csv", 12), ("truelayer", 5)),
            (("starling", 8), ("starling-csv", 13), ("truelayer", 8)),
        ]


class TestAnExportRowWithNoPartnerInWindow:
    @pytest.mark.parametrize("file_order", FILE_ORDERS)
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_ExportRow_WhenNoPaymentIsWithinTheWindow_IsHeldAsItsOwnRow(
        self, stores, order, file_order
    ):
        store = stores(order, file_order, feed_days=[2, 3], export_days=[4, 20])

        assert payments(store) == [
            (("starling", 2), ("truelayer", 2)),
            (("starling", 3), ("starling-csv", 4), ("truelayer", 3)),
            (("starling-csv", 20),),
        ]


def day(number: int) -> date:
    return date(2026, 9, number)


def anything_goes(_record: int, _row: str) -> bool:
    return True


class TestAssignAsASetOverInventedDays:
    """Known answers worked by hand: records are (key, day), rows are (name, day)."""

    def test_Assignment_WhenSameDatePairExists_ItIsTakenBeforeTheNearestOfTheRest(self):
        chosen = assign_as_a_set(
            [(0, day(7)), (1, day(8))], [("a", day(5)), ("b", day(8))], anything_goes
        )

        assert chosen == {0: "a", 1: "b"}

    def test_Assignment_WhenNearestWouldStrandARecord_NoRecordIsLeftWhileAPartnerExists(self):
        def reach(record: int, row: str) -> bool:
            return (record, row) in {(0, "a"), (0, "b"), (1, "b")}

        chosen = assign_as_a_set(
            [(0, day(8)), (1, day(9))], [("a", day(6)), ("b", day(7))], reach
        )

        assert chosen == {0: "a", 1: "b"}

    def test_Assignment_WhenRecordsAreListedInEitherOrder_TheSameRowsAreChosen(self):
        records = [(0, day(7)), (1, day(9)), (2, day(12))]
        rows = [("a", day(5)), ("b", day(8)), ("c", day(9))]

        forwards = assign_as_a_set(records, rows, anything_goes)
        backwards = assign_as_a_set(list(reversed(records)), list(reversed(rows)), anything_goes)

        assert forwards == backwards == {0: "a", 1: "c", 2: "b"}

    def test_Assignment_WhenFewerRowsThanRecords_TheFurthestRecordIsTheOneLeftOver(self):
        chosen = assign_as_a_set(
            [(0, day(7)), (1, day(8)), (2, day(14))], [("a", day(8)), ("b", day(13))], anything_goes
        )

        assert chosen == {1: "a", 2: "b"}

    def test_Assignment_WhenAGroupIsPastTheBound_OnlySameDatePairsAreSet(self):
        count = ASSIGNMENT_SET_LIMIT + 1
        records = [(i, date(2026, 8, 1 + 2 * i)) for i in range(count)]
        rows = [(f"r{i}", date(2026, 8, 2 + 2 * i)) for i in range(count)]
        same_day_as_the_first = [(count, date(2026, 8, 2))]

        unpaired = assign_as_a_set(records, rows, anything_goes)
        one_pair = assign_as_a_set([*records, *same_day_as_the_first], rows, anything_goes)

        assert unpaired == {}
        assert one_pair == {count: "r0"}


class TestASetPastTheBound:
    @pytest.mark.parametrize("order", [ORDERS[0], ORDERS[-1]], ids=order_id)
    def test_EqualPayments_WhenMoreRowsThanTheBound_EveryExportRowIsStillHeld(
        self, stores, order
    ):
        count = ASSIGNMENT_SET_LIMIT + 2
        feed = list(range(14, 14 + count))
        export = [day + 1 for day in feed]
        store = stores(order, "ascending", feed_days=feed, export_days=export)

        listed = [seen for seen in payments(store) if any(s == "starling-csv" for s, _ in seen)]
        assert sum(1 for seen in listed for s, _ in seen if s == "starling-csv") == count
