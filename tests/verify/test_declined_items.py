"""A payment the bank later declined is history, in every arrival order, live and rebuilt.

Each world is invented and landed through the real doors: a feed fetch is reconciled as a
pull does it and then the pass runs (`declined_items.void_declined_items`); an export is
imported by `import_file`, which runs the pass itself; and one scenario is a real
`pull_starling` over a stand-in provider. A rebuild replays the same artefacts.
The answers were decided before the first run.

KNOWN ANSWERS:

    five payments SETTLED in one fetch and DECLINED in a later one
        all five rows are void, the balance holds none of them, and the measurement that
        named five now names none: the rule changed exactly what the measurement said
    a payment PENDING then DECLINED
        void
    a payment DECLINED first and SETTLED later; SETTLED, DECLINED, then SETTLED again
        booked: the newest statement is that it settled
    a payment SETTLED then DECLINED that the export also lists, in each of three arrival orders
        left counted and queued for a person, never voided: the bank's own export says the
        money moved, which nothing here can overrule
    a payment with a round-up, SETTLED then DECLINED
        the payment's row is void and the round-up leg stays booked, as before
    a transfer into a Space, SETTLED then DECLINED, with the Space's side landed
        the declined leg is void and is offered to no pair
    live and rebuilt
        every row's status, date, and content key are the same
    a span protected through the payment's day
        reads as changed once the row is voided
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import pytest

from late_settlement_corpus import export_text
from obdi.core.models import TransactionStatus
from obdi.ingest import rebuild
from obdi.ingest.declined_items import DECLINED_DOUBT, declined_void_entities, void_declined_items
from obdi.ingest.feed_statuses import rows_with_no_row_status
from obdi.ingest.pipeline import import_file, pair_transfers_across_store
from obdi.ingest.providers import starling
from obdi.ingest.pull import pull_starling
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.verify.agreement import standing_of
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.exact_rule_measure import exact_rule_report
from obdi.verify.movement_completeness import MovementCompleteness
from obdi.verify.protection import broken_protections, press, recheck
from round_up_corpus import SPACE_FEED_ORIGIN, card_payment, space_arrival
from test_absorbed_rows import arrive
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import BILLS, MAIN, MAP
from test_space_blind_rows_and_internal_legs import transfer_to_the_space

ROUND_UP = {"goalCategoryUid": "cat-bills", "amount": {"currency": "GBP", "minorUnits": 50}}
VOID = TransactionStatus.VOID
BOOKED = TransactionStatus.BOOKED
PENDING = TransactionStatus.PENDING


def payment(day: int, status: str = "SETTLED", **more: Any) -> dict[str, Any]:
    return card_payment(f"f-day-{day}", f"Shop {day}", 100 + day, day, status=status, **more)


def feed(items: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    return ("feed", items)


def space(items: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    return ("space", items)


def export(*rows: tuple[str, int, date]) -> tuple[str, list[tuple[str, int, date]]]:
    return ("export", list(rows))


@pytest.fixture
def worlds(tmp_path, monkeypatch) -> Iterator[Callable[..., Store]]:
    opened: list[Store] = []

    def build(*steps, rebuild_it: bool = False, rule: bool = True) -> Store:
        here: pathlib.Path = tmp_path / f"d{len(opened)}"
        here.mkdir()
        store = Store(here / "household.sqlite3")
        opened.append(store)
        land_evidence(store)
        with monkeypatch.context() as patch:
            if not rule:
                patch.setattr(rebuild, "void_declined_items", lambda store: None)
            for number, (kind, content) in enumerate(steps):
                if kind == "export":
                    path = here / f"export-{number}.csv"
                    path.write_text(export_text(content), encoding="utf-8")
                    if rule:
                        import_file(store, path, account_id=MAIN, account_map=MAP)
                    else:
                        with patch.context() as off:
                            off.setattr(
                                "obdi.ingest.declined_items.void_declined_items", lambda store: None
                            )
                            import_file(store, path, account_id=MAIN, account_map=MAP)
                    continue
                main = kind == "feed"
                asked = f"?changesSince=2026-09-{number + 2:02}T00:00:00Z"
                arrive(
                    store,
                    starling.artefact_for(
                        json.dumps({"feedItems": content}).encode(),
                        account_id="starling:cat-main" if main else "starling:cat-bills",
                        kind="feed",
                        origin=(FEED_ORIGIN if main else SPACE_FEED_ORIGIN) + asked,
                    ),
                    MAIN if main else BILLS,
                )
                if rule:
                    void_declined_items(store)
            if rebuild_it:
                assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def row_of(store: Store, uid: str):
    (held,) = [t for t in store.transactions_for_account(MAIN) if t.source_id == uid]
    return held


def state(store: Store) -> dict[str, tuple]:
    return {
        t.entity_id: (t.status.value, t.value_date, t.content_key, t.occurrence)
        for account in (MAIN, BILLS)
        for t in store.transactions_for_account(account)
    }


def balance(store: Store) -> int:
    rows = store.transactions_for_account(MAIN)
    return sum(t.amount_minor for t in rows if not t.status.is_history)


def measured(store: Store):
    (found,) = [f for f in exact_rule_report(store, MAP).no_row_status if f.account == MAIN]
    return found


SHAPES = [False, True]
SHAPE_IDS = ["live", "rebuilt"]
DAYS = range(3, 8)


@pytest.mark.parametrize("rebuilt", SHAPES, ids=SHAPE_IDS)
class TestAPaymentTheBankLaterDeclined:
    def test_Rows_WhenSettledThenDeclined_AreVoidAndNoLongerCounted(self, worlds, rebuilt):
        store = worlds(
            feed([payment(d) for d in DAYS]),
            feed([payment(d, "DECLINED") for d in DAYS]),
            rebuild_it=rebuilt,
        )

        assert [row_of(store, f"f-day-{d}").status for d in DAYS] == [VOID] * 5
        assert balance(store) == 0

    def test_Rule_WhenSettledThenDeclined_ChangesExactlyTheRowsTheMeasurementNamed(
        self, worlds, rebuilt
    ):
        steps = (feed([payment(d) for d in DAYS]), feed([payment(d, "DECLINED") for d in DAYS]))
        before = worlds(*steps, rebuild_it=rebuilt, rule=False)
        said = {r.row.entity_id for r in measured(before).rows}
        after = worlds(*steps, rebuild_it=rebuilt)

        changed = {
            t.entity_id
            for t in after.transactions_for_account(MAIN)
            if state(before)[t.entity_id][0] != t.status.value
        }

        assert len(said) == 5
        assert changed == said
        assert measured(after).rows == []
        assert len(declined_void_entities(after)) == 5

    def test_Row_WhenPendingThenDeclined_IsVoid(self, worlds, rebuilt):
        store = worlds(
            feed([payment(4, "PENDING")]), feed([payment(4, "DECLINED")]), rebuild_it=rebuilt
        )

        assert row_of(store, "f-day-4").status is VOID

    def test_Row_WhenAnotherStatusMakesNoRow_IsVoidToo(self, worlds, rebuilt):
        store = worlds(
            feed([payment(4)]), feed([payment(4, "ACCOUNT_CHECK")]), rebuild_it=rebuilt
        )

        assert row_of(store, "f-day-4").status is VOID

    def test_Row_WhenDeclinedFirstAndSettledLater_IsBooked(self, worlds, rebuilt):
        store = worlds(
            feed([payment(4, "DECLINED")]), feed([payment(4)]), rebuild_it=rebuilt
        )

        assert row_of(store, "f-day-4").status is BOOKED
        assert declined_void_entities(store) == frozenset()

    def test_Row_WhenSettledDeclinedThenSettledAgain_IsBooked(self, worlds, rebuilt):
        store = worlds(
            feed([payment(4)]),
            feed([payment(4, "DECLINED")]),
            feed([payment(4)]),
            rebuild_it=rebuilt,
        )

        assert row_of(store, "f-day-4").status is BOOKED

    def test_Row_WhenNothingWasDeclined_IsUntouched(self, worlds, rebuilt):
        store = worlds(feed([payment(4)]), feed([payment(4)]), rebuild_it=rebuilt)

        assert row_of(store, "f-day-4").status is BOOKED


LISTED = export(("Shop 9", -109, date(2026, 9, 9)))
SETTLED_9, DECLINED_9 = feed([payment(9)]), feed([payment(9, "DECLINED")])
EXPORT_ARRIVAL_ORDERS = {
    "feed-declined-export": (SETTLED_9, DECLINED_9, LISTED),
    "feed-export-declined": (SETTLED_9, LISTED, DECLINED_9),
    "export-feed-declined": (LISTED, SETTLED_9, DECLINED_9),
}


@pytest.mark.parametrize("rebuilt", SHAPES, ids=SHAPE_IDS)
class TestAPaymentAnotherSourceAlsoLists:
    @pytest.mark.parametrize("order", sorted(EXPORT_ARRIVAL_ORDERS))
    def test_Row_WhenTheExportListsADeclinedPayment_IsLeftCountedAndQueuedForAPerson(
        self, worlds, rebuilt, order
    ):
        store = worlds(*EXPORT_ARRIVAL_ORDERS[order], rebuild_it=rebuilt)

        held = [t for t in store.transactions_for_account(MAIN) if t.amount_minor == -109]
        reasons = [
            r["reason"]
            for r in store.connection.execute("SELECT reason FROM review_queue")
            if DECLINED_DOUBT in r["reason"]
        ]

        assert [t.status for t in held] == [BOOKED]
        assert len(reasons) == 1
        assert [r.corroborated for r in rows_with_no_row_status(store, MAIN)] == [True]


@pytest.mark.parametrize("rebuilt", SHAPES, ids=SHAPE_IDS)
class TestWhatHangsOffAVoidedPayment:
    def test_RoundUpLeg_WhenItsPaymentIsDeclined_StaysBooked(self, worlds, rebuilt):
        store = worlds(
            feed([payment(6, round_up=ROUND_UP)]),
            feed([payment(6, "DECLINED", round_up=ROUND_UP)]),
            rebuild_it=rebuilt,
        )

        leg = row_of(store, "f-day-6:round-up")

        assert row_of(store, "f-day-6").status is VOID
        assert leg.status is BOOKED
        assert leg.amount_minor == -50

    def test_TransferPair_WhenTheOutLegIsDeclined_IsOfferedNoPair(self, worlds, rebuilt):
        world = (
            feed([transfer_to_the_space("f-move", 777, 5)]),
            space([space_arrival("s-move", 777, 5)]),
        )
        paired = worlds(*world, rebuild_it=rebuilt)
        pair_transfers_across_store(paired, MAP)
        declined = worlds(
            *world, feed([transfer_to_the_space("f-move", 777, 5) | {"status": "DECLINED"}]),
            rebuild_it=rebuilt,
        )
        pair_transfers_across_store(declined, MAP)

        confirmed = {t.source_id for t in paired.all_transactions() if t.transfer_confirmed}
        left = {t.source_id for t in declined.all_transactions() if t.transfer_confirmed}

        assert {"f-move", "s-move"} <= confirmed
        assert row_of(declined, "f-move").status is VOID
        assert left == set()


class TestLiveAndRebuiltAgree:
    @pytest.mark.parametrize(
        "steps",
        [
            pytest.param(
                lambda: (
                    feed([payment(d) for d in DAYS]),
                    feed([payment(d, "DECLINED") for d in DAYS]),
                ),
                id="settled-declined",
            ),
            pytest.param(
                lambda: (feed([payment(4, "DECLINED")]), feed([payment(4)])), id="declined-settled"
            ),
            pytest.param(
                lambda: (
                    feed([payment(4), payment(5, "PENDING")]),
                    feed([payment(4, "DECLINED"), payment(5, "DECLINED")]),
                    feed([payment(4)]),
                ),
                id="mixed",
            ),
        ],
    )
    def test_Rows_WhenAFeedIsLandedLiveThenRebuilt_EveryStatusDateAndKeyIsTheSame(
        self, worlds, steps
    ):
        live = worlds(*steps())
        before = state(live)

        assert rebuild_from_raw(live, account_map=MAP).problems == []

        assert state(live) == before


class TestAProtectedSpan:
    def test_Protection_WhenARowInTheSpanIsVoidedBecauseTheBankDeclinedIt_ReadsAsChanged(
        self, worlds
    ):
        store = worlds(feed([payment(d) for d in range(4, 9)]), rule=False)
        record_stated_anchor(store, MAIN, "2026-09-03", "100.00")
        record_stated_anchor(store, MAIN, "2026-09-08", "94.70")
        opening = effective_opening(store, MAIN)
        press(
            store,
            MAIN,
            "2026-09-08",
            opening=opening,
            standing=standing_of(opening, [MAIN], MovementCompleteness()),
        )
        assert broken_protections(store) == []

        arrive(
            store,
            starling.artefact_for(
                json.dumps({"feedItems": [payment(6, "DECLINED")]}).encode(),
                account_id="starling:cat-main",
                kind="feed",
                origin=f"{FEED_ORIGIN}?changesSince=2026-09-09T00:00:00Z",
            ),
        )
        recheck(store)
        assert broken_protections(store) == [], "a declined item alone changes no row"

        void_declined_items(store)
        recheck(store)

        (broken,) = broken_protections(store)
        assert broken.account == MAIN


class TestWhatThePassReads:
    """The pass costs what a pull lands, not what the store holds.

    Measured before the gate: 400 artefacts of five items took 406 statements and 0.155 s cold,
    and the count grew in step. Declared in advance, with the gate: the statements are the
    scan, one read per body that may hold a no-row item, and no more, however many artefacts
    are held, and no account's artefacts are read whole while no counted row was sighted
    under a no-row uid.
    """

    @staticmethod
    def landed(store: Store, count: int, declined_every: int) -> int:
        """`count` fetches of five payments each; every `declined_every`th also holds a
        declined attempt that never made a row. Returns how many held one."""
        held = 0
        for n in range(count):
            items = [
                card_payment(f"f-{n}-{k}", f"Shop {k}", 100 + k + n, 1 + (n % 27))
                for k in range(5)
            ]
            if n % declined_every == 0:
                items.append(card_payment(f"f-declined-{n}", "Cafe", 999, 9, status="DECLINED"))
                held += 1
            arrive(
                store,
                starling.artefact_for(
                    json.dumps({"feedItems": items}).encode(),
                    account_id="starling:cat-main",
                    kind="feed",
                    origin=f"{FEED_ORIGIN}?changesSince=2026-09-01T00:00:00Z&n={n}",
                ),
            )
        return held

    @pytest.mark.parametrize("count", [30, 120])
    def test_Pass_WhenDeclinedAttemptsNeverMadeARow_ReadsOnlyTheBodiesThatHoldOne(
        self, tmp_path, monkeypatch, count
    ):
        from obdi.ingest import feed_statuses

        with Store(tmp_path / "scale.sqlite3") as store:
            land_evidence(store)
            held = self.landed(store, count, declined_every=10)
            feed_statuses._ITEMS_BY_DIGEST.clear()
            monkeypatch.setattr(
                feed_statuses.FeedStatuses,
                "__init__",
                lambda *a, **k: pytest.fail("an account's artefacts were read whole"),
            )
            statements: list[str] = []
            store.connection.set_trace_callback(statements.append)

            outcome = void_declined_items(store)

            store.connection.set_trace_callback(None)
            assert outcome.voided == 0
            reads = [s for s in statements if "SELECT payload FROM raw_artefacts" in s]
            assert len(reads) == held
            assert len(statements) <= held + 12

    def test_Pass_WhenARowIsSightedUnderADeclinedUid_ReadsThatAccountWholeAndVoidsIt(
        self, tmp_path
    ):
        with Store(tmp_path / "scale.sqlite3") as store:
            land_evidence(store)
            self.landed(store, 12, declined_every=4)
            arrive(
                store,
                starling.artefact_for(
                    json.dumps({"feedItems": [payment(5)]}).encode(),
                    account_id="starling:cat-main",
                    kind="feed",
                    origin=f"{FEED_ORIGIN}?changesSince=2026-09-05T00:00:00Z",
                ),
            )
            arrive(
                store,
                starling.artefact_for(
                    json.dumps({"feedItems": [payment(5, "DECLINED")]}).encode(),
                    account_id="starling:cat-main",
                    kind="feed",
                    origin=f"{FEED_ORIGIN}?changesSince=2026-09-06T00:00:00Z",
                ),
            )

            outcome = void_declined_items(store)

            assert outcome.voided == 1
            assert row_of(store, "f-day-5").status is VOID


class FakeStarling:
    """A stand-in provider whose main feed answers with the items it is told to."""

    def __init__(self, monkeypatch) -> None:
        self.items: list[dict[str, Any]] = []
        accounts = [{"accountUid": "acc-main", "defaultCategory": "cat-main"}]
        body = json.dumps({"accounts": accounts}).encode()
        monkeypatch.setattr(starling, "fetch_accounts", lambda token: (accounts, body))
        categories = [
            starling.Category("cat-main", "main", False),
            starling.Category("cat-bills", "Bills", True),
        ]
        goals = json.dumps({"savingsGoals": [{"savingsGoalUid": "cat-bills"}]}).encode()
        monkeypatch.setattr(starling, "fetch_categories", lambda token, uid: (categories, goals))
        monkeypatch.setattr(starling, "fetch_balance", lambda token, uid: b'{"clearedBalance": {}}')
        monkeypatch.setattr(
            starling, "fetch_identifiers", lambda token, uid: b'{"accountIdentifiers": []}'
        )
        monkeypatch.setattr(starling, "fetch_feed", self.feed)

    def feed(self, token, account_uid, category_uid, *, since=None, since_at=None, client=None):
        got = list(self.items) if category_uid == "cat-main" else []
        return got, json.dumps({"feedItems": got}).encode(), "changesSince=2016-01-01T00:00:00Z"


class TestARealPull:
    @pytest.mark.parametrize("rebuilt", SHAPES, ids=SHAPE_IDS)
    def test_Pull_WhenALaterFetchReportsTheItemDeclined_TheRowIsVoid(
        self, tmp_path, monkeypatch, rebuilt
    ):
        provider = FakeStarling(monkeypatch)
        with Store(tmp_path / "pulled.sqlite3") as store:
            provider.items = [payment(5)]
            pull_starling(store, "token", account_map=MAP)
            assert row_of(store, "f-day-5").status is BOOKED

            provider.items = [payment(5, "DECLINED")]
            pull_starling(store, "token", account_map=MAP)

            assert row_of(store, "f-day-5").status is VOID
            live = state(store)
            if rebuilt:
                assert rebuild_from_raw(store, account_map=MAP).problems == []
                assert state(store) == live
