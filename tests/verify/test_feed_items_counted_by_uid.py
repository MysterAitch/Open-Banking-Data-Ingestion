"""The bank's own feed lists a row by its uid, whichever fetch listed it.

The deployed page said, of a Space fed only by the bank's own feed:

    2026-10-01 starling-space-bills via starling (out): 1 row of one size and direction
    listed, 2 held: the surplus row is booked, sighted by 3 other artefacts of starling
    (observed 2026-10-01); an earlier-listed row's id is absent from 4 of the 6 later fetches
    that ask for its day (4 asking by changesSince), the newest included, and no item of the same
    size, direction, and recipient under another id first appears in the first of them

Two payments of one size on one day, each with its own uid, were never listed TOGETHER by one
incremental fetch, and the check took the most any ONE artefact lists. The feed's uids are stable
for an item's life (`matching.SETTLEMENT_KEEPS_ID`), so what is listed is every distinct uid any
fetch listed. Expected on that store after this change, and not known until it is read: two listed,
two held.

What counting by uid stops seeing is a RE-ISSUE: the bank dropping an item and making it again under
a new uid. Its signature (`movement_completeness._reissue_evidence`) is still a fault, said as what
it is. A `changesSince` fetch lists what CHANGED, so its silence about an unchanged item is not
absence and is treated as saying nothing; only a fetch asked by a window of transaction time counts.

The aggregator is NOT counted this way: its `transaction_id` changes between fetches for one
payment (`test_provider_id_continuity`), so distinct aggregator ids across artefacts are not
distinct payments (`test_card_row_fault_measured` keeps that shape reporting a surplus).

Each scenario is bare-store fetches of one account (the bank's feed items through the real
provider), landed live through the door a pull uses and rebuilt from raw, with and without a third
artefact that lists the same rows again. Artefacts are built after the store's evidence is landed,
because a rebuild replays by the stamp an artefact is built with.

KNOWN ANSWERS, decided before the first run:

    two payments of one size on one day, each listed by a different changesSince fetch, either order
        two listed, two held, no fault
    a later WINDOW fetch over the day lists only a re-issue of the item
        one fault: a row whose id the feed stopped listing ... possibly one payment held twice
    the same with a changesSince fetch
        no fault: it says nothing of an unchanged item
    the window ends before the day, or the later item is another recipient's
        no fault
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from landing import rebuild_from_raw
from obdi.core.models import RawArtefact
from obdi.ingest.family_anchors import families_of
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling
from obdi.ingest.rebuild import parse_artefact_transactions
from obdi.ingest.space_attribution import fold_space_copies
from obdi.ingest.store import Store
from obdi.verify.movement_completeness import check_rows
from round_up_corpus import card_payment
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_movement_rows_listed import canonical
from test_space_attribution import MAIN, MAP

SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]

REISSUE = (
    "a row whose id the feed stopped listing when another id of the same size and recipient "
    "appeared: possibly one payment held twice"
)


def feed(items: list[dict[str, Any]], ask: str) -> RawArtefact:
    return starling.artefact_for(
        json.dumps({"feedItems": items}).encode(),
        account_id="starling:cat-main",
        kind="feed",
        origin=f"{FEED_ORIGIN}?{ask}",
    )


def since(day: str) -> str:
    return f"changesSince={day}T00:00:00Z"


def window(first: str, last: str) -> str:
    return (
        f"minTransactionTimestamp={first}T00:00:00.000Z&"
        f"maxTransactionTimestamp={last}T23:59:59.000Z"
    )


def coffee(uid: str) -> dict[str, Any]:
    return card_payment(uid, "Coffee", 350, 3)


Fetches = Callable[[], list[RawArtefact]]


def after(first: str, second_ask: str, third_ask: str, *, again: bool, newer: str) -> Fetches:
    """The item `first`, a later fetch listing only `newer`, then maybe `newer` once more."""

    def build() -> list[RawArtefact]:
        found = [
            feed([coffee(first)], since("2026-09-01")),
            feed([coffee(newer)], second_ask),
        ]
        if again:
            # The newer item again beside an unrelated one, so the bytes differ.
            found.append(feed([coffee(newer), card_payment("f-extra", "Extra", 111, 5)], third_ask))
        return found

    return build


@pytest.fixture
def make(tmp_path) -> Iterator[Callable[..., Store]]:
    opened: list[Store] = []

    def build(fetches: Fetches, *, rebuild: bool) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = Store(directory / "feed.sqlite3")
        opened.append(store)
        land_evidence(store)
        for artefact in fetches():
            store.land_artefact(artefact)
            if not rebuild:
                reconcile_batch(
                    store,
                    parse_artefact_transactions(
                        artefact.source, artefact.payload, MAIN, artefact.digest
                    ),
                    digest=artefact.digest,
                    space_blind=families_of(store, MAP).blind_in,
                )
                fold_space_copies(store, MAP)
        if rebuild:
            assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def faults(store: Store) -> list[str]:
    return [fault.says() for fault in check_rows(store, canonical).row_faults]


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestTwoPaymentsNeverListedTogether:
    @pytest.mark.parametrize("uids", [("f-a", "f-b"), ("f-b", "f-a")], ids=["a-then-b", "b-then-a"])
    def test_Fault_WhenTwoPaymentsOfOneSizeAreEachListedByAnIncrementalFetch_ThereIsNone(
        self, make, rebuild, again, uids
    ):
        first, second = uids
        fetches = after(first, since("2026-09-02"), since("2026-09-03"), again=again, newer=second)

        store = make(fetches, rebuild=rebuild)

        report = check_rows(store, canonical)
        assert report.row_faults == []
        assert report.rows_listed == report.rows_held

    def test_Fault_WhenTheyWereListedTogetherToo_ThereIsStillNone(self, make, rebuild, again):
        def fetches() -> list[RawArtefact]:
            return [
                feed([coffee("f-a")], since("2026-09-01")),
                feed([coffee("f-a"), coffee("f-b")], since("2026-09-02")),
            ]

        assert faults(make(fetches, rebuild=rebuild)) == []


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestAReissueIsStillAFault:
    def test_Fault_WhenALaterWindowFetchListsOnlyAReissue_SaysPossiblyOnePaymentHeldTwice(
        self, make, rebuild, again
    ):
        fetches = after(
            "f-coffee",
            window("2026-09-01", "2026-09-10"),
            window("2026-09-02", "2026-09-11"),
            again=again,
            newer="f-coffee-2",
        )

        (line,) = faults(make(fetches, rebuild=rebuild))

        how_many = "all 2 later fetches that ask" if again else "the 1 later fetch that asks"
        assert line == (
            f"2026-09-03 {MAIN} via starling (out): {REISSUE} (its id is absent from {how_many} "
            "for its day, asking by transaction-time window)"
        )

    def test_Fault_WhenTheLaterFetchIsChangesSince_SaysNothing(self, make, rebuild, again):
        fetches = after(
            "f-coffee", since("2026-09-02"), since("2026-09-03"), again=again, newer="f-coffee-2"
        )

        assert faults(make(fetches, rebuild=rebuild)) == []

    def test_Fault_WhenTheWindowEndsBeforeTheItemsDay_SaysNothing(self, make, rebuild, again):
        fetches = after(
            "f-coffee",
            window("2026-09-04", "2026-09-10"),
            window("2026-09-05", "2026-09-11"),
            again=again,
            newer="f-coffee-2",
        )

        assert faults(make(fetches, rebuild=rebuild)) == []

    def test_Fault_WhenTheLaterWindowListsAnotherRecipientsPayment_SaysNothing(
        self, make, rebuild, again
    ):
        def fetches() -> list[RawArtefact]:
            return [
                feed([coffee("f-coffee")], since("2026-09-01")),
                feed(
                    [card_payment("f-other", "Garage", 350, 3)],
                    window("2026-09-01", "2026-09-10"),
                ),
            ]

        assert faults(make(fetches, rebuild=rebuild)) == []
