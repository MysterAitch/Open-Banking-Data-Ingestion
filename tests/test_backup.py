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


class TestCheckingABackupTakenLongAgo:
    """The case the module's own docstring promised and did not deliver.

    `verify_copy` compares a copy against the live store. For any backup not
    taken moments ago that comparison is guaranteed to disagree, so the answer
    it gives an archive is always "not trustworthy" - which is useless as a
    trust signal and actively dangerous as a discard signal.

    What CAN be said about a backup on its own: it opens, it passes its own
    integrity check, it holds these tables and lacks those, and it contains
    this many rows. What cannot be said, at all, from the file alone: whether
    it holds everything the store held when it was taken. That is the claim
    `take_backup` makes at the moment of copying and the reason verification
    belongs there. This is why the standalone check is called INSPECT.
    """

    def test_InspectBackup_WhenTheStoreHasMovedOnEntirely_StillReportsWhatTheBackupHolds(
        self, tmp_path: Path
    ) -> None:
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)
        copy = tmp_path / "archive.sqlite3"
        take_backup(live, copy)

        _store_with_work(live, rows=40, prefix="months-of-later-work")

        from obdi.backup import inspect_backup

        found = inspect_backup(copy)

        assert found.row_counts["transactions"] == 10
        assert found.integrity == "ok"
        assert found.bytes_held == copy.stat().st_size

    def test_InspectBackup_StatesWhatItCannotKnow_RatherThanReadingAsAVerification(
        self, tmp_path: Path
    ) -> None:
        """The bound is the point, so it is asserted rather than assumed.

        Somebody reading "inspected 12 tables, 4,812 rows" will take it as
        proof the backup is complete unless the report says otherwise, and
        completeness is exactly the thing a lone file cannot testify to.
        """
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)
        copy = tmp_path / "archive.sqlite3"
        take_backup(live, copy)

        from obdi.backup import inspect_backup

        described = inspect_backup(copy).describe()

        assert "cannot" in described.lower()
        assert "verified" not in described.lower()

    def test_InspectBackup_WhenTheBackupPredatesATable_NamesTheAbsence(
        self, tmp_path: Path
    ) -> None:
        """An old backup legitimately lacks tables added since.

        Reported as a named absence, not a crash and not silence: which tables
        an archive predates is exactly what somebody restoring it needs to know
        before they are surprised by it.
        """
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=6)
        copy = tmp_path / "before-that-table-existed.sqlite3"
        take_backup(live, copy)

        older = sqlite3.connect(copy)
        older.execute("DROP TABLE review_queue")
        older.commit()
        older.close()

        from obdi.backup import inspect_backup

        found = inspect_backup(copy)

        assert "review_queue" in found.tables_absent
        assert "review_queue" not in found.row_counts
        assert "review_queue" in found.describe()

    def test_InspectBackup_WhenTheFileIsNotADatabase_RefusesAndNamesIt(
        self, tmp_path: Path
    ) -> None:
        rubbish = tmp_path / "not-a-database.sqlite3"
        rubbish.write_bytes(b"this is not a database")

        from obdi.backup import inspect_backup

        with pytest.raises(BackupRefused) as refusal:
            inspect_backup(rubbish)

        assert "not-a-database" in str(refusal.value)

    def test_InspectBackup_WhenTheFileIsAbsent_RefusesAndNamesThePath(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "never-taken.sqlite3"

        from obdi.backup import inspect_backup

        with pytest.raises(BackupRefused) as refusal:
            inspect_backup(missing)

        assert str(missing) in str(refusal.value)

    def test_InspectBackup_WhenRunFromTheCommandLine_ReportsAndExitsZero(
        self, tmp_path, capsys
    ) -> None:
        """The route somebody takes when deciding whether to keep an archive."""
        from obdi.cli import main

        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)
        copy = tmp_path / "archive.sqlite3"
        take_backup(live, copy)

        _store_with_work(live, rows=25, prefix="since")

        assert main(["--db", str(live), "inspect-backup", str(copy)]) == 0

        printed = capsys.readouterr().out
        assert str(copy) in printed
        assert "cannot" in printed.lower()

    def test_InspectBackup_WhenTheFileIsRubbish_ExitsNonZeroAndSaysWhy(
        self, tmp_path, capsys
    ) -> None:
        from obdi.cli import main

        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=4)
        rubbish = tmp_path / "not-a-database.sqlite3"
        rubbish.write_bytes(b"this is not a database")

        assert main(["--db", str(live), "inspect-backup", str(rubbish)]) == 1
        assert "not-a-database" in capsys.readouterr().err


def test_Verification_WhenTheCopyIsNotADatabase_FailsRatherThanRaising(tmp_path: Path) -> None:
    live = tmp_path / "store.sqlite3"
    _store_with_work(live)
    rubbish = tmp_path / "not-a-database.sqlite3"
    rubbish.write_bytes(b"this is not a database")

    with pytest.raises(BackupRefused) as refusal:
        verify_copy(live, rubbish)

    assert "not-a-database" in str(refusal.value)


class TestACopyTakenWhileTheStoreIsBeingWritten:
    """A snapshot of a moving store is still a snapshot.

    MEASURED 2026-08-15 on the live instance: a converge finished, the scheduler
    restarted and logged one fetch attempt, and the backup that ran ten seconds
    later was refused for `fetch_attempts: source 801, copy 800`. The copy was
    deleted and the deploy failed - over a copy that was perfectly good and had
    passed its own integrity check.

    The check compared the copy against the source AS IT WAS AFTERWARDS, which
    for a store under continuous write asks whether the copy matches a moving
    target. The question it needs is whether the copy faithfully captured the
    source at the instant it was taken, and reading the source on BOTH sides of
    the copy bounds that instant without weakening anything.
    """

    def test_Copy_WhenSourceGrewDuringTheCopy_IsAcceptedWithinTheObservedBand(
        self, tmp_path: Path
    ) -> None:
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)
        copy = tmp_path / "copy.sqlite3"
        take_backup(live, copy)

        before = _row_counts(live)
        _store_with_work(live, rows=6, prefix="later")

        # The copy sits at the lower edge of the band: it holds what the source
        # held when it was taken, and the source has moved on since.
        assert verify_copy(live, copy, source_before=before) == before

    def test_Copy_WhenShorterThanBothReadings_IsStillRefused(
        self, tmp_path: Path
    ) -> None:
        # The band must not become a licence. A copy below everything the source
        # was ever observed to hold is the truncation this module exists for.
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=4)
        copy = tmp_path / "copy.sqlite3"
        take_backup(live, copy)

        _store_with_work(live, rows=6, prefix="later")
        after = _row_counts(live)

        # As if the source already held its later totals before the copy was
        # taken, which puts this copy below the whole band.
        with pytest.raises(BackupRefused) as refusal:
            verify_copy(live, copy, source_before=after)

        assert "transactions" in str(refusal.value)

    def test_Copy_WhenHoldingMoreThanTheSourceEverHeld_IsRefused(
        self, tmp_path: Path
    ) -> None:
        # The other edge, and the one a naive "a copy may be smaller" rule
        # misses: a copy holding rows the source never had is not a stale
        # snapshot, it is a copy of something else. Built from two stores
        # because that is what the case actually is - an earlier attempt
        # inflated the band instead and proved only that the band works.
        fuller = tmp_path / "fuller.sqlite3"
        _store_with_work(fuller, rows=16)
        copy = tmp_path / "copy.sqlite3"
        take_backup(fuller, copy)

        smaller = tmp_path / "smaller.sqlite3"
        _store_with_work(smaller, rows=4)

        with pytest.raises(BackupRefused) as refusal:
            verify_copy(smaller, copy, source_before=_row_counts(smaller))

        assert "transactions" in str(refusal.value)

    def test_VerifyCopy_WhenGivenNoEarlierReading_StillDemandsAnExactMatch(
        self, tmp_path: Path
    ) -> None:
        # The standalone verify-backup command has no "before" to work with, so
        # its strictness must be untouched by any of this.
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)
        copy = tmp_path / "copy.sqlite3"
        take_backup(live, copy)

        _store_with_work(live, rows=3, prefix="later")

        with pytest.raises(BackupRefused):
            verify_copy(live, copy)

    def test_Backup_WhenSourceIsQuiet_ClaimsNoMovementAtAll(
        self, tmp_path: Path
    ) -> None:
        # The nightly case: nothing writes at 03:21, the band collapses to a
        # point, and the result must not imply movement that did not happen.
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)

        result = take_backup(live, tmp_path / "copy.sqlite3")

        assert result.source_advanced == {}
        assert "advanced" not in result.describe()

    def test_VerifyCopy_WhenTheBackupIsMerelyOlder_SaysTheShortfallCouldBeEither(
        self, tmp_path: Path
    ) -> None:
        """The refusal must not read as "your backup is corrupt".

        A backup taken last month is short against today's store for the
        entirely ordinary reason that a month happened. The counts CANNOT
        distinguish that from a truncated copy - both lose the newest rows -
        so the message has to say which two things it is unable to tell apart,
        or a healthy archive gets thrown away on its evidence.
        """
        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)
        copy = tmp_path / "last-month.sqlite3"
        take_backup(live, copy)

        _store_with_work(live, rows=6, prefix="later")

        with pytest.raises(BackupRefused) as refusal:
            verify_copy(live, copy)

        message = str(refusal.value)
        assert "transactions" in message
        assert "older" in message.lower()
        assert "inspect-backup" in message

    def test_Backup_WhenSourceMovesDuringTheCopy_NamesTheTableAndTheDelta(
        self, tmp_path: Path
    ) -> None:
        # Evidence rather than a verdict: "the source advanced" is unactionable,
        # while the table and the amount let a reader judge whether the writer
        # was the expected one.
        from obdi import backup as backup_module

        live = tmp_path / "store.sqlite3"
        _store_with_work(live, rows=10)
        real_counts = backup_module._counts
        seen: list[str] = []

        def counting(path: Path, *, what: str) -> dict[str, int]:
            if what == "the source" and not seen:
                seen.append(what)
                counts = real_counts(path, what=what)
                # A row lands between the two source readings, which is exactly
                # what a restarted worker does seconds after a deploy.
                _store_with_work(live, rows=2, prefix="racer")
                return counts
            return real_counts(path, what=what)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(backup_module, "_counts", counting)
        try:
            result = take_backup(live, tmp_path / "copy.sqlite3")
        finally:
            monkey.undo()

        assert result.source_advanced
        assert "advanced" in result.describe()
