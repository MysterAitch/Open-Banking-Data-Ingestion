"""Identity health counts the pairs of stored rows an exact rule names as one payment.

The join of two stored rows (`matching.second_row_named_by_exact_rules`) deletes a row, and it
has only met invented data, so what it would do to a store is read before it is trusted with one:
`exact_rule_measure.pair_figures` replays it over the stored rows and sightings, read-only.

KNOWN ANSWERS, decided before the first run, on the late-settlement household with an aggregator
that states the feed's uid (`late_settlement_corpus`):

    the feed arriving last after both others
        two pairs: the two payments settled months later, each held as the aggregator's row
        (seen by starling and truelayer, dated the day it was made) and the export's row (seen by
        starling-csv alone, dated the settlement day); the sentence names dates and sources
    any other order
        no pair: the feed or the export found the row first
    seven payments of that kind
        seven pairs, five named and "and 2 more"
    two equal payments settled on one day, the export listing both that day
        no pair, and four candidate pairs refused because the settlement day names two rows
    a week of one payee paid one amount on consecutive days
        no pair; every candidate refused, by the id that contradicts it (an aggregator that
        states the feed's uids) or by the source that had already sighted it (one that does not)
    a payment only the aggregator and the export report, beside one the feed reports
        no pair, the candidate refused because the aggregator called it by an id of its own
    a payment the bank reversed, beside one it settled
        no pair, the candidate refused as a history row
Rendering changes nothing: the rows and the writes the connection has made are the same after.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Callable, Iterator
from datetime import date

import pytest

from consecutive_days_corpus import consecutive_payments
from late_settlement_corpus import (
    HOUSEHOLD_EXPORT,
    LATE_DAY,
    Payment,
    export_text,
    household,
    late_settlement_payments,
)
from obdi.ingest import pipeline as ingest
from obdi.ingest.matching import (
    REFUSED_CONTRADICTED,
    REFUSED_KIND,
    REFUSED_OTHER_ID,
    REFUSED_SAME_SOURCE,
    REFUSED_SEVERAL,
)
from obdi.ingest.pipeline import import_file
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.verify.exact_rule_measure import NAMED_PAIRS, exact_rule_report
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import order_id

FEED_LAST = [("export", "aggregator", "feed"), ("aggregator", "export", "feed")]
SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]
LATE_PAIR = (
    "starling's own id names the row dated 2026-09-14 seen by starling and truelayer; its "
    "settlement day and payee name the row dated 2027-01-20 seen by starling-csv alone"
)


def second_export(store: Store, directory: pathlib.Path, payments: list[Payment]) -> None:
    listed = [
        *HOUSEHOLD_EXPORT,
        *((p.name, -p.minor, p.listed) for p in payments if p.listed is not None),
        # One row more than the first file, so the bytes differ and it lands as its own.
        ("Extra", -111, date(2027, 1, 25)),
    ]
    path = directory / "export-overlap.csv"
    path.write_text(export_text(listed), encoding="utf-8")
    import_file(store, path, account_id=MAIN, account_map=MAP)


@pytest.fixture
def stores(tmp_path, monkeypatch) -> Iterator[Callable[..., Store]]:
    """Households built with the join switched off, which is the store the count is read on."""
    monkeypatch.setattr(ingest, "_absorb_second_row", lambda _s, _t, _i, result, _m: result)
    opened: list[Store] = []

    def build(order, payments, *, rebuild=False, again=False, **kwargs) -> Store:
        here = tmp_path / f"d{len(opened)}"
        here.mkdir()
        store = household(here, order, payments, **{"linked": True, **kwargs})
        opened.append(store)
        if again:
            second_export(store, here, payments)
        if rebuild:
            assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def pairs_of(store: Store):
    found = [p for p in exact_rule_report(store, MAP).pairs if p.account == MAIN]
    return found[0] if found else None


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestThePairsTheJoinWouldMake:
    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Pairs_WhenTheFeedArrivedLastAfterBoth_NamesTheTwoLatePayments(
        self, stores, order, rebuild, again
    ):
        store = stores(order, late_settlement_payments(), rebuild=rebuild, again=again)

        found = pairs_of(store)

        assert found is not None
        assert found.joins == [LATE_PAIR, LATE_PAIR]
        assert not found.refused

    @pytest.mark.parametrize(
        "order",
        [
            ("feed", "aggregator", "export"),
            ("feed", "export", "aggregator"),
            ("export", "feed", "aggregator"),
            ("aggregator", "feed", "export"),
        ],
        ids=order_id,
    )
    def test_Pairs_WhenTheFeedOrTheExportFoundTheRowFirst_NamesNone(
        self, stores, order, rebuild, again
    ):
        store = stores(order, late_settlement_payments(), rebuild=rebuild, again=again)

        assert pairs_of(store) is None


class TestTheReport:
    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Page_WhenRendered_ChangesNothingInTheStore(self, stores, order):
        store = stores(order, late_settlement_payments())
        rows = len(store.all_transactions())
        count_sightings = "SELECT COUNT(*) FROM transaction_sources"
        sightings = store.connection.execute(count_sightings).fetchone()[0]
        writes = store.connection.total_changes

        text = exact_rule_report(store, MAP).describe()

        assert "2 pairs of stored rows" in text
        assert LATE_PAIR in text
        assert len(store.all_transactions()) == rows
        assert store.connection.execute(count_sightings).fetchone()[0] == sightings
        assert store.connection.total_changes == writes

    def test_Page_WhenNoRecordNamesTwoRows_SaysSo(self, stores):
        store = stores(("feed", "aggregator", "export"), late_settlement_payments())

        text = exact_rule_report(store, MAP).describe()

        assert "No account holds a record whose id names one row and whose settlement day" in text

    def test_Page_WhenMoreThanFivePairs_NamesFiveAndCountsTheRest(self, stores):
        far = [
            Payment(
                f"f-far-{n}",
                f"Far Shop {n}",
                3000 + 13 * n,
                f"2026-09-14T10:{30 + n:02}:00.000Z",
                "2027-01-20T02:44:19.000Z",
                LATE_DAY,
                14,
            )
            for n in range(7)
        ]
        store = stores(("export", "aggregator", "feed"), far)

        text = exact_rule_report(store, MAP).describe()

        assert "7 pairs of stored rows" in text
        assert text.count("starling's own id names the row dated") == NAMED_PAIRS
        assert "  and 2 more" in text

    def test_Page_WhenMasked_NamesNoFigureDescriptionOrPayee(self, stores):
        store = stores(("export", "aggregator", "feed"), late_settlement_payments())

        text = exact_rule_report(store, MAP).describe().split("Stored rows one record")[1]

        for word in ("Abroad", "Shop", "4210", "1780", "42.10", "Bakery"):
            assert word not in text


class TestTheCandidatesAGuardRefuses:
    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Refusal_WhenTheSettlementDayNamesTwoExportRows_IsSeveralCandidates(
        self, stores, order
    ):
        late = [
            Payment(
                f"f-twin-{n}", "Twin Shop", 2500, f"2026-09-14T10:0{n}:00.000Z",
                "2027-01-20T02:44:19.000Z", LATE_DAY, 14,
            )
            for n in (1, 2)
        ]
        store = stores(order, late)

        found = pairs_of(store)

        assert found is not None
        assert found.joins == []
        assert dict(found.refused) == {REFUSED_SEVERAL: 4}

    @pytest.mark.parametrize(
        "order",
        [("feed", "aggregator", "export"), ("export", "aggregator", "feed")],
        ids=order_id,
    )
    def test_Refusal_WhenAWeekOfEqualPaymentsSettlesTheDayAfter_IsTheIdThatContradicts(
        self, stores, order
    ):
        store = stores(order, consecutive_payments())

        found = pairs_of(store)

        assert found is not None
        assert found.joins == []
        assert set(found.refused) == {REFUSED_CONTRADICTED}

    @pytest.mark.parametrize(
        "order",
        [("feed", "aggregator", "export"), ("export", "aggregator", "feed")],
        ids=order_id,
    )
    def test_Refusal_WhenTheAggregatorStatesNoUid_IsTheSourceThatAlreadySightedTheRow(
        self, stores, order
    ):
        store = stores(order, consecutive_payments(), linked=False)

        found = pairs_of(store)

        assert found is not None
        assert found.joins == []
        assert set(found.refused) == {REFUSED_SAME_SOURCE}

    def test_Refusal_WhenOnlyTheAggregatorAndTheExportReportTheNextPayment_IsAnotherId(
        self, stores
    ):
        first, second = consecutive_payments(2)
        store = stores(
            ("export", "aggregator", "feed"),
            [first, dataclasses.replace(second, in_feed=False)],
            linked=False,
        )

        found = pairs_of(store)

        assert found is not None
        assert found.joins == []
        assert REFUSED_OTHER_ID in found.refused

    def test_Refusal_WhenTheNextPaymentWasReversed_IsAHistoryRow(self, stores):
        first, second = consecutive_payments(2)
        reversed_ = dataclasses.replace(
            second, feed_status="REVERSED", reported=None, listed=None
        )
        store = stores(("export", "aggregator", "feed"), [first, reversed_], linked=False)

        found = pairs_of(store)

        assert found is not None
        assert found.joins == []
        assert REFUSED_KIND in found.refused
