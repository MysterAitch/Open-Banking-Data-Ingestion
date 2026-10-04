"""A held-more-than-listed fault says whether the bank's own feed stopped listing a row.

The deployed page said, of a Space fed only by the bank's own feed:

    2026-10-01 starling-space-bills via starling (out): 1 row of one size and direction
    listed, 2 held: the surplus row is booked, sighted by 3 other artefacts of starling
    (observed 2026-10-01)

Three earlier fetches sighted a row the newest listing does not contain beside the one it does.
Whether the bank withdrew the item (and a store that keeps the row is wrong) or the two are
different payments is not decided by anything the line carried. A `changesSince` ask lists what
CHANGED, so its silence about an item says nothing of whether the bank still holds it; a window
asked by transaction time lists what exists. The line now counts the later fetches that asked for
the row's day without listing its id, says how each asked, and says whether another id of the same
size, direction, and recipient first appears in the first of them.

Each scenario is bare-store fetches of one account (the bank's feed items through the real
provider), landed live through the door a pull uses and again rebuilt from raw, with and without a
third artefact that lists the newer item again (an overlapping fetch). Artefacts are built after
the store's evidence is landed, because a rebuild replays by the stamp an artefact is built with.

KNOWN ANSWERS, decided before the first run:

    a later changesSince fetch lists only a re-issue of the item, asking from before its day
        absent from the 1 later fetch (all 2 with the overlapping one), by changesSince,
        and the other id first appears in it
    the later fetch asks from the day AFTER the item
        no later fetch asks for the day: nothing says the bank withdrew it
    the later fetch is a bounded window over the item's day
        absent, asking by transaction-time window
    the later fetch lists a different payment of that size to that recipient's neighbour
        absent, and no item of that size and recipient first appears
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from obdi.family_anchors import families_of
from obdi.ingest import reconcile_batch
from obdi.models import RawArtefact
from obdi.movement_completeness import check_rows
from obdi.providers import starling
from obdi.rebuild import parse_artefact_transactions, rebuild_from_raw
from obdi.space_attribution import fold_space_copies
from obdi.store import Store
from round_up_corpus import card_payment
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_movement_rows_listed import canonical
from test_space_attribution import MAIN, MAP

SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]

SUCCESSOR = (
    "an item of the same size, direction, and recipient under another id first appears in the "
    "first of them"
)
NOTHING_SAYS = (
    "no fetch landed after a row's first listing asks for its day without listing that row, "
    "so nothing says the bank withdrew one"
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


def reissue(second_ask: str, third_ask: str, *, again: bool, newer: str = "f-coffee-2") -> Fetches:
    """The item, then a later fetch listing only its re-issue, then maybe an overlapping one."""

    def build() -> list[RawArtefact]:
        found = [
            feed([coffee("f-coffee")], since("2026-09-01")),
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


def said(store: Store) -> list[str]:
    return [fault.says() for fault in check_rows(store, canonical).row_faults]


def feed_clause(line: str) -> str:
    """What the line says of the feed, between its sightings clause and its balances clause."""
    clauses = line.split("; ")
    return "; ".join(c for c in clauses[1:] if not c.startswith("the known balances"))


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestWhenALaterFetchStopsListingTheItem:
    def test_Fault_WhenALaterChangesSinceFetchListsOnlyAReissue_CountsItAndSaysTheIdReappeared(
        self, make, rebuild, again
    ):
        fetches = reissue(since("2026-09-02"), since("2026-09-03"), again=again)

        (line,) = said(make(fetches, rebuild=rebuild))

        how_many = "all 2 later fetches that ask" if again else "the 1 later fetch that asks"
        asked = "2 asking by changesSince" if again else "1 asking by changesSince"
        assert feed_clause(line) == (
            f"an earlier-listed row's id is absent from {how_many} for its day ({asked}), "
            f"the newest included, and {SUCCESSOR}"
        )

    def test_Fault_WhenTheLaterFetchAsksFromTheDayAfterTheItem_SaysNothingSaysItWasWithdrawn(
        self, make, rebuild, again
    ):
        fetches = reissue(since("2026-09-04"), since("2026-09-05"), again=again)

        (line,) = said(make(fetches, rebuild=rebuild))

        assert feed_clause(line) == NOTHING_SAYS

    def test_Fault_WhenTheLaterFetchIsAWindowOverTheItemsDay_SaysItAskedByWindow(
        self, make, rebuild, again
    ):
        fetches = reissue(
            window("2026-09-01", "2026-09-10"), window("2026-09-02", "2026-09-11"), again=again
        )

        (line,) = said(make(fetches, rebuild=rebuild))

        count = 2 if again else 1
        assert f"({count} asking by transaction-time window)" in feed_clause(line)
        assert feed_clause(line).endswith(SUCCESSOR)

    def test_Fault_WhenTheWindowEndsBeforeTheItemsDay_SaysNothingSaysItWasWithdrawn(
        self, make, rebuild, again
    ):
        fetches = reissue(
            window("2026-09-04", "2026-09-10"), window("2026-09-05", "2026-09-11"), again=again
        )

        (line,) = said(make(fetches, rebuild=rebuild))

        assert feed_clause(line) == NOTHING_SAYS


@pytest.mark.parametrize("rebuild", [False, True], ids=["live", "rebuilt"])
class TestWhenTheLaterFetchListsADifferentItem:
    def test_Fault_WhenTheLaterItemIsAnotherRecipientsPayment_SaysNoItemOfThatRecipientAppears(
        self, make, rebuild
    ):
        def fetches() -> list[RawArtefact]:
            return [
                feed([coffee("f-coffee")], since("2026-09-01")),
                feed([card_payment("f-other", "Garage", 350, 3)], since("2026-09-02")),
            ]

        (line,) = said(make(fetches, rebuild=rebuild))

        assert "no item of the same size, direction, and recipient under another id first" in (
            feed_clause(line)
        )
