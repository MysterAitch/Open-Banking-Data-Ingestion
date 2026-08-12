"""The layer nobody can re-download needs an export more than the one that can.

Layer 0 already projects onto the filesystem: every raw artefact, with a sidecar
carrying its provenance. That layer is the RECOVERABLE one - the bank still has
it, the statements are still in the inbox. The layer that cannot be recreated by
any amount of fetching is the human one: categories somebody typed, accounts
somebody declared, review decisions somebody made. It had no export at all.

KEYED ON CONTENT IDENTITY, NOT ENTITY ID, and that is the substance rather than a
detail. An entity id folds in the account and the artefact that first carried the
row, so it is re-minted whenever the store is rebuilt or the filing corrected -
which is exactly what the project's own documentation says makes it unfit for
export. The annotation layer is keyed on it internally, which is the root of both
detachment defects this review found. An export keyed on content plus occurrence
replays against a rebuilt store, a fresh installation, or something that is not
this application at all.

Orphaned work is exported too, and marked. An annotation whose transaction has
gone is the work most at risk in the whole store - invisible from every other
angle - so an export that silently dropped it would be discarding precisely what
it exists to preserve.
"""

from __future__ import annotations

import json

import pytest

from obdi.store import Store


def _store_with_hand_work(path) -> dict[str, str]:
    """A store holding the things no fetch can recreate, over real evidence.

    The artefact is LANDED and replayed rather than the rows being reconciled
    directly, because one of these scenarios rebuilds the store - and a rebuild
    replays layer 0, so a fixture with no layer 0 empties itself and every
    assertion afterwards passes vacuously. The first version of this file did
    exactly that.
    """
    from obdi.accounts import AccountRecord
    from obdi.cli import replay_single_artefact
    from obdi.providers.truelayer import artefact_for

    body = json.dumps(
        {
            "results": [
                {
                    "transaction_id": "t-netflix",
                    "normalised_provider_transaction_id": "txn-netflix",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "amount": -10.99,
                    "currency": "GBP",
                    "description": "NETFLIX",
                },
                {
                    # A different amount on purpose: two rows alike in amount
                    # and date are what the matcher queues for review by itself,
                    # and its entry wins the one-row-per-transaction slot - so
                    # the fixture would be testing the matcher, not the export.
                    "transaction_id": "t-coffee",
                    "normalised_provider_transaction_id": "txn-coffee",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "amount": -3.45,
                    "currency": "GBP",
                    "description": "COFFEE",
                },
            ],
            "status": "Succeeded",
        }
    ).encode()
    with Store(path) as store:
        store.land_artefact(
            artefact_for(
                body,
                account_id="acc-1",
                kind="booked",
                requested="from=2026-06-01&to=2026-07-31",
                account_ref="halifax-current",
            )
        )
        artefact_id = int(
            store.connection.execute("SELECT rowid FROM raw_artefacts LIMIT 1").fetchone()[0]
        )
    replay_single_artefact(path, artefact_id)

    with Store(path) as store:
        by_description = {
            row.description: row.entity_id for row in store.all_transactions()
        }
        store.annotate(by_description["NETFLIX"], "category", "Subscriptions", provenance="human")
        store.annotate(by_description["COFFEE"], "payee", "The Coffee Place", provenance="human")
        store.queue_for_review(by_description["COFFEE"], "possible duplicate")
        store.resolve_review(by_description["COFFEE"])
        store.declare_account(
            AccountRecord(ref="halifax-current", kind="current", label="Halifax Current")
        )
    return by_description


def _exported(directory, name: str):
    return json.loads((directory / name).read_text(encoding="utf-8"))


class TestExportingWhatCannotBeFetchedAgain:
    def test_Annotations_AreKeyedOnContentIdentityRatherThanEntityId(self, tmp_path):
        from obdi.export_declared import export_declared

        store_path = tmp_path / "store.sqlite3"
        entities = _store_with_hand_work(store_path)
        out = tmp_path / "export"

        with Store(store_path) as store:
            export_declared(store, out)

        annotations = _exported(out, "annotations.json")
        assert annotations, "nothing was exported"
        for entry in annotations:
            assert "content_key" in entry and "occurrence" in entry
            assert entry.get("entity_id") not in entities.values(), (
                "the export carries the identifier that is re-minted on every "
                "rebuild, which is what makes an export unusable afterwards"
            )

    def test_TheExport_StillMatchesItsRowsAfterARebuild(self, tmp_path):
        """The property the whole design is for. A rebuild re-mints entity ids;
        content identity survives it, so an export taken before still lines up
        with the store afterwards."""
        from obdi.export_declared import export_declared
        from obdi.rebuild import rebuild_from_raw

        store_path = tmp_path / "store.sqlite3"
        _store_with_hand_work(store_path)
        out = tmp_path / "export"
        with Store(store_path) as store:
            export_declared(store, out)
        exported = _exported(out, "annotations.json")

        with Store(store_path) as store:
            rebuild_from_raw(store)

        with Store(store_path) as store:
            live = {
                (row.content_key, row.occurrence)
                for row in store.all_transactions()
            }
        # Without this the whole scenario passes when the rebuild produces
        # NOTHING, which is what happened while the fixture had no layer 0 to
        # replay: an empty set contains no counter-example.
        assert live, "the rebuild left no rows, so nothing below proves anything"
        assert exported, "nothing was exported, so nothing below proves anything"

        for entry in exported:
            if entry.get("orphaned"):
                continue
            assert (entry["content_key"], entry["occurrence"]) in live, (
                f"an exported annotation no longer identifies any row: {entry}"
            )

    def test_DeclaredAccountsAndReviewDecisions_AreExportedToo(self, tmp_path):
        from obdi.export_declared import export_declared

        store_path = tmp_path / "store.sqlite3"
        _store_with_hand_work(store_path)
        out = tmp_path / "export"
        with Store(store_path) as store:
            export_declared(store, out)

        accounts = _exported(out, "declared-accounts.json")
        decisions = _exported(out, "review-decisions.json")
        assert [a["ref"] for a in accounts] == ["halifax-current"]
        assert decisions and decisions[0]["reason"] == "possible duplicate"
        assert decisions[0]["resolved_at"], "an unresolved flag is not a decision"

    def test_WorkThatHasLostItsTransaction_IsExportedAndMarked(self, tmp_path):
        """The most at-risk thing in the store. An annotation pointing at
        nothing is invisible everywhere else, so an export that dropped it would
        discard exactly what it exists to preserve."""
        from obdi.export_declared import export_declared

        store_path = tmp_path / "store.sqlite3"
        entities = _store_with_hand_work(store_path)
        with Store(store_path) as store:
            store.connection.execute(
                "DELETE FROM transactions WHERE entity_id = ?", (entities["NETFLIX"],)
            )
            store.connection.commit()

        out = tmp_path / "export"
        with Store(store_path) as store:
            export_declared(store, out)

        annotations = _exported(out, "annotations.json")
        orphaned = [entry for entry in annotations if entry.get("orphaned")]
        assert len(orphaned) == 1, f"the stranded work was not exported: {annotations}"
        assert orphaned[0]["value"] == "Subscriptions"
        assert orphaned[0]["content_key"] is None, (
            "an orphan has no content identity to carry - saying so is the point"
        )

    def test_TheManifest_SaysWhatWasWrittenAndWhatItCameFrom(self, tmp_path):
        from obdi.export_declared import export_declared

        store_path = tmp_path / "store.sqlite3"
        _store_with_hand_work(store_path)
        out = tmp_path / "export"
        with Store(store_path) as store:
            result = export_declared(store, out)

        manifest = _exported(out, "manifest.json")
        assert manifest["counts"]["annotations"] == 2
        assert manifest["counts"]["declared_accounts"] == 1
        assert manifest["counts"]["review_decisions"] == 1
        assert manifest["build"], "the export does not say which code wrote it"
        assert result.describe()


class TestTheCommandLine:
    def test_ExportDeclared_WritesTheTreeAndSaysWhatItWrote(
        self, tmp_path, capsys, monkeypatch
    ):
        from obdi import cli

        store_path = tmp_path / "store.sqlite3"
        _store_with_hand_work(store_path)
        out = tmp_path / "export"

        monkeypatch.setenv("OBDI_DB_PATH", str(store_path))
        code = cli.main(["export-declared", str(out)])
        printed = capsys.readouterr().out

        assert code == 0
        assert (out / "annotations.json").is_file()
        assert "2" in printed, f"the counts are not in the report: {printed}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
