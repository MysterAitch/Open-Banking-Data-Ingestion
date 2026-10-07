"""Cooperative leases: who is mid-something, so nobody tramples them.

The stack has several actors whose critical sections must not overlap
with a container update: the scheduler mid-pull-cycle (bank quota is
precious and a killed fetch wastes a scheduled slot), the applier
mid-import, a person mid-bank-authorisation (the SCA window is five
minutes and does not come back). And the reverse: none of those should
START while an update is about to recreate their container.

A lease is one JSON file in a shared directory: {name, holder, taken_at,
ttl_seconds}. Cooperative, not enforced - every reader chooses to honour
what it sees. The TTL is mandatory and load-bearing: a holder that
crashes must never wedge updates forever, so an expired lease reads as
absent everywhere.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

#: The updater's own lease name - workers check for this one before
#: starting new critical work.
STACK_UPDATE = "stack-update"


def locks_dir(db_path: Path) -> Path:
    configured = os.getenv("OBDI_LOCKS_DIR", "").strip()
    return Path(configured) if configured else db_path.parent / "locks"


def acquire(directory: Path, name: str, holder: str, ttl_seconds: int) -> Path:
    """Write-temp-then-rename so readers can never see a torn lease: the
    rename is atomic on one filesystem, and an unparseable file would read
    as absent - the one failure direction a lease must not have."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    tmp = directory / f".{name}.json.tmp"
    tmp.write_text(
        json.dumps(
            {
                "name": name,
                "holder": holder,
                "taken_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ttl_seconds": ttl_seconds,
            }
        ),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def acquire_exclusive(
    directory: Path, name: str, holder: str, ttl_seconds: int
) -> bool:
    """Take the lease only if nobody live holds it; True on success.

    acquire() is a plain overwrite (fine for renewal by the holder), so it
    cannot arbitrate two actors racing for the same lease. This variant
    uses O_EXCL creation as the arbiter: exactly one creator wins. An
    existing-but-expired lease is removed and contested again - if two
    actors race the removal, the O_EXCL retry still picks one winner.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    payload = json.dumps(
        {
            "name": name,
            "holder": holder,
            "taken_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ttl_seconds": ttl_seconds,
        }
    )
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entry = None
            if isinstance(entry, dict) and not _expired(entry, datetime.now(UTC)):
                return False
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return True
    return False


def release(directory: Path, name: str) -> None:
    with contextlib.suppress(OSError):
        (directory / f"{name}.json").unlink()


def _expired(entry: dict[str, object], now: datetime) -> bool:
    try:
        taken = datetime.fromisoformat(
            str(entry.get("taken_at", "")).replace("Z", "+00:00")
        )
        ttl = int(str(entry.get("ttl_seconds", 0)))
    except ValueError:
        return True
    return (now - taken).total_seconds() > ttl


def active(directory: Path, now: datetime | None = None) -> list[dict[str, object]]:
    """Unexpired leases, oldest first. Expired files read as absent - the
    crash-safety property - but are left in place for forensics."""
    if not directory.is_dir():
        return []
    now = now or datetime.now(UTC)
    leases = []
    for path in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and not _expired(entry, now):
            leases.append(entry)
    return leases


def held(directory: Path, name: str, now: datetime | None = None) -> bool:
    return any(entry.get("name") == name for entry in active(directory, now))


@contextlib.contextmanager
def lease(
    directory: Path, name: str, holder: str, ttl_seconds: int
) -> Iterator[None]:
    acquire(directory, name, holder, ttl_seconds)
    try:
        yield
    finally:
        release(directory, name)
