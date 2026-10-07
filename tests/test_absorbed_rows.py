"""Two stored rows that an exact rule proves are one payment are joined, and nothing else is.

The late-settlement household (`late_settlement_corpus`) with an aggregator that states the feed's
own uid, as a real one does. When the aggregator and the export both arrive BEFORE the feed, each
makes its own row: the aggregator's row has no settlement day and the export's row lies beyond
its window. The feed's item then names the aggregator's row by id and the export's row by the
settlement day it states, and the two rows are one payment held twice (`ingest._absorb_second_row`).

KNOWN ANSWERS, decided before the first run:

    the household, feed last after both others, every shape
        six stored payments, each sighted by all three sources, the stated balances agree, and
        the movement count reports nothing: in each of the two orders, live, rebuilt, and with
        a second export of an overlapping span listing the same rows again, live and rebuilt
    the row kept
        the row whose artefact arrived first, whichever source it was
    what moves to the kept row
        every sighting and stated time, an annotation (a person's over a rule's), a review
        flag, an event, and a transfer pair, so no table keyed by an entity id names the
        absorbed row
    two equal payments of one payee settled on one day, two export rows on that day
        the settlement day names both export rows, so it names neither: no row is joined
    an aggregator that states no uid
        nothing exact names its row, so nothing is joined and the order stays open
        (`test_settlement_dated_export`)
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date

import pytest

from late_settlement_corpus import (
    HOUSEHOLD_EXPORT,
    LATE_DAY,
    LATE_FIRST,
    NEXT_DAY_PAYMENTS,
    Payment,
    equal_payments,
    export_text,
    household,
    late_settlement_payments,
)
from obdi import ingest
from obdi.core.models import RawArtefact, Transaction
from obdi.core.namespaces import ENTITY_KEYED_TABLES
from obdi.exact_rule_measure import exact_rule_report
from obdi.family_anchors import families_of
from obdi.ingest import import_file, reconcile_batch
from obdi.movement_completeness import check_rows
from obdi.protection import diff_span, fingerprint_of, span_rows
from obdi.providers import starling
from obdi.rebuild import parse_artefact_transactions, rebuild_from_raw
from obdi.space_attribution import fold_space_copies
from obdi.store import Store
from round_up_corpus import main_feed
from test_family_anchors import FEED_ORIGIN
from test_movement_chains import canonical
from test_settlement_dated_export import LATE_MINORS, payment_rows
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import order_id, sources, walk_differences

ALL_THREE = {"starling", "starling-csv", "truelayer"}
FEED_LAST = [("export", "aggregator", "feed"), ("aggregator", "export", "feed")]
SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]


def arrive(store: Store, artefact: RawArtefact, account_ref: str = MAIN) -> None:
    store.land_artefact(artefact)
    reconcile_batch(
        store,
        parse_artefact_transactions(
            artefact.source, artefact.payload, account_ref, artefact.digest
        ),
        digest=artefact.digest,
        space_blind=families_of(store, MAP).blind_in,
    )
    fold_space_copies(store, MAP)


def land_the_feed(store: Store, payments: list[Payment]) -> None:
    """The bank's feed arriving last, through the door a pull uses."""
    items = [*main_feed(), *(p.feed_item() for p in payments if p.in_feed)]
    arrive(
        store,
        starling.artefact_for(
            json.dumps({"feedItems": items}).encode(),
            account_id="starling:cat-main",
            kind="feed",
            origin=f"{FEED_ORIGIN}?changesSince=2026-09-02T00:00:00Z",
        ),
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
def directory(tmp_path) -> Iterator[Callable[[], pathlib.Path]]:
    count = 0

    def fresh() -> pathlib.Path:
        nonlocal count
        count += 1
        made = tmp_path / f"d{count}"
        made.mkdir()
        return made

    yield fresh


@pytest.fixture
def stores(directory) -> Iterator[Callable[..., Store]]:
    opened: list[Store] = []

    def build(order, payments, *, rebuild=False, again=False, **kwargs) -> Store:
        here = directory()
        store = household(here, order, payments, linked=True, **kwargs)
        opened.append(store)
        if again:
            second_export(store, here, payments)
        if rebuild:
            assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
@pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
class TestTheFeedArrivingLastAfterTheAggregatorAndTheExport:
    def test_Payments_WhenTheFeedFindsTwoRowsForOne_EachIsOneRowSeenByAllThree(
        self, stores, order, rebuild, again
    ):
        store = stores(order, late_settlement_payments(), rebuild=rebuild, again=again)

        rows = payment_rows(store, LATE_MINORS)
        assert len(rows) == 6
        assert [sources(store, t) for t in rows] == [ALL_THREE] * 6

    def test_Balances_WhenTheTwoRowsAreJoined_EveryStatedBalanceAgreesAndNothingIsCountedTwice(
        self, stores, order, rebuild, again
    ):
        store = stores(order, late_settlement_payments(), rebuild=rebuild, again=again)

        assert walk_differences(store) == {}
        assert check_rows(store, canonical).row_faults == []


def minors(rows: list[Transaction]) -> list[int]:
    return sorted(-t.amount_minor for t in rows)


class TestWhichRowIsKept:
    """The two rows a payment has before the feed arrives, and the one that is left after."""

    def before_the_feed(self, directory, stores, order):
        payments = late_settlement_payments()
        store = stores(order[:2], payments)
        held = {
            t.entity_id: t for t in payment_rows(store, {LATE_FIRST})
        }
        return store, payments, held

    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Row_WhenTheFeedJoinsTwo_TheRowWhoseArtefactArrivedFirstSurvives(
        self, directory, stores, order
    ):
        store, payments, held = self.before_the_feed(directory, stores, order)
        assert len(held) == 2, "the aggregator's row and the export's row, before the feed"
        (earlier,) = [
            entity
            for entity, row in held.items()
            if row.source == ("starling-csv" if order[0] == "export" else "truelayer")
        ]

        land_the_feed(store, payments)

        (survivor,) = payment_rows(store, {LATE_FIRST})
        assert survivor.entity_id == earlier
        assert store.connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE entity_id = ?",
            (next(e for e in held if e != earlier),),
        ).fetchone()[0] == 0


class TestWhatMovesToTheKeptRow:
    def planted(self, directory, stores, order):
        payments = late_settlement_payments()
        store = stores(order[:2], payments)
        held = payment_rows(store, {LATE_FIRST})
        assert len(held) == 2
        first, second = sorted(
            held, key=lambda t: store.arrival_of(t.entity_id) or (0, 0)
        )
        return store, payments, first.entity_id, second.entity_id

    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_AbsorbedRow_WhenItCarriesEveryKindOfKeyedRow_NoTableStillNamesIt(
        self, directory, stores, order
    ):
        store, payments, kept, absorbed = self.planted(directory, stores, order)
        partner = payment_rows(store, {NEXT_DAY_PAYMENTS[0][1]})[0]
        assert store.annotate(absorbed, "category", "Travel", provenance="human")
        assert store.annotate(kept, "category", "Lunch", provenance="rule:v1")
        assert store.annotate(absorbed, "payee", "Abroad", provenance="rule:v1")
        store.queue_for_review(absorbed, "looked odd")
        store.append_event("test_event", absorbed, {"note": "carried"})
        store.replace_transfer_pairs([(absorbed, partner.entity_id)])
        # A sighting that states a coded word, whichever source made the row.
        held = next(t for t in store.transactions_for_account(MAIN) if t.entity_id == absorbed)
        store.record_source(
            replace(
                held,
                source="truelayer",
                artefact_digest="planted-sighting",
                raw={"transaction_category": "PURCHASE"},
            )
        )
        store.connection.commit()
        planted_in = {
            table
            for table, columns in ENTITY_KEYED_TABLES.items()
            for column in columns
            if store.connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ?",  # noqa: S608
                (absorbed,),
            ).fetchone()
        }
        assert planted_in == set(ENTITY_KEYED_TABLES), (
            "a table keyed by an entity id has no row planted on the absorbed row here"
        )

        land_the_feed(store, payments)

        for table, columns in ENTITY_KEYED_TABLES.items():
            for column in columns:
                assert (
                    store.connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",  # noqa: S608
                        (absorbed,),
                    ).fetchone()[0]
                    == 0
                ), f"{table}.{column} still names the absorbed row"
        assert store.orphaned_entity_rows() == dict.fromkeys(store.orphaned_entity_rows(), 0)
        annotations = {
            (str(r["kind"]), str(r["value"]))
            for r in store.connection.execute(
                "SELECT kind, value FROM annotations WHERE entity_id = ?", (kept,)
            )
        }
        assert annotations == {("category", "Travel"), ("payee", "Abroad")}, (
            "a person's categorisation outranks a rule's, and a kind only the absorbed row "
            "had moves"
        )
        assert [str(r["entity_id"]) for r in store.review_queue()] == [kept]
        assert store.confirmed_transfer_pairs() == [(kept, partner.entity_id)]
        assert store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE entity_id = ?", (kept,)
        ).fetchone()[0] == 1


class TestTheCountNamesWhatTheJoinJoins:
    """`exact_rule_measure.pair_figures` on the store without the join says what the join does."""

    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Count_WhenTheJoinIsOff_NamesExactlyTheRowsTheJoinRemoves(
        self, directory, monkeypatch, order
    ):
        with monkeypatch.context() as patch:
            patch.setattr(ingest, "_absorb_second_row", lambda _s, _t, _i, result, _m: result)
            unjoined = household(directory(), order, late_settlement_payments(), linked=True)
        joined = household(directory(), order, late_settlement_payments(), linked=True)
        try:
            named = next(p for p in exact_rule_report(unjoined, MAP).pairs if p.account == MAIN)
            held_twice = len(payment_rows(unjoined, LATE_MINORS))
            held_once = len(payment_rows(joined, LATE_MINORS))

            assert len(named.joins) == 2
            assert held_twice - held_once == len(named.joins)
            assert [p for p in exact_rule_report(joined, MAP).pairs if p.account == MAIN] == []
        finally:
            unjoined.close()
            joined.close()


class TestAProtectedSpanAnAbsorbChanges:
    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Span_WhenTheFeedJoinsTwoRowsInsideIt_ReadsAsAChangeLikeAnyOther(
        self, stores, order
    ):
        payments = late_settlement_payments()
        store = stores(order[:2], payments)
        whole = (date(2026, 9, 1), date(2027, 1, 31))
        before = span_rows(store, MAIN, *whole)

        land_the_feed(store, payments)

        after = span_rows(store, MAIN, *whole)
        change = diff_span(before, after)
        assert fingerprint_of(before) != fingerprint_of(after)
        assert not change.empty
        # Measured, not reasoned: the two late payments' export rows (dated the settlement day)
        # are no longer held under their own identity, whichever of the two rows was kept.
        assert change.gone.count(LATE_DAY) == 2
        assert any("gone from it" in line for line in change.says())


class TestWhereTheExactRulesDoNotNameOneRow:
    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Payments_WhenTheSettlementDayNamesTwoExportRows_NoRowIsJoined(self, stores, order):
        payments = equal_payments(both_listed_on_the_later_day=True)
        store = stores(order, payments)

        rows = payment_rows(store, {2000})

        feed_uids = set()
        for row in rows:
            feed_uids |= set(store.stated_ids_by_entity()[1].get(row.entity_id, set()))
        assert feed_uids == {"f-equal-1", "f-equal-2"}
        assert len(rows) >= 2, "two payments are never joined into one row"
        for row in rows:
            assert len(store.stated_ids_by_entity()[1].get(row.entity_id, set())) <= 1

    @pytest.mark.parametrize("order", FEED_LAST, ids=order_id)
    def test_Payments_WhenTheAggregatorStatesNoUid_NoRowIsJoinedForWantOfAnExactRule(
        self, directory, order
    ):
        store = household(directory(), order, late_settlement_payments(), linked=False)
        try:
            rows = payment_rows(store, LATE_MINORS)

            assert len(rows) > 6, "the order stays open: only a heuristic reaches the second row"
        finally:
            store.close()
