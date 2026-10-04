"""What the pages that show an account's standing hold between requests.

The movement report walks every artefact and every row, so a page asking for it on every view
would pay that each time. It is held for as long as the derived layer is unchanged, judged by a
key that moves whenever a rebuild, an import, a pull, or a pairing pass rewrites anything the
checks read. A key that is read cheaply and moves too often costs one recomputation; one that
fails to move would show a stale fault, which is why it reads what a derivation writes (the
rows' last sighting, the pairs, the artefacts) rather than a clock.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING

from .agreement import Standing, standing_of
from .balance_anchors import effective_opening
from .family_anchors import Families
from .store import Store

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .movement_completeness import MovementCompleteness


def movement_key(store: Store) -> tuple[object, ...]:
    """A value that differs whenever the rows or artefacts the movement checks read have changed."""
    rows = store.connection.execute(
        "SELECT COUNT(*), COALESCE(MAX(last_seen_at), ''), COALESCE(MAX(first_seen_at), '') "
        "FROM transactions"
    ).fetchone()
    pairs = store.connection.execute("SELECT COUNT(*) FROM transfer_pairs").fetchone()
    artefacts = store.connection.execute("SELECT COUNT(*) FROM raw_artefacts").fetchone()
    sightings = store.connection.execute("SELECT COUNT(*) FROM transaction_sources").fetchone()
    return (*tuple(rows), pairs[0], artefacts[0], sightings[0])


class MovementMemo:
    """One movement report, reused while `movement_key` is unchanged."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: tuple[object, MovementCompleteness] | None = None

    def get(
        self, store: Store, compute: Callable[[], MovementCompleteness]
    ) -> MovementCompleteness:
        key = movement_key(store)
        with self._lock:
            if self._held is not None and self._held[0] == key:
                return self._held[1]
        report = compute()
        with self._lock:
            self._held = (key, report)
        return report


def standings_for(
    store: Store,
    refs: Iterable[str],
    *,
    families: Families | None,
    movement: MovementCompleteness | None,
) -> Mapping[str, Standing]:
    """The standing of each account that holds rows, from the same opening its page would build."""
    found: dict[str, Standing] = {}
    for ref in refs:
        rows = store.transactions_for_account(ref)
        opening = effective_opening(store, ref, rows, families=families)
        members = [ref, *(families.spaces_of(ref) if families is not None else ())]
        found[ref] = standing_of(opening, members, movement)
    return found
