"""A row the export lists on its settlement day is the payment that settled on it.

The rule (`matching.guarded_settlement_plan`: the plan's moves, kept only where a whole group of
them changes no day's total) and what it must NOT do, read against the same
households as the measurement (`test_settlement_measurement`) and the same arrival orders,
shapes, and second export as the defect's pinned tests (`test_consecutive_days_nothing_joined`).
The last matcher change that moved which date a merged row keeps duplicated 245 rows on the
real store, so every test here compares a store built with the rule against the same store built
without it (`rule_off`), and the answers were decided before the first run.

KNOWN ANSWERS:

    the week of equal payments on consecutive days, in the deployed arrival order
        without the rule the measurement says seven rows sit one payment off and seven stored
        transactions would carry another date; with it, the very same seven transactions carry
        the date their export row lists (each one day later than it was made), no other
        transaction changes date or content key, and the measurement then says none
    a broken chain (the last export row missing), a chain whose last link the plan refuses
        left exactly as the existing order makes them: the stored transactions are identical
    a closed chain and a broken chain of another payee together
        only the closed one is applied: seven moves, seven re-dated, no day's total changed
    every arrival order, every corpus, a chain split across two files
        no day's total differs from the store built without the rule
    known balances stated on every row
        every balance that agreed before agrees after
    a second export listing the same rows again, live and rebuilt
        no row gains a second export sighting on another day: the first listing's rows are found
    an export row no stored transaction's settlement day names
        the stored transactions are exactly what they were without the rule, in every order
    an aggregator that states no id; an account with no first-party feed
        the same
    two rows of one size and date naming one transaction, two payees of one size on one day
        left to the existing order, so the stored transactions are as they were
"""

from __future__ import annotations

import pathlib
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date

import pytest

from consecutive_days_corpus import consecutive_payments
from late_settlement_corpus import (
    Payment,
    equal_payments,
    export_text,
    household,
    late_settlement_payments,
)
from obdi.ingest import matching
from obdi.ingest.pipeline import import_file
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.verify.exact_rule_measure import exact_rule_report
from test_absorbed_rows import second_export
from test_consecutive_days_nothing_joined import ORDERS, repeat_the_feed
from test_settlement_measurement import bakery_week
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import order_id, sources, walk_differences

REAL_ORDER = ("feed", "aggregator", "export")
SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]
WEEK = consecutive_payments(7)
#: The week, but the export never lists the last payment's settlement day.
BROKEN_WEEK = [*WEEK[:-1], replace(WEEK[-1], listed=None)]
BAKERY_BROKEN = bakery_week(broken=True)


@pytest.fixture
def build(tmp_path, monkeypatch) -> Iterator[Callable[..., Store]]:
    opened: list[Store] = []

    def make(
        order,
        payments,
        *,
        rule=True,
        rebuild=False,
        again=False,
        linked=True,
        more_exports=(),
        **more,
    ) -> Store:
        here: pathlib.Path = tmp_path / f"d{len(opened)}"
        here.mkdir()
        with monkeypatch.context() as patch:
            if not rule:
                patch.setattr(matching, "plan_settlement", lambda batch, index, **kw: {})
            store = household(here, order, payments, linked=linked, **more)
            opened.append(store)
            if again:
                repeat_the_feed(store, payments)
                second_export(store, here, payments)
            for number, rows in enumerate(more_exports):
                path = here / f"further-{number}.csv"
                path.write_text(export_text(rows), encoding="utf-8")
                import_file(store, path, account_id=MAIN, account_map=MAP)
            if rebuild:
                assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield make
    for store in opened:
        store.close()


def snapshot(store: Store) -> Counter[tuple]:
    """Every stored transaction of the account as a value, with no identity of its own."""
    return Counter(
        (
            t.amount_minor,
            t.value_date,
            t.content_key,
            t.occurrence,
            t.status.value,
            tuple(sorted(sources(store, t))),
        )
        for t in store.transactions_for_account(MAIN)
    )


def by_feed_uid(store: Store) -> dict[str, tuple[date, str | None]]:
    """Each payment's feed uid -> (the date its row carries, the day the export listed it)."""
    uids = store.stated_ids_by_entity()[1]
    listed = store.sighting_days([MAIN], ["starling-csv"])["starling-csv"]
    named = {
        t.entity_id: next(iter(uids[t.entity_id]))
        for t in store.transactions_for_account(MAIN)
        if t.entity_id in uids and not t.status.is_history
    }
    return {
        uid: (t.value_date, listed.get(t.entity_id))
        for t in store.transactions_for_account(MAIN)
        if (uid := named.get(t.entity_id, "")).startswith(("f-coffee-", "f-bread-"))
    }


def totals_by_day(store: Store) -> dict[date, int]:
    """What the counted transactions of the account add up to on each day they carry."""
    totals: dict[date, int] = {}
    for t in store.transactions_for_account(MAIN):
        if not t.status.is_history:
            totals[t.value_date] = totals.get(t.value_date, 0) + t.amount_minor
    return totals


def settlement_figures(store: Store):
    (found,) = [f for f in exact_rule_report(store, MAP).settlement if f.account == MAIN]
    return found


class TestWhatTheMeasurementSaysTheRuleChanges:
    @pytest.mark.parametrize("again", [False, True], ids=["once", "again"])
    @pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
    @pytest.mark.parametrize(
        ("payments", "safe_moves", "re_dated", "unsafe_moves"),
        [
            (WEEK, 7, 7, 0),
            (BROKEN_WEEK, 0, 0, 6),
            ([*WEEK, *BAKERY_BROKEN], 7, 7, 6),
        ],
        ids=["closed-chain", "broken-chain", "one-of-each"],
    )
    def test_Rule_WhenTheCorpusArrivesInTheDeployedOrder_DoesExactlyTheSafeMovesTheMeasurementSaid(
        self, build, rebuild, again, payments, safe_moves, re_dated, unsafe_moves
    ):
        before = build(REAL_ORDER, payments, rule=False, rebuild=rebuild, again=again)
        said = settlement_figures(before)
        after = build(REAL_ORDER, payments, rule=True, rebuild=rebuild, again=again)

        was, now = by_feed_uid(before), by_feed_uid(after)
        date_changed = [uid for uid in was if was[uid][0] != now[uid][0]]
        host_before = {(uid.split("-")[1], day): uid for uid, (_, day) in was.items() if day}
        host_after = {(uid.split("-")[1], day): uid for uid, (_, day) in now.items() if day}
        export_moved = [row for row in host_before if host_after.get(row) != host_before[row]]

        assert (said.moves_safe, said.redated_safe, said.moves_unsafe) == (
            safe_moves,
            re_dated,
            unsafe_moves,
        )
        assert len(export_moved) == said.moves_safe
        assert len(date_changed) == said.redated_safe
        assert totals_by_day(after) == totals_by_day(before), "no day's total changes"
        later = settlement_figures(after)
        assert (later.moves_safe, later.redated_safe) == (0, 0), "no safe move remains"
        assert later.moves_unsafe == said.moves_unsafe, "the unsafe groups are as they were"
        assert later.unreproduced == 0

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    @pytest.mark.parametrize(
        "payments",
        [WEEK, BROKEN_WEEK, [*WEEK, *BAKERY_BROKEN], WEEK[:4]],
        ids=["closed-chain", "broken-chain", "one-of-each", "short-week"],
    )
    def test_Rule_WhateverTheArrivalOrder_NoDaysTotalChanges(self, build, order, payments):
        before = build(order, payments, rule=False)
        after = build(order, payments, rule=True)

        assert totals_by_day(after) == totals_by_day(before)
        assert len(after.transactions_for_account(MAIN)) == len(
            before.transactions_for_account(MAIN)
        )

    @pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
    def test_Rule_WhenAnUnsafeGroupIsLeft_ItsTransactionsAreExactlyAsTheExistingOrderMadeThem(
        self, build, rebuild
    ):
        before = snapshot(build(REAL_ORDER, BROKEN_WEEK, rule=False, rebuild=rebuild))
        after = snapshot(build(REAL_ORDER, BROKEN_WEEK, rule=True, rebuild=rebuild))

        assert after == before

    def test_Rule_WhenTheLastLinkOfAChainIsRefused_NothingOfTheChainMoves(self, build):
        twin = (("Coffee Co", -450, date(2026, 9, 22)),)

        before = snapshot(build(REAL_ORDER, WEEK, rule=False, extra_listed=twin))
        after = snapshot(build(REAL_ORDER, WEEK, rule=True, extra_listed=twin))

        assert after == before

    @pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
    def test_Rule_WhenAChainArrivesInTwoFiles_NeitherFileMovesHalfAGroup(self, build, rebuild):
        """Seven payments, the first file listing the first four settlement days and the second
        the other three. Each file is judged on its own, against the rows held when it arrives,
        and a half is applied only if it changes no day's total by itself."""
        first = [replace(p, listed=p.listed if n < 4 else None) for n, p in enumerate(WEEK)]
        later = [[(p.name, -p.minor, p.listed) for p in WEEK[4:] if p.listed]]

        without = build(REAL_ORDER, first, rule=False, rebuild=rebuild, more_exports=later)
        with_rule = build(REAL_ORDER, first, rule=True, rebuild=rebuild, more_exports=later)

        assert totals_by_day(with_rule) == totals_by_day(without)

    def test_Rule_WhenKnownBalancesAreStatedOnEveryDay_EveryOneReproducedBeforeIsReproducedAfter(
        self, build
    ):
        """The export states a balance on each row it lists, which is how the real account
        comes by its known balances. The closed chain is applied, and no balance that agreed
        before disagrees after."""
        before = build(REAL_ORDER, WEEK, rule=False)
        after = build(REAL_ORDER, WEEK, rule=True)

        assert walk_differences(before) == {}
        assert walk_differences(after) == {}
        assert by_feed_uid(after) != by_feed_uid(before), "and the rule did move rows"

    def test_Rule_WhenAnUnsafeGroupWouldHaveBrokenAKnownBalance_ThatBalanceStillAgrees(
        self, build
    ):
        before = build(REAL_ORDER, BROKEN_WEEK, rule=False)
        after = build(REAL_ORDER, BROKEN_WEEK, rule=True)

        assert set(walk_differences(after)) <= set(walk_differences(before))

    def test_Rule_WhenTheWeekArrivesInTheDeployedOrder_EachRowCarriesTheDayItsExportRowLists(
        self, build
    ):
        after = by_feed_uid(build(REAL_ORDER, WEEK))

        for day in range(15, 22):
            assert after[f"f-coffee-{day}"] == (date(2026, 9, day + 1), f"2026-09-{day + 1}")

    def test_Rule_WhenTheWeekArrivesInTheDeployedOrder_NoOtherTransactionMovesDateOrKey(
        self, build
    ):
        before = snapshot(build(REAL_ORDER, WEEK, rule=False))
        after = snapshot(build(REAL_ORDER, WEEK, rule=True))

        household_rows = {k for k in before if k[0] != -450}
        assert {k for k in after if k[0] != -450} == household_rows
        assert all(before[k] == after[k] for k in household_rows)


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestAFileListingTheSameRowsAgain:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Rule_WhenASecondExportListsTheSameRows_NoRowIsStoredTwiceOrLeftWithoutAnExportDay(
        self, build, order, rebuild, again
    ):
        store = build(order, WEEK, rebuild=rebuild, again=again)

        rows = by_feed_uid(store)

        assert len(rows) == 7
        assert all(listed is not None for _, listed in rows.values())
        days = [listed for _, listed in rows.values()]
        assert len(set(days)) == 7

    def test_Rule_WhenTheFeedIsFetchedAgainAfterTheExport_EachRowKeepsItsExportDay(
        self, build, rebuild, again
    ):
        store = build(REAL_ORDER, WEEK, rebuild=rebuild, again=again)

        for day in range(15, 22):
            assert by_feed_uid(store)[f"f-coffee-{day}"][1] == f"2026-09-{day + 1}"


@pytest.mark.parametrize("order", ORDERS, ids=order_id)
class TestWhereTheRuleHasNothingToSay:
    def test_Rule_WhenNoStoredTransactionSettlesOnTheRowsDay_StoredTransactionsAreUnchanged(
        self, build, order
    ):
        lone = Payment(
            "f-lone", "Abroad Shop", 3333, "2026-09-14T10:00:00.000Z", None, None, 14
        )
        twelve_days_on = (("Abroad Shop", -3333, date(2026, 9, 26)),)

        before = snapshot(build(order, [lone], rule=False, extra_listed=twelve_days_on))
        after = snapshot(build(order, [lone], rule=True, extra_listed=twelve_days_on))

        assert after == before

    def test_Rule_WhenTheAggregatorStatesNoId_StoredTransactionsMatchTheLateSettlementOnes(
        self, build, order
    ):
        before = snapshot(build(order, late_settlement_payments(), rule=False, linked=False))
        after = snapshot(build(order, late_settlement_payments(), rule=True, linked=False))

        if order == REAL_ORDER:
            assert after == before
        assert sum(after.values()) >= 6

    def test_Rule_WhenTwoRowsOfOneSizeAndDateNameOneTransaction_ThoseRowsAreLeftToTheExistingOrder(
        self, build, order
    ):
        lone = Payment(
            "f-lone", "Abroad Shop", 3333, "2026-09-14T10:00:00.000Z",
            "2026-09-15T03:00:00.000Z", date(2026, 9, 15), 14,
        )
        twin = (("Abroad Shop", -3333, date(2026, 9, 15)),)

        before = snapshot(build(order, [lone], rule=False, extra_listed=twin))
        after = snapshot(build(order, [lone], rule=True, extra_listed=twin))

        assert after == before

    def test_Rule_WhenTwoPayeesOfOneSizeSettleOnOneDay_EachRowStillFindsItsOwnPayee(
        self, build, order
    ):
        settled = "2026-09-17T03:00:00.000Z"
        listed = date(2026, 9, 17)
        both = [
            Payment("f-gym", "Gym", 700, "2026-09-15T10:00:00.000Z", settled, listed, 15),
            Payment("f-pool", "Pool", 700, "2026-09-16T10:00:00.000Z", settled, listed, 16),
        ]

        before = snapshot(build(order, both, rule=False))
        after = snapshot(build(order, both, rule=True))

        assert after == before


class TestAccountsWithNoFirstPartyFeed:
    @pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
    def test_Rule_WhenAnAccountHasNoFeed_StoredTransactionsAreAsTheyWereWithoutIt(
        self, build, rebuild
    ):
        order = ("export", "aggregator")

        before = snapshot(build(order, WEEK, rule=False, rebuild=rebuild))
        after = snapshot(build(order, WEEK, rule=True, rebuild=rebuild))

        assert after == before


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestSetsOfEqualPayments:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Rule_WhenTwoEqualPaymentsAreListedTogether_EachIsOneRowSeenByAllThree(
        self, build, order, rebuild, again
    ):
        store = build(
            order, equal_payments(both_listed_on_the_later_day=True), rebuild=rebuild, again=again
        )

        rows = [t for t in store.transactions_for_account(MAIN) if t.amount_minor == -2000]

        assert len(rows) == 2
        assert [sources(store, t) for t in rows] == [
            {"starling", "starling-csv", "truelayer"}
        ] * 2
