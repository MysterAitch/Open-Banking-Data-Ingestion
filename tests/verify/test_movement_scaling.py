"""The movement checks cost in proportion to what the store holds.

The household is invented and shaped like a polled feed: each fetch of the main
category lists the newest items and the ones before them, so every row is
sighted by several artefacts, and the Space's own feed does the same. Every
tenth item is a transfer to the Space whose arrival the Space's feed lists. A
store of this shape has far more sightings than rows, which is the shape the
checks read.

The answers are pinned apart from the cost: the corpus is clean, so no fault is
reported and every transfer leg is verified against the account it names, which
a check that gave up early would not manage.
"""

from __future__ import annotations

import json
import pathlib
import time
from datetime import date, timedelta

from obdi.ingest.providers import starling
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.verify.movement_completeness import check_rows, movement_completeness
from round_up_corpus import SPACE_FEED_ORIGIN
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAP

#: Items a fetch lists that the fetch before it did not.
FRESH = 20
#: How many fetches list each item.
OVERLAP = 6


def canonical(ref: str) -> str:
    return str(MAP.resolve(*ref.split(":", 1))) if ":" in ref else ref


def item(index: int, *, space: bool) -> dict:
    when = date(2024, 1, 1) + timedelta(days=index // FRESH)
    transfer = index % 10 == 0
    minor = 100 + index
    base = {
        "feedItemUid": f"{'s' if space else 'm'}-{index}",
        "amount": {"currency": "GBP", "minorUnits": minor},
        "transactionTime": f"{when.isoformat()}T10:00:{1 if space else 0:02}.000Z",
        "status": "SETTLED",
    }
    if transfer:
        return base | {
            "direction": "IN" if space else "OUT",
            "source": "INTERNAL_TRANSFER",
            "counterPartyType": "CATEGORY",
            "counterPartyUid": "cat-main" if space else "cat-bills",
            "counterPartyName": "Transfer",
        }
    if space:
        return base | {
            "direction": "OUT",
            "source": "MASTER_CARD",
            "counterPartyName": f"Shop {index}",
        }
    return base | {"direction": "OUT", "source": "MASTER_CARD", "counterPartyName": f"Shop {index}"}


def build(directory: pathlib.Path, fetches: int) -> Store:
    """`fetches` landed fetches of each feed, each overlapping the ones before it."""
    store = Store(directory / "polled.sqlite3")
    land_evidence(store)
    for fetch in range(fetches):
        first = max(0, (fetch + 1 - OVERLAP) * FRESH)
        indexes = range(first, (fetch + 1) * FRESH)
        for origin, space in ((FEED_ORIGIN, False), (SPACE_FEED_ORIGIN, True)):
            body = json.dumps(
                {"feedItems": [item(index, space=space) for index in indexes]}
            ).encode()
            store.land_artefact(
                starling.artefact_for(
                    body,
                    account_id="starling:cat-main" if not space else "starling:cat-bills",
                    kind="feed",
                    origin=f"{origin}?changesSince=2024-01-01T00:{fetch // 60:02}:{fetch % 60:02}Z",
                )
            )
    assert rebuild_from_raw(store, account_map=MAP).problems == []
    return store


class TestTheChecksOverAPolledFeed:
    def test_Checks_WhenTheFeedsAreCleanAndOverlap_ReportNoFaultAndVerifyEveryLeg(self, tmp_path):
        with build(tmp_path, 30) as store:
            report = movement_completeness(store, canonical)

            assert report.faults == 0
            assert report.legs > 0
            assert report.legs == report.legs_verified
            assert report.rows_listed > 0

    def test_Checks_WhenTheStoreHoldsFourTimesAsMuch_CostAboutFourTimesAsMuch(self, tmp_path):
        """Stated as a ratio over the minimum of three runs, as in test_family_scaling.

        Four times the artefacts is four times the work when each check reads
        the store once, and sixteen times when a row or an artefact re-reads it.
        The listed rows of an artefact are read once per process, so the runs
        after the first are the checks themselves.
        """

        def timed(directory: pathlib.Path, fetches: int) -> float:
            directory.mkdir()
            with build(directory, fetches) as store:
                movement_completeness(store, canonical)
                runs = []
                for _ in range(3):
                    start = time.perf_counter()
                    movement_completeness(store, canonical)
                    runs.append(time.perf_counter() - start)
                return min(runs)

        small = timed(tmp_path / "small", 30)
        large = timed(tmp_path / "large", 120)

        assert large < small * 9, (
            f"four times the artefacts made the checks {large / small:.1f}x slower - "
            "the cost is scaling faster than the store"
        )

    def test_RowsListed_WhenEveryArtefactIsListedBefore_ParsesNoPayloadAgain(
        self, tmp_path, monkeypatch
    ):
        import obdi.ingest.rebuild as rebuild

        with build(tmp_path, 12) as store:
            check_rows(store, canonical)
            parsed: list[str] = []
            real = rebuild.parse_artefact_transactions
            monkeypatch.setattr(
                rebuild,
                "parse_artefact_transactions",
                lambda *args: parsed.append(args[3]) or real(*args),
            )

            check_rows(store, canonical)

        assert parsed == []
