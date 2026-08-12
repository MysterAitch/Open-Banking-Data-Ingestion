"""A backup nobody has restored is not yet a backup.

The store is copied nightly and each copy is verified against the live one at
the moment it is taken. That proves the copy is complete; it does not prove it
can become the store again, and those are different claims. Everything below is
about the second one.

The operator's situation is the design constraint. A restore happens on a bad
day - the store is gone, or wrong, and the person doing it is not at their best.
So: nothing is deleted, the existing store is moved aside rather than replaced,
a copy that cannot be trusted is refused BEFORE anything is touched, and the
result says what landed rather than "done".

The sidecars are the trap worth naming. A SQLite store running with write-ahead
logging has `-wal` and `-shm` files beside it, and they belong to the database
that was there before. Left in place next to a restored file they are at best
ignored and at worst folded in, which is the same class of fault as copying the
main file alone during a backup - the one this project already met, measured at
600-750 missing rows, and passing every check anybody would naturally run.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from obdi.backup import BackupRefused, take_backup
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def _store_with_rows(path, count: int = 3) -> None:
    with Store(path) as store:
        reconcile_batch(
            store,
            [
                Transaction(
                    account_id="halifax-current",
                    amount_minor=-100 * (index + 1),
                    currency="GBP",
                    value_date=date(2026, 7, 1),
                    booking_date=date(2026, 7, 1),
                    description=f"PAYMENT {index}",
                    source="truelayer",
                    source_id=f"tl-{index}",
                    tier=SourceTier.AUTHORITATIVE,
                    content_key=f"key-{index}",
                )
                for index in range(count)
            ],
            digest="d1",
        )


class TestBringingAStoreBackFromACopy:
    def test_ABackup_RestoredToAFreshPath_HoldsWhatTheStoreHeld(self, tmp_path):
        from obdi.restore import restore_backup

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        copy = take_backup(live, tmp_path / "backups" / "obdi-2026-08-12.sqlite3")

        result = restore_backup(copy.destination, tmp_path / "restored.sqlite3")

        assert result.row_counts["transactions"] == 3
        assert result.row_counts == copy.row_counts, (
            "the restored store holds different rows from the backup it came from"
        )
        with Store(tmp_path / "restored.sqlite3") as store:
            assert len(store.all_transactions()) == 3

    def test_ARestore_OntoAnExistingStore_RefusesRatherThanOverwriting(self, tmp_path):
        from obdi.restore import restore_backup

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        copy = take_backup(live, tmp_path / "backup.sqlite3")

        with pytest.raises(BackupRefused) as refusal:
            restore_backup(copy.destination, live)

        assert "already" in str(refusal.value).lower()
        assert "keep" in str(refusal.value).lower() or "aside" in str(refusal.value).lower(), (
            f"the refusal does not say how to proceed deliberately: {refusal.value}"
        )
        with Store(live) as store:
            assert len(store.all_transactions()) == 3, "the refusal touched the store"

    def test_ARestore_AskedToReplace_MovesTheOldStoreAsideRatherThanDeletingIt(
        self, tmp_path
    ):
        # The bad-day rule. Restoring the wrong backup is a mistake somebody will
        # make, and it is recoverable only while the store they overwrote still
        # exists somewhere they can find without knowing to look.
        from obdi.restore import restore_backup

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 5)
        copy = take_backup(live, tmp_path / "backup.sqlite3")
        _store_with_rows(live, 2)  # the store moves on after the backup

        result = restore_backup(copy.destination, live, replace=True)

        assert result.kept_aside is not None, "the replaced store was not kept"
        assert result.kept_aside.exists()
        with Store(result.kept_aside) as store:
            assert len(store.all_transactions()) == 5, (
                "what was kept is not what was replaced"
            )

    def test_ARestore_TakesTheOldStoresSidecarsAwayWithIt(self, tmp_path):
        """The `-wal` and `-shm` files belong to the database that was there.

        Asserted on where they END UP rather than on whether the old ones are
        still beside the restored file, which was the first version of this test
        and proved nothing: SQLite rewrites a mismatched write-ahead log when it
        opens the database, so the assertion passed with the handling disabled.
        A sidecar that travelled with its database is a fact nothing else can
        produce.
        """
        from obdi.restore import restore_backup

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        copy = take_backup(live, tmp_path / "backup.sqlite3")

        # A store left running with write-ahead logging has these beside it.
        for suffix in ("-wal", "-shm"):
            (tmp_path / f"store.sqlite3{suffix}").write_bytes(b"stale sidecar")

        result = restore_backup(copy.destination, live, replace=True)

        assert result.kept_aside is not None
        for suffix in ("-wal", "-shm"):
            travelled = result.kept_aside.with_name(f"{result.kept_aside.name}{suffix}")
            assert travelled.exists(), (
                f"the replaced store's {suffix} was left behind rather than kept "
                "with the database it belongs to"
            )
            assert travelled.read_bytes() == b"stale sidecar"

    def test_ABackupThatCannotBeTrusted_IsRefusedBeforeAnythingIsTouched(self, tmp_path):
        from obdi.restore import restore_backup

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        corrupt = tmp_path / "corrupt.sqlite3"
        corrupt.write_bytes(b"this is not a database")

        with pytest.raises(BackupRefused):
            restore_backup(corrupt, live, replace=True)

        with Store(live) as store:
            assert len(store.all_transactions()) == 3, (
                "a store was replaced from a backup that was never readable"
            )

    def test_ARestoredStore_IsUsableByTheCodeDoingTheRestoring(self, tmp_path):
        """The claim worth making. A file that opens is not the same as a store
        this release can use: a backup taken before a schema change has to come
        forward through the migration ladder, and a restore that stopped at
        copying would leave that to be discovered by the first pull instead."""
        from obdi.restore import restore_backup

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        copy = take_backup(live, tmp_path / "backup.sqlite3")

        result = restore_backup(copy.destination, tmp_path / "restored.sqlite3")

        assert result.schema_version, "the restore does not say what shape it produced"
        with sqlite3.connect(tmp_path / "restored.sqlite3") as connection:
            version = connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()
        assert version and str(version[0]) == str(result.schema_version)

    def test_TheResult_SaysWhatLandedRatherThanThatItWorked(self, tmp_path):
        from obdi.restore import restore_backup

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        copy = take_backup(live, tmp_path / "backup.sqlite3")

        described = restore_backup(copy.destination, tmp_path / "restored.sqlite3").describe()

        assert "3" in described, f"the row count is not in the report: {described}"
        assert str(copy.destination) in described
        assert "restored" in described.lower()


class TestTheCommandLine:
    def test_Restore_ReportsWhatItRestored(self, tmp_path, capsys, monkeypatch):
        from obdi import cli

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        copy = take_backup(live, tmp_path / "backup.sqlite3")

        exit_code = cli.main(
            ["restore", str(copy.destination), "--to", str(tmp_path / "restored.sqlite3")]
        )
        printed = capsys.readouterr().out

        assert exit_code == 0
        assert "3" in printed

    def test_Restore_WhenTheDestinationExists_FailsLoudlyAndChangesNothing(
        self, tmp_path, capsys
    ):
        from obdi import cli

        live = tmp_path / "store.sqlite3"
        _store_with_rows(live, 3)
        copy = take_backup(live, tmp_path / "backup.sqlite3")

        exit_code = cli.main(["restore", str(copy.destination), "--to", str(live)])
        printed = capsys.readouterr()

        assert exit_code != 0
        assert "already" in (printed.out + printed.err).lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
