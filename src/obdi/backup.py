"""Taking a copy of the store that can be trusted to hold what it claims.

Copying `store.sqlite3` as a file is the obvious thing and it is wrong. With
write-ahead logging on - which is how the store runs - committed rows live in
the `-wal` sidecar until a checkpoint folds them into the main file, so a copy
of the main file alone is a copy of the database as of some earlier moment. It
opens cleanly. It passes `PRAGMA integrity_check`. Measured across four trials
it was short by 600-750 committed rows every time, and reported no problem on
any of them.

That is the whole reason this module exists rather than a `cp` in a shell
script: the failure is invisible to every check somebody would naturally run,
so the check has to be one nobody would naturally run - counting the rows on
both sides and refusing when they disagree.

`VACUUM INTO` is the mechanism. It reads through an open connection, so it sees
the WAL contents like any other reader, and it writes a fresh, defragmented
database rather than a byte copy. The verification is separate and stands alone
(`verify_copy`), because a backup taken months ago still needs a way to be
checked before it is trusted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .store import TABLE_NAMES


class BackupRefused(Exception):
    """A backup could not be taken, or a copy could not be trusted.

    Refusing is the point. A backup step that reports success on a copy it
    could not verify is worse than no backup at all, because the belief that
    one exists is what stops anybody checking.
    """


@dataclass(frozen=True)
class BackupResult:
    """What was copied, and the evidence that it was copied completely."""

    source: Path
    destination: Path
    row_counts: dict[str, int]
    bytes_written: int
    #: Tables the SOURCE gained rows in while the copy was being taken, and by
    #: how many. Empty is the ordinary case and means the store was quiet.
    #: Reported rather than merely tolerated: a nightly run that starts showing
    #: movement is telling you something writes at 03:21 that did not use to.
    source_advanced: dict[str, int] = field(default_factory=dict)

    @property
    def tables_verified(self) -> int:
        return len(self.row_counts)

    @property
    def rows_verified(self) -> int:
        return sum(self.row_counts.values())

    def describe(self) -> str:
        """One line per fact, every count beside what it is out of.

        A backup that reports "done" has told you nothing you can act on; one
        that reports 12 of 12 tables and 4,812 rows can be compared against
        what you expected to see.
        """
        lines = [
            f"backed up   {self.source}",
            f"to          {self.destination}",
            f"verified    {self.tables_verified} of {len(TABLE_NAMES)} tables, "
            f"{self.rows_verified} rows",
            f"size        {self.bytes_written} bytes",
        ]
        if self.source_advanced:
            moved = ", ".join(
                f"{table} +{gained}"
                for table, gained in sorted(self.source_advanced.items())
            )
            lines.append(
                f"note        the source advanced while the copy was taken: {moved}. "
                "The copy is a snapshot of the store before those rows landed."
            )
        return "\n".join(lines)


def _counts(path: Path, *, what: str) -> dict[str, int]:
    """Row counts per table, for the tables that exist in this database.

    Tables are counted rather than assumed: a backup of an older store predates
    some of them, and a missing table is a fact to report rather than a crash.
    """
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as error:  # pragma: no cover - platform-dependent text
        raise BackupRefused(f"{what} could not be opened ({path}): {error}") from error

    try:
        present = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        return {
            # A table name cannot be a bound parameter. These are not user input:
            # TABLE_NAMES is read out of this package's own SCHEMA text, and only
            # names also present in the database are used.
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            )
            for table in TABLE_NAMES
            if table in present
        }
    except sqlite3.DatabaseError as error:
        raise BackupRefused(f"{what} is not a readable database ({path}): {error}") from error
    finally:
        connection.close()


def verify_copy(
    source: Path, copy: Path, *, source_before: dict[str, int] | None = None
) -> dict[str, int]:
    """Prove a copy holds what the source held. Refuse if it does not.

    Returns the verified row counts, so the caller can report the evidence
    rather than a bare verdict. Both sides of any disagreement are named: a
    detector that says only "mismatch" leaves the next person to re-derive what
    it already knew.

    `source_before` is the source's counts read BEFORE the copy was taken, and
    it is what makes this safe on a store that is being written to. Without it
    the comparison is against the source as it stands now, which asks whether
    the copy matches a moving target rather than whether it faithfully captured
    the source at the instant it was taken.

    MEASURED 2026-08-15: a deploy finished, a restarted worker logged one fetch
    attempt, and the backup ten seconds later was refused for
    `fetch_attempts: source 801, copy 800`. The copy was good, had passed its
    own integrity check, and was deleted anyway - taking the deploy with it.

    Given both readings, a count is accepted when it lies BETWEEN them: any
    value in that band is consistent with a snapshot taken somewhere inside the
    window. Nothing is weakened. A truncated copy still falls below the band, a
    copy of something else still rises above it, and when the store is quiet the
    two readings are equal and this is exactly the old exact-match check.

    Omitting `source_before` keeps that strict behaviour, which is what the
    standalone verify-backup command wants: it has no earlier reading, and a
    backup being checked long afterwards must not be given the benefit of a
    band nobody measured.
    """
    if not copy.exists():
        raise BackupRefused(f"the copy does not exist: {copy}")

    integrity = _integrity(copy)
    if integrity != "ok":
        raise BackupRefused(f"the copy fails its own integrity check ({copy}): {integrity}")

    live = _counts(source, what="the source")
    held = _counts(copy, what="the copy")
    earlier = live if source_before is None else source_before

    short: list[str] = []
    for table, after in live.items():
        was = earlier.get(table, after)
        low, high = min(was, after), max(was, after)
        got = held.get(table)
        if got is not None and low <= got <= high:
            continue
        expected = f"{low}" if low == high else f"{low}..{high} while it was copied"
        short.append(f"  {table}: source {expected}, copy {got if got is not None else 'absent'}")

    if short:
        raise BackupRefused(
            "the copy does not hold what the source holds, table by table:\n"
            + "\n".join(short)
            + "\n\nintegrity_check passed on this copy, which is why it is not"
            " the check that matters."
        )
    return held


def _advanced(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Tables the source gained rows in between two readings, and by how many."""
    return {
        table: after[table] - before[table]
        for table in after
        if after[table] > before.get(table, after[table])
    }


def _integrity(path: Path) -> str:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as error:  # pragma: no cover - platform-dependent text
        raise BackupRefused(f"the copy could not be opened ({path}): {error}") from error
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.DatabaseError as error:
        raise BackupRefused(f"the copy is not a readable database ({path}): {error}") from error
    finally:
        connection.close()


def take_backup(source: Path, destination: Path) -> BackupResult:
    """Copy the store to `destination` and verify the copy before returning.

    Refuses rather than overwrites: an existing file at the destination may be
    the only surviving backup, and a routine that silently replaces one it did
    not verify has a bad day ahead of it. Callers that rotate backups name a
    fresh path per run.
    """
    source = Path(source)
    destination = Path(destination)

    if not source.exists():
        raise BackupRefused(f"there is no store to back up at {source}")
    if destination.exists():
        raise BackupRefused(
            f"the destination already exists and will not be overwritten: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Read the source on BOTH sides of the copy, so the verification can tell a
    # snapshot of a moving store from a truncated one. Reading only afterwards
    # compares the copy against a target that may have moved since, which is how
    # a good backup was refused - and a deploy failed - over a single row.
    before = _counts(source, what="the source")

    # Read through a connection, not through the filesystem: this is what sees
    # the write-ahead log, and seeing it is the entire point.
    connection = sqlite3.connect(source)
    try:
        connection.execute("VACUUM INTO ?", (str(destination),))
    except sqlite3.Error as error:
        destination.unlink(missing_ok=True)
        raise BackupRefused(f"the copy could not be written to {destination}: {error}") from error
    finally:
        connection.close()

    try:
        counts = verify_copy(source, destination, source_before=before)
    except BackupRefused:
        # An unverified copy must not be left lying about looking like a backup.
        destination.unlink(missing_ok=True)
        raise

    return BackupResult(
        source=source,
        destination=destination,
        row_counts=counts,
        bytes_written=destination.stat().st_size,
        source_advanced=_advanced(before, _counts(source, what="the source")),
    )
