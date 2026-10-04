"""A person's "Fetch now": one connection's routine pull, attended, in the background.

The routine pull (tier ask, card pass, healing of days no landed ask covered)
runs unattended on a schedule and spends the provider's unattended allowance.
A request a person makes deliberately is attended, so the page can offer the
same pull on demand, declaring the requester's address.

THE ADDRESS RULE, stated here and nowhere else: a press on an aggregator
connection with no address that can honestly be declared is REFUSED.
Running it unattended instead would spend the very allowance the press exists
to avoid (a scheduler restarting repeatedly had overspent it in a day), under
a button whose label promises an attended request.
The scheduler already runs the unattended pull, so the refusal costs nothing
that was not already on offer, and it says how to declare an address.
A first-party Starling press needs no address, because that provider has no
customer-present distinction to declare.

THE COLLISION RULE: a press refuses, changing nothing, while a rebuild or a
stack update is in progress, while the scheduler holds its cycle lease, or
while another attended fetch holds the backfill lease.
The post-authorisation backfill's lease is reused rather than a new one made,
so the scheduler's own cycle gate and the stack updater already defer to a
press without either having to learn a new name.

THE STATUS FILE is the backfill's (`backfill-status.json`), generalised: its
single slot says what is running, and a `presses` map beside it keeps each
connection's last finished result, which a single slot cannot.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable, Collection
from datetime import UTC, date, datetime
from pathlib import Path

from . import leases
from .accounts import AccountMap
from .asked_coverage import canonical_resolver, coverage_by_account
from .pull import STARLING_CONNECTION
from .store import Store

#: The trigger label an attended press stamps on every ask it makes, so the
#: attempts ledger shows these apart from scheduled, cli, cli-attended and
#: web-extend asks.
FETCH_NOW_TRIGGER = "web-fetch-now"

#: The first-party provider's name as the pull command and the page know it.
STARLING_TARGET = "starling"

#: The lease a press holds, shared with the post-authorisation backfill.
PRESS_LEASE = "post-auth-backfill"

#: Longer than a pull over several accounts and cards takes; the lease is
#: released when the run ends, and expires by itself if the process dies.
PRESS_LEASE_TTL_SECONDS = 1800

#: How a status record marks itself as a press rather than a backfill.
PRESS_KIND = "fetch-now"

#: A provider's reason is kept short: it is shown on a phone-width page.
REASON_LIMIT = 200


class PressRefused(Exception):
    """A press that was not started, with the sentence that says why."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def write_status(path: Path, connection: str, **fields: object) -> None:
    """Replace the running-state slot, keeping every connection's last press."""
    presses = read_presses(path)
    record: dict[str, object] = {
        "connection": connection,
        "updated_at": _stamp(),
        **fields,
    }
    if presses:
        record["presses"] = presses
    write_json_atomic(path, record)


def record_press(path: Path, connection: str, result: dict[str, object]) -> None:
    """Keep a finished press's result for its connection, beside the slot."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    presses = read_presses(path)
    presses[connection] = result
    current["presses"] = presses
    write_json_atomic(path, current)


def read_presses(path: Path) -> dict[str, object]:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = current.get("presses") if isinstance(current, dict) else None
    return dict(found) if isinstance(found, dict) else {}


def write_json_atomic(path: Path, record: dict[str, object]) -> None:
    """Write-temp-then-rename, so a reader never sees a torn file.

    A failed write is swallowed: these files report on work, and the work must not fail for them.
    Every status file beside the store is written through this one function.
    """
    with contextlib.suppress(OSError):
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(path)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spawn_in_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="fetch-now", daemon=True).start()


def _today() -> date:
    return datetime.now(UTC).date()


def holes_remaining(
    store: Store, connection: str, account_map: AccountMap, today: date
) -> tuple[int, int, int]:
    """(healable spans, healable days, lost days) over one connection's accounts and cards."""
    refs = [account["account_id"] for account in store.accounts_for_connection(connection)] + [
        card["account_id"] for card in store.cards_for_connection(connection)
    ]
    mine = {str(account_map.resolve("truelayer", ref)) for ref in refs}
    coverage = coverage_by_account(store, canonical_resolver(account_map), today)
    spans = days = lost = 0
    for name in mine:
        found = coverage.get(name)
        if found is None:
            continue
        spans += len(found.reachable)
        days += sum(hole.days for hole in found.reachable)
        lost += sum(hole.days for hole in found.lost)
    return spans, days, lost


def start_press(
    *,
    name: str,
    psu_ip: str | None,
    db_path: Path,
    connections: Callable[[], Collection[str]],
    pull: Callable[[str, str | None, str], int | None],
    account_map: Callable[[Store], AccountMap],
    busy_note: Callable[[], str | None],
    spawn: Callable[[Callable[[], None]], None] = _spawn_in_thread,
    today: Callable[[], date] = _today,
) -> str:
    """Start one connection's attended routine pull; say what was started.

    `pull` runs the routine pull with the address and trigger it is given and
    returns how many rows were new (None when it cannot say); it raises on a
    refusal. `spawn` runs the work, so a test can make it joinable.
    Every refusal is a PressRefused raised before anything is written.
    """
    known = connections()
    if name not in known:
        raise PressRefused(f"There is no connection named {name!r} to fetch.", status=404)
    if name != STARLING_TARGET and not psu_ip:
        raise PressRefused(
            "No address for the device you are pressing from could be declared "
            "(the request arrived with none but this machine's own), so this was not "
            "run: an unattended request would spend the unattended allowance under a "
            "button that promises an attended one. The scheduler runs the unattended "
            "pull; to run it attended from a shell, give your address with "
            "obdi pull NAME --attended-from ADDRESS.",
            status=422,
        )
    busy = busy_note()
    if busy:
        raise PressRefused(f"Nothing was fetched: {busy}")
    locks = leases.locks_dir(db_path)
    if leases.held(locks, leases.STACK_UPDATE):
        raise PressRefused(
            "Nothing was fetched: a stack update is in progress and the container "
            "may be recreated underneath a fetch. Try again when it has finished."
        )
    if leases.held(locks, "pull-cycle"):
        raise PressRefused(
            "Nothing was fetched: the scheduler is mid-cycle and is pulling the same "
            "store. Try again in a few minutes; its lease expires by itself if it crashed."
        )
    status_path = db_path.parent / "backfill-status.json"
    if not leases.acquire_exclusive(locks, PRESS_LEASE, "obdi-web", PRESS_LEASE_TTL_SECONDS):
        raise PressRefused(_who_holds_the_lease(status_path))

    attended = name != STARLING_TARGET
    write_status(status_path, name, state="running", kind=PRESS_KIND, attended=attended)

    def run() -> None:
        ledger_id = STARLING_CONNECTION if name == STARLING_TARGET else name
        before = ledger_tail(db_path, ledger_id)
        stopped: dict[str, object] | None = None
        new_rows: int | None = None
        # The lease is released only after the result is recorded, so a second
        # press cannot start and have its running state overwritten by this one's end.
        try:
            try:
                new_rows = pull(name, psu_ip if attended else None, FETCH_NOW_TRIGGER)
            except Exception as exc:
                stopped = {
                    "status": getattr(exc, "status", None),
                    "code": str(getattr(exc, "code", "") or ""),
                    "reason": short_reason(str(exc)),
                }
            result = _result(
                db_path, name, ledger_id, before, new_rows, stopped, account_map, today()
            )
            record_press(status_path, name, result)
            write_status(status_path, name, state="done", kind=PRESS_KIND, outcome="completed")
        except Exception as exc:
            write_status(
                status_path, name, state="done", kind=PRESS_KIND, outcome=f"failed: {exc}"
            )
        finally:
            leases.release(locks, PRESS_LEASE)

    spawn(run)
    return (
        f"Started an attended fetch for {name}: the routine pull, with the days no "
        "request has covered. It runs in the background; the result appears on the "
        "Connections page when it finishes."
        if attended
        else f"Started a fetch for {name}. This provider has no customer-present "
        "distinction, so it is the same pull the scheduler runs. It runs in the "
        "background; the result appears on the Connections page when it finishes."
    )


def _who_holds_the_lease(status_path: Path) -> str:
    try:
        current = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if isinstance(current, dict) and current.get("state") == "running":
        connection = str(current.get("connection", "a connection"))
        if current.get("kind") == PRESS_KIND:
            return (
                f"Nothing was started: an attended fetch for {connection} is already "
                "running. Its result will appear on the Connections page."
            )
        return (
            f"Nothing was started: the post-authorisation backfill for {connection} "
            "is running and spending an authentication window on the same accounts."
        )
    return (
        "Nothing was started: another attended fetch or backfill holds its lease "
        "(it expires by itself if its process died)."
    )


def ledger_tail(db_path: Path, connection_id: str) -> int:
    with Store(db_path) as store:
        row = store.connection.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM fetch_attempts WHERE connection_id = ?",
            (connection_id,),
        ).fetchone()
    return int(row[0])


def short_reason(reason: str) -> str:
    cut = reason.split(" | headers:")[0].strip()
    return cut if len(cut) <= REASON_LIMIT else cut[: REASON_LIMIT - 3] + "..."


def _result(
    db_path: Path,
    name: str,
    ledger_id: str,
    before: int,
    new_rows: int | None,
    stopped: dict[str, object] | None,
    account_map: Callable[[Store], AccountMap],
    today: date,
) -> dict[str, object]:
    """What the press asked, what landed, what was refused, and what is still open."""
    with Store(db_path) as store:
        rows = store.connection.execute(
            "SELECT outcome, http_status, error_code, detail FROM fetch_attempts "
            "WHERE connection_id = ? AND rowid > ? ORDER BY rowid",
            (ledger_id, before),
        ).fetchall()
        refused: list[dict[str, object]] = [
            {
                "status": row["http_status"],
                "code": str(row["error_code"] or ""),
                "reason": short_reason(str(row["detail"] or "")),
            }
            for row in rows
            if row["outcome"] == "refused"
        ]
        if stopped is not None and not any(
            seen["status"] == stopped["status"] and seen["code"] == stopped["code"]
            for seen in refused
        ):
            refused.append(stopped)
        spans = days = lost = 0
        if name != STARLING_TARGET:
            spans, days, lost = holes_remaining(store, name, account_map(store), today)
    return {
        "finished_at": _stamp(),
        "attended": name != STARLING_TARGET,
        "asked": len(rows),
        "landed": sum(1 for row in rows if row["outcome"] == "landed"),
        "refused": refused[:3],
        "refused_total": len(refused),
        "stopped": stopped is not None,
        "new_rows": new_rows,
        "spans_remaining": spans,
        "days_remaining": days,
        "days_lost": lost,
    }
