"""Who said so, and who is allowed to overwrite them.

The annotation ladder is human > model > rule, decided by the prefix
before any ':'. It was enforced against a dict of three strings with a
`.get(prefix, 0)` behind it, and that default is the whole problem: an
unregistered prefix ranked BENEATH every registered one, so an
annotation written with a mistyped provenance was fair game for the next
rule sweep - the opposite of what a ladder is for, and silent either way.

So the write door refuses what it cannot rank, and the registry is tied
to the source tree: a provenance used in code that is not declared fails
here rather than being discovered as lost categorisation months later.
"""

from __future__ import annotations

import pathlib
import re
from datetime import date

import pytest

from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.namespaces import PROVENANCE_RANKS
from obdi.store import Store

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "obdi"

#: An annotate/forget_annotation call, including one spread over several
#: lines. Scoped to those two doors on purpose: "provenance" also names
#: the window ladder in timeline.py and the reading ladder in
#: account_observations.py, which are different vocabularies entirely.
_ANNOTATION_CALL = re.compile(
    r"\b(?:annotate|forget_annotation)\((?:[^()]|\([^()]*\))*\)", re.S
)
_PROVENANCE_ARGUMENT = re.compile(
    r"\b(?:up_to_)?provenance\s*=\s*[\"']([^\"']+)[\"']"
)


def _txn(source_id: str) -> Transaction:
    return Transaction(
        account_id="current",
        amount_minor=-899,
        currency="GBP",
        value_date=date(2026, 3, 5),
        booking_date=date(2026, 3, 5),
        description="NETFLIX",
        source="truelayer",
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
        content_key="key-netflix",
    )


def _one_transaction(store: Store) -> str:
    reconcile_batch(store, [_txn("tl-1")], digest="d1")
    return store.all_transactions()[0].entity_id


def _plant_unrankable(store: Store, entity: str, provenance: str) -> None:
    """An annotation carrying a provenance no rung declares.

    Only reachable from a store written before the write door refused
    them, or from somebody's hand-edited database - which is exactly the
    case these tests exist to pin down.
    """
    store.connection.execute(
        "INSERT INTO annotations (entity_id, kind, value, provenance, annotated_at) "
        "VALUES (?, 'category', 'Imported', ?, '2026-01-01T00:00:00')",
        (entity, provenance),
    )
    store.connection.commit()


class TestTheRegistryDescribesTheCode:
    def test_EveryProvenanceWrittenInCode_IsDeclaredOnTheLadder(self):
        used: dict[str, str] = {}
        for path in SRC.rglob("*.py"):
            if path.name == "namespaces.py":
                continue
            text = path.read_text(encoding="utf-8")
            for call in _ANNOTATION_CALL.finditer(text):
                for match in _PROVENANCE_ARGUMENT.finditer(call.group(0)):
                    used.setdefault(match.group(1).split(":", 1)[0], path.name)

        undeclared = {
            prefix: where
            for prefix, where in used.items()
            if prefix not in PROVENANCE_RANKS
        }

        assert not undeclared, (
            "these provenance prefixes are written by code but not declared "
            f"in namespaces.PROVENANCE_RANKS: {undeclared}"
        )
        assert used, "the scan found no annotation writes at all - it has drifted"

    def test_NoRungIsRankedZero_BecauseZeroIsWhatUnknownUsedToMean(self):
        """The fault this registry exists to prevent was an unknown
        prefix scoring zero and losing every comparison. A declared rung
        at zero would resurrect it under a different name."""
        assert 0 not in PROVENANCE_RANKS.values()


class TestAWriteThatCannotSayWhereItStands:
    def test_Annotation_WhenTheProvenanceIsUnregistered_IsRefusedNotRankedLast(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            entity = _one_transaction(store)

            with pytest.raises(ValueError, match="unregistered"):
                store.annotate(entity, "category", "Telly", provenance="analyst:jo")

            assert store.annotations("category") == {}

    def test_Retraction_WhenTheCeilingIsUnregistered_IsRefusedNotSilentlyNoOp(
        self, tmp_path
    ):
        """A retraction that cannot say how high it reaches would reach
        nothing and report that as "there was nothing to retract"."""
        with Store(tmp_path / "s.sqlite3") as store:
            entity = _one_transaction(store)
            store.annotate(entity, "category", "Telly", provenance="rule:sweep")

            with pytest.raises(ValueError, match="unregistered"):
                store.forget_annotation(entity, "category", up_to_provenance="analyst")

            assert store.annotations("category")[entity][0] == "Telly"


class TestAnAnnotationNobodyCanRank:
    """Rows written before the door existed. Unknown authority must not
    be the LOWEST authority - that is precisely the fault - but it must
    not lock the row against the person either."""

    def test_ARuleSweep_CannotOverwriteAnAnnotationItCannotRank(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            entity = _one_transaction(store)
            _plant_unrankable(store, entity, "analyst:jo")

            landed = store.annotate(
                entity, "category", "Subscriptions", provenance="rule:sweep"
            )

            assert landed is False
            assert store.annotations("category")[entity] == ("Imported", "analyst:jo")

    def test_ARuleSweep_CannotRetractAnAnnotationItCannotRank(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            entity = _one_transaction(store)
            _plant_unrankable(store, entity, "analyst:jo")

            retracted = store.forget_annotation(
                entity, "category", up_to_provenance="rule"
            )

            assert retracted is False
            assert entity in store.annotations("category")

    def test_APerson_CanStillCorrectAnAnnotationNobodyCanRank(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            entity = _one_transaction(store)
            _plant_unrankable(store, entity, "analyst:jo")

            landed = store.annotate(
                entity, "category", "Telly", provenance="human"
            )

            assert landed is True
            assert store.annotations("category")[entity] == ("Telly", "human")


class TestTheLadderItselfStillHolds:
    """The behaviour the refusal must not have disturbed."""

    def test_AModel_DoesNotOverwriteAPerson(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            entity = _one_transaction(store)
            store.annotate(entity, "category", "Telly", provenance="human")

            landed = store.annotate(
                entity, "category", "Media", provenance="model:propagation"
            )

            assert landed is False
            assert store.annotations("category")[entity][0] == "Telly"

    def test_ARule_MayRevisitItsOwnWorkAsTheRulesEvolve(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            entity = _one_transaction(store)
            store.annotate(entity, "category", "Media", provenance="rule:v1")

            landed = store.annotate(
                entity, "category", "Subscriptions", provenance="rule:v2"
            )

            assert landed is True
            assert store.annotations("category")[entity][0] == "Subscriptions"
