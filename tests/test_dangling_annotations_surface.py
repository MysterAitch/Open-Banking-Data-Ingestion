"""Lost hand-work is reported where somebody is already looking.

An annotation whose transaction no longer exists is the quietest possible loss.
From every other angle the row simply looks uncategorised - indistinguishable from
one nobody has got to yet - so nothing else will ever say the work was LOST rather
than never done. Only a count of annotations pointing at nothing can tell those
apart.

The count existed, was tested, and nothing called it. A detector nobody invokes is
not a detector: it is the same failure as a guard that is never registered, and it
had gone unnoticed for exactly the reason it was written - the symptom is silence.

Reported in two places on purpose. `status` is where somebody weighing a teardown is
already looking, beside the other counts of work no rebuild can restore. `doctor` is
where somebody investigating a fault looks, and a fault is when this is most likely
to be non-zero.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def _one_transaction(store: Store) -> str:
    reconcile_batch(
        store,
        [
            Transaction(
                account_id="current",
                amount_minor=-899,
                currency="GBP",
                value_date=date(2026, 3, 5),
                booking_date=date(2026, 3, 5),
                description="NETFLIX",
                source="truelayer",
                source_id="tl-1",
                tier=SourceTier.AUTHORITATIVE,
                content_key="key-netflix",
            )
        ],
        digest="d1",
    )
    return store.all_transactions()[0].entity_id


def _strand_an_annotation(store: Store) -> None:
    """Categorise a transaction, then remove the transaction under it.

    The deletion goes straight to the table on purpose: this is the state the
    application is not supposed to be able to produce, which is precisely why a
    detector for it exists. Declared in the fixture enumeration for that reason.
    """
    entity = _one_transaction(store)
    store.annotate(entity, "category", "Telly", provenance="human")
    store.connection.execute("DELETE FROM transactions WHERE entity_id = ?", (entity,))
    store.connection.commit()


class TestWhereLostHandWorkIsReported:
    def test_Status_WhenAnAnnotationPointsAtNothing_SaysSo(self, tmp_path, capsys, monkeypatch):
        from obdi import cli

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _strand_an_annotation(store)

        monkeypatch.setenv("OBDI_DB_PATH", str(db))
        cli.main(["status"])
        printed = capsys.readouterr().out.lower()

        assert "annotation" in printed
        assert "1" in printed

    def test_Status_WhenNothingIsStranded_StillReportsTheZeroAndItsDenominator(
        self, tmp_path, capsys, monkeypatch
    ):
        # A count worth printing at zero. The reader learns that the check RAN,
        # which is the difference between "nothing is lost" and "nothing looked".
        # The denominator carries that: at zero there are no per-table lines to
        # print, so without it the line is a bare 0 that proves nothing.
        from obdi import cli

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _one_transaction(store)

        monkeypatch.setenv("OBDI_DB_PATH", str(db))
        cli.main(["status"])
        printed = capsys.readouterr().out.lower()

        assert "stranded work" in printed
        assert "entity-keyed columns" in printed, (
            f"the zero says nothing about what was checked: {printed}"
        )

    def test_Doctor_WhenAnAnnotationPointsAtNothing_ReportsItAsAFault(self, tmp_path):
        from obdi.doctor import collision_checks

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _strand_an_annotation(store)
            findings = collision_checks(store)

        # Matched on the subject rather than on the word "annotation": the check
        # was widened to every table keyed to a transaction, and these scenarios
        # are about annotations being one of them rather than the only one.
        stranded = [f for f in findings if "points at transactions that exist" in f.name]
        assert stranded, f"doctor said nothing about it: {[f.name for f in findings]}"
        assert not stranded[0].ok
        assert "1" in stranded[0].detail

    def test_Doctor_WhenNothingIsStranded_SaysThatToo(self, tmp_path):
        # Reported at zero as well. A check that only speaks up when it finds
        # something leaves the reader unable to tell "nothing is lost" from
        # "nothing looked" - which is the whole distinction this count exists for.
        from obdi.doctor import collision_checks

        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _one_transaction(store)
            findings = collision_checks(store)

        # Matched on the subject rather than on the word "annotation": the check
        # was widened to every table keyed to a transaction, and these scenarios
        # are about annotations being one of them rather than the only one.
        stranded = [f for f in findings if "points at transactions that exist" in f.name]
        assert stranded, "the check must report even when it finds nothing"
        assert stranded[0].ok


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
