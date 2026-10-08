"""A row-count fault says where the sightings went, from the sightings themselves.

The deployed page listed three faults and stopped at the counts:

    2021-03-25 starling-personal via starling-csv (out): 2 rows ... listed, 1 held
    2025-11-01 santander-all-in-one-credit-card via truelayer (out): 2 rows ... listed, 1 held
    2026-10-01 starling-space-bills via starling (out): 1 row ... listed, 2 held

Each line now says where the artefact's sightings (`transaction_sources`: artefact, source,
observed date) are.

Every scenario is the `round_up_corpus` household (September 2026, amounts in pence) with the
sources landed in EVERY order and rebuilt. KNOWN ANSWERS, decided before the first run:

    IMPORTER-MADE
    (i)   the feed holds one payment of 2000 on day 5, the aggregator two on day 6, the export two
          rows on day 6
          one fault, starling-csv, day 6, 2 listed and 1 held: "both listed rows are sighted on
          one stored row, dated 2026-09-06", where the export is REPLAYED last, and no fault
          where it is replayed earlier (`export_is_replayed_last` says why the replay's order
          is read from the store and not taken from the arrival order)
    (ii)  the feed holds payments on days 5 and 8, the aggregator two on day 6, the export two
          on day 6
          no fault
    (iii) a Coffee reissued by a later feed fetch under a new id (the same day and size), settled,
          the later fetch a WINDOW of transaction time
          one fault, starling, day 3: "a row whose id the feed stopped listing when another id of
          the same size and recipient appeared: possibly one payment held twice"
    (iv)  the same, the reissue pending, or reversed: the same fault
    (v)   the same, the later fetch a changesSince: no fault, because a changesSince fetch lists
          what changed and its silence about an unchanged item is not absence

    PLANTED, because the importer derives rows and sightings from the same bytes and cannot
    disagree with itself
    (vi)  the Taxi sighting removed
          "the listed row is sighted on no stored row"
    (vii) the Taxi sighting observed five days later
          "the listed row is sighted on a stored row of another day, observed 2026-09-13"
    (viii) the same, and the stored row moved to the Bills Space
          "... of another account, starling-space-bills, observed 2026-09-13"
"""

from __future__ import annotations

import pytest

from landing import rebuild_from_raw
from obdi.ingest.arrival_order import in_arrival_order
from obdi.ingest.store import Store
from obdi.verify.movement_completeness import check_rows
from round_up_corpus import card_payment, household_store, main_feed, space_feed
from test_export_cuts import Row
from test_movement_rows_listed import EXPORT, canonical, drop_sighting, export_sightings
from test_space_attribution import BILLS, MAIN, MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    aggregator_record,
    corpus,
    order_id,
)

CASES = [pytest.param(order, id=order_id(order)) for order in ORDERS]

#: What the measurement of a listed-more-than-held fault adds for an id-less export file.
BY_ONE_ARTEFACT = "; listed by 1 artefact, stating no id"


@pytest.fixture
def stores(tmp_path):
    opened: list[Store] = []

    def build(order, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = corpus(directory, order, **kwargs)
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


def two_equal_export_rows(feed_days: tuple[int, ...]) -> dict:
    return {
        "main": [*main_feed(), *[card_payment(f"f-p{d}", "Pay", 2000, d) for d in feed_days]],
        "space": space_feed(),
        "export_rows": [*BASE_EXPORT, Row("Pay", -2000, 6, 6), Row("Pay", -2000, 6, 6)],
        "aggregator": [aggregator_record(f"tl-p{n}", "-20.00", 6, "PAY") for n in range(2)],
    }


def said(store) -> list[str]:
    return [fault.says() for fault in check_rows(store, canonical).row_faults]


def export_is_replayed_last(store) -> bool:
    """Whether the rebuild replays the export after the feed and the aggregator.

    Read from the store through the rebuild's own ordering (`in_arrival_order`) and not from
    the order the test landed them in, so the answer is said by the order actually replayed.
    """
    replayed = [
        str(row["source"])
        for row in in_arrival_order(
            store.connection.execute(
                "SELECT rowid, source, fetched_at FROM raw_artefacts"
            ).fetchall()
        )
    ]
    listing = ("csv", "starling-feed", "truelayer-booked")
    listers = [source for source in replayed if source in listing]
    return bool(listers) and listers[-1] == "csv"


class TestTwoListedRowsSightedOnOneStoredRow:
    @pytest.mark.parametrize("order", CASES)
    def test_Fault_WhenTheExportListsTwoEqualPaymentsAndTheFeedOne_SaysBothAreOnOneStoredRow(
        self, stores, order
    ):
        store = stores(order, **two_equal_export_rows((5,)))

        # The matcher puts both export rows on one stored row only where the export is
        # replayed after both other sources; replayed earlier, each row keeps its own.
        assert said(store) == (
            [
                f"2026-09-06 {MAIN} via {EXPORT} (out): 2 rows of one size and direction "
                "listed, 1 held: both listed rows are sighted on one stored row, dated 2026-09-06"
                f"{BY_ONE_ARTEFACT}"
            ]
            if export_is_replayed_last(store)
            else []
        )

    @pytest.mark.parametrize("order", CASES)
    def test_Fault_WhenTheFeedsTwoPaymentsSitDaysApart_ThereIsNoFault(self, stores, order):
        store = stores(order, **two_equal_export_rows((5, 8)))

        assert said(store) == []


class TestARowHeldTwiceWhereOneIsListed:
    @pytest.fixture
    def reissued(self, tmp_path):
        opened: list[Store] = []

        def build(status: str, export_last: bool, windows: bool = True) -> Store:
            directory = tmp_path / f"{len(opened)}"
            directory.mkdir()
            again = card_payment("f-coffee-again", "Coffee", 350, 3, status=status)
            store = household_store(
                directory,
                export_last=export_last,
                main_refetches=([again],),
                refetch_windows=windows,
            )
            opened.append(store)
            assert rebuild_from_raw(store, account_map=MAP).problems == []
            return store

        yield build
        for store in opened:
            store.close()

    @pytest.mark.parametrize("export_last", [False, True], ids=["export-first", "export-last"])
    @pytest.mark.parametrize("status", ["SETTLED", "PENDING", "REVERSED"])
    def test_Fault_WhenALaterWindowFetchReissuesTheRow_SaysPossiblyOnePaymentHeldTwice(
        self, reissued, status, export_last
    ):
        store = reissued(status, export_last)

        assert said(store) == [
            f"2026-09-03 {MAIN} via starling (out): a row whose id the feed stopped listing when "
            "another id of the same size and recipient appeared: possibly one payment held twice "
            "(its id is absent from the 1 later fetch that asks for its day, asking by "
            "transaction-time window)"
        ]

    @pytest.mark.parametrize("export_last", [False, True], ids=["export-first", "export-last"])
    @pytest.mark.parametrize("status", ["SETTLED", "PENDING", "REVERSED"])
    def test_Fault_WhenALaterChangesSinceFetchListsOnlyTheNewId_ThereIsNoFault(
        self, reissued, status, export_last
    ):
        """A changesSince fetch lists what changed: silence about an unchanged item is not absence,
        so two ids listed one size, direction, and day are two listed rows and two held."""
        store = reissued(status, export_last, windows=False)

        assert said(store) == []


def move_sighting(store: Store, entity_id: str, observed: str, account: str | None = None) -> None:
    store.connection.execute(
        "UPDATE transaction_sources SET observed_date = ? WHERE entity_id = ? AND source = ?",
        (observed, entity_id, EXPORT),
    )
    if account is not None:
        store.connection.execute(
            "UPDATE transactions SET account_id = ? WHERE entity_id = ?", (account, entity_id)
        )
    store.connection.commit()


class TestAListedRowWhoseSightingIsElsewhere:
    @pytest.mark.parametrize("order", CASES)
    def test_Fault_WhenTheRowsSightingIsAbsent_SaysItIsOnNoStoredRow(self, stores, order):
        store = stores(order, **two_equal_export_rows((5, 8)))
        (only,) = export_sightings(store, -1275, 8)
        drop_sighting(store, only["entity_id"])

        assert said(store) == [
            f"2026-09-08 {MAIN} via {EXPORT} (out): 1 row of one size and direction listed, "
            f"0 held: the listed row is sighted on no stored row{BY_ONE_ARTEFACT}"
        ]

    @pytest.mark.parametrize("order", CASES)
    def test_Fault_WhenTheSightingIsFiveDaysLater_SaysItIsOnARowOfAnotherDay(self, stores, order):
        store = stores(order, **two_equal_export_rows((5, 8)))
        (only,) = export_sightings(store, -1275, 8)
        move_sighting(store, only["entity_id"], "2026-09-13")

        assert said(store)[0] == (
            f"2026-09-08 {MAIN} via {EXPORT} (out): 1 row of one size and direction listed, "
            "0 held: the listed row is sighted on a stored row of another day, "
            f"observed 2026-09-13{BY_ONE_ARTEFACT}"
        )

    @pytest.mark.parametrize("order", CASES)
    def test_Fault_WhenTheStoredRowIsInAnotherAccount_NamesTheAccount(self, stores, order):
        store = stores(order, **two_equal_export_rows((5, 8)))
        (only,) = export_sightings(store, -1275, 8)
        move_sighting(store, only["entity_id"], "2026-09-13", account=BILLS)

        assert said(store)[0] == (
            f"2026-09-08 {MAIN} via {EXPORT} (out): 1 row of one size and direction listed, "
            f"0 held: the listed row is sighted on a stored row of another account, {BILLS}, "
            f"observed 2026-09-13{BY_ONE_ARTEFACT}"
        )

    def test_Fault_WhenAnotherListedRowSharesTheSizeOnAnotherDay_ItIsNotMistakenForTheMissingOne(
        self, stores
    ):
        kwargs = two_equal_export_rows((5, 8))
        kwargs["export_rows"] = [*BASE_EXPORT, Row("Taxi again", -1275, 10, 10)]
        kwargs["aggregator"] = []
        store = stores(ORDERS[0], **kwargs)
        (only,) = export_sightings(store, -1275, 8)
        drop_sighting(store, only["entity_id"])

        assert said(store) == [
            f"2026-09-08 {MAIN} via {EXPORT} (out): 1 row of one size and direction listed, "
            f"0 held: the listed row is sighted on no stored row{BY_ONE_ARTEFACT}"
        ]
