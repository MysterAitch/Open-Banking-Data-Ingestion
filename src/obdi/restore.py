"""Turning a copy back into the store, on the worst day somebody will have.

The backup half of this pair proves a copy holds every row the live store held.
It says nothing about whether that copy can BECOME the store again, and those
are different claims - the second one is only ever tested by doing it, which is
why it tends to be tested for the first time during an emergency.

Everything here is shaped by who is running it and when. A restore happens
because something has gone wrong, under time pressure, by somebody who is not at
their best. So:

  NOTHING IS DELETED. A store being replaced is moved aside, keeping its name and
  gaining a suffix, and the result says where it went. Restoring the wrong
  backup is a mistake somebody will make; it is recoverable only while what they
  overwrote still exists somewhere they can find without knowing to look.

  A COPY THAT CANNOT BE TRUSTED IS REFUSED BEFORE ANYTHING IS TOUCHED. The order
  matters more than the check: verifying afterwards means the store is already
  gone when the bad news arrives.

  THE SIDECARS GO WITH THE DATABASE THEY BELONG TO. A store running with
  write-ahead logging has `-wal` and `-shm` files beside it holding its recent
  commits. Leaving those next to a restored file is the same family of fault as
  copying the main file alone when taking a backup - which this project met, and
  measured at 600-750 missing rows while every ordinary check passed.

  THE RESTORED FILE IS OPENED THROUGH THE APPLICATION before the result is
  returned, so what is reported is a store this release can USE rather than a
  file that exists. A copy taken before a schema change has to come forward
  through the migration ladder, and a restore that stopped at copying would
  leave that to be discovered by the next pull. Considered and rejected: a pure
  byte-for-byte restore that touches nothing. It is a cleaner operation and a
  less useful one - it hands back a file whose usability is exactly the question
  being asked.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .backup import BackupRefused, _counts, _integrity

#: Suffixes SQLite keeps beside a database in write-ahead logging mode.
SIDECARS = ("-wal", "-shm")


@dataclass(frozen=True)
class RestoreResult:
    """What was restored, from what, and what was kept in case it was wrong."""

    backup: Path
    destination: Path
    row_counts: dict[str, int]
    schema_version: str
    #: Where the previous store went, or None when there was nothing to keep.
    kept_aside: Path | None

    @property
    def rows_restored(self) -> int:
        return sum(self.row_counts.values())

    def describe(self) -> str:
        lines = [
            f"restored    {self.destination}",
            f"from        {self.backup}",
            f"holding     {len(self.row_counts)} tables, {self.rows_restored} rows",
            f"schema      version {self.schema_version}",
        ]
        # Named, not merely done. Somebody who restored the wrong copy needs this
        # line to be the thing they remember seeing.
        lines.append(
            f"kept        {self.kept_aside}"
            if self.kept_aside is not None
            else "kept        nothing was replaced"
        )
        return "\n".join(lines)


def _next_free(path: Path) -> Path:
    """`store.sqlite3` -> `store.sqlite3.replaced`, then `.replaced-2`, and so on.

    Numbered rather than stamped with the time: two restores in one minute are
    exactly what a bad day looks like, and a name that collides would quietly
    destroy the copy kept by the first one.
    """
    candidate = path.with_name(f"{path.name}.replaced")
    attempt = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.replaced-{attempt}")
        attempt += 1
    return candidate


def restore_backup(
    backup: Path, destination: Path, *, replace: bool = False
) -> RestoreResult:
    """Make `backup` the store at `destination`, and prove it is usable.

    Refuses when something is already at the destination unless `replace` is
    asked for explicitly, and even then keeps what was there.
    """
    backup = Path(backup)
    destination = Path(destination)

    if not backup.exists():
        raise BackupRefused(f"there is no backup to restore at {backup}")

    # Before anything is touched. A backup that cannot be read is not news worth
    # having after the store it was going to replace has already been moved.
    integrity = _integrity(backup)
    if integrity != "ok":
        raise BackupRefused(
            f"the backup fails its own integrity check ({backup}): {integrity}"
        )
    expected = _counts(backup, what="the backup")

    kept_aside: Path | None = None
    if destination.exists():
        if not replace:
            raise BackupRefused(
                f"a store already exists at {destination} and will not be replaced. "
                "It may be the only copy of something. Pass replace to go ahead - "
                "the existing store is kept aside, not deleted."
            )
        kept_aside = _next_free(destination)
        destination.rename(kept_aside)

    # The sidecars belong to whatever was there before, so they travel with it -
    # or are removed when there was nothing to keep them for.
    for suffix in SIDECARS:
        sidecar = destination.with_name(f"{destination.name}{suffix}")
        if not sidecar.exists():
            continue
        if kept_aside is not None:
            sidecar.rename(kept_aside.with_name(f"{kept_aside.name}{suffix}"))
        else:
            sidecar.unlink()

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, destination)

    held = _counts(destination, what="the restored store")
    short = [
        f"  {table}: backup {expected[table]}, restored {held.get(table, 'absent')}"
        for table in expected
        if held.get(table) != expected[table]
    ]
    if short:
        raise BackupRefused(
            "the restored store does not hold what the backup holds, table by "
            "table:\n" + "\n".join(short)
        )

    # Opened through the application, so the ladder runs and what is reported is
    # a store this release can use. Imported here rather than at module scope:
    # store imports nothing from this module, and keeping it that way means a
    # restore can be reasoned about without the whole application in view.
    from .store import Store

    with Store(destination) as store:
        version = store.connection.execute(
            "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
        ).fetchone()
        schema_version = str(version[0]) if version else "unknown"
        # Counted AFTER the ladder ran: a migration that rebuilds a table is
        # entitled to change these, and the number worth reporting is the one
        # the store actually holds once it is usable.
        restored = {
            table: int(
                store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            )
            for table in held
        }

    return RestoreResult(
        backup=backup,
        destination=destination,
        row_counts=restored,
        schema_version=schema_version,
        kept_aside=kept_aside,
    )
