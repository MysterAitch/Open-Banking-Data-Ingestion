"""What the pages that show an account's standing hold between requests.

The movement report walks every artefact and every row, so a page asking for it on every view
would pay that each time. It is held for as long as the derived layer is unchanged, judged by a
key that moves whenever a rebuild, an import, a pull, or a pairing pass rewrites anything the
checks read. A key that is read cheaply and moves too often costs one recomputation; one that
fails to move would show a stale fault, which is why it reads what a derivation writes (the
rows' last sighting, the pairs, the artefacts) rather than a clock.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Generic, TypeVar, cast

from .agreement import Standing, held_sentence, standing_line, standing_of
from .balance_anchors import effective_opening
from .family_anchors import Families
from .protection import check_span
from .store import Store

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .movement_completeness import MovementCompleteness
    from .rebuild_hold import RebuildEpoch

T = TypeVar("T")


def movement_key(store: Store) -> tuple[object, ...]:
    """A value that differs whenever the rows or artefacts the movement checks read have changed.

    The store's standing epoch is what guarantees that (`store.EPOCH_TABLES`); the counts and
    stamps beside it are kept as a second witness that costs nothing.
    """
    rows = store.connection.execute(
        "SELECT COUNT(*), COALESCE(MAX(last_seen_at), ''), COALESCE(MAX(first_seen_at), '') "
        "FROM transactions"
    ).fetchone()
    pairs = store.connection.execute("SELECT COUNT(*) FROM transfer_pairs").fetchone()
    artefacts = store.connection.execute("SELECT COUNT(*) FROM raw_artefacts").fetchone()
    sightings = store.connection.execute("SELECT COUNT(*) FROM transaction_sources").fetchone()
    return (*tuple(rows), pairs[0], artefacts[0], sightings[0], store.standing_epoch())


def standing_key(store: Store) -> tuple[object, ...]:
    """`movement_key` and what else an account's standing reads: the balances a person stated,
    the account registry, and the protections.

    Anything that file-backed reads, such as the account-map file, is the caller's to add: this
    reads the store alone.
    """
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

    SINGLE FLIGHT: callers that arrive for a key while it is being computed wait for that one
    computation and share its outcome, error included. The first page after a deploy did not answer
    within 110 seconds when each concurrent request (a tab and two polls, at least) started its own
    walk of the whole store. An error is not held, so the next call after it computes afresh.

    Each computation says on stderr which memo it was, how long it took, and whatever `detail`
    names about the value, because where the time goes is the next deploy's question.
    """

    def __init__(
        self,
        key: Callable[[Store], tuple[object, ...]],
        *,
        name: str = "memo",
        clock: Callable[[], float] = time.perf_counter,
        detail: Callable[[T], str] | None = None,
        epoch: Callable[[], RebuildEpoch] | None = None,
    ) -> None:
        self._key = key
        self._name = name
        self._clock = clock
        self._detail = detail
        self._epoch = epoch
        self._lock = threading.Lock()
        self._held: tuple[object, T] | None = None
        self._flights: dict[object, _Flight[T]] = {}

    def get(self, store: Store, compute: Callable[[], T]) -> T:
        key = self._key(store)
        with self._lock:
            if self._held is not None and self._held[0] == key:
                return self._held[1]
            flight = self._flights.get(key)
            leading = flight is None
            if flight is None:
                flight = self._flights[key] = _Flight()
        if not leading:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return cast(T, flight.value)
        started = self._clock()
        began = None if self._epoch is None else self._epoch()
        try:
            value = compute()
        except BaseException as exc:
            flight.error = exc
            print(
                f"{self._name}: failed after {self._clock() - started:.1f} s",
                file=sys.stderr,
                flush=True,
            )
            raise
        else:
            flight.value = value
            more = "" if self._detail is None else f" ({self._detail(value)})"
            print(
                f"{self._name}: worked out in {self._clock() - started:.1f} s{more}",
                file=sys.stderr,
                flush=True,
            )
            # The key was read before the computation began, so a rebuild that reproduces the
            # same rows leaves it unchanged and would keep a value read from a half-built layer
            # for as long as the key holds. The value is kept only if no rebuild held the layer
            # when it began and none began or ended while it ran.
            settled = self._epoch is None or (
                began is not None and not began.held and began == self._epoch()
            )
            if settled:
                with self._lock:
                    self._held = (key, value)
            return value
        finally:
            with self._lock:
                del self._flights[key]
            flight.done.set()


class _Flight(Generic[T]):
    """One computation in progress, and what its waiters take from it."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: T | None = None
        self.error: BaseException | None = None


@dataclass(frozen=True)
class AccountStanding:
    """What a card or a list line says of an account: its agreement and its protection."""

    standing: Standing
    protected_through: date | None
    protection_broken: bool
    #: The date of the newest row that counts as money, or None where the account holds none.
    newest_row: date | None = None


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
            max((r.value_date for r in rows if not r.status.is_history), default=None),
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
