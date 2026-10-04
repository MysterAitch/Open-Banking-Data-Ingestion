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
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Generic, TypeVar

from .agreement import Standing, held_sentence, standing_line, standing_of
from .balance_anchors import effective_opening
from .family_anchors import Families
from .protection import check_span
from .store import Store

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .movement_completeness import MovementCompleteness

T = TypeVar("T")


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


def standing_key(store: Store) -> tuple[object, ...]:
    """`movement_key` and what else an account's standing reads: the balances a person stated,
    the account registry, and the protections."""
    stated = store.connection.execute(
        "SELECT COUNT(*), COALESCE(MAX(ingested_at), '') FROM valuations"
    ).fetchone()
    declared = store.connection.execute("SELECT COUNT(*) FROM declared_accounts").fetchone()
    protections = store.connection.execute(
        "SELECT COUNT(*), COALESCE(MAX(through), ''), COALESCE(MAX(pressed_at), ''), "
        "COALESCE(MAX(accepted_at), '') FROM protections"
    ).fetchone()
    events = store.connection.execute("SELECT COUNT(*) FROM protection_history").fetchone()
    return (*movement_key(store), *tuple(stated), declared[0], *tuple(protections), events[0])


class KeyedMemo(Generic[T]):
    """One computed value, reused while its key is unchanged.

    Held in the process and keyed by what the value was read from rather than by a clock, so a
    page never shows a state older than the rows beneath it and never pays twice for one.
    """

    def __init__(self, key: Callable[[Store], tuple[object, ...]]) -> None:
        self._key = key
        self._lock = threading.Lock()
        self._held: tuple[object, T] | None = None

    def get(self, store: Store, compute: Callable[[], T]) -> T:
        key = self._key(store)
        with self._lock:
            if self._held is not None and self._held[0] == key:
                return self._held[1]
        value = compute()
        with self._lock:
            self._held = (key, value)
        return value


@dataclass(frozen=True)
class AccountStanding:
    """What a card or a list line says of an account: its agreement and its protection."""

    standing: Standing
    protected_through: date | None
    protection_broken: bool


def standings_for(
    store: Store,
    refs: Iterable[str],
    *,
    families: Families | None,
    movement: MovementCompleteness | None,
) -> Mapping[str, AccountStanding]:
    """The standing of each account that holds rows, from the same opening its page would build."""
    protections = {str(r["account"]): r for r in store.protection_records()}
    found: dict[str, AccountStanding] = {}
    for ref in refs:
        rows = store.transactions_for_account(ref)
        record = protections.get(ref)
        opening = effective_opening(
            store,
            ref,
            rows,
            families=families,
            explain_after=(
                date.fromisoformat(str(record["through"]))
                if record is not None and check_span(store, record).intact
                else None
            ),
        )
        members = [ref, *(families.spaces_of(ref) if families is not None else ())]
        found[ref] = AccountStanding(
            standing_of(opening, members, movement),
            None if record is None else date.fromisoformat(str(record["through"])),
            record is not None and not check_span(store, record).intact,
        )
    return found


def standing_lines(item: AccountStanding) -> tuple[str, ...]:
    """The sentences a card or list line shows: the three dates, what holds agreement back, and
    a broken protection. Plain text; the caller escapes it."""
    own = item.standing.own
    lines = [standing_line(own, item.protected_through)]
    if item.protection_broken:
        lines.append("The protection is broken: its span has changed.")
    held = held_sentence(own)
    if held:
        lines.append(held)
    whole = item.standing.whole
    if whole is not None:
        lines.append(
            "The whole account, with its Spaces: "
            + standing_line(whole, None, with_protection=False)
        )
    return tuple(lines)
