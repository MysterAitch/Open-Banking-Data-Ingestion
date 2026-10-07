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
database rather than a byte copy.

WHAT CAN BE PROVED, AND WHEN. `verify_copy` proves a copy holds what the source
holds by counting both sides, so it is only meaningful while the source still
holds what it held when the copy was taken - which in practice means at the
moment of copying. This module used to claim that it also served "a backup taken
months ago"; it never could. Against a store that has moved on, an archive is
short by construction and the answer is always "not trustworthy", which is
useless as a trust signal and dangerous as a discard signal.

Worse, the two explanations for a shortfall are INDISTINGUISHABLE by counts. A
copy that lost its write-ahead log is missing the newest rows; a backup taken
last month is missing the newest rows. No comparison of totals can separate
them, and a check that appeared to would be inventing a verdict. So the refusal
names both readings rather than picking one.

What an archive CAN be asked on its own is `inspect_backup`: does it open, does
it pass its own integrity check, which tables does it hold and which does it
predate, and how many rows are in each. That is a description, not a
verification, and it says so - because "inspected 12 tables, 4,812 rows" reads
as proof of completeness to anybody not told otherwise.
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

    That strictness is also why this is the wrong question to ask of an archive.
    See the module docstring: a backup that is merely old fails here for the
    same reason a truncated one does, and the counts cannot tell you which.
    `inspect_backup` is what an archive can answer on its own.
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
    #: Every disagreement is the copy holding FEWER rows than the source ever
    #: did. That is the one shape an older backup produces, so it is the one
    #: shape where age has to be offered as an explanation.
    all_below = True
    for table, after in live.items():
        was = earlier.get(table, after)
        low, high = min(was, after), max(was, after)
        got = held.get(table)
        if got is not None and low <= got <= high:
            continue
        if got is None or got > high:
            all_below = False
        expected = f"{low}" if low == high else f"{low}..{high} while it was copied"
        short.append(f"  {table}: source {expected}, copy {got if got is not None else 'absent'}")

    if short:
        raise BackupRefused(
            "the copy does not hold what the source holds, table by table:\n"
            + "\n".join(short)
            + "\n\nintegrity_check passed on this copy, which is why it is not"
            " the check that matters."
            + (_AGE_OR_TRUNCATION if all_below else "")
        )
    return held


#: Appended when every disagreement is the copy being SHORT. Two explanations
#: fit that equally, a lost write-ahead log and an ordinary passage of time, and
#: no comparison of totals can separate them - both lose the newest rows. Said
#: out loud because the alternative is somebody deleting a healthy archive on
#: the strength of the word "REFUSED".
_AGE_OR_TRUNCATION = (
    "\n\nEvery difference here is the copy holding FEWER rows, which has two"
    " explanations this check cannot tell apart: the copy lost rows it should"
    " have had, or the copy is simply OLDER than the store and the store has"
    " moved on since. Both lose the newest rows and nothing in the totals"
    " distinguishes them. If this is an archive rather than a copy just taken,"
    " this is the wrong question to ask of it - use inspect-backup, which"
    " reports what the file holds without asking the live store anything."
)


@dataclass(frozen=True)
class BackupInspection:
    """What a backup file says about itself, with no reference to any store."""

    path: Path
    integrity: str
    row_counts: dict[str, int]
    #: Tables this package's schema defines that the file does not have. An
    #: archive predating a table is the ordinary reason, and knowing WHICH ones
    #: is what somebody restoring it needs before the absence surprises them.
    tables_absent: list[str]
    bytes_held: int

    def describe(self) -> str:
        """Every count beside its denominator, and the claim's limit stated.

        The limit is not a footnote. A report reading "12 tables, 4,812 rows"
        is taken as proof of completeness by anybody not explicitly told that
        no file can testify to its own completeness - which is the whole
        difference between this and what `take_backup` proves at copy time.
        """
        lines = [
            f"inspected   {self.path}",
            f"integrity   {self.integrity}",
            f"holds       {len(self.row_counts)} of {len(TABLE_NAMES)} tables, "
            f"{sum(self.row_counts.values())} rows",
            f"size        {self.bytes_held} bytes",
        ]
        if self.tables_absent:
            lines.append(
                f"absent      {', '.join(self.tables_absent)} - this file predates them, "
                "or they were dropped from it"
            )
        lines.append(
            "limit       this describes the file; it CANNOT tell you whether the file "
            "holds everything the store held when it was taken. Only the copy step can "
            "know that, and it checks at the time."
        )
        return "\n".join(lines)


def inspect_backup(path: Path) -> BackupInspection:
    """Report what a backup holds, asking the live store nothing.

    The check for an archive. `verify_copy` compares against a live source and
    so can only ever refuse a backup older than the store's current state - see
    the module docstring for why that refusal is unreadable rather than
    informative. This makes the weaker claim that can actually be supported by
    a file on its own, and names the weakness.
    """
    path = Path(path)
    if not path.exists():
        raise BackupRefused(f"there is no backup to inspect at {path}")

    integrity = _integrity(path)
    counts = _counts(path, what="the backup")
    return BackupInspection(
        path=path,
        integrity=integrity,
        row_counts=counts,
        tables_absent=[table for table in TABLE_NAMES if table not in counts],
        bytes_held=path.stat().st_size,
    )


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
