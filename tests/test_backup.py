"""Taking a backup that can be trusted, and proving it holds what it claims.

The motivating measurement: copying `store.sqlite3` as a file, with WAL on,
produced databases missing 600-750 committed rows on every trial - and every one
of those truncated copies passed `PRAGMA integrity_check`. A backup that is
verified only by "the file opens" is a backup verified by the one check that
cannot see the failure mode it actually has.

So the property under test is not "a file appeared". It is "the copy holds every
row the live store held, table by table, and says so".
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from obdi.backup import BackupRefused, take_backup, verify_copy
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import TABLE_NAMES, Store


def _txn(index: int, *, prefix: str = "row") -> Transaction:
    return Transaction(
        account_id="halifax-current",
        amount_minor=-(100 + index),
        currency="GBP",
        value_date=date(2026, 1, 1 + (index % 28)),
        booking_date=date(2026, 1, 1 + (index % 28)),
        description=f"Payment {index}",
        source="statement:halifax",
        source_id=f"{prefix}-{index}",
        tier=SourceTier.AUTHORITATIVE,
    )


def _fill(store: Store, rows: int, *, prefix: str = "row") -> None:
    """Work landed through the real write door, not by raw INSERT.

    A fixture that reaches past the application's doors cannot detect the
    writer and the reader disagreeing, which is frequently the only thing
    worth detecting - the lesson from the blind irreplaceable() counter.
    """
    reconcile_batch(
        store,
        [_txn(index, prefix=prefix) for index in range(rows)],
        digest=f"digest-{prefix}-{rows}",
    )


def _store_with_work(path: Path, rows: int = 40, *, prefix: str = "row") -> None:
    with Store(path) as store:
        _fill(store, rows, prefix=prefix)


def _row_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            # Names come from this package's own SCHEMA, never from input.
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            )
            for table in TABLE_NAMES
            if connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
        }
    finally:
        connection.close()


def test_Backup_WhenTheStoreHoldsUncheckpointedWork_CopyHoldsEveryRow(tmp_path: Path) -> None:
    live = tmp_path / "store.sqlite3"
    _store_with_work(live)

    result = take_backup(live, tmp_path / "backup.sqlite3")

    assert result.destination.exists()
    assert _row_counts(result.destination) == _row_counts(live)


def test_Backup_WhenAFileCopyWouldMissCommittedRows_TheBackupDoesNot(tmp_path: Path) -> None:
    """The failure that motivated this, pinned as a test.

    The live store is left OPEN with committed but un-checkpointed work, which
    is the normal state of a running service - and the state in which copying
    the main database file alone loses whatever is still only in the WAL.
    """
    live = tmp_path / "store.sqlite3"
    with Store(live) as store:
        _fill(store, 200, prefix="live")

        expected = _row_counts(live)

        naive = tmp_path / "naive-copy.sqlite3"
        shutil.copy2(live, naive)

        result = take_backup(live, tmp_path / "backup.sqlite3")

    assert _row_counts(result.destination) == expected
    # The naive copy is not asserted to be broken - whether it is depends on when
    # SQLite last checkpointed - but if it IS short, that is precisely what the
    # verified backup exists to avoid, so it must not be short in the same way.
    naive_counts = _row_counts(naive)
    if naive_counts != expected:
        assert _row_counts(result.destination) != naive_counts


def test_Backup_ReportsEveryTableItVerified_WithItsDenominator(tmp_path: Path) -> None:
    """A count with no denominator cannot distinguish thorough from lucky."""
    live = tmp_path / "store.sqlite3"
    _store_with_work(live)

    result = take_backup(live, tmp_path / "backup.sqlite3")

    assert result.tables_verified == len(result.row_counts)
    assert result.tables_verified == len(TABLE_NAMES)
    assert result.rows_verified == sum(result.row_counts.values())
    assert "transactions" in result.row_counts


def test_Backup_WhenTheDestinationAlreadyExists_RefusesRatherThanOverwriting(
    tmp_path: Path,
) -> None:
    live = tmp_path / "store.sqlite3"
    _store_with_work(live)
    occupied = tmp_path / "backup.sqlite3"
    occupied.write_bytes(b"an earlier backup nobody has checked")

    with pytest.raises(BackupRefused) as refusal:
        take_backup(live, occupied)

    assert "exists" in str(refusal.value).lower()
    assert occupied.read_bytes() == b"an earlier backup nobody has checked"


def test_Backup_WhenTheSourceIsMissing_RefusesAndNamesThePath(tmp_path: Path) -> None:
    absent = tmp_path / "not-here.sqlite3"

    with pytest.raises(BackupRefused) as refusal:
        take_backup(absent, tmp_path / "backup.sqlite3")

    assert str(absent) in str(refusal.value)


def test_Backup_WhenTheStoreChangesAfterwards_TheCopyIsPointInTime(tmp_path: Path) -> None:
    live = tmp_path / "store.sqlite3"
    _store_with_work(live, rows=10)

    result = take_backup(live, tmp_path / "backup.sqlite3")
    taken = _row_counts(result.destination)["transactions"]

    _store_with_work(live, rows=5, prefix="later")  # more work lands after the backup

    assert _row_counts(result.destination)["transactions"] == taken


def test_Verification_WhenTheCopyIsMissingRows_FailsAndNamesTheTable(tmp_path: Path) -> None:
    """The check that a truncated copy must not survive.

    `integrity_check` passes on a copy that is missing hundreds of committed
    rows, so it is asserted here that verification looks at the rows themselves.
    """
    live = tmp_path / "store.sqlite3"
    _store_with_work(live)
    result = take_backup(live, tmp_path / "backup.sqlite3")

    tampered = sqlite3.connect(result.destination)
    tampered.execute(
        "DELETE FROM transactions WHERE rowid IN (SELECT rowid FROM transactions LIMIT 3)"
    )
    tampered.commit()
    assert tampered.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    tampered.close()

    with pytest.raises(BackupRefused) as refusal:
        verify_copy(live, result.destination)

    message = str(refusal.value)
    assert "transactions" in message
    # The evidence, not a bare verdict: both sides of the comparison.
    assert "37" in message and "40" in message


class TestTheCommandLineDoor:
    """The route the nightly job actually takes."""

    def test_Backup_WhenRun_WritesTheFileAndReportsWhatItVerified(self, tmp_path, capsys) -> None:
        from obdi.cli import main

        live = tmp_path / "store.sqlite3"
        _store_with_work(live)
        destination = tmp_path / "out" / "obdi-2026-08-11.sqlite3"

        assert main(["--db", str(live), "backup", str(destination)]) == 0

        printed = capsys.readouterr().out
        assert destination.exists()
        assert f"{len(TABLE_NAMES)} tables" in printed
        assert str(destination) in printed

    def test_Backup_WhenTheDestinationExists_ExitsNonZeroAndSaysWhy(self, tmp_path, capsys) -> None:
        from obdi.cli import main

        live = tmp_path / "store.sqlite3"
        _store_with_work(live)
        destination = tmp_path / "taken.sqlite3"
        destination.write_bytes(b"an earlier backup")

        assert main(["--db", str(live), "backup", str(destination)]) == 1
        assert "REFUSED" in capsys.readouterr().err

    def test_VerifyBackup_WhenTheCopyIsShort_ExitsNonZero(self, tmp_path, capsys) -> None:
        from obdi.cli import main

        live = tmp_path / "store.sqlite3"
        _store_with_work(live)
        copy = take_backup(live, tmp_path / "backup.sqlite3").destination

        assert main(["--db", str(live), "verify-backup", str(copy)]) == 0

        _store_with_work(live, rows=3, prefix="after")  # the live store moves on
        assert main(["--db", str(live), "verify-backup", str(copy)]) == 1
        assert "transactions" in capsys.readouterr().err


def test_Verification_WhenTheCopyIsNotADatabase_FailsRatherThanRaising(tmp_path: Path) -> None:
    live = tmp_path / "store.sqlite3"
    _store_with_work(live)
    rubbish = tmp_path / "not-a-database.sqlite3"
    rubbish.write_bytes(b"this is not a database")

    with pytest.raises(BackupRefused) as refusal:
        verify_copy(live, rubbish)

    assert "not-a-database" in str(refusal.value)
