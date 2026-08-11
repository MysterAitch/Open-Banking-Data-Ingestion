"""What a teardown would cost, said before anybody needs to ask.

Most of this store is cheap to lose: transactions rebuild from artefacts,
artefacts re-download or re-upload, connections take five minutes to
recreate. That is what makes wiping it a reasonable thing to do while a
system is young - and a clean-slate run is the only honest test of what
installing it from nothing is like.

Some of it is not. A category applied BY HAND has no artefact behind it,
and neither does a declared account with no feed - a passbook, cash in a
tin. Those do not come back.

The decision to treat teardown as cheap therefore has an expiry, and an
expiry nobody can see is one nobody honours. So the count of work that no
rebuild can restore is reported beside the row counts, where somebody
about to discard a store is already looking - rather than living in a note
written months earlier and remembered at the wrong moment.
"""

from __future__ import annotations

from datetime import UTC, datetime

from obdi.accounts import AccountRecord, AccountRef
from obdi.store import Store


def _annotate(store: Store, entity: str, provenance: str) -> None:
    store.connection.execute(
        "INSERT OR REPLACE INTO annotations "
        "(entity_id, kind, value, provenance, annotated_at) VALUES (?,?,?,?,?)",
        (entity, "category", "Groceries", provenance, datetime.now(UTC).isoformat()),
    )
    store.connection.commit()


class TestWhatARebuildCannotRestore:
    def test_ACategoryAppliedByHand_IsCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _annotate(store, "entity-1", "human:roger")

            assert store.irreplaceable()["hand-entered categories"] == 1

    def test_ACategoryAppliedByARule_IsNotCounted(self, tmp_path):
        # A rule's output is re-derivable by re-running the rule, so losing
        # it costs a command rather than a decision. Counting it would
        # inflate the number that is supposed to stop somebody.
        with Store(tmp_path / "s.sqlite3") as store:
            _annotate(store, "entity-1", "rule:groceries")
            _annotate(store, "entity-2", "model:suggested")

            assert store.irreplaceable()["hand-entered categories"] == 0

    def test_ADeclaredAccount_IsCounted(self, tmp_path):
        # There is no artefact a cash tin could be replayed from. The
        # registry is declared state and a teardown loses it.
        with Store(tmp_path / "s.sqlite3") as store:
            store.declare_account(
                AccountRecord(ref=AccountRef("cash-tin"), kind="cash", label="Cash")
            )

            assert store.irreplaceable()["declared accounts"] == 1

    def test_AnEmptyStore_ReportsNothingToLose_RatherThanNothingAtAll(self, tmp_path):
        # Zero is an answer and must be stated: a missing line reads as
        # "not measured", and the two want opposite reactions from somebody
        # deciding whether to wipe.
        with Store(tmp_path / "s.sqlite3") as store:
            report = store.irreplaceable()

        assert report["hand-entered categories"] == 0
        assert report["declared accounts"] == 0

    def test_EveryCountedThing_IsNamed_SoTheTotalCanBeChecked(self, tmp_path):
        # A single number nobody can decompose is a number nobody trusts.
        with Store(tmp_path / "s.sqlite3") as store:
            report = store.irreplaceable()

        assert set(report) == {"hand-entered categories", "declared accounts"}
