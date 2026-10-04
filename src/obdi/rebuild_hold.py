"""Whether a rebuild holds the derived layer, and the one sentence every verdict says meanwhile.

A rebuild empties the derived layer and replays it, taking a minute or two.
Every check that reads the derived rows reads a half-built store for that long.
On the live instance the Overview said, mid-rebuild, that thousands of days disagreed with their
sources, that over a hundred transfer legs had no partner, that a thousand rows were flagged
for review, and that no artefact's rows were held; each account card said agreement was held
back by faults, and none of it was true of the finished store a minute later.

THE HOLD IS THE LEASE. A rebuild holds the `rebuild-derived` lease from before it empties
anything until after it has finished (`cli.start_background_rebuild`), and a lease that is not
renewed expires on its TTL (`leases`), so a rebuild that died stops holding the checks silent
by itself. The status file is read only for when the rebuild started: it says "running" for a
dead rebuild for ever, which is why it cannot be the authority.

THE OTHER HALF IS `abandoned`. A status file that says "running" with no live lease is a rebuild
that started and never finished, so the store may be half-built and the checks are reading it
as it stands. That is said, loudly, once, by whoever reports findings.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import leases

LEASE = "rebuild-derived"
STATUS_FILE = "rebuild-status.json"


@dataclass(frozen=True)
class RebuildHold:
    """A rebuild is replaying the derived layer, and since when."""

    started: str

    def sentence(self) -> str:
        """The one thing a paused verdict says, the same wherever it is read."""
        since = f" (started {self.started})" if self.started else ""
        return (
            f"A rebuild of the derived data is in progress{since}, so the checks that read it "
            "are paused; they resume when the rebuild finishes."
        )


class RebuildInProgress(Exception):
    """Raised by a derived-layer reader that was asked while a rebuild holds the layer."""

    def __init__(self, hold: RebuildHold) -> None:
        super().__init__(hold.sentence())
        self.hold = hold


@dataclass(frozen=True)
class RebuildEpoch:
    """The rebuild state at one moment, comparable with the state at another.

    A value worked out across a rebuild reads rows from before it, during it, or after it, and
    nothing it carries says which. Two epochs that differ say a rebuild began or ended between
    them, whatever the rows' own counts and stamps say.
    """

    held: bool
    marks: tuple[str, ...]


def _status(db_path: Path) -> dict[str, object]:
    with contextlib.suppress(OSError, ValueError):
        decoded = json.loads((db_path.parent / STATUS_FILE).read_text(encoding="utf-8"))
        if isinstance(decoded, dict):
            return decoded
    return {}


def hold_for(db_path: Path, now: datetime | None = None) -> RebuildHold | None:
    """The hold, if a rebuild's lease is live; `now` is injected so a test never waits out a TTL."""
    live = [
        entry
        for entry in leases.active(leases.locks_dir(db_path), now)
        if entry.get("name") == LEASE
    ]
    if not live:
        return None
    status = _status(db_path)
    started = str(status.get("started_at") or "") if status.get("state") == "running" else ""
    return RebuildHold(started or str(live[0].get("taken_at") or ""))


def require_idle(db_path: Path, now: datetime | None = None) -> None:
    """Refuse to read the derived layer while a rebuild holds it."""
    hold = hold_for(db_path, now)
    if hold is not None:
        raise RebuildInProgress(hold)


def abandoned_for(db_path: Path, now: datetime | None = None) -> str | None:
    """What to say of a rebuild that started and left no live lease, or None.

    The status file is written "running" before the thread starts and "done" after the lease is
    released, so for the instant between those two a finished rebuild reads as abandoned.
    The reader that finds it then is told again a cycle later, when it has gone.
    """
    if hold_for(db_path, now) is not None:
        return None
    status = _status(db_path)
    if status.get("state") != "running":
        return None
    started = str(status.get("started_at") or "an unrecorded time")
    return (
        f"a rebuild started at {started} never finished and its lease has expired, so the "
        "derived layer may be half-built and every check is reading it as it stands - run "
        "'Rebuild from raw' again"
    )


def epoch_for(db_path: Path, now: datetime | None = None) -> RebuildEpoch:
    status = _status(db_path)
    return RebuildEpoch(
        held=hold_for(db_path, now) is not None,
        marks=(
            str(status.get("state") or ""),
            str(status.get("started_at") or ""),
            str(status.get("finished_at") or ""),
        ),
    )
