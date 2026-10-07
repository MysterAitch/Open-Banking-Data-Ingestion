"""What Identity health says of stored rows whose own feed item the bank later made no row.

Each household is invented and landed through the real doors, live (each fetch reconciled as
it lands) and rebuilt from raw. The answers were decided before the first run.

KNOWN ANSWERS:

    five payments SETTLED in one fetch and DECLINED in a later one
        five stored transactions, none of them history: five still booked, none pending, all
        DECLINED, dated the five days they were made
    seven of them
        the first five dates are named and "and 2 more" counts the rest
    a payment PENDING in one fetch and DECLINED in a later one
        one, still pending
    a payment SETTLED then ACCOUNT_CHECK, and another SETTLED then DECLINED
        two, each named under its own status, in name order
    a payment DECLINED in one fetch and SETTLED in a later one
        none: the newest status makes a row, and the sentence says none rather than saying nothing
    a payment SETTLED then DECLINED, which the export also lists
        one, and it was also sighted by another source
    a payment with a round-up, SETTLED then DECLINED
        one: the payment's own row, and never the round-up leg derived from its item
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable, Iterator
from datetime import date

import pytest

from late_settlement_corpus import export_text
from obdi.exact_rule_measure import NAMED_DATES, exact_rule_report
from obdi.ingest import rebuild
from obdi.ingest.pipeline import import_file
from obdi.ingest.providers import starling
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from round_up_corpus import card_payment
from test_absorbed_rows import arrive
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP

SHAPES = [False, True]
SHAPE_IDS = ["live", "rebuilt"]
ROUND_UP = {"goalCategoryUid": "cat-bills", "amount": {"currency": "GBP", "minorUnits": 50}}


def payment(day: int, status: str = "SETTLED", **more):
    return card_payment(f"f-day-{day}", f"Shop {day}", 100 + day, day, status=status, **more)


@pytest.fixture(autouse=True)
def without_the_rule(monkeypatch):
    """A rebuild as it was before a row whose item the bank declined was voided.

    The measurement says what the rule WOULD change in a store built without it, so these
    stores are built without it. `test_declined_items` asserts that the same measurement says
    none once the rule has run. A live landing here never runs the pass (it is a call a pull
    and an import make), so only the rebuild has to be told.
    """
    monkeypatch.setattr(rebuild, "void_declined_items", lambda store: None)


@pytest.fixture
def stores(tmp_path) -> Iterator[Callable[..., Store]]:
    opened: list[Store] = []

    def build(*fetches, rebuild: bool = False, listed: tuple = ()) -> Store:
        here: pathlib.Path = tmp_path / f"d{len(opened)}"
        here.mkdir()
        store = Store(here / "household.sqlite3")
        opened.append(store)
        land_evidence(store)
        for number, items in enumerate(fetches):
            arrive(
                store,
                starling.artefact_for(
                    json.dumps({"feedItems": items}).encode(),
                    account_id="starling:cat-main",
                    kind="feed",
                    origin=f"{FEED_ORIGIN}?changesSince=2026-09-{number + 2:02}T00:00:00Z",
                ),
            )
        if listed:
            path = here / "export.csv"
            path.write_text(export_text(list(listed)), encoding="utf-8")
            import_file(store, path, account_id=MAIN, account_map=MAP)
        if rebuild:
            assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def figures_of(store: Store):
    (found,) = [f for f in exact_rule_report(store, MAP).no_row_status if f.account == MAIN]
    return found


def describe(store: Store) -> str:
    return "\n".join(figures_of(store).sentences())


@pytest.mark.parametrize("rebuild", SHAPES, ids=SHAPE_IDS)
class TestAPaymentTheBankLaterDeclined:
    def test_Measurement_WhenFivePaymentsAreSettledThenDeclined_CountsFiveBookedRows(
        self, stores, rebuild
    ):
        days = range(3, 8)
        store = stores(
            [payment(d) for d in days], [payment(d, "DECLINED") for d in days], rebuild=rebuild
        )

        found = figures_of(store)

        assert len(found.rows) == 5
        assert {r.status for r in found.rows} == {"DECLINED"}
        assert [r.row.value_date for r in found.rows] == [date(2026, 9, d) for d in days]
        text = describe(store)
        assert "5 stored transactions that are not history have, as the newest landed" in text
        assert "Of those, still pending: 0. Still booked: 5." in text
        assert (
            "DECLINED: 5, dated 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07."
        ) in text

    def test_Measurement_WhenSevenAreDeclined_NamesTheFirstFiveDatesAndCountsTheRest(
        self, stores, rebuild
    ):
        days = range(3, 10)
        store = stores(
            [payment(d) for d in days], [payment(d, "DECLINED") for d in days], rebuild=rebuild
        )

        text = describe(store)

        assert NAMED_DATES == 5
        assert (
            "DECLINED: 7, dated 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07 "
            "and 2 more."
        ) in text

    def test_Measurement_WhenAPendingPaymentIsDeclined_CountsOneStillPending(
        self, stores, rebuild
    ):
        store = stores([payment(4, "PENDING")], [payment(4, "DECLINED")], rebuild=rebuild)

        text = describe(store)

        assert "1 stored transaction that is not history has," in text
        assert "Of those, still pending: 1. Still booked: 0." in text

    def test_Measurement_WhenTwoStatusesMakeNoRow_NamesEachInNameOrder(self, stores, rebuild):
        store = stores(
            [payment(4), payment(5)],
            [payment(4, "ACCOUNT_CHECK"), payment(5, "DECLINED")],
            rebuild=rebuild,
        )

        lines = figures_of(store).sentences()

        assert lines[-2:] == [
            "ACCOUNT_CHECK: 1, dated 2026-09-04.",
            "DECLINED: 1, dated 2026-09-05.",
        ]

    def test_Measurement_WhenADeclinedPaymentIsSettledLater_SaysNoneRatherThanNothing(
        self, stores, rebuild
    ):
        store = stores([payment(4, "DECLINED")], [payment(4)], rebuild=rebuild)

        assert figures_of(store).rows == []
        assert describe(store) == (
            "No stored transaction that is not history has, as the newest landed status of "
            "its own feed item, a status that makes no row."
        )

    def test_Measurement_WhenNothingWasDeclined_SaysNoneRatherThanNothing(self, stores, rebuild):
        store = stores([payment(4)], [payment(4)], rebuild=rebuild)

        assert figures_of(store).rows == []

    def test_Measurement_WhenTheExportAlsoListsTheDeclinedPayment_SaysAnotherSourceSightedIt(
        self, stores, rebuild
    ):
        store = stores(
            [payment(9)],
            [payment(9, "DECLINED")],
            rebuild=rebuild,
            listed=(("Shop 9", -109, date(2026, 9, 9)),),
        )

        assert "Also reported by a source other than the bank's feed: 1." in describe(store)

    def test_Measurement_WhenOnlyTheFeedSightedTheDeclinedPayment_SaysNoOtherSourceDid(
        self, stores, rebuild
    ):
        store = stores([payment(9)], [payment(9, "DECLINED")], rebuild=rebuild)

        assert "Also reported by a source other than the bank's feed: 0." in describe(store)

    def test_Measurement_WhenTheDeclinedPaymentCarriedARoundUp_CountsThePaymentNotTheLeg(
        self, stores, rebuild
    ):
        store = stores(
            [payment(6, round_up=ROUND_UP)],
            [payment(6, "DECLINED", round_up=ROUND_UP)],
            rebuild=rebuild,
        )

        found = figures_of(store)

        assert [r.row.source_id for r in found.rows] == ["f-day-6"]


class TestTheReportAsAWhole:
    def test_Report_WhenNoAccountHoldsARowTheFeedSighted_SaysThereIsNoFeedStatusToRead(
        self, tmp_path
    ):
        with Store(tmp_path / "empty.sqlite3") as store:
            text = exact_rule_report(store, MAP).describe()

        assert "No account holds a transaction the bank's feed reported" in text

    def test_Report_WhenARowIsDeclined_ShowsNoPayeeOrFigure(self, stores):
        store = stores([payment(6)], [payment(6, "DECLINED")])

        text = exact_rule_report(store, MAP).describe()

        for hidden in ("Shop 6", "106", "1.06"):
            assert hidden not in text
