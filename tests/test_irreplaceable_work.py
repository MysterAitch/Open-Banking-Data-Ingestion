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

from obdi.accounts import AccountRecord, AccountRef
from obdi.store import Store


def _annotate(store: Store, entity: str, provenance: str) -> None:
    """Through the write door the application uses, never raw SQL.

    The first version of this file inserted rows directly, and chose the
    provenance string itself - "human:roger", which nothing in the
    application has ever written. The counter looked for that shape and
    found it, so the test passed while the feature was blind on every real
    store. A fixture that builds state its own way cannot detect the writer
    and the reader disagreeing, which is the only thing worth detecting
    here.
    """
    store.annotate(entity, "category", "Groceries", provenance=provenance)


class TestWhatARebuildCannotRestore:
    def test_ACategoryAppliedByHand_IsCounted(self, tmp_path):
        # "human" with no suffix is what the review surface actually
        # writes - categorise.apply_to_group and defer_group both pass it
        # verbatim. A counter that only recognised "human:something" saw
        # none of them.
        with Store(tmp_path / "s.sqlite3") as store:
            _annotate(store, "entity-1", "human")

            assert store.irreplaceable()["hand-entered categories"] == 1

    def test_ACategoryCarryingWhoEnteredIt_IsAlsoCounted(self, tmp_path):
        # The rank is decided by the part before the colon, so a suffix
        # naming the person must not change whether it counts.
        with Store(tmp_path / "s.sqlite3") as store:
            _annotate(store, "entity-1", "human:roger")

            assert store.irreplaceable()["hand-entered categories"] == 1

    def test_ADeferral_IsCountedSeparately_NotAsACategory(self, tmp_path):
        # A withheld decision is human work and survives a rebuild, so it
        # belongs in the report - but it is not a category, and a line
        # labelled "categories" that includes deferrals is a wrong answer
        # rather than a rounded one.
        with Store(tmp_path / "s.sqlite3") as store:
            store.annotate("entity-1", "review", "deferred", provenance="human")
            report = store.irreplaceable()

        assert report["hand-entered categories"] == 0
        assert report["deferred decisions"] == 1

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
        # Asserted as "at least these", not as an exact set: the day a
        # fourth irreplaceable thing legitimately appears, this should be
        # an addition to make rather than a test to repair.
        with Store(tmp_path / "s.sqlite3") as store:
            report = store.irreplaceable()

        assert {"hand-entered categories", "declared accounts"} <= set(report)
        assert all(isinstance(count, int) for count in report.values())
