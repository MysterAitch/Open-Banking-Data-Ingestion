"""The generated corpus is only useful if the application agrees with it.

Two halves, and both are needed. The generator is checked against itself - a
world with a known shape produces a manifest describing that shape - and then the
whole import path is run over the artefacts and its results compared against what
was planted. Neither half alone is worth much: a manifest nobody imports against
describes nothing, and an import nobody has a manifest for can only be admired.

This is stage 1 as scoped in the design note: CSV only, because the import path
for it already exists and so the pipeline runs end to end without a document
renderer. What it buys immediately is an oracle for the pattern features -
recurring payments and coverage gaps - which over real data can be checked by eye
and nothing else.

THE SEED IS IN EVERY FAILURE MESSAGE that could depend on generated content. A
defect found here is worth nothing if the corpus cannot be rebuilt.
"""

from __future__ import annotations

import json

import pytest

from obdi.store import Store
from obdi.synthetic import build_world, write_corpus

SEED = 20260812


@pytest.fixture
def corpus(tmp_path):
    world = build_world(seed=SEED, months=6)
    manifest = write_corpus(world, tmp_path / "corpus")
    return tmp_path / "corpus", world, manifest


class TestTheGeneratedWorld:
    def test_TheManifest_DescribesEveryEventItPlanted(self, corpus):
        directory, world, manifest = corpus

        assert manifest["seed"] == SEED, "the corpus cannot be rebuilt without this"
        assert manifest["totals"]["events"] == len(world.events)
        # Six months, each with a salary, five commitments and a two-legged
        # sweep: the SHAPE is fixed even though the content moves with the seed.
        assert manifest["totals"]["events"] == 6 * (1 + 5 + 2)
        assert manifest["totals"]["transfers"] == 6

        on_disk = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        assert on_disk == manifest, (
            "the manifest a later process would read differs from the one returned"
        )

    def test_Descriptors_CarryTheNoiseARealOneWould(self, corpus):
        """A generator emitting tidy names flatters a normaliser rather than
        testing it - so the same merchant must arrive looking different each
        time, with the intended name recorded separately."""
        _, world, _ = corpus

        netflix = [e for e in world.events if e.merchant == "Netflix"]
        assert len(netflix) == 6
        assert len({e.description for e in netflix}) == 6, (
            f"the same merchant produced identical descriptors (seed {SEED}) - "
            "nothing here would exercise normalisation"
        )
        assert all(e.description != e.merchant for e in netflix)

    def test_EveryTransfer_HasBothLegsAndTheyCancel(self, corpus):
        _, world, manifest = corpus

        assert manifest["transfer_pairs"], f"no transfers planted (seed {SEED})"
        by_id: dict[str, list] = {}
        for event in world.events:
            if event.transfer_id:
                by_id.setdefault(event.transfer_id, []).append(event)
        for transfer_id, legs in by_id.items():
            assert len(legs) == 2, f"{transfer_id} has {len(legs)} leg(s), seed {SEED}"
            assert sum(leg.amount_minor for leg in legs) == 0, (
                f"{transfer_id} does not cancel - the same money must appear as "
                f"one debit and one credit (seed {SEED})"
            )
            assert len({leg.account for leg in legs}) == 2


class TestWhatTheApplicationDerivesFromIt:
    def _import(self, store_path, directory, world) -> None:
        """Every statement through the ordinary import path."""
        from obdi.ingest import reconcile_batch
        from obdi.parsers.uk_banks import detect

        for account in world.accounts:
            payload = (directory / f"{account}.csv").read_bytes()
            parser = detect(payload)
            rows = list(parser.parse(payload, account_id=account))
            with Store(store_path) as store:
                reconcile_batch(store, rows, digest=f"synthetic-{account}")

    def test_EveryPlantedEvent_ArrivesExactlyOnce(self, corpus, tmp_path):
        directory, world, manifest = corpus
        store_path = tmp_path / "store.sqlite3"

        self._import(store_path, directory, world)

        with Store(store_path) as store:
            derived = store.all_transactions()

        assert len(derived) == manifest["totals"]["events"], (
            f"planted {manifest['totals']['events']} events and derived "
            f"{len(derived)} (seed {SEED})"
        )
        planted = {(e.account, e.when, e.amount_minor) for e in world.events}
        arrived = {
            (row.account_id, row.value_date.isoformat(), row.amount_minor)
            for row in derived
        }
        assert arrived == planted, (
            "what was derived differs from what was planted - only in "
            f"{sorted(arrived - planted)[:3]} and {sorted(planted - arrived)[:3]} "
            f"(seed {SEED})"
        )

    def test_ImportingTheSameCorpusTwice_AddsNothing(self, corpus, tmp_path):
        """The property every real import depends on, checkable here because the
        right answer is known: the same statement arriving again is the same
        payments, not more of them."""
        directory, world, manifest = corpus
        store_path = tmp_path / "store.sqlite3"

        self._import(store_path, directory, world)
        self._import(store_path, directory, world)

        with Store(store_path) as store:
            derived = store.all_transactions()
        assert len(derived) == manifest["totals"]["events"], (
            f"a second import of the same corpus produced {len(derived)} rows from "
            f"{manifest['totals']['events']} events (seed {SEED})"
        )

    def test_TheTransfersMoney_IsNotCountedAsSpending(self, corpus, tmp_path):
        """Both legs of a sweep are in the corpus, which is what inflates
        spending when nothing pairs them. The assertion is deliberately about
        the PLANTED truth: the transfers sum to zero, so any total that includes
        them and any total that excludes them differ by exactly nothing."""
        directory, world, _ = corpus
        store_path = tmp_path / "store.sqlite3"
        self._import(store_path, directory, world)

        transfer_total = sum(e.amount_minor for e in world.events if e.transfer_id)
        assert transfer_total == 0

        with Store(store_path) as store:
            derived = store.all_transactions()
        moved = [
            row
            for row in derived
            if "TRANSFER TO SAVINGS" in row.description or "FROM CURRENT" in row.description
        ]
        assert len(moved) == 12, (
            f"expected six sweeps as twelve rows, found {len(moved)} (seed {SEED})"
        )
        assert sum(row.amount_minor for row in moved) == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
