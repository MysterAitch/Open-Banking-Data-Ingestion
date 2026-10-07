"""A test that says "every arrival order" really runs more than one order.

A rebuild once replayed artefacts by their stamp as text, and an import stamps local
time with an offset where a pull stamps UTC. On a machine ahead of UTC an import then
replayed last whatever order it arrived in, so six "orders" were one order run six
times, and a release that passed there failed its build at UTC.

Two halves defend that property.
The shared harnesses that land a feed, an aggregator artefact, and an import are
measured directly: the sequence the rebuild really replays must differ in every
arrival order.
The static half makes a new test module that permutes arrivals and rebuilds say which
of those measurements covers it, so a fresh harness cannot skip the measurement.
"""

from __future__ import annotations

import itertools
import pathlib
import re

import obdi.rebuild
from late_settlement_corpus import ARRIVALS, household, late_settlement_payments
from obdi.rebuild import rebuild_from_raw
from round_up_corpus import main_feed, space_feed
from test_export_cuts import Row
from test_space_attribution import MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    aggregator_record,
    corpus,
)

ORDERS = list(itertools.permutations(ARRIVALS))
TESTS = pathlib.Path(__file__).parent

#: Test modules that permute arrivals and rebuild, and what makes their orders real.
#: Anything not listed here fails `test_ModuleThatPermutesArrivalsAndRebuilds_*`.
COVERED = {
    "late_settlement_corpus.py": "measured here: household",
    "test_space_blind_rows_and_internal_legs.py": "measured here: corpus",
    "test_review_settlement.py": "pulls only, all stamped in UTC, so text and instant agree",
    "test_space_attribution.py": "lands through reconcile_batch, unstamped; order is call order",
    "test_consecutive_days_nothing_joined.py": "measured here: household, the harness it lands "
    "the three sources through",
}


def replayed_sources(store) -> tuple[str, ...]:
    """The sources in the order the rebuild really parses them, recorded at the parser."""
    seen: list[str] = []
    real = obdi.rebuild.parse_artefact_transactions

    def record(source, *args, **kwargs):
        seen.append(str(source))
        return real(source, *args, **kwargs)

    obdi.rebuild.parse_artefact_transactions = record
    try:
        assert rebuild_from_raw(store, account_map=MAP).problems == []
    finally:
        obdi.rebuild.parse_artefact_transactions = real
    return tuple(seen)


def corpus_kwargs() -> dict:
    return {
        "main": main_feed(),
        "space": space_feed(),
        "export_rows": [*BASE_EXPORT, Row("Pay", -2000, 6, 6)],
        "aggregator": [aggregator_record("tl-p0", "-20.00", 6, "PAY")],
    }


class TestEveryArrivalOrderIsReplayedDifferently:
    def test_SpaceBlindCorpus_WhenLandedInEachOfSixOrders_ReplaysSixDistinctSequences(
        self, tmp_path
    ):
        sequences = set()
        for number, order in enumerate(ORDERS):
            directory = tmp_path / f"c{number}"
            directory.mkdir()
            store = corpus(directory, order, **corpus_kwargs())
            sequences.add(replayed_sources(store))
            store.close()

        assert len(sequences) == len(ORDERS)

    def test_LateSettlementHousehold_WhenLandedInEachOfSixOrders_ReplaysSixDistinctSequences(
        self, tmp_path
    ):
        sequences = set()
        for number, order in enumerate(ORDERS):
            directory = tmp_path / f"h{number}"
            directory.mkdir()
            store = household(directory, order, late_settlement_payments())
            sequences.add(replayed_sources(store))
            store.close()

        assert len(sequences) == len(ORDERS)

    def test_RecordedSequence_WhenTheSameOrderIsLandedTwice_IsTheSameBothTimes(self, tmp_path):
        """The opposite scenario: the measurement tells orders apart, it does not
        merely produce noise."""
        sequences = set()
        for number in range(2):
            directory = tmp_path / f"r{number}"
            directory.mkdir()
            store = corpus(directory, ORDERS[0], **corpus_kwargs())
            sequences.add(replayed_sources(store))
            store.close()

        assert len(sequences) == 1


def modules_that_permute_arrivals_and_rebuild() -> set[str]:
    found = set()
    paths = sorted(TESTS.rglob("*.py"))
    assert len(paths) > 300, f"read {len(paths)} files under {TESTS}; the walk has lost a directory"
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if re.search(r"\bpermutations\(", source) and "rebuild_from_raw" in source:
            found.add(path.name)
    found.discard(pathlib.Path(__file__).name)
    return found


class TestEveryModuleThatPermutesArrivalsSaysWhatMakesItsOrdersReal:
    def test_ModuleThatPermutesArrivalsAndRebuilds_WhenNotListed_IsRefused(self):
        unlisted = modules_that_permute_arrivals_and_rebuild() - COVERED.keys()

        assert unlisted == set(), (
            f"{sorted(unlisted)} permute arrivals and rebuild: measure their orders as "
            "TestEveryArrivalOrderIsReplayedDifferently does, or list them in COVERED "
            "with the reason their orders are real"
        )

    def test_CoveredList_WhenAModuleNoLongerPermutesAndRebuilds_IsRefusedAsStale(self):
        stale = COVERED.keys() - modules_that_permute_arrivals_and_rebuild()

        assert stale == set()

