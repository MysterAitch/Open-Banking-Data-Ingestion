"""The bank's export lists a card payment on its settlement day, however far that is on.

The deployed store counted two card payments twice: the bank's feed and the
aggregator dated them on the day they were made, the export listed them 136 days
later because that was their settlement day, and the export's rows lay beyond the
matcher's window of the payments, found no partner, and became rows of their own.
The corpus (`late_settlement_corpus`) is invented, with its known answers recorded
beside it; each scenario lands the three sources in every order a live pull and
import can, and (where noted) rebuilds the store from raw.

KNOWN ANSWERS, decided before the first run:

    late settlement (two payments a minute apart, settled 127 days later in one
    second; four others settled the next day)
        six stored payments, each sighted by the feed, the export, and the
        aggregator; the export's own sightings on its own days; every stated balance
        agrees; the movement count reports nothing
        WITHOUT THE RULE: eight rows, and the family over by 5990 from the 15th on
        (a row's own date is its latest sighting's, as it was before the rule:
        `matching.supersede` says why keeping the day it was made was withdrawn)
    a second export of an overlapping span listing the same rows again
        still six, and the movement count reports nothing
    equal payments, near (one size to one payee, made on the 16th and 19th,
    settled on the 18th and 19th, the export listing the 18th and 19th)
        two rows, each sighted by all three, the export's sightings on the 18th and 19th
        (the matcher already got this one right)
    the same, the export listing both on the 19th, both settled on the 19th
        two rows, each sighted by all three
    no settlement time, the export twelve days on
        two rows: the payment seen by the feed and the aggregator, and the export's
        row alone, as before the rule
    a stranger (the export's row of the payment's size on its settlement day, to
    another payee)
        beyond the window: kept apart, so two rows
        inside the window: merged, as the matcher has always merged two sources
    summer midnight (settled at 23:30 UTC on 30 June, which is 1 July in London)
        the export's row of the 30th and of 1 July are each the payment; of 2 July is not

THE THREE ORDERS THAT STAY OPEN FOR AN AGGREGATOR THAT STATES NO ID. When the aggregator and
the export both arrive BEFORE the feed, neither can pair with the other (the aggregator's row
has no settlement day, and the export's row is beyond its window), so the feed finds two
stored rows for one payment and can join only one. And when the aggregator arrives
AFTER the export has joined the feed's row, the row carries the export's date, months
from the aggregator's, and the aggregator states no settlement day to reach it by.
None is the deployed store's order (`FEED_FIRST`). Marked as expected failures
so that the day they are closed they say so.
The movement count reports nothing in them either: it compares rows listed with rows
held per source, and each source's rows are all held.
With an aggregator that states the feed's uid (`TestWhenTheAggregatorStatesTheFeedsUid`, which
is what a real one does) all three pass: the third by id, and the first two because the feed
finds the aggregator's row by id and the export's by settlement day and the two rows are joined.
Without an id nothing exact names the aggregator's row (only its amount and date window do, which
is a heuristic), so the join of two rows never has a second row to reach and the three stay open.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest

from late_settlement_corpus import (
    HOUSEHOLD_EXPORT,
    LATE_DAY,
    LATE_FIRST,
    LATE_SECOND,
    NEXT_DAY_PAYMENTS,
    ORDERS,
    Payment,
    equal_payments,
    export_text,
    household,
    late_settlement_payments,
)
from obdi.ingest import import_file
from obdi.movement_completeness import check_rows
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from test_movement_chains import canonical
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import order_id, sources, walk_differences

ALL_THREE = {"starling", "starling-csv", "truelayer"}
#: The order the deployed store's artefacts arrived in, and so the order a rebuild of it replays.
FEED_FIRST = ("feed", "aggregator", "export")
FEED_LAST_AFTER_BOTH = [("export", "aggregator", "feed"), ("aggregator", "export", "feed")]
AGGREGATOR_AFTER_THE_EXPORT_JOINED = [("feed", "export", "aggregator")]
STILL_OPEN = [*FEED_LAST_AFTER_BOTH, *AGGREGATOR_AFTER_THE_EXPORT_JOINED]

OPEN = pytest.mark.xfail(
    strict=True,
    reason="the feed finds two stored rows for one payment and joins only one, or the "
    "aggregator arrives after the export has moved the row's date beyond its window",
)


def orders(*, with_open: bool = True):
    return [
        pytest.param(o, id=order_id(o), marks=OPEN if o in STILL_OPEN else ())
        for o in ORDERS
        if with_open or o not in STILL_OPEN
    ]


@pytest.fixture
def made(tmp_path):
    opened: list[Store] = []

    def build(order, payments, **kwargs) -> Store:
        directory: pathlib.Path = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = household(directory, order, payments, **kwargs)
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


def payment_rows(store: Store, minors: set[int]):
    return [
        t
        for t in store.transactions_for_account(MAIN)
        if -t.amount_minor in minors and not t.status.is_history
        and not (t.source_id or "").endswith(":round-up")
        and t.description not in ("Round-up",)
    ]


LATE_MINORS = {LATE_FIRST, LATE_SECOND, *(m for _, m in NEXT_DAY_PAYMENTS)}


def export_days(store: Store) -> dict[str, date]:
    return {
        entity: date.fromisoformat(text[:10])
        for entity, text in store.sighting_days([MAIN], ["starling-csv"])["starling-csv"].items()
    }


class TestLateSettlement:
    @pytest.mark.parametrize("order", orders())
    def test_Payments_WhenTheExportListsTwoBySettlementMonthsLater_EachIsOneRowSeenByAllThree(
        self, made, order
    ):
        store = made(order, late_settlement_payments())

        rows = payment_rows(store, LATE_MINORS)
        assert len(rows) == 6
        assert [sources(store, t) for t in rows] == [ALL_THREE] * 6

    @pytest.mark.parametrize("order", orders())
    def test_ExportSightings_WhenSettlementDiffersFromThePaymentDay_AreOnTheExportsOwnDays(
        self, made, order
    ):
        store = made(order, late_settlement_payments())

        days = export_days(store)
        by_amount = {t.amount_minor: days[t.entity_id] for t in payment_rows(store, LATE_MINORS)}
        assert by_amount[-LATE_FIRST] == LATE_DAY
        assert by_amount[-LATE_SECOND] == LATE_DAY
        assert {by_amount[-m] for _, m in NEXT_DAY_PAYMENTS} == {date(2026, 9, 15)}

    @pytest.mark.parametrize("order", orders())
    def test_StatedBalances_WhenTwoPaymentsSettleMonthsLater_EveryExportBalanceAgrees(
        self, made, order
    ):
        store = made(order, late_settlement_payments())

        assert walk_differences(store) == {}

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_MovementCount_WhenTheExportListsPaymentsOnSettlementDays_ReportsNothing(
        self, made, order
    ):
        store = made(order, late_settlement_payments())

        assert check_rows(store, canonical).row_faults == []

    def test_Rebuild_WhenTheStoreIsRebuiltFromRaw_StillHoldsEachPaymentOnce(self, made):
        # One order only, the deployed store's: a rebuild replays by the landing stamp as
        # text, which puts an import last on a machine ahead of UTC and where it arrived on
        # one at UTC, so any other order here would be a different scenario on each.
        store = made(FEED_FIRST, late_settlement_payments(), rebuild=True)

        rows = payment_rows(store, LATE_MINORS)
        assert len(rows) == 6
        assert walk_differences(store) == {}

    def test_Rebuild_WhenRebuiltFromRaw_StatesTheSettlementAgainstTheRow(self, made):
        store = made(FEED_FIRST, late_settlement_payments(), rebuild=True)

        late = payment_rows(store, {LATE_FIRST})
        (moment,) = [
            m for m in store.stated_times_for(late[0].entity_id) if m["field"] == "settlementTime"
        ]
        assert moment["stated"].startswith("2027-01-20T02:44:19")


class TestWhenTheAggregatorStatesTheFeedsUid:
    """The same household with an aggregator that states the feed's own uid, as a real one does.

    The id joins the aggregator's item to the feed's row whatever date the row carries
    (`matching._Judgement.named_by_id`), which closes the order the settlement build left open
    (the aggregator arriving after the export had moved the row's date away from its own).
    The orders where the feed arrives last find two rows, one the id names and one the
    settlement day names, and join them (`ingest._absorb_second_row`;
    `test_absorbed_rows.py` says what moves).
    """

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Payments_WhenTheAggregatorStatesTheUid_EachIsOneRowSeenByAllThree(self, made, order):
        store = made(order, late_settlement_payments(), linked=True)

        rows = payment_rows(store, LATE_MINORS)
        assert len(rows) == 6
        assert [sources(store, t) for t in rows] == [ALL_THREE] * 6


class TestASecondExportOfAnOverlappingSpan:
    """The same rows listed again by a second export file are the same payments.

    The deployed store holds two exports of one year whose spans overlap.
    When a row merged by settlement kept the day the payment was made, the second file's
    row (dated the settlement day) no longer found the row the first file's had joined,
    and every card payment settled on a later day than it was made was stored twice:
    245 surplus rows on the first rebuild.
    """

    @staticmethod
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

    @pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
    def test_Payments_WhenASecondOverlappingExportListsThemAgain_AreStillHeldOnce(
        self, made, tmp_path, rebuild
    ):
        payments = late_settlement_payments()
        store = made(("feed", "aggregator", "export"), payments)

        self.second_export(store, tmp_path, payments)
        if rebuild:
            assert rebuild_from_raw(store, account_map=MAP).problems == []

        assert len(payment_rows(store, LATE_MINORS)) == 6
        assert check_rows(store, canonical).row_faults == []
        assert walk_differences(store) == {}


class TestEqualPaymentsNear:
    @pytest.mark.parametrize("order", orders(with_open=False))
    def test_Payments_WhenTwoOfOneSizeAreListedOnTheirSettlementDays_EachExportRowIsItsOwnPayment(
        self, made, order
    ):
        store = made(order, equal_payments())

        days = export_days(store)
        rows = sorted(payment_rows(store, {2000}), key=lambda t: days[t.entity_id])
        assert [sources(store, t) for t in rows] == [ALL_THREE, ALL_THREE]
        assert [days[t.entity_id] for t in rows] == [date(2026, 9, 18), date(2026, 9, 19)]
        assert walk_differences(store) == {}
        assert check_rows(store, canonical).row_faults == []

    @pytest.mark.parametrize("order", orders(with_open=False))
    def test_Payments_WhenTheExportListsBothOnTheLaterDay_EachIsStillOneRowSeenByAllThree(
        self, made, order
    ):
        store = made(order, equal_payments(both_listed_on_the_later_day=True))

        rows = payment_rows(store, {2000})
        assert len(rows) == 2
        assert [sources(store, t) for t in rows] == [ALL_THREE, ALL_THREE]
        assert {export_days(store)[t.entity_id] for t in rows} == {date(2026, 9, 19)}
        assert walk_differences(store) == {}
        assert check_rows(store, canonical).row_faults == []

    @pytest.mark.parametrize("order", orders(with_open=False))
    def test_Payments_WhenRebuilt_TwoEqualPaymentsAreStillTwoRows(self, made, order):
        store = made(order, equal_payments(), rebuild=True)

        assert len(payment_rows(store, {2000})) == 2
        assert walk_differences(store) == {}


MADE = "2026-09-14T10:00:00.000Z"


def lone_payment(minor, *, settled, listed, made_at=MADE, name="Abroad Shop", reported=14):
    return Payment("f-lone", name, minor, made_at, settled, listed, reported)


class TestWhereTheRuleDoesNotApply:
    @pytest.mark.parametrize("order", orders(with_open=False))
    def test_Payment_WhenTheFeedStatesNoSettlement_AnExportRowTwelveDaysOnIsAnotherPayment(
        self, made, order
    ):
        store = made(
            order,
            [lone_payment(3333, settled=None, listed=date(2026, 9, 26))],
        )

        rows = payment_rows(store, {3333})
        assert sorted(tuple(sorted(sources(store, t))) for t in rows) == [
            ("starling", "truelayer"),
            ("starling-csv",),
        ]

    @pytest.mark.parametrize("order", orders(with_open=False))
    def test_Stranger_WhenAnotherPayeesRowIsListedOnTheSettlementDayBeyondTheWindow_StaysApart(
        self, made, order
    ):
        store = made(
            order,
            [lone_payment(3333, settled="2027-01-20T02:44:19.000Z", listed=None)],
            extra_listed=(("Pet Store", -3333, LATE_DAY),),
        )

        rows = payment_rows(store, {3333})
        assert sorted(tuple(sorted(sources(store, t))) for t in rows) == [
            ("starling", "truelayer"),
            ("starling-csv",),
        ]

    @pytest.mark.parametrize("order", orders(with_open=False))
    def test_Stranger_WhenAnotherPayeesRowIsListedInsideTheWindow_MergesAsTheMatcherAlwaysDid(
        self, made, order
    ):
        store = made(
            order,
            [lone_payment(3333, settled="2026-09-15T03:00:00.000Z", listed=None)],
            extra_listed=(("Pet Store", -3333, date(2026, 9, 15)),),
        )

        rows = payment_rows(store, {3333})
        assert [sources(store, t) for t in rows] == [ALL_THREE]


class TestTheSettlementZone:
    SETTLED = "2027-06-30T23:30:00.000Z"

    @pytest.mark.parametrize(
        ("listed", "merged"),
        [
            (date(2027, 6, 30), True),
            (date(2027, 7, 1), True),
            (date(2027, 7, 2), False),
        ],
    )
    @pytest.mark.parametrize("order", [FEED_FIRST, ("export", "feed", "aggregator")], ids=order_id)
    def test_Export_WhenSettledAtSummerMidnight_ListsItOnEitherZonesDayButNoOther(
        self, made, order, listed, merged
    ):
        store = made(order, [lone_payment(3333, settled=self.SETTLED, listed=listed)])

        rows = payment_rows(store, {3333})
        assert len(rows) == (1 if merged else 2)

    def test_Export_WhenSettledAtWinterMidnight_ListsItOnTheUtcDayOnly(self, made):
        winter = "2027-12-31T23:30:00.000Z"
        next_day = made(
            FEED_FIRST, [lone_payment(3333, settled=winter, listed=date(2028, 1, 1))]
        )

        assert len(payment_rows(next_day, {3333})) == 2
